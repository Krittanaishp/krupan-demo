"""ค่าที่ระบบจำไว้ (ตาราง app_settings, value เป็น JSON)"""
import json

SIGNERS_KEY = "report_signers"
DEFAULT_SIGNERS = [{"name": "", "role": "ประธานกรรมการ"}, {"name": "", "role": "กรรมการ"},
                   {"name": "", "role": "กรรมการ"}]


def get(db, key, default=None):
    row = db.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def put(db, key, value):
    db.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, json.dumps(value, ensure_ascii=False)))


def signers(db):
    """ช่องลงชื่อท้ายรายงาน 3 ช่อง [{"name", "role"}] (ค่าล่าสุดที่บันทึกไว้)"""
    saved = get(db, SIGNERS_KEY) or []
    out = []
    for i, default in enumerate(DEFAULT_SIGNERS):
        s = saved[i] if i < len(saved) and isinstance(saved[i], dict) else {}
        out.append({"name": str(s.get("name") or ""), "role": str(s.get("role") or default["role"])})
    return out


# ---------------------------------------------------------------- ชื่อหน่วยงาน (ตั้งที่หน้า /settings โดย admin)

ORG_DISPLAY_KEY = "ORG_DISPLAY_NAME"   # หัวเว็บมุมบนซ้าย, สติกเกอร์ QR, หน้า /q/
ORG_REPORT_KEY = "ORG_REPORT_NAME"     # หัวรายงานทะเบียน, รายงานผลการตรวจสอบพัสดุ และเอกสารอื่นที่พิมพ์ออกไป
ORG_KEYS = (ORG_DISPLAY_KEY, ORG_REPORT_KEY)
ORG_MAX_LEN = 120
_org_cache = {}
ORG_CACHE_SECONDS = 30


def org_names(db):
    """{"display": ..., "report": ...} ค่าว่างถ้ายังไม่ได้ตั้ง (ไม่มีชื่อหน่วยงานฝังในโค้ด)"""
    return {"display": str(get(db, ORG_DISPLAY_KEY) or ""), "report": str(get(db, ORG_REPORT_KEY) or "")}


def org_names_cached():
    """ชื่อหน่วยงานสำหรับหัวเว็บทุกหน้า (เปิด db เอง เก็บผลไว้ 30 วินาที แยกตามไฟล์ db) ล้างเมื่อบันทึกใหม่"""
    import time
    from app.db import connect, db_path
    key = str(db_path())
    hit = _org_cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ORG_CACHE_SECONDS:
        return hit[1]
    try:
        conn = connect()
        try:
            value = org_names(conn)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - db ยังไม่พร้อม (เช่นยังไม่ migrate) ไม่ให้หน้าเว็บล้ม
        value = {"display": "", "report": ""}
    _org_cache[key] = (now, value)
    return value


def clear_org_cache():
    _org_cache.clear()


def office_line(db, extra=False):
    """บรรทัดหน่วยงานใต้หัวรายงาน เช่น "ของ<ชื่อหน่วยงาน> (งานการเงินและบัญชี)" """
    name = org_names(db)["report"]
    if extra:  # แบบชีต "เพิ่ม xx" เดิม
        return f"ของ{name} งานการเงินและบัญชี (เพิ่มเติม)"
    return f"ของ{name} (งานการเงินและบัญชี)"
