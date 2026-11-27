"""ค่าตั้งค่าที่เป็นความลับ อ่านจาก environment ก่อน แล้วจึงอ่านไฟล์ .env (ไฟล์นี้ไม่ขึ้น git)"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def _read_env_file():
    values = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def get_setting(name, default=None):
    return os.environ.get(name) or _read_env_file().get(name) or default


def qr_enabled():
    """QR_ENABLED=on เปิดส่วน QR (สติกเกอร์ ตรวจสอบพัสดุด้วยการสแกน คำเตือน BASE_URL) ค่าเริ่มต้นปิด (23/11/69 ไม่ใช้ QR)
    หน้า /q/{code} เปิดได้เสมอแม้ปิดอยู่ เผื่อมีลิงก์หรือสติกเกอร์เก่า"""
    return (get_setting("QR_ENABLED") or "off").strip().lower() in ("on", "1", "yes", "true")


def sandbox_mode():
    """ระบบทดลอง (scripts/start_sandbox.ps1 ตั้ง KRUPAN_SANDBOX=on): แสดงแถบสีส้มทุกหน้า
    อ่านจาก environment เท่านั้น ไม่อ่าน .env (ไฟล์ .env เป็นค่าของระบบจริง)"""
    return (os.environ.get("KRUPAN_SANDBOX") or "").strip().lower() in ("on", "1", "yes", "true")


def session_cookie_name():
    """ระบบทดลองใช้ชื่อ cookie ต่างจากระบบจริง (cookie ไม่แยกตาม port: ล็อกอินที่ 8002 ต้องไม่ทับ session ของ 8000)"""
    return os.environ.get("KRUPAN_SESSION_COOKIE") or "krupan_session"


def secret_key():
    key = get_setting("KRUPAN_SECRET_KEY")
    if not key or len(key) < 32:
        raise RuntimeError(
            "ไม่พบ KRUPAN_SECRET_KEY (อย่างน้อย 32 ตัวอักษร) ในไฟล์ .env\n"
            "สร้างได้ด้วย:  uv run python -c \"import secrets; print('KRUPAN_SECRET_KEY=' + secrets.token_urlsafe(48))\" > .env"
        )
    return key
