"""กัน CSRF: token สุ่มต่อ session ใส่ในทุกฟอร์มที่ POST (และ header X-CSRF-Token สำหรับ htmx)

ตรวจทุก request ที่ไม่ใช่ GET/HEAD/OPTIONS ผ่าน dependency ระดับ app (ดู app/main.py)
"""
import secrets

from fastapi import Request
from markupsafe import Markup

FIELD = "csrf_token"
HEADER = "X-CSRF-Token"
SESSION_KEY = "csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


class CsrfFailed(Exception):
    pass


def get_token(request: Request):
    """token ของ session นี้ (สร้างใหม่ถ้ายังไม่มี เช่นหลังล็อกอินซึ่งล้าง session)"""
    if "session" not in request.scope:
        return ""
    token = request.session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_KEY] = token
    return token


def hidden_field(request: Request):
    return Markup('<input type="hidden" name="{}" value="{}">').format(FIELD, get_token(request))


# อ่านฟอร์มครั้งแรกที่นี่ (route อ่านซ้ำได้จากที่เก็บไว้) จึงต้องตั้งเพดานจำนวนช่องที่นี่
# หน้ากรอกผลตรวจสอบพัสดุทั้งห้องมี 2 ช่องต่อชิ้น และห้อง "ยังไม่จัดเข้าห้อง" มีได้หลายร้อยชิ้น (ค่าเริ่มต้นของ Starlette คือ 1000)
MAX_FORM_FIELDS = 20000


async def csrf_protect(request: Request):
    if request.method in SAFE_METHODS:
        return
    expected = request.session.get(SESSION_KEY) if "session" in request.scope else None
    sent = request.headers.get(HEADER)
    if not sent and request.headers.get("content-type", "").startswith(FORM_TYPES):
        # FastAPI/Starlette เก็บผล form() ไว้ใน request route จึงอ่านซ้ำได้
        sent = (await request.form(max_fields=MAX_FORM_FIELDS)).get(FIELD)
    if not expected or not sent or not secrets.compare_digest(str(sent), expected):
        raise CsrfFailed()
