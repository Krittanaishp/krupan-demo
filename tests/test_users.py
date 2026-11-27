"""หน้า /users (admin เท่านั้น)"""
import json

import pytest

from app import audit
from tests.conftest import PASSWORD, audit_rows, login


def uid(db, username):
    return db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[0]


def user_row(db, username):
    return db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


@pytest.mark.parametrize("who", ["editor1", "viewer1"])
def test_users_page_admin_only(client, db, who):
    login(client, who)
    assert client.get("/users").status_code == 403
    assert client.post("/users", data={"username": "x"}).status_code == 403
    assert client.post(f"/users/{uid(db, 'viewer1')}", data={"role": "admin"}).status_code == 403
    assert user_row(db, "viewer1")["role"] == "viewer"
    assert 'href="/users"' not in client.get("/assets").text  # ไม่มีเมนู


def test_users_page_requires_login(client):
    r = client.get("/users", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_create_user_and_login(admin, client, db):
    r = admin.post("/users", data={"username": " Somchai ", "display_name": "สมชาย", "role": "editor",
                                   "password": "new-password-1", "password2": "new-password-1"})
    assert "สร้างผู้ใช้ somchai แล้ว" in r.text
    row = user_row(db, "somchai")
    assert row["role"] == "editor" and row["password_hash"].startswith("$argon2")
    ch = json.loads(audit_rows(db, "users", row["id"])[0]["changes"])
    assert ch["username"]["new"] == "somchai" and "password" not in json.dumps(ch)
    client.post("/logout")
    assert login(client, "somchai", "new-password-1").status_code == 303


@pytest.mark.parametrize("form,msg", [
    ({"username": "ab"}, "ชื่อผู้ใช้ใช้ได้เฉพาะ"),
    ({"username": "editor1"}, "มีผู้ใช้ editor1 อยู่แล้ว"),
    ({"password": "short"}, "อย่างน้อย 8"),
    ({"password2": "different-1"}, "ไม่ตรงกัน"),
    ({"role": "superuser"}, "เลือกสิทธิ์"),
])
def test_create_user_validation(admin, db, form, msg):
    data = {"username": "newuser", "display_name": "", "role": "editor",
            "password": "new-password-1", "password2": "new-password-1", **form}
    r = admin.post("/users", data=data)
    assert r.status_code == 400 and msg in r.text
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3


def test_change_role_takes_effect_immediately(admin, db, client):
    from tests.conftest import Client
    from app.main import app
    with Client(app) as viewer:
        login(viewer, "viewer1")
        assert viewer.get("/assets/1/edit").status_code == 403
        admin.post(f"/users/{uid(db, 'viewer1')}", data={"display_name": "ผู้ใช้ viewer1", "role": "editor"})
        assert viewer.get("/assets/1/edit").status_code == 200
    log = audit_rows(db, "users", uid(db, "viewer1"))
    assert json.loads(log[-1]["changes"]) == {"role": {"old": "viewer", "new": "editor"}}


def test_reset_password(admin, db, client):
    target = uid(db, "editor1")
    db.execute("INSERT INTO login_attempts (username, failed_count, locked_until) VALUES ('editor1', 0, '2999-01-01 00:00:00')")
    db.commit()
    r = admin.post(f"/users/{target}/password", data={"password": "brand-new-pw", "password2": "brand-new-pw"})
    assert "ตั้งรหัสผ่านใหม่ให้ editor1 แล้ว" in r.text
    assert db.execute("SELECT COUNT(*) FROM login_attempts WHERE username = 'editor1'").fetchone()[0] == 0  # ปลดล็อกด้วย
    ch = json.loads(audit_rows(db, "users", target)[-1]["changes"])
    assert "brand-new-pw" not in json.dumps(ch) and "$argon2" not in json.dumps(ch)
    client.post("/logout")
    assert login(client, "editor1", PASSWORD).status_code == 400
    assert login(client, "editor1", "brand-new-pw").status_code == 303


def test_reset_password_validation(admin, db):
    before = user_row(db, "editor1")["password_hash"]
    r = admin.post(f"/users/{uid(db, 'editor1')}/password", data={"password": "short", "password2": "short"})
    assert "อย่างน้อย 8" in r.text
    assert user_row(db, "editor1")["password_hash"] == before


def test_deactivate_and_reactivate_user(admin, db, client):
    target = uid(db, "editor1")
    admin.post(f"/users/{target}/active", data={"active": "0"})
    assert user_row(db, "editor1")["active"] == 0
    assert [r["action"] for r in audit_rows(db, "users", target)] == ["update"]
    admin.post(f"/users/{target}/active", data={"active": "1"})
    assert user_row(db, "editor1")["active"] == 1


def test_users_cannot_be_deleted(admin):
    paths = [r.path for r in __import__("tests.test_security", fromlist=["all_routes"]).all_routes(
        __import__("app.main", fromlist=["app"]).app.routes)]
    assert not any("users" in p and "delete" in p for p in paths)


def test_cannot_demote_or_deactivate_last_admin(admin, db):
    me = uid(db, "admin1")
    r = admin.post(f"/users/{me}", data={"display_name": "", "role": "editor"})
    assert "ต้องมีผู้ดูแลระบบ" in r.text
    assert user_row(db, "admin1")["role"] == "admin"
    assert audit_rows(db, "users", me) == []


def test_cannot_deactivate_self(admin, db):
    admin.post("/users", data={"username": "admin2", "display_name": "", "role": "admin",
                               "password": "new-password-1", "password2": "new-password-1"})
    r = admin.post(f"/users/{uid(db, 'admin1')}/active", data={"active": "0"})
    assert "บัญชีของตัวเองไม่ได้" in r.text
    assert user_row(db, "admin1")["active"] == 1


def test_last_active_admin_protected_even_if_other_admin_inactive(admin, client, db):
    admin.post("/users", data={"username": "admin2", "display_name": "", "role": "admin",
                               "password": "new-password-1", "password2": "new-password-1"})
    admin.post(f"/users/{uid(db, 'admin2')}/active", data={"active": "0"})
    r = admin.post(f"/users/{uid(db, 'admin1')}", data={"display_name": "", "role": "viewer"})
    assert "ต้องมีผู้ดูแลระบบ" in r.text
    # ถ้ามี admin อีกคนที่ใช้งานได้ ก็ลดสิทธิ์ได้
    admin.post(f"/users/{uid(db, 'admin2')}/active", data={"active": "1"})
    client.post("/logout")
    login(client, "admin2", "new-password-1")
    client.post(f"/users/{uid(db, 'admin1')}", data={"display_name": "", "role": "editor"})
    assert user_row(db, "admin1")["role"] == "editor"
    r = client.post(f"/users/{uid(db, 'admin2')}", data={"display_name": "", "role": "editor"})
    assert "ต้องมีผู้ดูแลระบบ" in r.text


def test_system_account_is_read_only(admin, db):
    sid = audit.system_user_id(db)
    db.commit()
    html = admin.get("/users").text
    assert "system-migration" in html and "เข้าสู่ระบบไม่ได้" in html
    assert admin.post(f"/users/{sid}", data={"role": "admin"}).status_code == 404
    assert admin.post(f"/users/{sid}/password", data={"password": "x" * 10, "password2": "x" * 10}).status_code == 404
