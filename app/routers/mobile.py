"""หน้าที่เปิดจากการสแกน QR (/q/{code}) ออกแบบสำหรับมือถือ + ปุ่มลัดเปลี่ยนสถานะ / ย้ายห้อง
และกล่องตรวจสอบพัสดุประจำปี (เมื่อมีรอบที่เปิดอยู่และล็อกอินแล้ว) ใช้กล้องปกติของมือถือสแกน ไม่ต้องสแกนในหน้าเว็บ"""
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app import audit, places, qr, records, timeutil
from app import inventory as inv
from app.auth import can_edit, get_user, require_editor
from app.config import qr_enabled
from app.db import get_db
from app.routers.assets import DISPOSE_ONLY_MSG, DISPOSED, EDITABLE_STATUSES, get_asset_or_404
from app.templating import templates

router = APIRouter()


def not_found(request, code):
    return templates.TemplateResponse(request, "q/not_found.html", {"code": qr.normalize(code)}, status_code=404)


def inventory_box(request, db, a):
    """ข้อมูลกล่องตรวจสอบพัสดุบนหน้า QR หรือ None ถ้าไม่ต้องแสดง (ยังไม่ล็อกอิน / ไม่มีรอบที่เปิดอยู่)"""
    if get_user(request) is None or not inv.schema_ready(db):
        return None
    check = inv.open_check(db)
    if check is None:
        return None
    item = inv.get_item(db, check["id"], a["id"])
    preselect = None
    if item is not None:
        # ห้องที่พบไว้ก่อนหน้า หรือห้องตามทะเบียนตอนนี้
        preselect = item["found_location_id"] or a["location_id"] or item["expected_location_id"]
    return {"check": check, "item": item, "preselect": preselect,
            "locations": places.location_options(db, preselect) if item is not None else [],
            "RESULT_CLASS": inv.RESULT_CLASS, "METHOD_LABELS": inv.METHOD_LABELS, "choices": (inv.OK, inv.BROKEN)}


def q_page(request, db, a, code, error=None, status_code=200, inv_error=None, ask_move=False):
    editable = can_edit(request)
    box = inventory_box(request, db, a) if qr_enabled() else None  # ปิด QR: ไม่มีกล่องตรวจสอบพัสดุ
    ask = None
    if box and box["item"] and ask_move and box["item"]["found_location_id"] is not None \
            and box["item"]["found_location_id"] != a["location_id"]:
        ask = {"to": box["item"]["found_location_name"], "from": a["location_name"]}
    moves = db.execute("""SELECT m.moved_at, m.reason, tl.name AS to_location FROM asset_moves m
                          LEFT JOIN locations tl ON tl.id = m.to_location_id
                          WHERE m.asset_id = ? ORDER BY m.id DESC LIMIT 3""", (a["id"],)).fetchall()
    return templates.TemplateResponse(request, "q/view.html", {
        "repairs": records.repairs(db, a["id"]), "repair_back": f"/q/{qr.code_for(a)}", "can_edit_repairs": editable,
        "today": timeutil.today_th(),
        "a": a, "code": code, "canonical": qr.code_for(a), "can_edit": editable, "error": error, "moves": moves,
        "statuses": EDITABLE_STATUSES, "inv": box, "inv_error": inv_error, "ask_move": ask,
        "locations": places.location_options(db, a["location_id"]) if editable else [],
        "staff": places.staff_options(db, a["holder_id"]) if editable else [],
    }, status_code=status_code)


@router.get("/q/{code}")
def scan(request: Request, code: str, ask: str = "", db: sqlite3.Connection = Depends(get_db)):
    asset_id = qr.find_asset_id(db, code)
    if asset_id is None:
        return not_found(request, code)
    return q_page(request, db, get_asset_or_404(db, asset_id), code, ask_move=ask == "move")


def back(request, code, message):
    request.session["flash"] = message
    return RedirectResponse(f"/q/{qr.normalize(code)}", status_code=303)


@router.post("/q/{code}/status")
def quick_status(request: Request, code: str, status: str = Form(""), user=Depends(require_editor),
                 db: sqlite3.Connection = Depends(get_db)):
    asset_id = qr.find_asset_id(db, code)
    if asset_id is None:
        return not_found(request, code)
    a = get_asset_or_404(db, asset_id)
    if a["status"] == DISPOSED:
        return q_page(request, db, a, code, error="ชิ้นนี้จำหน่ายแล้ว เปลี่ยนสถานะไม่ได้ (ยกเลิกการจำหน่ายได้เฉพาะ admin)",
                      status_code=400)
    if status == DISPOSED:
        return q_page(request, db, a, code, error=DISPOSE_ONLY_MSG, status_code=400)
    if status not in EDITABLE_STATUSES:
        return q_page(request, db, a, code, error="เลือกสถานะ", status_code=400)
    if status == a["status"]:
        return back(request, code, "สถานะเหมือนเดิม ไม่ได้บันทึก")
    with db:
        db.execute("UPDATE assets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (status, asset_id))
        audit.log(db, user["id"], "assets", asset_id, "update",
                  {"status": {"old": a["status"], "new": status}, "via": "qr"})
    return back(request, code, f"เปลี่ยนสถานะเป็น “{status}” แล้ว")


@router.post("/q/{code}/move")
def quick_move(request: Request, code: str, location_id: str = Form(""), holder_id: str = Form(""),
               user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    asset_id = qr.find_asset_id(db, code)
    if asset_id is None:
        return not_found(request, code)
    a = get_asset_or_404(db, asset_id)
    new = {}
    for field, raw, table, label in (("location_id", location_id, "locations", "ห้อง"),
                                     ("holder_id", holder_id, "staff", "ผู้รับผิดชอบ")):
        value = int(raw) if raw.strip().isdigit() else None
        if raw.strip() and value is None:
            return q_page(request, db, a, code, error=f"เลือก{label}", status_code=400)
        # เลือกได้เฉพาะที่ยังใช้งานอยู่ (ยกเว้นค่าปัจจุบัน)
        if value is not None and value != a[field] and not db.execute(
                f"SELECT 1 FROM {table} WHERE id = ? AND active = 1", (value,)).fetchone():
            return q_page(request, db, a, code, error=f"ไม่พบ{label}นี้ หรือถูกปิดใช้งานแล้ว", status_code=400)
        new[field] = value
    changes = audit.diff(a, new, ("location_id", "holder_id"))
    if not changes:
        return back(request, code, "ห้องและผู้รับผิดชอบเหมือนเดิม ไม่ได้บันทึก")
    places.label_changes(db, changes)
    with db:
        db.execute("UPDATE assets SET location_id = ?, holder_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (new["location_id"], new["holder_id"], asset_id))
        audit.log(db, user["id"], "assets", asset_id, "update", {**changes, "via": "qr"})
        places.record_move(db, asset_id, a["location_id"], new["location_id"], a["holder_id"], new["holder_id"],
                           "ย้ายจากหน้าสแกน QR")
    where = places.location_name(db, new["location_id"]) or "ยังไม่จัดเข้าห้อง"
    return back(request, code, f"บันทึกแล้ว: {where}")


# ---------------------------------------------------------------- ตรวจสอบพัสดุประจำปีด้วย QR

def open_round(db):
    return inv.open_check(db) if inv.schema_ready(db) else None


@router.post("/q/{code}/inventory")
def quick_inventory(request: Request, code: str, result: str = Form(""), location_id: str = Form(""),
                    note: str = Form(""), user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    asset_id = qr.find_asset_id(db, code)
    if asset_id is None:
        return not_found(request, code)
    a = get_asset_or_404(db, asset_id)
    if not qr_enabled():
        return back(request, code, "การตรวจสอบพัสดุด้วยการสแกน QR ปิดอยู่ ตรวจจากใบรายการห้องในหน้าตรวจสอบพัสดุ")
    check = open_round(db)
    if check is None:
        return back(request, code, "ตอนนี้ไม่มีรอบตรวจสอบพัสดุที่เปิดอยู่ ไม่ได้บันทึก")
    item = inv.get_item(db, check["id"], asset_id)
    if item is None:
        return q_page(request, db, a, code, inv_error="ชิ้นนี้ไม่อยู่ในรอบตรวจสอบพัสดุนี้", status_code=400)
    if result not in (inv.OK, inv.BROKEN):
        return q_page(request, db, a, code, inv_error="กดปุ่ม “พบ ใช้งานได้” หรือ “พบ ชำรุด”", status_code=400)
    loc = int(location_id) if location_id.strip().isdigit() else None
    allowed = {item["expected_location_id"], item["found_location_id"], a["location_id"]}
    if (location_id.strip() and loc is None) or (loc is not None and loc not in allowed and not db.execute(
            "SELECT 1 FROM locations WHERE id = ? AND active = 1", (loc,)).fetchone()):
        return q_page(request, db, a, code, inv_error="ไม่พบห้องนี้ หรือถูกปิดใช้งานแล้ว", status_code=400)
    if len(note) > 500:
        return q_page(request, db, a, code, inv_error="หมายเหตุยาวเกินไป (ไม่เกิน 500 ตัวอักษร)", status_code=400)
    try:
        with db:
            changes = inv.record(db, user["id"], check, asset_id, result, "qr", note, loc)
    except inv.InventoryError as e:
        return q_page(request, db, a, code, inv_error=str(e), status_code=409)
    item = inv.get_item(db, check["id"], asset_id)
    request.session["flash"] = f"บันทึกผลตรวจสอบพัสดุ “{result}” แล้ว" if changes else "ผลตรวจเหมือนเดิม ไม่ได้บันทึก"
    # พบที่ห้องอื่นที่ไม่ตรงกับทะเบียนตอนนี้: ถามต่อว่าจะย้ายห้องในทะเบียนเลยไหม
    ask = item["found_location_id"] is not None and item["found_location_id"] != a["location_id"]
    return RedirectResponse(f"/q/{qr.normalize(code)}" + ("?ask=move" if ask else ""), status_code=303)


@router.post("/q/{code}/inventory/move")
def inventory_move(request: Request, code: str, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """ย้ายห้องในทะเบียนไปห้องที่พบตอนตรวจสอบพัสดุ (asset_moves เหตุผล "พบระหว่างตรวจสอบพัสดุ")"""
    asset_id = qr.find_asset_id(db, code)
    if asset_id is None:
        return not_found(request, code)
    a = get_asset_or_404(db, asset_id)
    if not qr_enabled():
        return back(request, code, "การตรวจสอบพัสดุด้วยการสแกน QR ปิดอยู่ ตรวจจากใบรายการห้องในหน้าตรวจสอบพัสดุ")
    check = open_round(db)
    item = inv.get_item(db, check["id"], asset_id) if check else None
    if item is None or item["found_location_id"] is None:
        return back(request, code, "ไม่มีห้องที่พบจากการตรวจสอบพัสดุ ไม่ได้ย้าย")
    if not inv.move_to_found(db, user["id"], a, item):
        return back(request, code, "ทะเบียนเป็นห้องนี้อยู่แล้ว ไม่ได้ย้าย")
    return back(request, code, f"ย้ายห้องในทะเบียนเป็น “{item['found_location_name']}” แล้ว")
