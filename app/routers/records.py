"""ประวัติการซ่อม (editor เพิ่ม/แก้/ลบ) และการจำหน่าย (editor บันทึก ทีละชิ้นหรือเป็นชุด, admin ยกเลิก)"""
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import records, thai
from app.auth import require_admin, require_editor, safe_next
from app.csrf import MAX_FORM_FIELDS
from app.db import get_db
from app.routers.assets import get_asset_or_404, parse_price
from app.templating import templates

router = APIRouter()


def back_to(request, url, message):
    request.session["flash"] = message
    return RedirectResponse(url, status_code=303)


# ---------------------------------------------------------------- ประวัติซ่อม

def repair_input(day, month, year_be, description, cost, note):
    repaired_at, err = thai.parse_be_date(day, month, year_be)
    if err:
        raise records.RecordError(f"วันที่ซ่อม: {err}")
    value, err = parse_price(cost)
    if err:
        raise records.RecordError(f"ค่าใช้จ่าย: {err}")
    return records.repair_values(repaired_at, description, value, note)


@router.post("/assets/{asset_id}/repairs")
def add_repair(request: Request, asset_id: int, day: str = Form(""), month: str = Form(""), year_be: str = Form(""),
               description: str = Form(""), cost: str = Form(""), note: str = Form(""), back: str = Form(""),
               user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    get_asset_or_404(db, asset_id)
    url = safe_next(back, f"/assets/{asset_id}") + "#repairs"
    try:
        values = repair_input(day, month, year_be, description, cost, note)
    except records.RecordError as e:
        return back_to(request, url, f"ยังไม่ได้บันทึกประวัติซ่อม: {e}")
    records.add_repair(db, user["id"], asset_id, values)
    return back_to(request, url, "เพิ่มประวัติการซ่อมแล้ว")


def repair_or_404(db, asset_id, repair_id):
    row = records.get_repair(db, asset_id, repair_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ไม่พบประวัติซ่อมรายการนี้")
    return row


@router.post("/assets/{asset_id}/repairs/{repair_id}")
def edit_repair(request: Request, asset_id: int, repair_id: int, day: str = Form(""), month: str = Form(""),
                year_be: str = Form(""), description: str = Form(""), cost: str = Form(""), note: str = Form(""),
                back: str = Form(""), user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    row = repair_or_404(db, asset_id, repair_id)
    url = safe_next(back, f"/assets/{asset_id}") + "#repairs"
    try:
        values = repair_input(day, month, year_be, description, cost, note)
    except records.RecordError as e:
        return back_to(request, url, f"ยังไม่ได้บันทึกการแก้ไข: {e}")
    changes = records.update_repair(db, user["id"], row, values)
    return back_to(request, url, "บันทึกการแก้ไขประวัติซ่อมแล้ว" if changes else "ข้อมูลเหมือนเดิม ไม่ได้บันทึก")


@router.post("/assets/{asset_id}/repairs/{repair_id}/delete")
def delete_repair(request: Request, asset_id: int, repair_id: int, back: str = Form(""),
                  user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    row = repair_or_404(db, asset_id, repair_id)
    records.delete_repair(db, user["id"], row)
    return back_to(request, safe_next(back, f"/assets/{asset_id}") + "#repairs", "ลบประวัติซ่อมแล้ว (ยังดูได้ในประวัติการแก้ไข)")


# ---------------------------------------------------------------- การจำหน่าย

def selected_assets(db, ids):
    ids = sorted({int(i) for i in ids if str(i).isdigit()})[:records.MAX_LOT]
    if not ids:
        return []
    return db.execute(f"""SELECT a.id, a.reg_no, a.temp_code, a.name, a.unit_price, a.status, l.name AS location_name,
                                 a.location_text FROM assets a LEFT JOIN locations l ON l.id = a.location_id
                          WHERE a.id IN ({','.join('?' * len(ids))})
                          ORDER BY a.reg_no IS NULL, a.reg_no, a.temp_code""", ids).fetchall()


def dispose_page(request, db, ids, form=None, error=None, status_code=200):
    rows = selected_assets(db, ids)
    return templates.TemplateResponse(request, "disposals/new.html", {
        "rows": rows, "ok_rows": [r for r in rows if r["status"] != records.DISPOSED],
        "done_rows": [r for r in rows if r["status"] == records.DISPOSED],
        "form": form or {**thai.date_parts(), "method": "", "doc_ref": "", "amount": "", "note": ""},
        "methods": records.METHODS, "error": error, "months": thai.MONTHS_ABBR,
    }, status_code=status_code)


@router.get("/disposals/new")
def new_disposal(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    return dispose_page(request, db, request.query_params.getlist("ids"))


@router.post("/disposals")
async def create_disposal(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    form = await request.form(max_fields=MAX_FORM_FIELDS)
    ids = form.getlist("ids")
    f = {k: str(form.get(k, "")) for k in ("day", "month", "year_be", "method", "doc_ref", "amount", "note")}
    try:
        disposed_at, err = thai.parse_be_date(f["day"], f["month"], f["year_be"])
        if err:
            raise records.RecordError(f"วันที่จำหน่าย: {err}")
        amount, err = parse_price(f["amount"])
        if err:
            raise records.RecordError(f"จำนวนเงินที่ได้: {err}")
        asset_ids = [r["id"] for r in selected_assets(db, ids)]
        lot_id, n = records.dispose(db, user["id"], asset_ids, disposed_at, f["method"], f["doc_ref"], amount, f["note"])
    except records.RecordError as e:
        return dispose_page(request, db, ids, f, str(e), 400)
    if n == 1:
        return back_to(request, f"/assets/{asset_ids[0]}", "บันทึกการจำหน่ายแล้ว สถานะเป็น “จำหน่ายแล้ว”")
    fy = thai.fiscal_year_of(date.fromisoformat(disposed_at))
    return back_to(request, f"/reports/disposals?fy={fy}",
                   f"บันทึกการจำหน่าย {n} ชิ้น ภายใต้หนังสือ {records.clean(f['doc_ref'], 200, '')} แล้ว")


def disposal_or_404(db, disposal_id):
    d = records.get_disposal(db, disposal_id)
    if d is None:
        raise HTTPException(status_code=404, detail="ไม่พบรายการจำหน่ายนี้")
    return d


@router.get("/disposals/{disposal_id}/cancel")
def cancel_form(request: Request, disposal_id: int, user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    d = disposal_or_404(db, disposal_id)
    return templates.TemplateResponse(request, "disposals/cancel.html", {"d": d, "error": None})


@router.post("/disposals/{disposal_id}/cancel")
def cancel(request: Request, disposal_id: int, reason: str = Form(""), user=Depends(require_admin),
           db: sqlite3.Connection = Depends(get_db)):
    d = disposal_or_404(db, disposal_id)
    try:
        restore = records.cancel_disposal(db, user["id"], d, reason)
    except records.RecordError as e:
        return templates.TemplateResponse(request, "disposals/cancel.html", {"d": d, "error": str(e)}, status_code=400)
    return back_to(request, f"/assets/{d['asset_id']}", f"ยกเลิกการจำหน่ายแล้ว สถานะกลับเป็น “{restore}”")
