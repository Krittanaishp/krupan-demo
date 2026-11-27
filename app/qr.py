"""QR ของครุภัณฑ์: รหัสใน URL (/q/{code}) และสร้างภาพ SVG

code = ส่วนตัวเลขของเลขทะเบียน (ไม่มี "ปค.") เช่น 99-9999-1-1201-0006 หรือรหัสชั่วคราว เช่น TMP-0001
ใช้ตัวอักษร ASCII ล้วน QR จึงเล็กและมือถือทุกรุ่นอ่าน URL ได้
"""
import re
from urllib.parse import unquote

import qrcode
import qrcode.image.svg
from qrcode.constants import ERROR_CORRECT_M

from app import network

REG_PREFIX = "ปค."
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
TMP_RE = re.compile(r"TMP-[0-9]+")
OLD_TMP_NOTE = "รหัสชั่วคราวเดิม {} "  # ข้อความที่หน้าแก้ไขเขียนลงหมายเหตุเมื่อได้เลขทะเบียน


def code_for(asset):
    """รหัสที่ใช้ใน QR: เลขทะเบียนถ้ามี (ตัด 'ปค.') ไม่อย่างนั้นใช้รหัสชั่วคราว"""
    reg = asset["reg_no"]
    if reg:
        return reg[len(REG_PREFIX):] if reg.startswith(REG_PREFIX) else reg
    return asset["temp_code"]


def url_for(code, request=None):
    base = network.base_url()
    if not base and request is not None:
        base = str(request.base_url).rstrip("/")
    return f"{base}/q/{code}"


def svg(data):
    """SVG ของ QR (error correction ระดับ M) สำหรับฝังในหน้าเว็บ ขนาดกำหนดด้วย CSS"""
    q = qrcode.QRCode(error_correction=ERROR_CORRECT_M, border=2, image_factory=qrcode.image.svg.SvgPathImage)
    q.add_data(data)
    q.make(fit=True)
    s = q.make_image().to_string(encoding="unicode")
    # ฝังหลายรูปในหน้าเดียว: ตัด id ที่ซ้ำกัน และให้ CSS กำหนดขนาด; crispEdges กันเส้นบางระหว่างช่องตอนพิมพ์
    s = re.sub(r'^<svg width="[^"]+" height="[^"]+"', '<svg class="qr" shape-rendering="crispEdges" '
               'role="img" aria-label="QR"', s)
    s = s.replace(' id="qr-path"', "")
    return re.sub(r"<\?xml[^>]*\?>", "", s).strip()


def normalize(code):
    code = unquote(code or "").translate(THAI_DIGITS).strip()
    code = re.sub(r"\s+", "", code)
    if code.upper().startswith("TMP"):
        return code.upper()
    if code.startswith(REG_PREFIX):
        code = code[len(REG_PREFIX):]
    elif code.startswith("ปค"):
        code = code[2:]
    return code


def find_asset_id(db, code):
    """หา id ของครุภัณฑ์จากรหัสใน QR หรือ None
    QR รหัสชั่วคราวที่ติดไปแล้วยังเปิดเจอ แม้ชิ้นนั้นได้เลขทะเบียนแล้ว (temp_code ยังเก็บไว้)"""
    code = normalize(code)
    if not code:
        return None
    if TMP_RE.fullmatch(code):
        row = db.execute("SELECT id FROM assets WHERE temp_code = ?", (code,)).fetchone()
        if row is None:  # กันไว้: ถ้ารหัสเหลืออยู่แค่ในหมายเหตุ
            row = db.execute("SELECT id FROM assets WHERE note LIKE ? ORDER BY id LIMIT 1",
                             ("%" + OLD_TMP_NOTE.format(code) + "%",)).fetchone()
        return row["id"] if row else None
    row = db.execute("SELECT id FROM assets WHERE reg_no = ?", (REG_PREFIX + code,)).fetchone()
    return row["id"] if row else None
