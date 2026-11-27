"""หน้าทะเบียนครุภัณฑ์: รายการ + รายละเอียด"""
import math
import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import audit, duplicates, flags, places, records, timeutil
from app.auth import can_edit, is_admin, require_admin, require_editor, safe_next
from app.db import get_db
from app.filter_history import is_filter_request, set_filter_url
from app.templating import templates

router = APIRouter()

PAGE_SIZE = 50
STATUSES = ["ใช้งาน", "ชำรุด", "รอจำหน่าย", "จำหน่ายแล้ว", "สูญหาย"]  # ตาม CHECK ใน schema
DISPOSED = "จำหน่ายแล้ว"
# สถานะที่เลือกเองได้ในหน้าแก้ไข / หน้า QR: "จำหน่ายแล้ว" ได้ทางเดียวคือบันทึกการจำหน่าย (ยกเลิกได้ทางยกเลิกการจำหน่าย)
EDITABLE_STATUSES = [s for s in STATUSES if s != DISPOSED]
DISPOSE_ONLY_MSG = "เปลี่ยนเป็น “จำหน่ายแล้ว” ได้ทางปุ่ม “บันทึกการจำหน่าย” ในหน้ารายละเอียดเท่านั้น"
NO_CATEGORY = "none"  # ครุภัณฑ์ที่ไม่มีรหัสประเภท
NO_LOCATION = "none"  # ครุภัณฑ์ที่ยังไม่ได้จัดเข้าห้อง
NO_AREA = "none"  # ห้องที่ยังไม่ได้ระบุอาคาร/ฝ่าย
DUPLICATE = duplicates.LABEL  # ค่าในตัวกรองสถานะสำหรับ "รายการซ้ำ" (ไม่ใช่ค่าใน assets.status)
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
# ปค.xx-xxxx-x-xxxx-xxxx ใช้ [0-9] ไม่ใช้ \d เพราะ \d ของ Python รับเลขไทยด้วย
REG_NO_RE = re.compile(r"ปค\.[0-9]{2}-[0-9]{4}-[0-9]-[0-9]{4}-[0-9]{4}")
PRECISIONS = ("day", "month", "year")
# ช่องที่หน้าแก้ไขเปลี่ยนได้ (ไม่รวม temp_code, acquired_date_raw, source_* ซึ่งเป็นข้อมูลต้นทาง)
EDIT_FIELDS = ("reg_no", "type_code", "name", "details", "acquired_date", "acquired_date_precision",
               "fiscal_year", "unit_price", "funding_source", "gfmis_code", "location_text",
               "location_id", "holder_id", "status", "note")


def to_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_filters(f):
    """คืน (WHERE clause, params) จากตัวกรองที่ผู้ใช้เลือก (f จาก parse_filters)"""
    where, params = [], []
    if f["q"]:
        pattern = f"%{like_escape(f['q'])}%"
        where.append(
            "(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in
                              ("a.reg_no", "a.temp_code", "a.name", "a.details", "a.location_text", "l.name")) + ")"
        )
        params += [pattern] * 6
    if f["category"] == NO_CATEGORY:
        where.append("a.type_code IS NULL")
    elif f["category"]:
        where.append("t.category_code = ?")
        params.append(f["category"])
    if f["status"] == DUPLICATE:
        where.append("a.duplicate_of_id IS NOT NULL")
    elif f["status"]:
        where.append("a.status = ? AND a.duplicate_of_id IS NULL")
        params.append(f["status"])
    if f["fy"]:
        where.append("a.fiscal_year = ?")
        params.append(f["fy"])
    if f["review"]:
        where.append("a.needs_review IS NOT NULL")
    if f["loc"] == NO_LOCATION:
        where.append("a.location_id IS NULL")
    elif f["loc"]:
        where.append("a.location_id = ?")
        params.append(int(f["loc"]))
    for key, col in (("building", "l.building_id"), ("dept", "l.department_id")):
        if f[key] == NO_AREA:
            where.append(f"{col} IS NULL")
        elif f[key]:
            where.append(f"{col} = ?")
            params.append(int(f[key]))
    if f["loc_text"]:  # ลิงก์จากหน้าจัดห้อง: ข้อความ "ใช้ประจำที่" เดิมตรงทั้งข้อความ
        where.append("a.location_text = ?")
        params.append(f["loc_text"])
    if f["unprinted"]:
        where.append("a.label_printed_at IS NULL")
    return ("WHERE " + " AND ".join(where)) if where else "", params


def normalize_query(q):
    """คำค้น: เลขไทยเป็นเลขอารบิก ช่องว่างหลายช่องเหลือช่องเดียว (ใช้ทั้งหน้า /assets และ /review)"""
    return re.sub(r"\s+", " ", (q or "").translate(THAI_DIGITS)).strip()


def parse_filters(q="", category="", status="", fy="", review="", loc="", loc_text="", unprinted="", building="", dept=""):
    """ค่าจากฟอร์ม/URL (string เสมอ ช่องว่าง = ไม่กรอง) -> ตัวกรองที่ตรวจแล้ว
    แปลงเองแทนให้ FastAPI ตอบ 422 ใช้ร่วมกันระหว่างหน้า /assets และหน้าพิมพ์สติกเกอร์"""
    on = ("1", "on", "true")
    return {
        "q": normalize_query(q),
        "category": category,
        "status": status if status in STATUSES or status == DUPLICATE else "",
        "fy": to_int(fy),
        "review": review in on,
        "loc": loc if loc == NO_LOCATION or loc.isdigit() else "",
        "loc_text": loc_text,
        "unprinted": unprinted in on,
        "building": building if building == NO_AREA or building.isdigit() else "",
        "dept": dept if dept == NO_AREA or dept.isdigit() else "",
    }


def filter_query(f):
    """ตัวกรองที่ใช้อยู่ สำหรับใส่ใน URL (ไม่มีช่องว่าง ๆ)"""
    return {k: v for k, v in {
        "q": f["q"], "category": f["category"], "status": f["status"], "fy": f["fy"] or "", "loc": f["loc"],
        "building": f["building"], "dept": f["dept"],
        "loc_text": f["loc_text"], "review": "1" if f["review"] else "",
        "unprinted": "1" if f["unprinted"] else ""}.items() if v}


ASSET_FROM = """FROM assets a
                LEFT JOIN asset_types t ON t.code = a.type_code
                LEFT JOIN categories c ON c.code = t.category_code
                LEFT JOIN locations l ON l.id = a.location_id"""
ASSET_COLUMNS = """a.id, a.reg_no, a.temp_code, a.name, c.name AS category_name,
                   a.acquired_date, a.acquired_date_precision, a.unit_price, a.label_printed_at,
                   a.location_text, l.name AS location_name, a.needs_review IS NOT NULL AS needs_review,
                   CASE WHEN a.duplicate_of_id IS NOT NULL THEN 'รายการซ้ำ' ELSE a.status END AS status"""
ASSET_ORDER = "ORDER BY a.reg_no IS NULL, a.reg_no, a.temp_code"


def count_assets(db, f):
    where, params = build_filters(f)
    return db.execute(f"SELECT COUNT(*) {ASSET_FROM} {where}", params).fetchone()[0]


def query_assets(db, f, limit=-1, offset=0):
    where, params = build_filters(f)
    return db.execute(f"SELECT {ASSET_COLUMNS} {ASSET_FROM} {where} {ASSET_ORDER} LIMIT ? OFFSET ?",
                      params + [limit, offset]).fetchall()


def filter_options(db):
    """ตัวเลือกของตัวกรอง (หมวด สถานะ ปีงบ ห้อง) พร้อมจำนวน"""
    return {
        "categories": db.execute(
            """SELECT c.code, c.name, COUNT(a.id) AS n
               FROM categories c
               JOIN asset_types t ON t.category_code = c.code
               JOIN assets a ON a.type_code = t.code
               GROUP BY c.code, c.name ORDER BY c.name""").fetchall(),
        "no_category_count": db.execute("SELECT COUNT(*) FROM assets WHERE type_code IS NULL").fetchone()[0],
        "statuses": STATUSES + [DUPLICATE],
        "fiscal_years": [r[0] for r in db.execute(
            "SELECT DISTINCT fiscal_year FROM assets WHERE fiscal_year IS NOT NULL ORDER BY fiscal_year DESC")],
        "NO_CATEGORY": NO_CATEGORY,
        "NO_LOCATION": NO_LOCATION,
        "locations": db.execute(
            """SELECT l.id, l.name, l.active, COUNT(a.id) AS n FROM locations l
               LEFT JOIN assets a ON a.location_id = l.id
               GROUP BY l.id HAVING l.active = 1 OR n > 0 ORDER BY l.name""").fetchall(),
        "no_location_count": db.execute("SELECT COUNT(*) FROM assets WHERE location_id IS NULL").fetchone()[0],
        "unprinted_count": db.execute("SELECT COUNT(*) FROM assets WHERE label_printed_at IS NULL").fetchone()[0],
        "NO_AREA": NO_AREA,
        "buildings": area_options(db, "buildings", "building_id"),
        "departments": area_options(db, "departments", "department_id"),
    }


def area_options(db, table, column):
    """อาคาร/ฝ่ายที่ใช้งานอยู่หรือมีครุภัณฑ์อยู่ พร้อมจำนวนชิ้น + แถว “ยังไม่ระบุ” (code = NO_AREA)"""
    rows = [dict(r) for r in db.execute(f"""SELECT x.id, x.name, x.active, COUNT(a.id) AS n FROM {table} x
                                           LEFT JOIN locations l ON l.{column} = x.id
                                           LEFT JOIN assets a ON a.location_id = l.id
                                           GROUP BY x.id HAVING x.active = 1 OR n > 0
                                           ORDER BY x.sort_order, x.name""")]
    none = db.execute(f"""SELECT COUNT(*) FROM assets a JOIN locations l ON l.id = a.location_id
                          WHERE l.{column} IS NULL""").fetchone()[0]
    return {"rows": rows, "none": none}


@router.get("/assets")
def list_assets(
    request: Request,
    q: str = "",
    category: str = "",
    status: str = "",
    fy: str = "",
    review: str = "",
    loc: str = "",
    loc_text: str = "",
    unprinted: str = "",
    building: str = "",
    dept: str = "",
    page: str = "1",
    db: sqlite3.Connection = Depends(get_db),
):
    f = parse_filters(q, category, status, fy, review, loc, loc_text, unprinted, building, dept)
    total = count_assets(db, f)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page_no = min(max(1, to_int(page, 1)), pages)
    rows = query_assets(db, f, PAGE_SIZE, (page_no - 1) * PAGE_SIZE)
    current = filter_query(f)

    def page_url(n):
        return "/assets?" + urlencode({**current, "page": n}) if n > 1 else "/assets" + (
            "?" + urlencode(current) if current else "")

    ctx = {
        "rows": rows, "total": total, "page": page_no, "pages": pages, "page_size": PAGE_SIZE,
        "page_url": page_url, "f": f, "filtered": bool(current),
        "labels_url": "/labels?" + urlencode({"mode": "filter", **current}),
    }

    # HTMX ขอเฉพาะส่วนผลลัพธ์ (ยกเว้นตอนกู้ประวัติหน้าเว็บ ซึ่งต้องการทั้งหน้า)
    if request.headers.get("HX-Request") and not request.headers.get("HX-History-Restore-Request"):
        resp = templates.TemplateResponse(request, "assets/_results.html", ctx)
        if is_filter_request(request):
            # ให้แถบที่อยู่แสดง URL สะอาด (ไม่มี category=&status=...) และแชร์ลิงก์ต่อได้
            set_filter_url(request, resp, page_url(page_no))
        return resp

    ctx.update(filter_options(db))
    return templates.TemplateResponse(request, "assets/list.html", ctx)


def get_asset_or_404(db, asset_id):
    a = db.execute(
        """SELECT a.*, t.name AS type_name, t.name_is_draft, c.code AS category_code, c.name AS category_name,
                  l.name AS location_name, s.full_name AS holder_full_name, s.nickname AS holder_nickname
           FROM assets a
           LEFT JOIN asset_types t ON t.code = a.type_code
           LEFT JOIN categories c ON c.code = t.category_code
           LEFT JOIN locations l ON l.id = a.location_id
           LEFT JOIN staff s ON s.id = a.holder_id
           WHERE a.id = ?""",
        (asset_id,),
    ).fetchone()
    if a is None:
        raise HTTPException(status_code=404, detail="ไม่พบครุภัณฑ์รายการนี้")
    return a


@router.get("/assets/{asset_id}")
def asset_detail(request: Request, asset_id: int, db: sqlite3.Connection = Depends(get_db)):
    a = get_asset_or_404(db, asset_id)

    # ปุ่มกลับ: กลับไปหน้ารายการพร้อมตัวกรองเดิม ถ้ามาจากหน้ารายการของระบบเอง
    back = "/assets"
    ref = urlparse(request.headers.get("referer", ""))
    if ref.netloc == request.url.netloc and ref.path == "/assets":
        back = "/assets" + (f"?{ref.query}" if ref.query else "")

    history = db.execute(
        """SELECT l.created_at, l.action, l.changes, COALESCE(u.display_name, u.username) AS who
           FROM audit_log l LEFT JOIN users u ON u.id = l.user_id
           WHERE l.table_name = 'assets' AND l.record_id = ?
           ORDER BY l.id DESC""",
        (asset_id,),
    ).fetchall()
    moves = db.execute(
        """SELECT m.moved_at, m.note, m.reason, fl.name AS from_location, tl.name AS to_location,
                  fs.full_name AS from_full, fs.nickname AS from_nick, ts.full_name AS to_full, ts.nickname AS to_nick
           FROM asset_moves m
           LEFT JOIN locations fl ON fl.id = m.from_location_id
           LEFT JOIN locations tl ON tl.id = m.to_location_id
           LEFT JOIN staff fs ON fs.id = m.from_holder_id
           LEFT JOIN staff ts ON ts.id = m.to_holder_id
           WHERE m.asset_id = ? ORDER BY m.id DESC""",
        (asset_id,),
    ).fetchall()
    return templates.TemplateResponse(request, "assets/detail.html", {
        "a": a, "back": back, "history": history, "moves": moves,
        "repairs": records.repairs(db, asset_id), "repair_back": f"/assets/{asset_id}",
        "can_edit_repairs": can_edit(request), "today": timeutil.today_th(),
        "disposal": records.active_disposal(db, asset_id), "disposals": records.disposal_history(db, asset_id),
        "dup_of": db.execute("SELECT id, reg_no, temp_code, name FROM assets WHERE id = ?",
                             (a["duplicate_of_id"],)).fetchone() if a["duplicate_of_id"] else None,
        "dup_children": duplicates.pointing_to(db, asset_id), "is_admin": is_admin(request),
        "dup_error": request.session.pop("dup_error", None)})


# ---------------------------------------------------------------- รายการซ้ำ (admin)

@router.post("/assets/{asset_id}/duplicate")
def mark_duplicate(request: Request, asset_id: int, target: str = Form(""), reason: str = Form(""),
                   user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    a = get_asset_or_404(db, asset_id)
    try:
        with db:
            main = duplicates.mark(db, user["id"], a, target, reason)
    except duplicates.DuplicateError as e:
        request.session["dup_error"] = str(e)
        return RedirectResponse(f"/assets/{asset_id}#duplicate", status_code=303)
    request.session["flash"] = (f"ตั้งเป็นรายการซ้ำของ {duplicates.code_of(main)} แล้ว "
                                "รายการนี้จะไม่นับในรายงานและไม่เข้ารอบตรวจสอบพัสดุ")
    return RedirectResponse(f"/assets/{asset_id}", status_code=303)


@router.post("/assets/{asset_id}/duplicate/cancel")
def cancel_duplicate(request: Request, asset_id: int, reason: str = Form(""),
                     user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    a = get_asset_or_404(db, asset_id)
    try:
        with db:
            duplicates.unmark(db, user["id"], a, reason)
    except duplicates.DuplicateError as e:
        request.session["dup_error"] = str(e)
        return RedirectResponse(f"/assets/{asset_id}#duplicate", status_code=303)
    request.session["flash"] = f"ยกเลิกสถานะรายการซ้ำแล้ว สถานะกลับเป็น “{a['status']}”"
    return RedirectResponse(f"/assets/{asset_id}", status_code=303)


# ---------------------------------------------------------------- หน้าแก้ไข

def clean_text(v):
    v = re.sub(r"\s+", " ", (v or "")).strip()
    return v or None


def form_from_asset(a):
    """ค่าเริ่มต้นของฟอร์มจากข้อมูลใน db (วันที่แปลงเป็น พ.ศ.)"""
    f = {k: ("" if a[k] is None else str(a[k])) for k in EDIT_FIELDS}
    if a["unit_price"] is not None:
        f["unit_price"] = f"{float(a['unit_price']):.2f}".removesuffix(".00")
    f["date_precision"] = a["acquired_date_precision"] or "none"
    f["date_day"] = f["date_month"] = f["date_year"] = ""
    if a["acquired_date"]:
        d = date.fromisoformat(str(a["acquired_date"])[:10])
        f["date_year"] = str(d.year + 543)
        if f["date_precision"] in ("day", "month"):
            f["date_month"] = str(d.month)
        if f["date_precision"] == "day":
            f["date_day"] = str(d.day)
    return f


def parse_price(raw):
    s = (raw or "").translate(THAI_DIGITS).replace(",", "").replace(" ", "").strip()
    if not s:
        return None, None  # เว้นว่าง = ไม่ทราบราคา -> NULL (ไม่ใช่ 0)
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None, "ราคาต้องเป็นตัวเลข เช่น 12500 หรือ 1,250.50"
    if not d.is_finite() or d < 0:
        return None, "ราคาต้องเป็นตัวเลขไม่ติดลบ"
    if d != d.quantize(Decimal("0.01")):
        return None, "ราคาใส่ทศนิยมได้ไม่เกิน 2 ตำแหน่ง"
    if d >= 10**10:
        return None, "ราคาสูงผิดปกติ ตรวจจำนวนหลักอีกครั้ง"
    return (int(d) if d == d.to_integral_value() else float(d)), None


def parse_date(f):
    """ช่องวันที่ในฟอร์ม (ปี พ.ศ.) -> (iso date, precision, error)"""
    precision = f["date_precision"]
    if precision not in PRECISIONS:
        return None, None, None  # เลือก "ไม่ทราบวันที่"
    year_be = to_int(f["date_year"].translate(THAI_DIGITS).strip())
    month = to_int(f["date_month"])
    day = to_int(f["date_day"].translate(THAI_DIGITS).strip())
    if year_be is None:
        return None, None, "กรอกปี พ.ศ. (หรือเลือก “ไม่ทราบวันที่”)"
    if not 2400 <= year_be <= 2700:
        return None, None, "ปีต้องเป็น พ.ศ. 4 หลัก เช่น 2567"
    if precision in ("day", "month") and not (month and 1 <= month <= 12):
        return None, None, "เลือกเดือน"
    if precision == "day" and day is None:
        return None, None, "กรอกวันที่ (หรือเลือกว่ารู้แค่เดือนหรือปี)"
    try:
        d = date(year_be - 543, month if precision != "year" else 1, day if precision == "day" else 1)
    except ValueError:
        return None, None, "ไม่มีวันที่นี้จริง ตรวจวันและเดือนอีกครั้ง"
    if d > date.today():
        return None, None, "วันที่ได้มาอยู่ในอนาคต ตรวจปี พ.ศ. อีกครั้ง"
    return d.isoformat(), precision, None


def parse_form(form, a, db):
    """ตรวจค่าจากฟอร์ม คืน (ค่าใหม่สำหรับบันทึก, errors {ช่อง: ข้อความ})"""
    errors, new = {}, {}

    reg_no = re.sub(r"\s+", "", form["reg_no"].translate(THAI_DIGITS)) or None
    if reg_no:
        other = db.execute("SELECT id, name FROM assets WHERE reg_no = ? AND id != ?", (reg_no, a["id"])).fetchone()
        if not REG_NO_RE.fullmatch(reg_no):
            errors["reg_no"] = "รูปแบบต้องเป็น ปค.xx-xxxx-x-xxxx-xxxx เช่น ปค.99-9999-1-1306-0004"
        elif other:
            errors["reg_no"] = f"เลขทะเบียนนี้ใช้กับรายการอื่นแล้ว (#{other['id']} {other['name']})"
    elif not a["temp_code"]:
        errors["reg_no"] = "รายการนี้ไม่มีรหัสชั่วคราว จึงต้องมีเลขทะเบียน"
    new["reg_no"] = reg_no

    new["type_code"] = form["type_code"].strip() or None
    if new["type_code"] and not db.execute("SELECT 1 FROM asset_types WHERE code = ?", (new["type_code"],)).fetchone():
        errors["type_code"] = "ไม่พบรหัสประเภทนี้"

    new["name"] = clean_text(form["name"])
    if not new["name"]:
        errors["name"] = "ต้องมีชื่อรายการ"

    for k in ("details", "funding_source", "gfmis_code", "location_text"):
        new[k] = clean_text(form[k])
    # หมายเหตุอาจมีหลายบรรทัด เก็บการขึ้นบรรทัดไว้
    new["note"] = "\n".join(line.strip() for line in form["note"].strip().splitlines()) or None

    for field, table, label in (("location_id", "locations", "ห้อง"), ("holder_id", "staff", "ผู้รับผิดชอบ")):
        raw = form[field].strip()
        new[field] = int(raw) if raw.isdigit() else None
        if raw and new[field] is None:
            errors[field] = f"เลือก{label}"
        elif new[field] is not None and new[field] != a[field] and not db.execute(
                f"SELECT 1 FROM {table} WHERE id = ? AND active = 1", (new[field],)).fetchone():
            errors[field] = f"ไม่พบ{label}นี้ หรือถูกปิดใช้งานแล้ว"

    if a["status"] == DISPOSED:
        new["status"] = DISPOSED  # ช่องสถานะอ่านอย่างเดียว ไม่สนค่าที่ส่งมา
    else:
        new["status"] = form["status"]
        if new["status"] == DISPOSED:
            errors["status"] = DISPOSE_ONLY_MSG
        elif new["status"] not in EDITABLE_STATUSES:
            errors["status"] = "เลือกสถานะ"

    fy_raw = form["fiscal_year"].translate(THAI_DIGITS).strip()
    new["fiscal_year"] = to_int(fy_raw)
    if fy_raw and (new["fiscal_year"] is None or not 2400 <= new["fiscal_year"] <= date.today().year + 544):
        errors["fiscal_year"] = "ปีงบประมาณต้องเป็น พ.ศ. 4 หลัก และไม่เกินปีงบประมาณหน้า"

    new["unit_price"], err = parse_price(form["unit_price"])
    if err:
        errors["unit_price"] = err

    new["acquired_date"], new["acquired_date_precision"], err = parse_date(form)
    if err:
        errors["acquired_date"] = err

    # ใส่เลขทะเบียนให้ชิ้นที่มีรหัสชั่วคราว: จด temp_code ไว้ในหมายเหตุด้วย เพราะ QR ที่ติดไปแล้วยังใช้รหัสเดิม
    if new["reg_no"] and a["temp_code"] and a["temp_code"] not in (new["note"] or ""):
        line = f"รหัสชั่วคราวเดิม {a['temp_code']} (QR ที่ติดไว้ยังใช้รหัสนี้)"
        new["note"] = f"{new['note']} | {line}" if new["note"] else line

    return new, errors


FORM_KEYS = (*EDIT_FIELDS, "date_precision", "date_day", "date_month", "date_year")


def edit_page(request, db, a, form, errors, next_url, status_code=200):
    ctx = {"a": a, "form": form, "errors": errors, "next": next_url, "statuses": EDITABLE_STATUSES,
           "disposal": records.active_disposal(db, a["id"]) if a["status"] == DISPOSED else None,
           "types": db.execute("SELECT code, name FROM asset_types ORDER BY code").fetchall(),
           "locations": places.location_options(db, a["location_id"]),
           "staff": places.staff_options(db, a["holder_id"])}
    return templates.TemplateResponse(request, "assets/edit.html", ctx, status_code=status_code)


@router.get("/assets/{asset_id}/edit")
def edit_asset_form(request: Request, asset_id: int, next: str = "",
                    user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    a = get_asset_or_404(db, asset_id)
    return edit_page(request, db, a, form_from_asset(a), {}, safe_next(next, f"/assets/{asset_id}"))


@router.post("/assets/{asset_id}/edit")
async def edit_asset(request: Request, asset_id: int,
                     user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    a = get_asset_or_404(db, asset_id)
    data = await request.form()
    form = {k: str(data.get(k, "")) for k in FORM_KEYS}
    next_url = safe_next(str(data.get("next", "")), f"/assets/{asset_id}")

    new, errors = parse_form(form, a, db)
    if errors:
        return edit_page(request, db, a, form, errors, next_url, status_code=400)

    changes = audit.diff(a, new, EDIT_FIELDS)
    if not changes:
        request.session["flash"] = "ไม่มีข้อมูลที่เปลี่ยนแปลง"
        return RedirectResponse(next_url, status_code=303)

    # ใส่ราคา / เลขทะเบียน / วันที่แล้ว ปัญหาเรื่องนั้นใน needs_review หายไปเอง (บันทึกใน audit_log แถวเดียวกัน)
    new["needs_review"], resolved = flags.auto_resolve(a["needs_review"], changes, new)
    if resolved:
        changes["needs_review"] = {"old": a["needs_review"], "new": new["needs_review"]}
    cols = list(changes)
    places.label_changes(db, changes)
    if resolved:
        changes["auto_resolved"] = resolved
    try:
        with db:  # แก้ข้อมูล + audit_log + asset_moves ใน transaction เดียวกัน สำเร็จทั้งหมดหรือไม่บันทึกเลย
            db.execute(
                f"UPDATE assets SET {', '.join(f'{c} = ?' for c in cols)}, updated_at = CURRENT_TIMESTAMP"
                " WHERE id = ?",
                [new[c] for c in cols] + [asset_id])
            audit.log(db, user["id"], "assets", asset_id, "update", changes)
            places.record_move(db, asset_id, a["location_id"], new["location_id"], a["holder_id"], new["holder_id"],
                               "แก้ไขในหน้าแก้ไขครุภัณฑ์")
    except sqlite3.IntegrityError:
        # มีคนบันทึกเลขเดียวกันพร้อมกัน: UNIQUE ใน db เป็นด่านสุดท้าย
        errors["reg_no"] = "เลขทะเบียนนี้ใช้กับรายการอื่นแล้ว"
        return edit_page(request, db, a, form, errors, next_url, status_code=400)

    msg = f"บันทึกการแก้ไขแล้ว ({len([c for c in cols if c != 'needs_review'])} ช่อง)"
    if resolved:
        msg += " · เอาออกจากรายการรอตรวจสอบ: " + ", ".join(resolved)
    request.session["flash"] = msg
    return RedirectResponse(next_url, status_code=303)
