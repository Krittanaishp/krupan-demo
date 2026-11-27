"""เปลี่ยนรหัสผ่านของตัวเอง (ทุกสิทธิ์) · บังคับเปลี่ยนครั้งแรกสำหรับบัญชีที่ admin สร้าง/รีเซ็ตให้

- ต้องใส่รหัสเดิม: ใส่ผิดนับรวมกับการล็อกเมื่อล็อกอินผิด (5 ครั้ง ล็อก 5 นาที) กันการเดารหัสจากหน้านี้
- รหัสใหม่อย่างน้อย MIN_PASSWORD_LENGTH ตัว ต้องพิมพ์ซ้ำให้ตรง และต้องไม่ซ้ำรหัสเดิม
- หลังเปลี่ยน session_version + 1: เครื่องอื่นที่ล็อกอินบัญชีนี้ค้างไว้หลุดทันที เครื่องนี้ยังใช้ต่อได้
"""
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import audit
from app.auth import require_user
from app.db import get_db
from app.routers.auth import locked_message, locked_until, record_failure
from app.routers.users import password_error
from app.security import MIN_PASSWORD_LENGTH, hash_password, verify_password
from app.templating import templates

router = APIRouter()


def page(request, error=None, status_code=200):
    return templates.TemplateResponse(request, "account/password.html",
                                      {"error": error, "min_password": MIN_PASSWORD_LENGTH}, status_code=status_code)


@router.get("/account/password")
def password_form(request: Request, user=Depends(require_user)):
    return page(request)


@router.post("/account/password")
def change_password(request: Request, current: str = Form(""), password: str = Form(""), password2: str = Form(""),
                    user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    if locked_until(db, user["username"]):
        return page(request, locked_message(locked_until(db, user["username"])), status_code=429)
    row = db.execute("SELECT password_hash, session_version FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not verify_password(row["password_hash"], current):
        until = record_failure(db, user["username"])
        if until:
            return page(request, locked_message(until), status_code=429)
        return page(request, "รหัสผ่านเดิมไม่ถูกต้อง", status_code=400)
    error = password_error(password, password2)
    if not error and password == current:
        error = "รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสเดิม"
    if error:
        return page(request, error, status_code=400)
    forced = user["must_change"]
    with db:
        db.execute("""UPDATE users SET password_hash = ?, session_version = session_version + 1, must_change_password = 0
                      WHERE id = ?""", (hash_password(password), user["id"]))
        db.execute("DELETE FROM login_attempts WHERE username = ?", (user["username"],))
        changes = {"password": {"old": "(ซ่อน)", "new": "เปลี่ยนรหัสผ่านเอง"}}
        if forced:
            changes["must_change_password"] = {"old": 1, "new": 0}
        audit.log(db, user["id"], "users", user["id"], "update", changes)
    request.session["sv"] = row["session_version"] + 1  # เครื่องนี้ใช้ต่อได้ เครื่องอื่นหลุด
    request.session["flash"] = "เปลี่ยนรหัสผ่านแล้ว เครื่องอื่นที่ล็อกอินบัญชีนี้ค้างไว้ต้องเข้าสู่ระบบใหม่"
    return RedirectResponse("/", status_code=303)
