"""เปลี่ยนรหัสผ่านของตัวเอง และการบังคับเปลี่ยนครั้งแรก (0.9.0)"""
import json

import pytest

from app.main import app
from tests.conftest import PASSWORD, Client, audit_rows, login

NEW = "brand-new-pass-9"


def form(current=PASSWORD, password=NEW, password2=None):
    return {"current": current, "password": password, "password2": password if password2 is None else password2}


def uid(db, username):
    return db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[0]


@pytest.mark.parametrize("who", ["viewer1", "editor1", "admin1"])
def test_every_role_can_change_own_password(client, db, who):
    login(client, who)
    page = client.get("/assets").text
    assert 'href="/account/password"' in page and "ออกจากระบบ" in page  # เมนูชื่อผู้ใช้
    r = client.post("/account/password", data=form(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert client.get("/assets").status_code == 200  # เครื่องนี้ยังล็อกอินอยู่
    client.post("/logout")
    assert login(client, who, PASSWORD).status_code == 400
    assert login(client, who, NEW).status_code == 303
    row = audit_rows(db, "users", uid(db, who))[-1]
    ch = json.loads(row["changes"])
    assert row["user_id"] == uid(db, who) and ch["password"]["new"] == "เปลี่ยนรหัสผ่านเอง"
    assert PASSWORD not in row["changes"] and NEW not in row["changes"]


def test_other_sessions_are_logged_out(client, db):
    login(client, "editor1")
    with Client(app) as other:
        login(other, "editor1")
        assert other.get("/account/password").status_code == 200
        client.post("/account/password", data=form())
        r = other.get("/account/password", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.get("/account/password").status_code == 200


@pytest.mark.parametrize("data,msg", [
    (form(current="wrong-password"), "รหัสผ่านเดิมไม่ถูกต้อง"),
    (form(password="short"), "อย่างน้อย 8"),
    (form(password2="something-else"), "ไม่ตรงกัน"),
    (form(password=PASSWORD), "ไม่ซ้ำกับรหัสเดิม"),
])
def test_validation(editor, db, data, msg):
    before = db.execute("SELECT password_hash, session_version FROM users WHERE username = 'editor1'").fetchone()
    r = editor.post("/account/password", data=data)
    assert r.status_code == 400 and msg in r.text
    after = db.execute("SELECT password_hash, session_version FROM users WHERE username = 'editor1'").fetchone()
    assert tuple(before) == tuple(after)


def test_wrong_current_password_counts_toward_lockout(editor):
    for _ in range(4):
        assert editor.post("/account/password", data=form(current="nope-nope")).status_code == 400
    r = editor.post("/account/password", data=form(current="nope-nope"))
    assert r.status_code == 429 and "ล็อกชั่วคราว" in r.text
    # ล็อกแล้ว รหัสเดิมที่ถูกก็ยังเปลี่ยนไม่ได้จนกว่าจะพ้นเวลา
    assert editor.post("/account/password", data=form()).status_code == 429


def test_requires_login(client):
    r = client.get("/account/password", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert client.post("/account/password", data=form(), follow_redirects=False).status_code == 303


# ---------------------------------------------------------------- บังคับเปลี่ยนรหัสครั้งแรก

def test_admin_creates_user_with_forced_change(admin, client, db):
    page = admin.get("/users").text
    assert 'name="force_change" value="1" checked' in page  # ติ๊กไว้ให้เป็นค่าเริ่มต้น
    r = admin.post("/users", data={"username": "clerk", "display_name": "เสมียนตรา", "role": "editor",
                                   "password": "first-pass-1", "password2": "first-pass-1", "force_change": "1"})
    assert "ต้องเปลี่ยนรหัสผ่านเมื่อเข้าระบบครั้งแรก" in r.text
    assert db.execute("SELECT must_change_password FROM users WHERE username = 'clerk'").fetchone()[0] == 1
    assert "ต้องเปลี่ยนรหัส" in admin.get("/users").text
    admin.post("/logout")

    login(client, "clerk", "first-pass-1")
    for url in ("/assets", "/review", "/reports"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/account/password", url
    r = client.get("/assets", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.headers.get("HX-Redirect") == "/account/password"
    assert client.post("/assets/1/edit", data={"name": "x"}, follow_redirects=False).headers["location"] == "/account/password"
    page = client.get("/account/password").text
    assert "กรุณาตั้งรหัสผ่านใหม่ของคุณเองก่อนใช้งาน" in page
    assert client.get("/health").status_code == 200

    client.post("/account/password", data=form(current="first-pass-1"))
    assert db.execute("SELECT must_change_password FROM users WHERE username = 'clerk'").fetchone()[0] == 0
    assert client.get("/assets").status_code == 200
    ch = json.loads(audit_rows(db, "users", uid(db, "clerk"))[-1]["changes"])
    assert ch["must_change_password"] == {"old": 1, "new": 0}


def test_forced_user_can_log_out(admin, client, db):
    admin.post("/users", data={"username": "clerk2", "role": "viewer", "password": "first-pass-1",
                               "password2": "first-pass-1", "force_change": "1"})
    admin.post("/logout")
    login(client, "clerk2", "first-pass-1")
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/assets"
    assert client.get("/assets").status_code == 200  # ไม่ได้ล็อกอินแล้ว ดูหน้าทะเบียนได้ตามปกติ


def test_admin_reset_can_force_change(admin, client, db):
    vid = uid(db, "viewer1")
    admin.post(f"/users/{vid}/password", data={"password": "reset-pass-1", "password2": "reset-pass-1", "force_change": "1"})
    assert db.execute("SELECT must_change_password FROM users WHERE id = ?", (vid,)).fetchone()[0] == 1
    ch = json.loads(audit_rows(db, "users", vid)[-1]["changes"])
    assert ch["must_change_password"] == {"old": 0, "new": 1} and "reset-pass-1" not in json.dumps(ch)
    # ไม่ติ๊ก = ไม่บังคับ (และล้างค่าที่ติ๊กไว้ก่อนหน้า)
    admin.post(f"/users/{vid}/password", data={"password": "reset-pass-2", "password2": "reset-pass-2"})
    assert db.execute("SELECT must_change_password FROM users WHERE id = ?", (vid,)).fetchone()[0] == 0


def test_admin_reset_own_password_never_forced(admin, db):
    aid = uid(db, "admin1")
    admin.post(f"/users/{aid}/password", data={"password": "reset-pass-1", "password2": "reset-pass-1", "force_change": "1"})
    assert db.execute("SELECT must_change_password FROM users WHERE id = ?", (aid,)).fetchone()[0] == 0
    assert admin.get("/users").status_code == 200
