"""หน้ารอตรวจสอบ: รายการที่ needs_review ยังไม่ว่าง แยกกลุ่มตามประเภทปัญหา"""
import io
import json
import sqlite3
from datetime import date, timedelta
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app import audit, flags, settings_store, timeutil
from app.auth import can_edit, require_editor
from app.db import get_db
from app.filter_history import is_filter_request, set_filter_url
from app.routers.assets import like_escape, normalize_query
from app.templating import money, templates, thai_date

router = APIRouter()

SEP = flags.SEP
UNDO_SECONDS = 5  # แถบ "ตรวจแล้ว · ยกเลิก" แสดงนานเท่านี้
# เซิร์ฟเวอร์ยอมให้ยกเลิกนานกว่าที่หน้าเว็บแสดงเล็กน้อย เผื่อเครือข่ายช้า
UNDO_WINDOW = timedelta(seconds=60)

# (key, ชื่อกลุ่ม, คำอธิบายสั้น ๆ, ฟังก์ชันจับคู่ข้อความปัญหา) เรียงตามลำดับที่แสดง
GROUPS = [
    ("price", "ไม่มีราคา", "ต้นฉบับไม่มีราคาต่อหน่วย", lambda f: f == flags.NO_PRICE),
    ("regno", "ไม่มีเลขทะเบียน", "ใช้รหัสชั่วคราวไปก่อน", lambda f: f == flags.NO_REG_NO),
    ("dup", "เลขซ้ำในชีต", "เลขทะเบียนเดียวกันปรากฏหลายแถวในชีตเดียว", lambda f: f == flags.DUP_IN_SHEET),
    ("nodate", "ไม่มีวันที่ได้มา", "ต้นฉบับไม่มีวันที่", lambda f: f == flags.NO_DATE),
    ("partial", "วันที่ไม่ครบ", "รู้แค่เดือน/ปี หรือแค่ปี", lambda f: f in (flags.DATE_MONTH_ONLY, flags.DATE_YEAR_ONLY)),
    ("excel_year", "ปีที่ Excel อ่านผิด", "Excel อ่านหรือเก็บปี พ.ศ. เป็น ค.ศ. ระบบแก้ให้แล้ว ต้องยืนยันกับต้นฉบับ",
     flags.is_excel_year),
    ("name", "ชื่อรายการอาจเพี้ยน", "ชื่อรายการอาจเป็นข้อความต่อจากแถวก่อนในไฟล์เดิม ให้ตั้งชื่อใหม่ในหน้าแก้ไข",
     lambda f: f == flags.NAME_SUSPECT),
    ("verify", "รอเจ้าหน้าที่ยืนยัน", "ลงทะเบียนซ้ำ หรือไม่พบของในห้องที่บันทึกไว้ ต้องให้เจ้าหน้าที่พัสดุตรวจเอกสารก่อน",
     flags.is_staff_verify_flag),
    ("inventory", "จากการตรวจสอบพัสดุ", "พบของที่ไม่มีในทะเบียน หรือไม่พบตอนตรวจสอบพัสดุประจำปี ให้กรรมการพิจารณา",
     flags.is_inventory_flag),
]
OTHER = ("other", "อื่น ๆ", "ปัญหาที่ไม่เข้ากลุ่มใด", lambda f: True)
ALL_GROUPS = GROUPS + [OTHER]
GROUP_BY_KEY = {g[0]: g for g in ALL_GROUPS}

ROW_QUERY = """SELECT id, reg_no, temp_code, name, details, location_text, acquired_date, acquired_date_precision,
                      acquired_date_raw, unit_price, source_sheet, source_row, needs_review
               FROM assets"""
SEARCH_COLUMNS = ("reg_no", "temp_code", "name", "details", "location_text")  # เลขทะเบียน / TMP / ชื่อ / รายละเอียด / ที่ตั้ง


def group_of(flag):
    for g in GROUPS:
        if g[3](flag):
            return g[0]
    return OTHER[0]


def make_row(r, flag):
    fl = flags.split(r["needs_review"])
    return {**dict(r), "flag": flag, "other_flags": [f for f in fl if f != flag]}


def collect(db, q=""):
    """คืน {group_key: [แถว]} โดย 1 แถว = 1 ปัญหาของครุภัณฑ์ 1 ชิ้น (q = คำค้นที่ normalize แล้ว)"""
    where, params = "needs_review IS NOT NULL", []
    if q:
        where += " AND (" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in SEARCH_COLUMNS) + ")"
        params = [f"%{like_escape(q)}%"] * len(SEARCH_COLUMNS)
    rows = db.execute(ROW_QUERY + f" WHERE {where} ORDER BY reg_no IS NULL, reg_no, temp_code", params).fetchall()
    groups = {g[0]: [] for g in ALL_GROUPS}
    for r in rows:
        for flag in flags.split(r["needs_review"]):
            groups[group_of(flag)].append(make_row(r, flag))
    return groups, len(rows)


def summary(groups):
    return [{"key": k, "label": label, "desc": desc, "n": len(groups[k])}
            for k, label, desc, _ in ALL_GROUPS if groups[k] or k != OTHER[0]]


def review_url(group="", q="", all_groups=False, path="/review"):
    """URL ของหน้ารอตรวจสอบ (หรือ export) ที่เก็บกลุ่มและคำค้นไว้ ไม่ใส่ค่าที่ว่าง"""
    params = [(k, v) for k, v in (("group", group), ("q", q), ("all", "1" if all_groups and q else "")) if v]
    return path + ("?" + urlencode(params) if params else "")


def search_ctx(group, q, all_groups):
    all_mode = bool(all_groups and q)
    return {"q": q, "all_mode": all_mode, "page_url": review_url(group, q, all_mode),
            "export_url": review_url("" if all_mode else group, q, path="/review/export.xlsx"),
            "export_all_url": review_url("", q, path="/review/export.xlsx")}


def counts_ctx(db, group, q="", all_groups=False):
    q = normalize_query(q)
    groups, n_assets = collect(db, q)
    group = GROUP_BY_KEY.get(group) or GROUPS[0]
    return {"summary": summary(groups), "n_assets": n_assets, "group": group, "oob": True,
            **search_ctx(group[0], q, all_groups)}


@router.get("/review")
def review_page(request: Request, group: str = "", q: str = "", all: str = "",
                db: sqlite3.Connection = Depends(get_db)):
    q = normalize_query(q)
    groups, n_assets = collect(db, q)
    if group not in GROUP_BY_KEY:
        group = next((k for k, *_ in ALL_GROUPS if groups[k]), GROUPS[0][0])
    ctx = search_ctx(group, q, all == "1")
    rows = [r for k, *_ in ALL_GROUPS for r in groups[k]] if ctx["all_mode"] else groups[group]
    undo = request.session.pop("undo", None)  # แถบยกเลิกหลังกด "ตรวจแล้ว" แบบไม่ใช้ htmx
    ctx.update({"summary": summary(groups), "n_assets": n_assets, "group": GROUP_BY_KEY[group],
                "rows": rows, "can_edit": can_edit(request), "undo": undo, "undo_seconds": UNDO_SECONDS})
    # พิมพ์ในช่องค้นหา (htmx): ส่งเฉพาะผลลัพธ์ + ตัวเลขในแท็บ + ลิงก์ export แบบ out-of-band ช่องค้นหาไม่ถูกแทนที่
    if is_filter_request(request):
        resp = templates.TemplateResponse(request, "review/_search_results.html", {**ctx, "oob": True})
        set_filter_url(request, resp, ctx["page_url"])
        return resp
    return templates.TemplateResponse(request, "review/list.html", ctx)


@router.post("/review/resolve")
def resolve(request: Request, asset_id: int = Form(...), flag: str = Form(...), group: str = Form(""),
            q: str = Form(""), all: str = Form(""),
            user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """ตรวจแล้ว: ลบปัญหานี้ออกจาก needs_review ของชิ้นนี้ และบันทึก audit_log"""
    group = group if group in GROUP_BY_KEY else ""
    row = db.execute("SELECT name, needs_review FROM assets WHERE id = ?", (asset_id,)).fetchone()
    current = flags.split(row and row["needs_review"])
    audit_id = None
    if flag in current:
        new_value = flags.join([f for f in current if f != flag])
        with db:
            db.execute("UPDATE assets SET needs_review = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                       (new_value, asset_id))
            audit_id = audit.log(db, user["id"], "assets", asset_id, "review",
                                 {"needs_review": {"old": row["needs_review"], "new": new_value}, "resolved": flag})

    undo = {"audit_id": audit_id, "flag": flag, "name": row["name"]} if audit_id else None
    if request.headers.get("HX-Request"):
        # แทนแถวด้วยแถบ "ตรวจแล้ว · ยกเลิก" (หายไปเองใน 5 วินาที) และอัปเดตตัวเลขแต่ละกลุ่มแบบ out-of-band
        return templates.TemplateResponse(request, "review/_resolved.html", {
            **counts_ctx(db, group, q, all == "1"), "undo": undo, "undo_seconds": UNDO_SECONDS})
    if undo:
        request.session["undo"] = undo
    return RedirectResponse(review_url(group, normalize_query(q), all == "1"), status_code=303)


def undo_error(db, entry, user):
    if entry is None or entry["user_id"] != user["id"]:
        return "ยกเลิกไม่ได้: ไม่พบรายการที่คุณกดตรวจแล้ว"
    if timeutil.parse_db_time(entry["created_at"]) + UNDO_WINDOW < timeutil.utcnow():
        return "เลยเวลายกเลิกแล้ว ถ้าต้องการให้ตรวจเรื่องนี้อีก แจ้งในหมายเหตุของรายการนั้น"
    undone = db.execute("SELECT 1 FROM audit_log WHERE action = 'review_undo' AND json_extract(changes, '$.undo_of') = ?",
                        (entry["id"],)).fetchone()
    if undone:
        return "ยกเลิกรายการนี้ไปแล้ว"
    return None


@router.post("/review/undo")
def undo_resolve(request: Request, audit_id: int = Form(...), group: str = Form(""),
                 q: str = Form(""), all: str = Form(""),
                 user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """ยกเลิก "ตรวจแล้ว": คืนปัญหาเดิมเข้า needs_review (ลำดับเดิม) และบันทึก audit_log"""
    group = group if group in GROUP_BY_KEY else ""
    entry = db.execute("SELECT * FROM audit_log WHERE id = ? AND action = 'review' AND table_name = 'assets'",
                       (audit_id,)).fetchone()
    error = undo_error(db, entry, user)
    restored_row = None
    if error is None:
        ch = json.loads(entry["changes"])
        flag, asset_id = ch["resolved"], entry["record_id"]
        before = flags.split(ch["needs_review"]["old"])
        row = db.execute("SELECT needs_review FROM assets WHERE id = ?", (asset_id,)).fetchone()
        current = flags.split(row["needs_review"])
        if flag not in current:
            # เรียงตามลำดับก่อนกดตรวจแล้ว ปัญหาที่เกิดใหม่ภายหลังต่อท้าย
            new_flags = [f for f in before if f in current or f == flag] + [f for f in current if f not in before]
            new_value = flags.join(new_flags)
            with db:
                db.execute("UPDATE assets SET needs_review = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                           (new_value, asset_id))
                audit.log(db, user["id"], "assets", asset_id, "review_undo",
                          {"needs_review": {"old": row["needs_review"], "new": new_value},
                           "restored": flag, "undo_of": entry["id"]})
        restored_row = make_row(db.execute(ROW_QUERY + " WHERE id = ?", (asset_id,)).fetchone(), flag)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "review/_undone.html", {
            **counts_ctx(db, group, q, all == "1"), "r": restored_row, "error": error, "can_edit": True})
    request.session["flash"] = error or f"ยกเลิกแล้ว: “{restored_row['flag']}” กลับมาอยู่ในรายการรอตรวจสอบ"
    return RedirectResponse(review_url(group, normalize_query(q), all == "1"), status_code=303)


# ---------------------------------------------------------------- export Excel

THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FONT = "TH SarabunIT๙"  # ฟอนต์แบบไฟล์ Excel เดิม ถ้าเครื่องไม่มี Excel จะใช้ฟอนต์อื่นแทนเอง
COLUMNS = [  # (หัวคอลัมน์, ความกว้าง)
    ("ลำดับ", 7), ("เลขทะเบียน / รหัสชั่วคราว", 26), ("ชื่อรายการ", 34), ("รายละเอียด", 30),
    ("ใช้ประจำที่", 18), ("วันที่ได้มา", 14), ("ราคา (บาท)", 12), ("ปัญหาที่ต้องตรวจ", 30),
    ("ชีต / แถวต้นฉบับ", 14), ("ข้อมูลที่ถูกต้อง (เจ้าหน้าที่กรอก)", 40),
]


def build_workbook(groups, keys, org="", q=""):
    wb = Workbook()
    wb.remove(wb.active)
    today = date.today()
    today_th = f"{today.day} {thai_date(today.isoformat()).split(' ', 1)[1]}"
    for key in keys:
        _, label, desc, _ = GROUP_BY_KEY[key]
        rows = groups[key]
        if not rows:
            continue
        ws = wb.create_sheet(label.replace("/", "-")[:31])
        ws.append([f"รายการครุภัณฑ์รอตรวจสอบ: {label} ({len(rows)} รายการ)" + (f" · ค้นหา “{q}”" if q else "")])
        ws.append([" · ".join(x for x in (desc, org, f"พิมพ์วันที่ {today_th}") if x)])
        ws.append([c[0] for c in COLUMNS])
        for i, r in enumerate(rows, 1):
            ws.append([
                i, r["reg_no"] or f"{r['temp_code']} (ชั่วคราว)", r["name"], r["details"] or "",
                r["location_text"] or "",
                thai_date(r["acquired_date"], r["acquired_date_precision"]) or (r["acquired_date_raw"] or "ไม่ทราบ"),
                money(r["unit_price"]) if r["unit_price"] is not None else "ไม่ทราบ",
                SEP.join([r["flag"], *r["other_flags"]]),
                f"{(r['source_sheet'] or '').strip()} / {r['source_row'] or ''}", "",
            ])
        ws["A1"].font = Font(name=FONT, size=18, bold=True)
        ws["A2"].font = Font(name=FONT, size=14, color="555555")
        for col, (_, width) in enumerate(COLUMNS, 1):
            ws.column_dimensions[ws.cell(row=3, column=col).column_letter].width = width
        for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
            for c in row:
                c.font = Font(name=FONT, size=14, bold=c.row == 3)
                c.border = BORDER
                c.alignment = Alignment(wrap_text=True, vertical="top")
                if c.row == 3:
                    c.fill = PatternFill("solid", fgColor="E8EFF9")
        for r in range(4, ws.max_row + 1):
            ws.row_dimensions[r].height = 42  # เผื่อที่ให้เจ้าหน้าที่เขียนด้วยมือ
        ws.freeze_panes = "A4"
        ws.print_title_rows = "3:3"
        ws.page_setup.orientation = "landscape"
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_options.horizontalCentered = True
        ws.page_margins.left = ws.page_margins.right = 0.4
        ws.oddFooter.center.text = "หน้า &P / &N"
    if not wb.worksheets:
        wb.create_sheet("ไม่มีรายการ").append([f"ไม่มีรายการรอตรวจสอบที่ตรงกับ “{q}”" if q else "ไม่มีรายการรอตรวจสอบ"])
    return wb


@router.get("/review/export.xlsx")
def export_xlsx(group: str = "", q: str = "", db: sqlite3.Connection = Depends(get_db)):
    q = normalize_query(q)
    groups, _ = collect(db, q)
    keys = [group] if group in GROUP_BY_KEY else [g[0] for g in ALL_GROUPS]
    buf = io.BytesIO()
    build_workbook(groups, keys, settings_store.org_names(db)["report"], q).save(buf)
    name = (f"ครุภัณฑ์รอตรวจสอบ_{GROUP_BY_KEY[group][1] if group in GROUP_BY_KEY else 'ทั้งหมด'}"
            f"{'_ค้นหา_' + q[:30] if q else ''}_{date.today():%Y%m%d}.xlsx")
    name = name.replace("/", "-")
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"review.xlsx\"; filename*=UTF-8''{quote(name)}"},
    )
