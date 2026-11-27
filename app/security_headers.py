"""header ความปลอดภัยพื้นฐานของทุก response (หน้าเว็บ ไฟล์ static Excel)

- Content-Security-Policy: โหลดได้เฉพาะจากเครื่องนี้ ไม่มี inline script / event handler / style attribute
  (ย้ายไป static/js/doc.js และ class ใน CSS แล้ว) ส่วน <style> ที่ต้องคำนวณจากค่าตั้งค่า (หน้าพิมพ์รายงาน สติกเกอร์)
  ใช้ nonce ที่สุ่มใหม่ทุก request: <style nonce="{{ csp_nonce(request) }}">
- X-Frame-Options: DENY + frame-ancestors 'none': หน้าเว็บของระบบถูกฝังใน iframe ของเว็บอื่นไม่ได้
- X-Content-Type-Options: nosniff · Referrer-Policy: same-origin
- header "server" ปิดที่ uvicorn (scripts/serve.py server_header=False, uvicorn --no-server-header)
"""
import secrets

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'nonce-{nonce}'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
}


def new_nonce():
    return secrets.token_urlsafe(16)


def csp_nonce(request):
    """nonce ของ request นี้ (ใช้ในเทมเพลต) · ว่างถ้าไม่ได้ผ่าน middleware"""
    return getattr(request.state, "csp_nonce", "") if request is not None else ""


async def middleware(request, call_next):
    request.state.csp_nonce = new_nonce()
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP.format(nonce=request.state.csp_nonce)
    for k, v in HEADERS.items():
        response.headers.setdefault(k, v)
    return response
