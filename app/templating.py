"""Jinja2 templates และตัวแปลงค่าสำหรับแสดงผลภาษาไทย"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import backup, csrf, network, settings_store
from app.config import qr_enabled, sandbox_mode
from app.auth import get_user
from app.security_headers import csp_nonce
from app.static_version import static_url

THAI_MONTHS_ABBR = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
                    "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]

STATUS_CLASS = {
    "ใช้งาน": "ok",
    "ชำรุด": "warn",
    "รอจำหน่าย": "pending",
    "จำหน่ายแล้ว": "muted",
    "สูญหาย": "danger",
    "รายการซ้ำ": "muted",   # ไม่ใช่ค่าใน assets.status (ดู app/duplicates.py) แต่แสดงเป็นสถานะ
}

# ชื่อช่องภาษาไทย ใช้แสดงประวัติการแก้ไข
FIELD_LABELS = {
    "reg_no": "เลขทะเบียน",
    "temp_code": "รหัสชั่วคราว",
    "type_code": "รหัสประเภท",
    "name": "ชื่อรายการ",
    "details": "รายละเอียด",
    "acquired_date": "วันที่ได้มา",
    "acquired_date_precision": "ความละเอียดของวันที่",
    "fiscal_year": "ปีงบประมาณ",
    "unit_price": "ราคาต่อหน่วย",
    "funding_source": "วิธีการได้มา",
    "gfmis_code": "รหัส GFMIS",
    "location_text": "ใช้ประจำที่ (ข้อความเดิม)",
    "location_id": "ห้อง",
    "holder_id": "ผู้รับผิดชอบ",
    "duplicate_of_id": "รายการซ้ำของ",
    "duplicate_reason": "เหตุผลที่เป็นรายการซ้ำ",
    "building_id": "อาคาร/พื้นที่",
    "department_id": "ฝ่ายที่ดูแล",
    "status": "สถานะ",
    "note": "หมายเหตุ",
    "needs_review": "ต้องตรวจสอบ",
    "name_is_draft": "ชื่อร่าง",
    "active": "ใช้งานอยู่",
    "full_name": "ชื่อ-นามสกุล",
    "nickname": "ชื่อเล่น",
    "position": "ตำแหน่ง",
    "username": "ชื่อผู้ใช้",
    "display_name": "ชื่อที่แสดง",
    "role": "สิทธิ์",
    "password": "รหัสผ่าน",
    "must_change_password": "บังคับเปลี่ยนรหัสผ่าน",
    "label_printed_at": "พิมพ์สติกเกอร์",
    "inventory_result": "ผลตรวจสอบพัสดุ",
    "repaired_at": "วันที่ซ่อม",
    "repair_description": "รายการซ่อม",
    "repair_cost": "ค่าใช้จ่ายซ่อม",
    "repair_note": "หมายเหตุการซ่อม",
    "disposed_at": "วันที่จำหน่าย",
    "disposal_method": "วิธีจำหน่าย",
    "doc_ref": "เลขที่หนังสืออนุมัติ",
    "disposal_amount": "จำนวนเงินที่ได้",
    "cancel_reason": "เหตุผลที่ยกเลิก",
    "inventory_note": "หมายเหตุการตรวจสอบพัสดุ",
    "found_location_id": "ห้องที่พบ",
    "started_at": "วันที่เริ่มตรวจ",
    "committee": "คณะกรรมการ",
    "closed_at": "วันที่ปิดรอบ",
    "ORG_DISPLAY_NAME": "ชื่อหน่วยงาน (หัวเว็บ)",
    "gdrive_confirmed": "ใช้ Google Drive สำรองแล้ว",
    "ORG_REPORT_NAME": "ชื่อหน่วยงานในรายงาน",
}
PRECISION_LABELS = {"day": "ระดับวัน", "month": "ระดับเดือน", "year": "ระดับปี"}
ROLE_LABELS = {"admin": "ผู้ดูแลระบบ (admin)", "editor": "แก้ไขข้อมูล (editor)", "viewer": "ดูอย่างเดียว (viewer)"}
ACTION_LABELS = {"update": "แก้ไข", "review": "ตรวจแล้ว", "review_undo": "ยกเลิกการตรวจ",
                 "insert": "เพิ่ม", "delete": "ลบ", "migrate": "แก้โดยสคริปต์ระบบ",
                 "label": "พิมพ์สติกเกอร์ QR", "inventory": "ตรวจสอบพัสดุ", "close": "ปิดรอบตรวจสอบพัสดุ",
                 "reopen": "เปิดรอบตรวจสอบพัสดุกลับ", "repair_insert": "เพิ่มประวัติซ่อม", "repair_update": "แก้ไขประวัติซ่อม",
                 "repair_delete": "ลบประวัติซ่อม", "dispose": "จำหน่าย", "dispose_cancel": "ยกเลิกการจำหน่าย"}


def thai_date(iso, precision="day"):
    """'2019-12-25' -> '25 ธ.ค. 2562' แสดงเฉพาะส่วนที่รู้ตาม precision (month -> 'ธ.ค. 2562', year -> '2562')"""
    if not iso:
        return ""
    d = date.fromisoformat(str(iso)[:10])
    year_be = d.year + 543
    if precision == "year":
        return str(year_be)
    if precision == "month":
        return f"{THAI_MONTHS_ABBR[d.month - 1]} {year_be}"
    return f"{d.day} {THAI_MONTHS_ABBR[d.month - 1]} {year_be}"


def thai_datetime(utc_text):
    """เวลาใน db เป็น UTC (CURRENT_TIMESTAMP) -> เวลาไทย '16 พ.ย. 2569 14:05 น.'"""
    if not utc_text:
        return ""
    try:
        t = datetime.fromisoformat(str(utc_text)) + timedelta(hours=7)
    except ValueError:
        return str(utc_text)
    return f"{thai_date(t.date().isoformat())} {t:%H:%M} น."


def money(v):
    """12500 -> '12,500' / 1234.5 -> '1,234.50' / None -> ''"""
    if v is None:
        return ""
    v = float(v)
    return f"{v:,.0f}" if v.is_integer() else f"{v:,.2f}"


def staff_label(s):
    """ชื่อผู้รับผิดชอบสำหรับแสดง: 'เอ็ม (นางสาว...)' หรือชื่อที่มีอย่างใดอย่างหนึ่ง"""
    if not s:
        return ""
    nick, full = s["nickname"], s["full_name"]
    if nick and full:
        return f"{nick} ({full})"
    return nick or full or ""


def audit_value(v, field):
    """ค่าใน audit_log -> ข้อความที่คนอ่านเข้าใจ"""
    if v is None or v == "":
        return "(ว่าง)"
    if field == "unit_price":
        return money(v) + " บาท"
    if field == "acquired_date":
        return thai_date(v)
    if field == "acquired_date_precision":
        return PRECISION_LABELS.get(v, v)
    if field == "name_is_draft":
        return "ใช่" if v else "ไม่ใช่ (ยืนยันแล้ว)"
    if field == "active":
        return "ใช้งาน" if v else "ปิดใช้งาน"
    if field == "role":
        return ROLE_LABELS.get(v, v)
    if field == "label_printed_at":
        return thai_datetime(v)
    if field in ("repair_cost", "disposal_amount"):
        return money(v) + " บาท"
    if field in ("started_at", "closed_at", "repaired_at", "disposed_at"):
        return thai_date(v)
    if field == "committee":
        return committee_text(v)
    return str(v)


def committee_text(v):
    """คณะกรรมการที่เก็บเป็น JSON -> 'ประธาน: ก / กรรมการ: ข, ค'"""
    try:
        d = json.loads(v)
    except (TypeError, ValueError):
        return str(v)
    if not isinstance(d, dict):
        return str(v)
    return f"ประธาน: {d.get('chair') or '-'} / กรรมการ: {', '.join(d.get('members') or []) or '-'}"


def audit_changes(changes_json):
    """JSON ใน audit_log -> [(ชื่อช่อง, ค่าเดิม, ค่าใหม่)]
    ช่องที่เก็บเป็น id (ห้อง ผู้รับผิดชอบ) มี old_label/new_label เป็นชื่อ ณ เวลาที่แก้"""
    out = []
    for field, ch in json.loads(changes_json or "{}").items():
        if isinstance(ch, dict) and "old" in ch:
            old = ch.get("old_label", ch["old"]) if ch["old"] is not None else None
            new = ch.get("new_label", ch["new"]) if ch["new"] is not None else None
            out.append((FIELD_LABELS.get(field, field), audit_value(old, field), audit_value(new, field)))
    return out


def common_context(request: Request):
    """ตัวแปรที่ทุกหน้าใช้: ผู้ใช้ที่ล็อกอิน ข้อความแจ้งเตือนครั้งเดียว (flash) และ CSRF token"""
    flash = request.session.pop("flash", None) if "session" in request.scope else None
    user = get_user(request)
    # admin เห็นแถบเตือนทุกหน้าถ้า BASE_URL (ที่ฝังใน QR) ไม่ตรงกับ IP ของเครื่อง
    net_warning = network.warning() if user and user["is_admin"] and qr_enabled() else None
    # และแถบเตือนเรื่องสำรองข้อมูล (แดง: ไม่สำเร็จเกิน 2 วัน, ส้ม: ไม่มีสำเนานอกเครื่องเกิน 7 วัน)
    backup_warnings = (backup.warnings() + backup.notices()) if user and user["is_admin"] else []
    return {"user": user, "flash": flash, "net_warning": net_warning, "backup_warnings": backup_warnings,
            "org": settings_store.org_names_cached(),
            "csrf_token": csrf.get_token(request), "csrf_field": csrf.hidden_field(request)}


templates = Jinja2Templates(directory=Path(__file__).resolve().parent / "templates",
                            context_processors=[common_context])
templates.env.filters["thai_date"] = thai_date
templates.env.filters["money"] = money
templates.env.filters["thai_datetime"] = thai_datetime
templates.env.filters["staff_label"] = staff_label
templates.env.filters["from_json"] = json.loads
templates.env.globals["STATUS_CLASS"] = STATUS_CLASS
templates.env.globals["THAI_MONTHS_ABBR"] = THAI_MONTHS_ABBR
templates.env.globals["ACTION_LABELS"] = ACTION_LABELS
templates.env.globals["ROLE_LABELS"] = ROLE_LABELS
templates.env.globals["audit_changes"] = audit_changes
templates.env.globals["csp_nonce"] = csp_nonce
templates.env.globals["static_url"] = static_url
templates.env.globals["qr_enabled"] = qr_enabled
templates.env.globals["sandbox_mode"] = sandbox_mode
