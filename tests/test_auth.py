from tests.conftest import PASSWORD, login


def test_login_success_sets_httponly_session(client):
    r = login(client)
    assert r.status_code == 303
    assert r.headers["location"] == "/assets"
    cookie = r.headers["set-cookie"].lower()
    assert "krupan_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    page = client.get("/assets")
    assert "ผู้ใช้ editor1" in page.text and "ออกจากระบบ" in page.text


def test_login_wrong_password(client):
    r = login(client, password="wrong-password")
    assert r.status_code == 400
    assert "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง" in r.text
    assert 'action="/logout"' not in client.get("/assets").text


def test_login_unknown_user(client):
    assert login(client, username="nobody").status_code == 400


def test_logout(editor):
    r = editor.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    page = editor.get("/assets")
    assert 'action="/logout"' not in page.text and "เข้าสู่ระบบ" in page.text


def test_login_next_cannot_redirect_to_other_site(client):
    for bad in ("//evil.example/x", "https://evil.example/", "/\\evil.example"):
        r = client.post("/login", data={"username": "editor1", "password": PASSWORD, "next": bad},
                        follow_redirects=False)
        assert r.headers["location"] == "/assets", bad
        client.post("/logout")


def test_password_is_hashed(db):
    h = db.execute("SELECT password_hash FROM users WHERE username = 'editor1'").fetchone()[0]
    assert h.startswith("$argon2") and PASSWORD not in h
