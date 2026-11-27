"""เก็บตกส่วนที่ 4: รีเซ็ตรหัสผ่านแล้ว session หลุด และเหตุผลของ asset_moves"""
from app.main import app
from tests.conftest import Client, add_location, asset_form, login


def uid(db, username):
    return db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[0]


def is_logged_in(c):
    return 'action="/logout"' in c.get("/assets").text


def test_reset_password_logs_out_all_sessions_of_that_user(admin, db):
    with Client(app) as phone, Client(app) as pc:
        login(phone, "editor1")
        login(pc, "editor1")
        assert is_logged_in(phone) and is_logged_in(pc)
        admin.post(f"/users/{uid(db, 'editor1')}/password", data={"password": "brand-new-pw", "password2": "brand-new-pw"})
        assert not is_logged_in(phone) and not is_logged_in(pc)
        assert phone.get("/assets/1/edit", follow_redirects=False).status_code == 303
        assert login(phone, "editor1", "brand-new-pw").status_code == 303 and is_logged_in(phone)
    assert is_logged_in(admin)  # admin ที่กดรีเซ็ตไม่โดนด้วย


def test_reset_own_password_keeps_current_session_only(admin, db):
    with Client(app) as other_device:
        login(other_device, "admin1")
        r = admin.post(f"/users/{uid(db, 'admin1')}/password",
                       data={"password": "my-new-pass-1", "password2": "my-new-pass-1"})
        assert "เครื่องอื่นที่ล็อกอินบัญชีนี้ไว้ต้องเข้าสู่ระบบใหม่" in r.text
        assert is_logged_in(admin)
        assert admin.get("/users").status_code == 200
        assert not is_logged_in(other_device)


def test_role_change_does_not_log_out(admin, db):
    with Client(app) as c:
        login(c, "viewer1")
        admin.post(f"/users/{uid(db, 'viewer1')}", data={"display_name": "", "role": "editor"})
        assert is_logged_in(c)


def test_create_user_script_update_bumps_session_version(db):
    from scripts.create_user import save_user
    before = db.execute("SELECT session_version FROM users WHERE username = 'editor1'").fetchone()[0]
    save_user(db, "editor1", "another-pass-1", "editor", update=True)
    assert db.execute("SELECT session_version FROM users WHERE username = 'editor1'").fetchone()[0] == before + 1


# ---------------------------------------------------------------- เหตุผลของการย้าย

def reasons(db, asset_id):
    return [r[0] for r in db.execute("SELECT reason FROM asset_moves WHERE asset_id = ? ORDER BY id", (asset_id,))]


def test_mapping_first_time_then_remap_reasons(editor, db):
    a = add_location(db, "ห้อง ก")
    b = add_location(db, "ห้อง ข")
    for room in (a, b):
        editor.post("/locations/mapping", data={"text": "ห้องปกครอง", "location": str(room)},
                    headers={"HX-Request": "true"})
    assert reasons(db, 1) == ["จัดห้องครั้งแรก", "แก้การจัดห้อง"]


def test_edit_page_move_reasons(editor, db):
    a = add_location(db, "ห้อง ก")
    b = add_location(db, "ห้อง ข")
    editor.post("/assets/1/edit", data=asset_form(location_id=str(a)))
    editor.post("/assets/1/edit", data=asset_form(location_id=str(b)))
    assert reasons(db, 1) == ["จัดห้องครั้งแรก", "ย้ายห้อง"]
    assert "จัดห้องครั้งแรก" in editor.get("/assets/1").text  # แสดงในประวัติการย้าย
