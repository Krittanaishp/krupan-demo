"""CSRF token และการล็อกชื่อผู้ใช้เมื่อล็อกอินผิด 5 ครั้ง"""
import re
from datetime import timedelta

import pytest

from app import timeutil
from app.main import app
from tests.conftest import PASSWORD, asset_form, login

# ค่าแทน path parameter ของทุก route ที่รับ POST
PATH_VALUES = {"asset_id": "1", "code": "1306", "location_id": "1", "staff_id": "1", "user_id": "1",
               "preset_id": "1", "check_id": "1", "room": "none", "group": "damaged", "repair_id": "1", "disposal_id": "1",
               "merge_id": "1", "rule_id": "1", "kind": "buildings", "area_id": "1",
               "item_id": "1", "unit_id": "1", "cat_id": "1", "rid": "1", "iid": "1", "batch_id": "1"}


def all_routes(routes):
    for r in routes:
        inner = getattr(r, "original_router", None)  # FastAPI รุ่นนี้ห่อ router ที่ include ไว้อีกชั้น
        if inner is not None:
            yield from all_routes(inner.routes)
        else:
            yield r


def post_routes():
    out = []
    for r in all_routes(app.routes):
        if "POST" in (getattr(r, "methods", None) or set()):
            out.append(re.sub(r"\{(\w+)\}", lambda m: PATH_VALUES[m.group(1)], r.path))
    return sorted(out)


def test_every_post_route_is_listed():
    routes = post_routes()
    assert "/login" in routes and "/logout" in routes and "/assets/1/edit" in routes
    assert "/locations/mapping" in routes and "/users/1/password" in routes and "/review/undo" in routes


@pytest.mark.parametrize("url", post_routes())
def test_post_without_csrf_token_rejected(admin, db, url):
    before = db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    r = admin.post(url, data={"name": "x", "asset_id": 1, "flag": "ไม่มีราคา", "text": "ห้องปกครอง"},
                   csrf=False, follow_redirects=False)
    assert r.status_code == 403, url
    assert "ส่งฟอร์มไม่สำเร็จ" in r.text
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == before


def test_wrong_or_foreign_csrf_token_rejected(editor, db, db_path):
    r = editor.post("/assets/1/edit", data=asset_form(name="ถูกปลอม", csrf_token="not-the-token"))
    assert r.status_code == 403
    # token ของ session อื่น (เช่นจากเบราว์เซอร์ของคนอื่น) ใช้ไม่ได้
    from tests.conftest import Client
    with Client(app) as other:
        login(other, "admin1")
        foreign = other.csrf_token()
    r = editor.post("/assets/1/edit", data=asset_form(name="ถูกปลอม", csrf_token=foreign))
    assert r.status_code == 403
    assert db.execute("SELECT name FROM assets WHERE id = 1").fetchone()[0] == "ตู้เหล็ก 4 ลิ้นชัก"


def test_csrf_header_accepted_for_htmx(editor, db):
    token = editor.csrf_token()
    r = editor.post("/review/resolve", data={"asset_id": 2, "flag": "ไม่มีหมายเลขทะเบียน", "group": "regno"},
                    headers={"HX-Request": "true", "X-CSRF-Token": token}, csrf=False)
    assert r.status_code == 200
    assert db.execute("SELECT needs_review FROM assets WHERE id = 2").fetchone()[0] is None


def test_htmx_csrf_failure_asks_page_refresh(editor):
    r = editor.post("/review/resolve", data={"asset_id": 2, "flag": "x"}, headers={"HX-Request": "true"}, csrf=False)
    assert r.status_code == 403 and r.headers.get("HX-Refresh") == "true"


def test_every_form_has_csrf_field(admin, db):
    db.execute("INSERT INTO locations (name) VALUES ('ห้องทดสอบ')")
    db.execute("INSERT INTO staff (nickname) VALUES ('เอ็ม')")
    db.commit()
    for url in ("/assets/1/edit", "/review?group=price", "/types", "/locations", "/locations/mapping",
                "/staff", "/users", "/assets"):
        html = admin.get(url).text
        forms = re.findall(r"<form[^>]*method=\"post\"[^>]*>(.*?)</form>", html, re.S)
        assert forms, url
        for f in forms:
            assert 'name="csrf_token"' in f, (url, f[:200])
        assert '"X-CSRF-Token"' in html  # htmx ส่ง header ด้วย


def test_csrf_token_changes_after_login(client):
    before = client.csrf_token()
    login(client)
    assert client.csrf_token() != before


# ---------------------------------------------------------------- ล็อกชื่อผู้ใช้

def fail(client, n, username="editor1"):
    return [login(client, username, "wrong-password").status_code for _ in range(n)]


def test_lock_after_five_failures(client, db):
    assert fail(client, 4) == [400] * 4
    r = login(client, password="wrong-password")
    assert r.status_code == 429 and "ถูกล็อกชั่วคราว" in r.text
    # ระหว่างล็อก รหัสถูกก็เข้าไม่ได้
    r = login(client)
    assert r.status_code == 429 and "ถูกล็อกชั่วคราว" in r.text
    assert 'action="/logout"' not in client.get("/assets").text


def test_lock_expires_after_five_minutes(client, monkeypatch):
    fail(client, 5)
    later = timeutil.utcnow() + timedelta(minutes=5, seconds=1)
    monkeypatch.setattr(timeutil, "utcnow", lambda: later)
    assert login(client).status_code == 303


def test_lock_is_per_username(client):
    fail(client, 5, "editor1")
    assert login(client, "admin1").status_code == 303


def test_success_resets_failure_count(client, db):
    fail(client, 4)
    assert login(client).status_code == 303
    client.post("/logout")
    assert fail(client, 4) == [400] * 4  # นับใหม่จาก 0 ยังไม่ล็อก
    assert login(client).status_code == 303


def test_unknown_username_also_locks(client, db):
    """ชื่อที่ไม่มีจริงก็ล็อกเหมือนกัน เดาไม่ได้ว่าชื่อไหนมีในระบบ"""
    assert fail(client, 5, "nobody")[-1] == 429
    assert db.execute("SELECT locked_until FROM login_attempts WHERE username = 'nobody'").fetchone()[0]


def test_inactive_user_cannot_login_and_session_ends(client, db):
    assert login(client).status_code == 303
    db.execute("UPDATE users SET active = 0 WHERE username = 'editor1'")
    db.commit()
    assert client.get("/assets/1/edit", follow_redirects=False).status_code == 303  # session เดิมใช้ไม่ได้แล้ว
    r = login(client)
    assert r.status_code == 400 and "ถูกปิดใช้งาน" in r.text


def test_password_never_in_lock_table(client, db):
    fail(client, 2)
    row = db.execute("SELECT * FROM login_attempts").fetchone()
    assert "wrong-password" not in str(tuple(row)) and PASSWORD not in str(tuple(row))
