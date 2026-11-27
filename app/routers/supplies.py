"""หน้าจอระบบวัสดุสิ้นเปลือง (0.9.0)

viewer ขึ้นไปดูยอด/บัญชีวัสดุ/ใบรับ/ใบเบิกได้ · editor ขึ้นไป (เสมียนตรา) บันทึกรับ จ่าย จัดการรายการ นำเข้า
ทุกการบันทึกลง audit_log ใน transaction เดียวกับข้อมูล (ดู app/supplies.py)
"""
import json
import sqlite3
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app import audit
from app import supplies as sp
from app import supplies_reports as rep
from app import supplies_xlsx, timeutil
from app.auth import require_editor, require_user
from app.db import get_db
from app.templating import THAI_MONTHS_ABBR, templates, thai_date, thai_datetime

router = APIRouter()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def flash(request, message):
    request.session["flash"] = message


def int_or_none(text):
    text = str(text or "").strip()
    return int(text) if text.isdigit() else None


def month_or_none(text):
    """'2026-11' ที่ถูกต้องและปีสมเหตุสมผล (ห่างจากปีปัจจุบันไม่เกิน 100 ปี) หรือ None"""
    text = str(text or "").strip()
    try:
        d = date.fromisoformat(text + "-01")
    except ValueError:
        return None
    return text if timeutil.reasonable_year(d) else None


def xlsx_response(data, filename):
    from urllib.parse import quote
    return Response(data, media_type=XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ---------------------------------------------------------------- ส่งออก Excel (ส่วนกลาง)
# ทุกไฟล์: หัวรายงานมีผู้ส่งออก + วันเวลา · ชื่อไฟล์ภาษาไทยพร้อมช่วงวันที่ · บันทึกใน audit_log (table exports, action export)

BAD_FILENAME = str.maketrans({c: "-" for c in '\\/:*?"<>|'})


def export_meta(user):
    return {"by": user["display_name"] or user["username"],
            "at": thai_datetime(timeutil.utcnow().isoformat(sep=" ", timespec="seconds"))}


def file_date(d):
    """date -> '26พ.ย.2569' (ใช้ในชื่อไฟล์)"""
    return f"{d.day}{THAI_MONTHS_ABBR[d.month - 1]}{d.year + 543}"


def file_month(month):
    y, m = map(int, month.split("-"))
    return f"{THAI_MONTHS_ABBR[m - 1]}{y + 543}"


def month_span(month):
    start, end = rep.month_range(month)
    return f"เดือน {thai_date(start.isoformat(), 'month')} (ช่วงวันที่ {thai_date(start.isoformat())} – {thai_date(end.isoformat())})"


def all_span(dates, today):
    """ช่วงวันที่ของรายงานที่ไม่ได้เลือกเดือน: ตั้งแต่เอกสารแรกถึงวันนี้"""
    if not dates:
        return f"ทุกเดือน (ถึงวันที่ {thai_date(today.isoformat())})"
    return f"ทุกเดือน (ช่วงวันที่ {thai_date(min(dates))} – {thai_date(max(max(dates), today.isoformat()))})"


def export(db, user, report, data, filename, filters, n_rows):
    filename = filename.translate(BAD_FILENAME)
    audit.log(db, user["id"], "exports", 0, "export", {
        "report": report, "file": filename, "filters": {k: v for k, v in filters.items() if v not in (None, "", False)},
        "rows": n_rows})
    db.commit()
    return xlsx_response(data, filename)


def item_or_404(db, item_id):
    try:
        return sp.get_item(db, item_id)
    except sp.SupplyError:
        raise HTTPException(status_code=404) from None


# ---------------------------------------------------------------- ยอดคงเหลือ

def as_of_or_none(text):
    """วันที่ของยอดคงเหลือ (ไม่เกินวันนี้) · ว่าง/ผิดรูปแบบ = ยอดปัจจุบัน"""
    try:
        d = date.fromisoformat(str(text or "").strip())
    except ValueError:
        return None
    return d if d < timeutil.today_th() and timeutil.reasonable_year(d) else None


def balance_filters(category="", q="", low="", as_of=""):
    return {"category": int_or_none(category), "q": (q or "").strip()[:100], "low": low == "1", "as_of": as_of_or_none(as_of)}


def balance_query(f):
    return urlencode({k: v for k, v in (("as_of", f["as_of"] or ""), ("category", f["category"] or ""), ("q", f["q"]),
                                        ("low", "1" if f["low"] else "")) if v})


@router.get("/supplies")
def balances_page(request: Request, category: str = "", q: str = "", low: str = "", as_of: str = "",
                  user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    f = balance_filters(category, q, low, as_of)
    rows = sp.balances(db, f["category"], f["q"], f["low"], as_of=f["as_of"])
    n_out, n_low = sp.low_count(db)
    ctx = {"rows": rows, "f": f, "cats": sp.categories(db, active_only=True), "n_out": n_out, "n_low": n_low,
           "query": balance_query(f), "tab": "balances", "LEVEL_LABELS": sp.LEVEL_LABELS,
           "today": timeutil.today_th(),
           "n_items": db.execute("SELECT COUNT(*) FROM supply_items WHERE active = 1").fetchone()[0]}
    if request.headers.get("HX-Request") and not request.headers.get("HX-History-Restore-Request"):
        return templates.TemplateResponse(request, "supplies/_balances.html", ctx)
    return templates.TemplateResponse(request, "supplies/balances.html", ctx)


@router.get("/supplies/low")
def low_page(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    rows = sp.balances(db, only_low=True)
    return templates.TemplateResponse(request, "supplies/low.html", {
        "rows": rows, "tab": "low", "LEVEL_LABELS": sp.LEVEL_LABELS,
        "n_out": sum(r["level"] == "out" for r in rows), "n_low": sum(r["level"] == "low" for r in rows)})


@router.get("/supplies/balances.xlsx")
def balances_xlsx(category: str = "", q: str = "", low: str = "", as_of: str = "",
                  user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    f = balance_filters(category, q, low, as_of)
    rows = sp.balances(db, f["category"], f["q"], f["low"], as_of=f["as_of"])
    day = f["as_of"] or timeutil.today_th()
    cat = db.execute("SELECT name FROM supply_categories WHERE id = ?", (f["category"],)).fetchone() if f["category"] else None
    data = supplies_xlsx.balances(db, rows, day, export_meta(user), category=cat["name"] if cat else None,
                                  only_low=f["low"], q=f["q"])
    return export(db, user, "ยอดคงเหลือวัสดุ", data, f"ยอดคงเหลือวัสดุ_ณ{file_date(day)}.xlsx",
                  {**f, "as_of": day.isoformat()}, len(rows))


@router.get("/supplies/low.xlsx")
def low_xlsx(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    rows = sp.balances(db, only_low=True)
    today = timeutil.today_th()
    data = supplies_xlsx.low(db, rows, today, export_meta(user))
    return export(db, user, "รายการวัสดุที่ต้องสั่งซื้อ", data, f"วัสดุที่ต้องสั่งซื้อ_ณ{file_date(today)}.xlsx",
                  {"as_of": today.isoformat()}, len(rows))


# ---------------------------------------------------------------- รายการวัสดุ / หมวด

def item_form_page(request, db, form, errors, item=None, units=(), status_code=200):
    return templates.TemplateResponse(request, "supplies/item_form.html", {
        "form": form, "errors": errors, "item": item, "units": units, "tab": "items",
        "cats": sp.categories(db, active_only=True) if item is None else sp.categories(db)}, status_code=status_code)


ITEM_STATUSES = [("", "ทุกสถานะ"), ("active", "ใช้งาน"), ("inactive", "ปิดใช้งาน")]


def item_filters(category="", q="", status=""):
    return {"category": int_or_none(category), "q": (q or "").strip()[:100],
            "status": status if status in {k for k, _ in ITEM_STATUSES} else ""}


def item_rows(db, f):
    rows = sp.balances(db, f["category"], f["q"], include_inactive=True)
    if f["status"] == "active":
        rows = [r for r in rows if r["active"]]
    elif f["status"] == "inactive":
        rows = [r for r in rows if not r["active"]]
    return rows


def item_query(f):
    return urlencode({k: v for k, v in (("category", f["category"] or ""), ("q", f["q"]), ("status", f["status"])) if v})


@router.get("/supplies/items")
def items_page(request: Request, category: str = "", q: str = "", status: str = "",
               user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    f = item_filters(category, q, status)
    return templates.TemplateResponse(request, "supplies/items.html", {
        "rows": item_rows(db, f), "tab": "items", "cats": sp.categories(db), "LEVEL_LABELS": sp.LEVEL_LABELS,
        "f": f, "statuses": ITEM_STATUSES, "query": item_query(f),
        "n_items": db.execute("SELECT COUNT(*) FROM supply_items").fetchone()[0]})


@router.get("/supplies/items.xlsx")
def items_xlsx(category: str = "", q: str = "", status: str = "",
               user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    """รายการวัสดุตามตัวกรอง ในรูปแบบแบบฟอร์มนำเข้า (ยอดยกมา = ยอดคงเหลือ ณ วันที่ส่งออก)"""
    f = item_filters(category, q, status)
    rows = item_rows(db, f)
    today = timeutil.today_th()
    cat = db.execute("SELECT name FROM supply_categories WHERE id = ?", (f["category"],)).fetchone() if f["category"] else None
    scope = " · ".join(x for x in (f"หมวด {cat['name']}" if cat else "", f"ค้นหา “{f['q']}”" if f["q"] else "",
                                   dict(ITEM_STATUSES)[f["status"]] if f["status"] else "") if x)
    data = supplies_xlsx.items_export(db, rows, today, export_meta(user), scope)
    return export(db, user, "รายการวัสดุ (แบบฟอร์มนำเข้า)", data, f"รายการวัสดุ_ณ{file_date(today)}.xlsx",
                  {**f, "as_of": today.isoformat()}, len(rows))


@router.get("/supplies/items/new")
def new_item_form(request: Request, category: str = "", user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    return item_form_page(request, db, {"category_id": category}, {})


@router.post("/supplies/items/new")
async def create_item(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    form = dict(await request.form())
    v, units, errors = sp.parse_item_form(db, form)
    if errors:
        return item_form_page(request, db, form, errors, status_code=400)
    try:
        item_id = sp.save_item(db, user["id"], v, units)
    except (sp.SupplyError, sqlite3.IntegrityError) as e:
        return item_form_page(request, db, form, {"form": str(e)}, status_code=400)
    flash(request, f"เพิ่มรายการ “{v['name']}” แล้ว")
    nxt = "/supplies/items/new?category=" + str(v["category_id"]) if form.get("again") else f"/supplies/items/{item_id}"
    return RedirectResponse(nxt, status_code=303)


@router.get("/supplies/items/{item_id}/edit")
def edit_item_form(request: Request, item_id: int, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    item = item_or_404(db, item_id)
    form = {k: item[k] if item[k] is not None else "" for k in ("category_id", "name", "base_unit", "reorder_point", "note")}
    units = db.execute("SELECT * FROM supply_units WHERE item_id = ? ORDER BY active DESC, factor DESC", (item_id,)).fetchall()
    return item_form_page(request, db, form, {}, item, units)


@router.post("/supplies/items/{item_id}/edit")
async def edit_item(request: Request, item_id: int, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    item = item_or_404(db, item_id)
    form = dict(await request.form())
    units = db.execute("SELECT * FROM supply_units WHERE item_id = ? ORDER BY active DESC, factor DESC", (item_id,)).fetchall()
    v, new_units, errors = sp.parse_item_form(db, form, item_id)
    if errors:
        return item_form_page(request, db, form, errors, item, units, status_code=400)
    try:
        sp.save_item(db, user["id"], v, new_units, item_id)
    except (sp.SupplyError, sqlite3.IntegrityError) as e:
        return item_form_page(request, db, form, {"form": str(e)}, item, units, status_code=400)
    flash(request, "บันทึกรายการแล้ว")
    return RedirectResponse(f"/supplies/items/{item_id}", status_code=303)


@router.post("/supplies/items/{item_id}/active")
def item_active(request: Request, item_id: int, active: str = Form(""), user=Depends(require_editor),
                db: sqlite3.Connection = Depends(get_db)):
    item_or_404(db, item_id)
    try:
        sp.set_item_active(db, user["id"], item_id, active == "1")
        flash(request, "เปิดใช้งานรายการแล้ว" if active == "1" else "ปิดใช้งานรายการแล้ว (ไม่โผล่ในหน้ารับ/เบิก ประวัติยังอยู่)")
    except sp.SupplyError as e:
        flash(request, str(e))
    return RedirectResponse(f"/supplies/items/{item_id}", status_code=303)


@router.post("/supplies/units/{unit_id}/active")
def unit_active(request: Request, unit_id: int, active: str = Form(""), user=Depends(require_editor),
                db: sqlite3.Connection = Depends(get_db)):
    try:
        item_id = sp.set_unit_active(db, user["id"], unit_id, active == "1")
    except sp.SupplyError:
        raise HTTPException(status_code=404) from None
    flash(request, "บันทึกหน่วยแล้ว")
    return RedirectResponse(f"/supplies/items/{item_id}/edit", status_code=303)


@router.get("/supplies/items/{item_id}")
def item_page(request: Request, item_id: int, date_from: str = "", date_to: str = "",
              user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    item = item_or_404(db, item_id)
    d_from, d_to = parse_range(date_from, date_to)
    opening, rows = sp.stock_card(db, item_id, d_from, d_to)
    bal = sp.balance(db, item_id)
    units = sp.units_by_item(db, active_only=False).get(item_id, [])
    active_units = [u for u in units if u["active"]]
    return templates.TemplateResponse(request, "supplies/item.html", {
        "item": item, "rows": rows, "opening": opening, "balance": bal, "units": units, "tab": "items",
        "breakdown": sp.breakdown(bal, item["base_unit"], active_units), "level": sp.level(bal, item["reorder_point"]),
        "LEVEL_LABELS": sp.LEVEL_LABELS, "date_from": d_from, "date_to": d_to,
        "query": urlencode({k: v for k, v in (("date_from", d_from or ""), ("date_to", d_to or "")) if v})})


@router.get("/supplies/items/{item_id}/card.xlsx")
def item_card_xlsx(item_id: int, date_from: str = "", date_to: str = "",
                   user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    item = item_or_404(db, item_id)
    d_from, d_to = parse_range(date_from, date_to)
    opening, rows = sp.stock_card(db, item_id, d_from, d_to)
    today = timeutil.today_th()
    start = d_from or (date.fromisoformat(rows[0]["date"]) if rows else today)
    end = d_to or today
    span = f"ช่วงวันที่ {thai_date(start.isoformat())} – {thai_date(end.isoformat())}"
    data = supplies_xlsx.stock_card(db, item, opening, rows, span, export_meta(user),
                                    opening_label="ยอดยกมา" if d_from else "เริ่มต้น")
    name = f"บัญชีวัสดุ_{item['name'][:40]}_{file_date(start)}-{file_date(end)}.xlsx"
    return export(db, user, "บัญชีวัสดุ", data, name, {"item_id": item_id, "date_from": d_from and d_from.isoformat(),
                                                       "date_to": d_to and d_to.isoformat()}, len(rows))


def parse_range(date_from, date_to):
    out = []
    for text in (date_from, date_to):
        try:
            d = date.fromisoformat(str(text).strip())
            out.append(d if timeutil.reasonable_year(d) else None)
        except ValueError:
            out.append(None)
    return tuple(out)


@router.get("/supplies/categories")
def categories_page(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "supplies/categories.html", {"cats": sp.categories(db), "tab": "items"})


@router.post("/supplies/categories")
def add_category(request: Request, name: str = Form(""), user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    try:
        sp.save_category(db, user["id"], name)
        flash(request, "เพิ่มหมวดแล้ว")
    except sp.SupplyError as e:
        flash(request, str(e))
    return RedirectResponse("/supplies/categories", status_code=303)


@router.post("/supplies/categories/{cat_id}")
def rename_category(request: Request, cat_id: int, name: str = Form(""), user=Depends(require_editor),
                    db: sqlite3.Connection = Depends(get_db)):
    try:
        sp.save_category(db, user["id"], name, cat_id)
        flash(request, "บันทึกหมวดแล้ว")
    except sp.SupplyError as e:
        flash(request, str(e))
    return RedirectResponse("/supplies/categories", status_code=303)


@router.post("/supplies/categories/{cat_id}/active")
def category_active(request: Request, cat_id: int, active: str = Form(""), user=Depends(require_editor),
                    db: sqlite3.Connection = Depends(get_db)):
    try:
        sp.set_category_active(db, user["id"], cat_id, active == "1")
        flash(request, "บันทึกหมวดแล้ว")
    except sp.SupplyError as e:
        flash(request, str(e))
    return RedirectResponse("/supplies/categories", status_code=303)


# ---------------------------------------------------------------- รับเข้า

def script_json(value):
    """JSON สำหรับใส่ใน <script type="application/json"> (ชื่อวัสดุที่มี </script> ปิดแท็กก่อนไม่ได้)"""
    return json.dumps(value, ensure_ascii=False).replace("<", r"\u003c").replace(">", r"\u003e").replace("&", r"\u0026")


def choices_json(choices):
    """ข้อมูลของช่องเลือกวัสดุสำหรับ supplies.js (label -> id ยอด หน่วย)"""
    return script_json({c["label"]: {"id": c["id"], "balance": c["balance"], "unit": c["base_unit"], "units": c["units"]}
                        for c in choices})


def options_json(choices):
    """รายการของช่องเลือกวัสดุ (combobox.js): ชื่อ หมวด ยอดคงเหลือพร้อมหน่วย ป้ายหมด/ใกล้หมด
    group = หมวด (หัวหมวดและปุ่มกรอง เรียงตามลำดับหมวดในระบบ) · keys = หมายเหตุ (เช่น ชื่อเดิมในไฟล์) ใช้ค้นหาด้วย"""
    tags = {"out": ("หมด", "danger"), "low": ("ใกล้หมด", "warn")}
    out = []
    for c in choices:
        tag, kind = tags.get(c["level"], (None, None))
        out.append({"value": c["label"], "name": c["name"], "sub": c["category"], "right": f"{c['balance']:,} {c['base_unit']}",
                    "tag": tag, "tagKind": kind, "dim": c["balance"] <= 0, "group": c["category"], "keys": c["note"]})
    return script_json(out)


def receive_page(request, db, form, rows, errors=(), status_code=200, copied_from=None):
    choices = sp.item_choices(db)
    return templates.TemplateResponse(request, "supplies/receive.html", {
        "form": form, "rows": rows, "errors": list(errors), "choices": choices, "choices_json": choices_json(choices), "options_json": options_json(choices),
        "sources": sp.sources(db), "n_rows": max(4, len(rows) + 2), "max_lines": sp.MAX_LINES, "tab": "receive",
        "copied_from": copied_from, "today": timeutil.today_th().isoformat()}, status_code=status_code)


@router.get("/supplies/receive")
def receive_form(request: Request, copy: str = "", user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    form, rows, copied = {"received_date": timeutil.today_th().isoformat()}, [], None
    if int_or_none(copy):
        try:
            r, lines = sp.get_receipt(db, int(copy))
        except sp.SupplyError:
            raise HTTPException(status_code=404) from None
        copied = r["id"]
        form.update({"source": r["source"], "doc_no": r["doc_no"] or "", "note": r["note"] or "",
                     "received_date": r["received_date"], "copied_from": r["id"]})
        units = sp.units_by_item(db)
        for ln in lines:
            u = next((u for u in units.get(ln["item_id"], []) if u["name"] == ln["unit_name"] and u["factor"] == ln["factor"]), None)
            rows.append({"item": f"{ln['name']} · {ln['category']}", "unit": str(u["id"]) if u else "base",
                         "qty": str(ln["unit_qty"] if u else ln["qty"])})
    return receive_page(request, db, form, rows, copied_from=copied)


@router.post("/supplies/receive")
async def receive(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    form = dict(await request.form())
    h, lines, errors, rows = sp.parse_receipt(db, form)
    copied = int_or_none(form.get("copied_from"))
    if errors:
        return receive_page(request, db, form, rows, errors, status_code=400, copied_from=copied)
    if copied and not db.execute("SELECT 1 FROM supply_receipts WHERE id = ?", (copied,)).fetchone():
        copied = None
    rid = sp.record_receipt(db, user["id"], h, lines, copied)
    flash(request, f"บันทึกใบรับ #{rid} แล้ว ({len(lines)} รายการ)")
    return RedirectResponse(f"/supplies/receipts/{rid}?saved=1", status_code=303)


@router.get("/supplies/receipts")
def receipts_page(request: Request, month: str = "", user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    m = month_or_none(month)
    return templates.TemplateResponse(request, "supplies/receipts.html", {
        "rows": sp.list_receipts(db, m), "month": m or "", "tab": "receipts",
        "query": urlencode({"month": m}) if m else ""})


@router.get("/supplies/receipts.xlsx")
def receipts_xlsx(month: str = "", user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    m = month_or_none(month)
    lines = rep.receipt_lines(db, m)
    today = timeutil.today_th()
    span = month_span(m) if m else all_span([r["received_date"] for r in lines], today)
    data = supplies_xlsx.receipts(db, lines, span, export_meta(user))
    name = f"ใบรับวัสดุ_{file_month(m) if m else 'ทั้งหมดถึง' + file_date(today)}.xlsx"
    return export(db, user, "รายการรับวัสดุเข้า", data, name, {"month": m}, len(lines))


@router.get("/supplies/receipts/{rid}")
def receipt_page(request: Request, rid: int, saved: str = "", user=Depends(require_user),
                 db: sqlite3.Connection = Depends(get_db)):
    try:
        r, lines = sp.get_receipt(db, rid)
    except sp.SupplyError:
        raise HTTPException(status_code=404) from None
    return templates.TemplateResponse(request, "supplies/receipt.html", {
        "r": r, "lines": lines, "tab": "receipts", "saved": saved == "1",
        "copies": db.execute("SELECT id FROM supply_receipts WHERE copied_from_id = ? ORDER BY id", (rid,)).fetchall()})


@router.post("/supplies/receipts/{rid}/cancel")
def cancel_receipt(request: Request, rid: int, reason: str = Form(""), user=Depends(require_editor),
                   db: sqlite3.Connection = Depends(get_db)):
    try:
        sp.cancel_receipt(db, user["id"], rid, reason)
        flash(request, f"ยกเลิกใบรับ #{rid} แล้ว ยอดคงเหลือกลับเป็นเหมือนก่อนรับ")
    except sp.SupplyError as e:
        flash(request, str(e))
    return RedirectResponse(f"/supplies/receipts/{rid}", status_code=303)


# ---------------------------------------------------------------- เบิกจ่าย

def issue_page(request, db, form, rows, errors=(), status_code=200, copied_from=None, short=()):
    choices = sp.item_choices(db)
    return templates.TemplateResponse(request, "supplies/issue.html", {
        "form": form, "rows": rows, "errors": list(errors), "choices": choices, "choices_json": choices_json(choices), "options_json": options_json(choices),
        "departments": sp.issuing_departments(db), "approvers": sp.approvers(db),
        "requesters_json": script_json(sp.requester_names(db)),
        "n_rows": max(4, len(rows) + 2), "max_lines": sp.MAX_LINES, "tab": "issue",
        "copied_from": copied_from, "short": list(short), "today": timeutil.today_th().isoformat()}, status_code=status_code)


@router.get("/supplies/issue")
def issue_form(request: Request, copy: str = "", user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    today = timeutil.today_th().isoformat()
    approver = sp.default_approver(db)
    form = {"issue_date": today, "approved_date": today, "approver_staff_id": str(approver or "")}
    # "บันทึกใบเบิกถัดไป": จำวันที่และผู้อนุมัติของใบก่อนหน้าไว้ใน session
    last = request.session.pop("last_issue", None)
    if isinstance(last, dict):
        form.update({k: str(v) for k, v in last.items() if k in ("issue_date", "approved_date", "approver_staff_id") and v})
    rows, copied = [], None
    if int_or_none(copy):
        try:
            r, lines = sp.get_issue(db, int(copy))
        except sp.SupplyError:
            raise HTTPException(status_code=404) from None
        copied = r["id"]
        form.update({"department_id": str(r["department_id"]), "requester_name": r["requester_name"],
                     "doc_no": r["doc_no"] or "", "note": r["note"] or "", "issue_date": r["issue_date"],
                     "approved_date": r["approved_date"], "approver_staff_id": str(r["approver_staff_id"] or ""),
                     "copied_from": r["id"]})
        for ln in lines:
            rows.append({"item": f"{ln['name']} · {ln['category']}", "req": str(ln["requested_qty"]), "out": ""})
    return issue_page(request, db, form, rows, copied_from=copied)


@router.post("/supplies/issue")
async def issue(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    form = dict(await request.form())
    h, lines, errors, rows = sp.parse_issue(db, form)
    copied = int_or_none(form.get("copied_from"))
    if errors:
        return issue_page(request, db, form, rows, errors, status_code=400, copied_from=copied)
    if copied and not db.execute("SELECT 1 FROM supply_issues WHERE id = ?", (copied,)).fetchone():
        copied = None
    try:
        iid = sp.record_issue(db, user["id"], h, lines, copied)
    except sp.StockChanged as e:
        # ยอดเปลี่ยนระหว่างกรอก (มีคนจ่ายไปก่อน หรือย้อนวันที่): เสนอจำนวนใหม่ = min(ขอ, ที่มี) ให้กดยืนยันอีกครั้ง
        for ln, avail in e.short:
            rows[ln["row"] - 1]["out"] = str(min(ln["requested_qty"], avail))
        form.update({f"out_{i}": r["out"] for i, r in enumerate(rows, 1)})
        msg = ["ยอดคงเหลือเปลี่ยนไประหว่างกรอก (หรือวันที่เบิกย้อนหลังก่อนรับของ) ระบบเสนอจำนวนจ่ายใหม่ให้แล้ว ตรวจแล้วกดบันทึกอีกครั้ง"]
        return issue_page(request, db, form, rows, msg, status_code=409, copied_from=copied, short=e.short)
    except sp.SupplyError as e:
        return issue_page(request, db, form, rows, [str(e)], status_code=400, copied_from=copied)
    request.session["last_issue"] = {"issue_date": h["issue_date"].isoformat(), "approved_date": h["approved_date"].isoformat(),
                                     "approver_staff_id": h["approver_staff_id"]}
    n_short = sum(ln["issued_qty"] < ln["requested_qty"] for ln in lines)
    flash(request, f"บันทึกใบเบิก #{iid} แล้ว ({len(lines)} รายการ"
                   + (f" · จ่ายไม่ครบ {n_short} รายการ" if n_short else "") + ")")
    return RedirectResponse(f"/supplies/issues/{iid}?saved=1", status_code=303)


@router.get("/supplies/issues")
def issues_page(request: Request, month: str = "", department: str = "", user=Depends(require_user),
                db: sqlite3.Connection = Depends(get_db)):
    m, dep = month_or_none(month), int_or_none(department)
    return templates.TemplateResponse(request, "supplies/issues.html", {
        "rows": sp.list_issues(db, m, dep), "month": m or "", "department": dep, "tab": "issues",
        "query": urlencode({k: v for k, v in (("month", m), ("department", dep)) if v}),
        "departments": db.execute("SELECT id, name FROM departments WHERE issues_supplies = 1 OR id IN "
                                  "(SELECT department_id FROM supply_issues) ORDER BY sort_order, name").fetchall()})


@router.get("/supplies/issues.xlsx")
def issues_xlsx(month: str = "", department: str = "", user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    m, dep = month_or_none(month), int_or_none(department)
    dep_name = db.execute("SELECT name FROM departments WHERE id = ?", (dep,)).fetchone() if dep else None
    dep_name = dep_name["name"] if dep_name else None
    lines = rep.issue_lines(db, m, dep if dep_name else None)
    today = timeutil.today_th()
    span = month_span(m) if m else all_span([r["issue_date"] for r in lines], today)
    data = supplies_xlsx.issues(db, lines, span, export_meta(user), department=dep_name)
    name = f"ใบเบิกวัสดุ_{file_month(m) if m else 'ทั้งหมดถึง' + file_date(today)}{'_' + dep_name if dep_name else ''}.xlsx"
    return export(db, user, "รายการเบิกวัสดุ", data, name, {"month": m, "department": dep_name}, len(lines))


# ---------------------------------------------------------------- สรุปรายเดือน

def report_month(month):
    return month_or_none(month) or timeutil.today_th().strftime("%Y-%m")


@router.get("/supplies/monthly")
def monthly_page(request: Request, month: str = "", user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    m = report_month(month)
    return templates.TemplateResponse(request, "supplies/monthly.html", {
        "month": m, "matrix": rep.issue_matrix(db, m), "move": rep.movement(db, m), "tab": "monthly",
        "query": urlencode({"month": m}), "LEVEL_LABELS": sp.LEVEL_LABELS})


@router.get("/supplies/monthly/issues.xlsx")
def monthly_issues_xlsx(month: str = "", user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    m = report_month(month)
    data_ = rep.issue_matrix(db, m)
    data = supplies_xlsx.issue_matrix(db, data_, month_span(m), export_meta(user))
    return export(db, user, "สรุปการเบิกวัสดุรายเดือน", data, f"สรุปการเบิกวัสดุ_{file_month(m)}.xlsx",
                  {"month": m}, len(data_["rows"]))


@router.get("/supplies/monthly/movement.xlsx")
def monthly_movement_xlsx(month: str = "", user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    m = report_month(month)
    data_ = rep.movement(db, m)
    data = supplies_xlsx.movement(db, data_, month_span(m), export_meta(user))
    return export(db, user, "สรุปรับ–จ่าย–คงเหลือวัสดุ", data, f"สรุปรับจ่ายคงเหลือวัสดุ_{file_month(m)}.xlsx",
                  {"month": m}, len(data_["rows"]))


@router.get("/supplies/issues/{iid}")
def issue_doc(request: Request, iid: int, saved: str = "", user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    try:
        r, lines = sp.get_issue(db, iid)
    except sp.SupplyError:
        raise HTTPException(status_code=404) from None
    return templates.TemplateResponse(request, "supplies/issue_doc.html", {
        "r": r, "lines": lines, "tab": "issues", "saved": saved == "1",
        "copies": db.execute("SELECT id FROM supply_issues WHERE copied_from_id = ? ORDER BY id", (iid,)).fetchall()})


@router.post("/supplies/issues/{iid}/cancel")
def cancel_issue(request: Request, iid: int, reason: str = Form(""), user=Depends(require_editor),
                 db: sqlite3.Connection = Depends(get_db)):
    try:
        sp.cancel_issue(db, user["id"], iid, reason)
        flash(request, f"ยกเลิกใบเบิก #{iid} แล้ว ยอดที่จ่ายไปคืนกลับเข้าคลัง")
    except sp.SupplyError as e:
        flash(request, str(e))
    return RedirectResponse(f"/supplies/issues/{iid}", status_code=303)


# ---------------------------------------------------------------- นำเข้าจาก Excel (แบบฟอร์มมาตรฐาน)

def import_page(request, db, error=None, status_code=200):
    from app import supplies_import as si
    return templates.TemplateResponse(request, "supplies/import.html", {
        "error": error, "batches": si.batches(db), "tab": "import", "today": timeutil.today_th().isoformat(),
        "max_mb": si.MAX_BYTES // (1024 * 1024), "columns": si.COLUMNS}, status_code=status_code)


@router.get("/supplies/import")
def import_form(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    return import_page(request, db)


@router.get("/supplies/import/template.xlsx")
def import_template(user=Depends(require_editor)):
    from app import supplies_import as si
    return xlsx_response(si.template(), "แบบฟอร์มนำเข้าวัสดุ.xlsx")


@router.post("/supplies/import/preview")
async def import_preview(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    from app import supplies_import as si
    form = await request.form(max_part_size=si.MAX_BYTES + 1024)
    upload = form.get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return import_page(request, db, "เลือกไฟล์ Excel ที่กรอกตามแบบฟอร์ม", status_code=400)
    data = await upload.read(si.MAX_BYTES + 1)
    try:
        opening_date = sp.parse_date(form.get("opening_date"), "วันที่ของยอดยกมา")
        parsed = si.read_file(data, upload.filename)
    except (si.ImportError_, sp.SupplyError) as e:
        return import_page(request, db, str(e), status_code=400)
    return preview_page(request, db, data, upload.filename, opening_date.isoformat(), si.classify(db, parsed))


def preview_page(request, db, data, filename, opening_date, groups, entered=None, errors=None, picks=None, message=None):
    """หน้าตรวจก่อนนำเข้า · entered/errors/picks = ค่าที่กรอกไว้ตอนกดยืนยันแล้วมีช่องผิด (แสดงค่าเดิมให้แก้ต่อ)"""
    import base64

    from app import supplies_import as si
    return templates.TemplateResponse(request, "supplies/import_preview.html", {
        "groups": groups, "filename": filename, "opening_date": opening_date, "sha": si.digest(data),
        "payload": base64.b64encode(data).decode("ascii"), "tab": "import", "today": timeutil.today_th().isoformat(),
        "entered": entered or {}, "errors": errors or {}, "picks": picks, "message": message,
        "n_save": len(groups["new"]) + sum(1 for p in groups["diff"] if any(f["can"] for f in p["fields"]))},
        status_code=400 if (errors or message) else 200)


@router.post("/supplies/import/confirm")
async def import_confirm(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    import base64
    import binascii

    from app import supplies_import as si
    form = await request.form(max_part_size=si.MAX_BYTES * 2)
    try:
        data = base64.b64decode(str(form.get("payload", "")), validate=True)
    except (binascii.Error, ValueError):
        data = b""
    if not data or si.digest(data) != form.get("sha"):
        return import_page(request, db, "ข้อมูลไฟล์ไม่ครบหรือถูกแก้ระหว่างทาง อัปโหลดไฟล์ใหม่อีกครั้ง", status_code=400)
    picks = set()
    for key, value in form.multi_items():
        if key.startswith("pick_") and value == "file":
            _, row, field = key.split("_", 2)
            if row.isdigit():
                picks.add((int(row), field))
    filename = str(form.get("filename", "นำเข้า.xlsx"))
    try:
        groups = si.classify(db, si.read_file(data, filename))
    except si.ImportError_ as e:
        return import_page(request, db, str(e), status_code=400)
    openings, errors = si.parse_openings(form.multi_items(), groups)
    raw_date = str(form.get("opening_date", ""))
    try:
        opening_date = sp.parse_date(raw_date, "วันที่ของยอดยกมา")
    except sp.SupplyError as e:
        date_error = str(e)
    else:
        date_error = None
    if errors or date_error:
        entered = {k[5:]: v for k, v in form.multi_items() if k.startswith("open_")}
        msg = date_error or f"ยอดยกมาไม่ถูกต้อง {len(errors)} แถว (ช่องที่มีกรอบสีแดง) ยังไม่ได้บันทึกอะไร"
        return preview_page(request, db, data, filename, raw_date, groups, entered, errors, picks, msg)
    try:
        bid, summary, saved = si.apply(db, user["id"], data, filename, opening_date, picks, openings)
    except (si.ImportError_, sp.SupplyError) as e:
        return import_page(request, db, str(e), status_code=400)
    flash(request, f"นำเข้าชุด #{bid} แล้ว: รายการใหม่ {summary['new_items']} · แก้รายการเดิม {summary['updated_items']}"
                   f" · ยอดยกมา {summary['opening_rows']} รายการ (ลงวันที่ {thai_date(opening_date.isoformat())})"
                   f" · สำรอง db ไว้ก่อนแล้วที่ {saved.name}")
    return RedirectResponse("/supplies/import", status_code=303)


@router.post("/supplies/import/{batch_id}/undo")
def import_undo(request: Request, batch_id: int, reason: str = Form(""), user=Depends(require_editor),
                db: sqlite3.Connection = Depends(get_db)):
    from app import supplies_import as si
    try:
        si.undo(db, user["id"], batch_id, reason)
        flash(request, f"ยกเลิกการนำเข้าชุด #{batch_id} แล้ว (กลับยอดยกมา ปิดใช้งานรายการที่ชุดนี้เพิ่ม ประวัติยังอยู่)")
    except sp.SupplyError as e:
        flash(request, str(e))
    return RedirectResponse("/supplies/import", status_code=303)
