"""ล็อกอิน / ออกจากระบบ (ล็อกชื่อผู้ใช้ 5 นาทีเมื่อใส่รหัสผ่านผิด 5 ครั้ง)"""
import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import csrf, timeutil
from app.auth import get_user, safe_next
from app.db import get_db
from app.security import burn_time, hash_password, needs_rehash, verify_password
from app.templating import templates, thai_datetime

router = APIRouter()

MAX_FAILED = 5
LOCK_MINUTES = 5
BAD_LOGIN = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"


def locked_until(db, username):
    row = db.execute("SELECT locked_until FROM login_attempts WHERE username = ?", (username,)).fetchone()
    if row and row["locked_until"]:
        until = datetime.fromisoformat(row["locked_until"])
        if until > timeutil.utcnow():
            return until
    return None


def record_failure(db, username):
    """นับครั้งที่ผิด ถ้าครบ MAX_FAILED ให้ล็อกและเริ่มนับใหม่ คืนเวลาที่ล็อกถึง (หรือ None)"""
    with db:
        db.execute("""INSERT INTO login_attempts (username, failed_count) VALUES (?, 1)
                      ON CONFLICT(username) DO UPDATE SET failed_count = failed_count + 1""", (username,))
        n = db.execute("SELECT failed_count FROM login_attempts WHERE username = ?", (username,)).fetchone()[0]
        if n >= MAX_FAILED:
            until = timeutil.utcnow() + timedelta(minutes=LOCK_MINUTES)
            db.execute("UPDATE login_attempts SET failed_count = 0, locked_until = ? WHERE username = ?",
                       (until.isoformat(sep=" ", timespec="seconds"), username))
            return until
    return None


def login_page(request, next_url, username, error=None, status_code=200):
    return templates.TemplateResponse(
        request, "login.html", {"next": safe_next(next_url), "error": error, "username": username},
        status_code=status_code)


def locked_message(until):
    return (f"ชื่อผู้ใช้นี้ถูกล็อกชั่วคราว เพราะใส่รหัสผ่านผิด {MAX_FAILED} ครั้ง "
            f"ลองใหม่ได้ตั้งแต่ {thai_datetime(until.isoformat(sep=' ', timespec='seconds')).split(' ')[-2]} น.")


@router.get("/login")
def login_form(request: Request, next: str = ""):
    if get_user(request):
        return RedirectResponse(safe_next(next), status_code=303)
    return login_page(request, next, "")


@router.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form(""),
          db: sqlite3.Connection = Depends(get_db)):
    username = username.strip().lower()[:64]
    # ชื่อที่ไม่มีอยู่จริงก็นับและล็อกเหมือนกัน คนนอกจึงเดาไม่ได้ว่าชื่อไหนมีในระบบ
    until = locked_until(db, username)
    if until:
        burn_time(password)
        return login_page(request, next, username, locked_message(until), status_code=429)

    row = db.execute("SELECT id, password_hash, active, session_version FROM users WHERE username = ?",
                     (username,)).fetchone()
    if row is None:
        burn_time(password)
        ok = False
    else:
        ok = verify_password(row["password_hash"], password)
    if not ok:
        until = record_failure(db, username)
        if until:
            return login_page(request, next, username, locked_message(until), status_code=429)
        return login_page(request, next, username, BAD_LOGIN, status_code=400)

    with db:
        db.execute("DELETE FROM login_attempts WHERE username = ?", (username,))
    if not row["active"]:
        return login_page(request, next, username, "บัญชีนี้ถูกปิดใช้งาน ติดต่อผู้ดูแลระบบ", status_code=400)
    if needs_rehash(row["password_hash"]):
        with db:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), row["id"]))
    request.session.clear()  # session ใหม่ทั้งหมด รวม CSRF token ใหม่ (กัน session fixation)
    request.session["uid"] = row["id"]
    request.session["sv"] = row["session_version"]
    csrf.get_token(request)
    return RedirectResponse(safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    request.session["flash"] = "ออกจากระบบแล้ว"
    return RedirectResponse("/assets", status_code=303)
