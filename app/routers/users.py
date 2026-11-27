"""จัดการผู้ใช้ (admin เท่านั้น): สร้าง เปลี่ยนสิทธิ์ รีเซ็ตรหัสผ่าน ปิด/เปิดใช้งาน

ไม่มีการลบผู้ใช้ เพราะ audit_log อ้างถึง และต้องเหลือ admin ที่ใช้งานได้อย่างน้อย 1 คนเสมอ
"""
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import audit
from app.auth import require_admin
from app.db import get_db
from app.places import normalize
from app.security import MIN_PASSWORD_LENGTH, ROLES, USERNAME_RE, hash_password
from app.templating import templates

router = APIRouter()

LAST_ADMIN = "ต้องมีผู้ดูแลระบบ (admin) ที่ใช้งานได้อย่างน้อย 1 คน"
# อัปเดตเฉพาะเมื่อไม่ทำให้ admin ที่ใช้งานได้หมดไป (ตรวจและแก้ในคำสั่งเดียว กันกดพร้อมกันสองคน)
KEEPS_AN_ADMIN = """NOT (role = 'admin' AND active = 1 AND (:role != 'admin' OR :active = 0)
                        AND (SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != :id) = 0)"""


def users_page(request, db, error=None, form=None, status_code=200):
    rows = db.execute("""SELECT u.id, u.username, u.display_name, u.role, u.active, u.must_change_password,
                                u.password_hash = ? AS is_system,
                                (SELECT MAX(created_at) FROM audit_log l WHERE l.user_id = u.id AND l.action != 'export') AS last_edit
                         FROM users u ORDER BY is_system, u.active DESC, u.username""",
                      (audit.UNUSABLE_PASSWORD_HASH,)).fetchall()
    return templates.TemplateResponse(request, "users/list.html", {
        "rows": rows, "roles": ROLES, "error": error, "form": form or {"role": "editor"},
        "min_password": MIN_PASSWORD_LENGTH}, status_code=status_code)


def user_or_404(db, user_id):
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or row["password_hash"] == audit.UNUSABLE_PASSWORD_HASH:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้นี้")  # บัญชีระบบแก้จากหน้าเว็บไม่ได้
    return row


def password_error(password, password2):
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD_LENGTH} ตัวอักษร"
    if password != password2:
        return "รหัสผ่านสองช่องไม่ตรงกัน"
    return None


def done(request, message):
    request.session["flash"] = message
    return RedirectResponse("/users", status_code=303)


@router.get("/users")
def list_users(request: Request, user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    return users_page(request, db)


@router.post("/users")
def create_user(request: Request, username: str = Form(""), display_name: str = Form(""), role: str = Form("editor"),
                password: str = Form(""), password2: str = Form(""), force_change: str = Form(""),
                user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    username = username.strip().lower()
    force = 1 if force_change else 0
    display_name = normalize(display_name) or None
    form = {"username": username, "display_name": display_name or "", "role": role, "force_change": force}
    error = None
    if not USERNAME_RE.fullmatch(username):
        error = "ชื่อผู้ใช้ใช้ได้เฉพาะ a-z 0-9 . _ - ยาว 3-32 ตัว"
    elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        error = f"มีผู้ใช้ {username} อยู่แล้ว"
    elif role not in ROLES:
        error = "เลือกสิทธิ์"
    else:
        error = password_error(password, password2)
    if error:
        return users_page(request, db, error=error, form=form, status_code=400)
    with db:
        uid = db.execute("""INSERT INTO users (username, password_hash, display_name, role, must_change_password)
                            VALUES (?, ?, ?, ?, ?)""", (username, hash_password(password), display_name, role, force)).lastrowid
        audit.log(db, user["id"], "users", uid, "insert", {
            "username": {"old": None, "new": username}, "display_name": {"old": None, "new": display_name},
            "role": {"old": None, "new": role}, **({"must_change_password": {"old": None, "new": 1}} if force else {})})
    return done(request, f"สร้างผู้ใช้ {username} แล้ว" + (" (ต้องเปลี่ยนรหัสผ่านเมื่อเข้าระบบครั้งแรก)" if force else ""))


@router.post("/users/{user_id}")
def update_user(request: Request, user_id: int, display_name: str = Form(""), role: str = Form(""),
                user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    old = user_or_404(db, user_id)
    if role not in ROLES:
        return done(request, "เลือกสิทธิ์")
    new = {"display_name": normalize(display_name) or None, "role": role}
    changes = audit.diff(old, new, ("display_name", "role"))
    if not changes:
        return done(request, "ไม่มีข้อมูลที่เปลี่ยนแปลง")
    with db:
        cur = db.execute(f"UPDATE users SET display_name = :name, role = :role WHERE id = :id AND {KEEPS_AN_ADMIN}",
                         {"name": new["display_name"], "role": role, "active": old["active"], "id": user_id})
        if cur.rowcount == 0:
            return done(request, f"เปลี่ยนสิทธิ์ {old['username']} ไม่ได้: {LAST_ADMIN}")
        audit.log(db, user["id"], "users", user_id, "update", changes)
    return done(request, f"บันทึกผู้ใช้ {old['username']} แล้ว")


@router.post("/users/{user_id}/password")
def reset_password(request: Request, user_id: int, password: str = Form(""), password2: str = Form(""),
                   force_change: str = Form(""), user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    old = user_or_404(db, user_id)
    force = 1 if force_change and user_id != user["id"] else 0  # รีเซ็ตรหัสตัวเองไม่ต้องบังคับ
    error = password_error(password, password2)
    if error:
        return done(request, f"รีเซ็ตรหัสผ่าน {old['username']} ไม่ได้: {error}")
    with db:
        # session_version + 1: ทุกเครื่องที่ล็อกอินเป็นผู้ใช้นี้อยู่หลุดทันที
        db.execute("""UPDATE users SET password_hash = ?, session_version = session_version + 1, must_change_password = ?
                      WHERE id = ?""", (hash_password(password), force, user_id))
        db.execute("DELETE FROM login_attempts WHERE username = ?", (old["username"],))  # ปลดล็อกด้วย
        changes = {"password": {"old": "(ซ่อน)", "new": "ตั้งรหัสผ่านใหม่"}}
        if force != old["must_change_password"]:
            changes["must_change_password"] = {"old": old["must_change_password"], "new": force}
        audit.log(db, user["id"], "users", user_id, "update", changes)
    if user_id == user["id"]:
        # รีเซ็ตรหัสของตัวเอง: คง session ปัจจุบันไว้ (เครื่องอื่นหลุด)
        request.session["sv"] = old["session_version"] + 1
        return done(request, "ตั้งรหัสผ่านใหม่ของคุณแล้ว เครื่องอื่นที่ล็อกอินบัญชีนี้ไว้ต้องเข้าสู่ระบบใหม่")
    return done(request, f"ตั้งรหัสผ่านใหม่ให้ {old['username']} แล้ว (เครื่องที่ล็อกอินค้างไว้หลุดหมดแล้ว) "
                         "แจ้งรหัสผ่านให้เจ้าของบัญชีโดยตรง"
                         + (" · เจ้าของบัญชีต้องเปลี่ยนรหัสเองเมื่อเข้าระบบครั้งถัดไป" if force else ""))


@router.post("/users/{user_id}/active")
def set_active(request: Request, user_id: int, active: int = Form(...),
               user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    old = user_or_404(db, user_id)
    active = 1 if active else 0
    if active == old["active"]:
        return done(request, "ไม่มีข้อมูลที่เปลี่ยนแปลง")
    if not active and user_id == user["id"]:
        return done(request, "ปิดใช้งานบัญชีของตัวเองไม่ได้ ให้ admin คนอื่นทำแทน")
    with db:
        cur = db.execute(f"UPDATE users SET active = :active WHERE id = :id AND {KEEPS_AN_ADMIN}",
                         {"active": active, "role": old["role"], "id": user_id})
        if cur.rowcount == 0:
            return done(request, f"ปิดใช้งาน {old['username']} ไม่ได้: {LAST_ADMIN}")
        audit.log(db, user["id"], "users", user_id, "update", {"active": {"old": old["active"], "new": active}})
    return done(request, f"{'เปิด' if active else 'ปิด'}ใช้งาน {old['username']} แล้ว")
