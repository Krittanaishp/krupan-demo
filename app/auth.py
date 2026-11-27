"""ผู้ใช้ปัจจุบันจาก session และการตรวจสิทธิ์"""
import sqlite3
from urllib.parse import quote, urlparse

from fastapi import Request

from app.db import connect
from app.security import EDIT_ROLES


class LoginRequired(Exception):
    def __init__(self, next_url):
        self.next_url = next_url


class Forbidden(Exception):
    pass


class SchemaOutdated(Exception):
    """db ยังไม่ได้รัน migration ล่าสุด (ไม่มีตาราง/คอลัมน์ที่โค้ดรุ่นนี้ใช้) -> หน้า "ต้องปรับฐานข้อมูลก่อน" (503)"""


class PasswordChangeRequired(Exception):
    """ผู้ใช้ที่ admin สร้าง/รีเซ็ตรหัสให้ (ติ๊กบังคับเปลี่ยน) ต้องเปลี่ยนรหัสก่อนเปิดหน้าอื่น"""


def safe_next(url, default="/assets"):
    """รับเฉพาะ path ภายในระบบ กันการพาไปเว็บอื่นหลังล็อกอิน (open redirect)"""
    if not url or not url.startswith("/") or url.startswith("//") or url.startswith("/\\"):
        return default
    p = urlparse(url)
    if p.scheme or p.netloc:
        return default
    return url


def get_user(request: Request):
    """ผู้ใช้ที่ล็อกอินอยู่ (dict) หรือ None; เก็บไว้ใน request.state เพื่อไม่ต้อง query ซ้ำ
    อ่าน role/active จาก db ทุก request: เปลี่ยนสิทธิ์หรือปิดใช้งานแล้วมีผลทันที"""
    if hasattr(request.state, "user"):
        return request.state.user
    user = None
    uid = request.session.get("uid") if "session" in request.scope else None
    if uid is not None:
        conn = connect()
        try:
            row = conn.execute("SELECT id, username, display_name, role, active, session_version, must_change_password FROM users "
                               "WHERE id = ?", (uid,)).fetchone()
        except sqlite3.OperationalError as e:
            if "no such" not in str(e):
                raise
            # ยังไม่ได้รัน migration 011: จำไว้ว่าไม่มีผู้ใช้ (หน้าแจ้งเตือนเรียก get_user ซ้ำตอนสร้างเมนูจะไม่พังอีก)
            request.state.user = None
            raise SchemaOutdated() from None
        finally:
            conn.close()
        # session_version เพิ่มทุกครั้งที่รีเซ็ตรหัสผ่าน: session ที่สร้างก่อนหน้านั้นใช้ไม่ได้อีก
        if row and row["active"] and request.session.get("sv", 0) == row["session_version"]:
            user = dict(row)
            user["can_edit"] = user["role"] in EDIT_ROLES
            user["is_admin"] = user["role"] == "admin"
            user["must_change"] = bool(user.pop("must_change_password"))
        else:
            request.session.clear()  # ผู้ใช้ถูกปิดใช้งาน หรือรหัสผ่านถูกรีเซ็ตหลังจากล็อกอิน
    request.state.user = user
    return user


def can_edit(request: Request):
    return bool((get_user(request) or {}).get("can_edit"))


def is_admin(request: Request):
    return bool((get_user(request) or {}).get("is_admin"))


def _login_next(request: Request):
    if request.method == "GET":
        return request.url.path + (f"?{request.url.query}" if request.url.query else "")
    ref = urlparse(request.headers.get("referer", ""))
    if ref.netloc == request.url.netloc and ref.path:
        return ref.path + (f"?{ref.query}" if ref.query else "")
    return "/assets"


def require_user(request: Request):
    """dependency ของหน้ารายงาน: ต้องล็อกอิน (สิทธิ์ใดก็ได้)"""
    user = get_user(request)
    if user is None:
        raise LoginRequired(_login_next(request))
    return user


def require_editor(request: Request):
    """dependency ของหน้าแก้ไขทุกหน้า: ต้องล็อกอินเป็น editor หรือ admin"""
    user = get_user(request)
    if user is None:
        raise LoginRequired(_login_next(request))
    if not user["can_edit"]:
        raise Forbidden()
    return user


def require_admin(request: Request):
    """dependency ของหน้าจัดการผู้ใช้: admin เท่านั้น"""
    user = get_user(request)
    if user is None:
        raise LoginRequired(_login_next(request))
    if not user["is_admin"]:
        raise Forbidden()
    return user


# หน้าที่เปิดได้ระหว่างที่ยังต้องเปลี่ยนรหัสผ่าน (หน้าเปลี่ยนรหัส ออกจากระบบ ตรวจสถานะ)
PASSWORD_GATE_OPEN = ("/account/password", "/logout", "/login", "/health")


def password_gate(request: Request):
    """dependency ของทุก route: ยังต้องเปลี่ยนรหัส -> ส่งไปหน้าเปลี่ยนรหัสผ่าน (static ไม่ผ่าน dependency นี้)"""
    if request.url.path in PASSWORD_GATE_OPEN:
        return
    user = get_user(request)
    if user and user["must_change"]:
        raise PasswordChangeRequired()


def login_url(next_url):
    return "/login?next=" + quote(next_url, safe="/")
