"""เลขไทย / วันที่แบบเอกสารราชการ / ปีงบประมาณ"""
from datetime import date

from app import timeutil

TO_ARABIC = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
TO_THAI = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
MONTHS_ABBR = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def digits(text, thai=True):
    """แปลงเลขอารบิกในข้อความเป็นเลขไทย (thai=False คืนค่าเดิม) None -> ''"""
    if text is None:
        return ""
    text = str(text)
    return text.translate(TO_THAI) if thai else text


def to_int(v):
    v = str(v or "").translate(TO_ARABIC).strip()
    return int(v) if v.isdigit() else None


def short_date(iso, precision="day"):
    """'2004-06-10' -> '10 มิ.ย. 47' แบบในไฟล์ทะเบียนเดิม (เดือน: 'มิ.ย. 47', ปี: '2547')"""
    if not iso:
        return ""
    d = date.fromisoformat(str(iso)[:10])
    be = d.year + 543
    if precision == "year":
        return str(be)
    if precision == "month":
        return f"{MONTHS_ABBR[d.month - 1]} {be % 100:02d}"
    return f"{d.day} {MONTHS_ABBR[d.month - 1]} {be % 100:02d}"


def parse_be_date(day, month, year_be, allow_future=False):
    """วัน/เดือน/ปี พ.ศ. จากฟอร์ม -> (iso, error)"""
    d, m, y = to_int(day), to_int(month), to_int(year_be)
    if y is None or not 2400 <= y <= 2700:
        return None, "กรอกปี พ.ศ. 4 หลัก เช่น 2569"
    try:
        value = date(y - 543, m or 0, d or 0)
    except ValueError:
        return None, "วันที่ไม่ถูกต้อง ตรวจวันและเดือนอีกครั้ง"
    if not allow_future and value > timeutil.today_th():
        return None, "วันที่อยู่ในอนาคต ตรวจปี พ.ศ. อีกครั้ง"
    return value.isoformat(), None


def date_parts(iso=None):
    """ค่าเริ่มต้นของช่องวันที่ในฟอร์ม (วันนี้ถ้าไม่ระบุ)"""
    d = date.fromisoformat(iso) if iso else timeutil.today_th()
    return {"day": str(d.day), "month": str(d.month), "year_be": str(d.year + 543)}


def fiscal_year_of(d):
    return d.year + 543 + (1 if d.month >= 10 else 0)


def current_fiscal_year():
    return fiscal_year_of(timeutil.today_th())


def fiscal_year_bounds(fy):
    """ปีงบประมาณ พ.ศ. -> (วันแรก, วันสุดท้าย) แบบ iso เช่น 2569 -> ('2025-10-01', '2026-09-30')"""
    return date(fy - 544, 10, 1).isoformat(), date(fy - 543, 9, 30).isoformat()
