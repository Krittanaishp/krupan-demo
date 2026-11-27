"""ตรวจสอบพัสดุประจำปี /inventory (editor ขึ้นไป)

รอบตรวจสอบพัสดุ ความคืบหน้ารายห้อง ใบรายการสำหรับพิมพ์ หน้ากรอกผลจากใบรายการ พบของที่ไม่มีในทะเบียน
ปิดรอบ ข้อเสนอเปลี่ยนสถานะ (กดยืนยันทีละกลุ่ม) และรายงานผล (พิมพ์ A4 / Excel)
การตรวจด้วย QR อยู่ใน routers/mobile.py (หน้า /q/{code})
"""
import sqlite3
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app import inventory as inv
from app import backup, inventory_xlsx, places, settings_store, timeutil
from app.auth import require_admin, require_editor
from app.csrf import MAX_FORM_FIELDS
from app.db import get_db
from app.templating import THAI_MONTHS_ABBR, templates

router = APIRouter()
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


class NotMigrated(Exception):
    """db ยังไม่ผ่าน scripts/migrate_005.py"""


def inv_db(db: sqlite3.Connection = Depends(get_db)):
    if not inv.schema_ready(db):
        raise NotMigrated()
    return db


def get_check_or_404(db, check_id):
    check = inv.get_check(db, check_id)
    if check is None:
        raise HTTPException(status_code=404, detail="ไม่พบรอบตรวจสอบพัสดุนี้")
    return check


def room_or_404(db, check, room):
    if not inv.valid_room(room):
        raise HTTPException(status_code=404, detail="ไม่พบห้องนี้")
    name = inv.room_name(db, room)
    if name is None:
        raise HTTPException(status_code=404, detail="ไม่พบห้องนี้")
    return name


def fail(request, message, back, status_code=400):
    return templates.TemplateResponse(request, "inventory/error.html", {"message": message, "back": back},
                                      status_code=status_code)


def flash_redirect(request, url, message):
    request.session["flash"] = message
    return RedirectResponse(url, status_code=303)


def to_int(v):
    v = str(v or "").translate(THAI_DIGITS).strip()
    return int(v) if v.isdigit() else None


def parse_started(day, month, year_be):
    """วันที่เริ่มตรวจ (ปี พ.ศ.) -> (iso, error)"""
    d, m, y = to_int(day), to_int(month), to_int(year_be)
    if y is None or not 2400 <= y <= 2700:
        return None, "กรอกปี พ.ศ. 4 หลักของวันที่เริ่มตรวจ"
    try:
        return date(y - 543, m or 0, d or 0).isoformat(), None
    except ValueError:
        return None, "วันที่เริ่มตรวจไม่ถูกต้อง"


def round_form(form, errors=None):
    return {"form": form, "errors": errors or {}, "months": THAI_MONTHS_ABBR}


def default_form():
    today = timeutil.today_th()
    return {"fiscal_year": str(inv.default_fiscal_year(today)), "day": str(today.day), "month": str(today.month),
            "year_be": str(today.year + 543), "chair": "", "members": ""}


def parse_round(form):
    errors = {}
    fy = to_int(form["fiscal_year"])
    if fy is None or not 2500 <= fy <= 2700:
        errors["fiscal_year"] = "ปีงบประมาณต้องเป็น พ.ศ. 4 หลัก เช่น 2569"
    started, err = parse_started(form["day"], form["month"], form["year_be"])
    if err:
        errors["started_at"] = err
    chair, members, err = inv.parse_round_form(form["chair"], form["members"])
    if err:
        errors["committee"] = err
    return {"fiscal_year": fy, "started_at": started, "chair": chair, "members": members}, errors


def committee_form(check):
    names = inv.committee(check)
    chair = next((n for n, role in names if role == "ประธานกรรมการ"), "")
    members = "\n".join(n for n, role in names if role == "กรรมการ")
    d = date.fromisoformat(check["started_at"])
    return {"fiscal_year": str(check["fiscal_year"]), "day": str(d.day), "month": str(d.month),
            "year_be": str(d.year + 543), "chair": chair, "members": members}


# ---------------------------------------------------------------- รายการรอบ / เปิดรอบใหม่

@router.get("/inventory")
def list_rounds(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(inv_db)):
    return templates.TemplateResponse(request, "inventory/list.html", {
        "checks": inv.list_checks(db), "open": inv.open_check(db), **round_form(default_form()),
        "n_assets": db.execute("SELECT COUNT(*) FROM assets WHERE status != ? AND duplicate_of_id IS NULL",
                               (inv.DISPOSED,)).fetchone()[0]})


@router.post("/inventory")
def create_round(request: Request, fiscal_year: str = Form(""), day: str = Form(""), month: str = Form(""),
                 year_be: str = Form(""), chair: str = Form(""), members: str = Form(""),
                 user=Depends(require_editor), db: sqlite3.Connection = Depends(inv_db)):
    form = {"fiscal_year": fiscal_year, "day": day, "month": month, "year_be": year_be, "chair": chair,
            "members": members}
    values, errors = parse_round(form)
    if not errors:
        try:
            cid, n = inv.create_check(db, user["id"], values["fiscal_year"], values["started_at"],
                                      values["chair"], values["members"])
        except inv.InventoryError as e:
            errors["open"] = str(e)
        else:
            return flash_redirect(request, f"/inventory/{cid}",
                                  f"เปิดรอบตรวจสอบพัสดุประจำปี {values['fiscal_year']} แล้ว มีครุภัณฑ์ในรอบ {n:,} ชิ้น")
    return templates.TemplateResponse(request, "inventory/list.html", {
        "checks": inv.list_checks(db), "open": inv.open_check(db), **round_form(form, errors),
        "n_assets": db.execute("SELECT COUNT(*) FROM assets WHERE status != ? AND duplicate_of_id IS NULL",
                               (inv.DISPOSED,)).fetchone()[0]},
        status_code=400)


# ---------------------------------------------------------------- ความคืบหน้า

def progress_ctx(db, check, **extra):
    closed = check["closed_at"] is not None
    history = inv.reopens(db, check["id"])
    return {"reopens": history, "applied": inv.applied_proposals(db, check["id"]) if history else [],
            "can_reopen": inv.can_reopen(check), "reopen_deadline": inv.reopen_deadline(check),"check": check, "c": inv.counts(db, check["id"]), "rooms": inv.rooms(db, check["id"]), "buildings": inv.building_rooms(db, check["id"]),
            "committee": inv.committee(check), "closed": closed,
            "proposals": inv.proposals(db, check) if closed else {}, "PROPOSALS": inv.PROPOSALS,
            "edit": round_form(committee_form(check)), "missing_flag": inv.missing_flag(check["fiscal_year"]),
            **extra}


@router.get("/inventory/{check_id}")
def progress(request: Request, check_id: int, user=Depends(require_editor), db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    return templates.TemplateResponse(request, "inventory/detail.html", progress_ctx(db, check))


@router.post("/inventory/{check_id}/edit")
def edit_round(request: Request, check_id: int, fiscal_year: str = Form(""), day: str = Form(""),
               month: str = Form(""), year_be: str = Form(""), chair: str = Form(""), members: str = Form(""),
               user=Depends(require_editor), db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    if check["closed_at"] is not None:
        return fail(request, "รอบตรวจสอบพัสดุนี้ปิดแล้ว แก้ไขไม่ได้", f"/inventory/{check_id}", 409)
    form = {"fiscal_year": fiscal_year, "day": day, "month": month, "year_be": year_be, "chair": chair,
            "members": members}
    values, errors = parse_round(form)
    if errors:
        ctx = progress_ctx(db, check, edit_open=True)
        ctx["edit"] = round_form(form, errors)
        return templates.TemplateResponse(request, "inventory/detail.html", ctx, status_code=400)
    changes = inv.update_check(db, user["id"], check, values["fiscal_year"], values["started_at"],
                               values["chair"], values["members"])
    return flash_redirect(request, f"/inventory/{check_id}",
                          "บันทึกข้อมูลรอบแล้ว" if changes else "ข้อมูลรอบเหมือนเดิม ไม่ได้บันทึก")


@router.get("/inventory/{check_id}/rooms/{room}/unchecked")
def room_unchecked(request: Request, check_id: int, room: str, user=Depends(require_editor),
                   db: sqlite3.Connection = Depends(inv_db)):
    """รายการที่ยังไม่ได้ตรวจของห้องหนึ่ง (htmx ใส่ใต้แถวห้องในหน้าความคืบหน้า)"""
    check = get_check_or_404(db, check_id)
    name = room_or_404(db, check, room)
    return templates.TemplateResponse(request, "inventory/_unchecked.html", {
        "check": check, "room": room, "room_name": name,
        "rows": inv.room_items(db, check_id, room, only_unchecked=True)})


# ---------------------------------------------------------------- ตรวจด้วยใบรายการ

def room_ctx(db, check, room, name, **extra):
    rows = inv.room_items(db, check["id"], room)
    return {"check": check, "room": room, "room_name": name, "rows": rows, "RESULTS": inv.RESULTS,
            "RESULT_CLASS": inv.RESULT_CLASS, "METHOD_LABELS": inv.METHOD_LABELS, "no_room": room == inv.NO_ROOM,
            "n_checked": sum(1 for r in rows if r["result"]), **extra}


@router.get("/inventory/{check_id}/rooms/{room}")
def room_entry(request: Request, check_id: int, room: str, user=Depends(require_editor),
               db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    name = room_or_404(db, check, room)
    return templates.TemplateResponse(request, "inventory/room.html", room_ctx(db, check, room, name))


@router.post("/inventory/{check_id}/rooms/{room}")
async def room_save(request: Request, check_id: int, room: str, user=Depends(require_editor),
                    db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    name = room_or_404(db, check, room)
    url = f"/inventory/{check_id}/rooms/{room}"
    if check["closed_at"] is not None:
        return fail(request, "รอบตรวจสอบพัสดุนี้ปิดแล้ว แก้ไขไม่ได้", url, 409)
    form = await request.form(max_fields=MAX_FORM_FIELDS)
    entries = {}
    for key, value in form.multi_items():
        if key.startswith("r-") and key[2:].isdigit():
            entries.setdefault(int(key[2:]), [None, None])[0] = value if value in inv.RESULTS else None
        elif key.startswith("n-") and key[2:].isdigit():
            entries.setdefault(int(key[2:]), [None, None])[1] = str(value)[:500]
    n = inv.save_room(db, user["id"], check, room, {k: tuple(v) for k, v in entries.items()})
    left = len(inv.room_items(db, check_id, room, only_unchecked=True))
    msg = f"บันทึกผล {n} ชิ้นแล้ว" if n else "ไม่มีผลที่เปลี่ยน ไม่ได้บันทึก"
    if left:
        msg += f" · ห้องนี้ยังไม่ได้ตรวจอีก {left} ชิ้น"
    return flash_redirect(request, url, msg)


def sheet(db, check, room, name):
    """ใบรายการของห้องหนึ่ง (ใช้ทั้งพิมพ์ทีละห้องและทีละอาคาร)"""
    return {"room": room, "room_name": name, "rows": inv.room_items(db, check["id"], room),
            "no_room": room == inv.NO_ROOM, "holder_focus": inv.holder_focus(db, room)}


@router.get("/inventory/{check_id}/rooms/{room}/print")
def room_print(request: Request, check_id: int, room: str, user=Depends(require_editor),
               db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    name = room_or_404(db, check, room)
    return templates.TemplateResponse(request, "inventory/room_print.html", {
        "check": check, "sheets": [sheet(db, check, room, name)], "committee": inv.committee(check),
        "office": settings_store.org_names(db)["report"], "blank_rows": 5,
        "back_url": f"/inventory/{check_id}/rooms/{room}", "back_label": "หน้ากรอกผลห้องนี้",
        "title": f"ใบรายการตรวจสอบพัสดุ {name} ปี {check['fiscal_year']}"})


@router.get("/inventory/{check_id}/buildings/{building}/print")
def building_print(request: Request, check_id: int, building: str, user=Depends(require_editor),
                   db: sqlite3.Connection = Depends(inv_db)):
    """ใบรายการทีละอาคาร: ห้องในอาคารเดียวกันต่อกัน (ห้องละหน้า) เรียงตามชื่อห้อง"""
    check = get_check_or_404(db, check_id)
    group = next((b for b in inv.building_rooms(db, check_id) if b["key"] == building), None)
    if group is None:
        raise HTTPException(status_code=404, detail="ไม่พบอาคารนี้ในรอบตรวจสอบพัสดุ")
    return templates.TemplateResponse(request, "inventory/room_print.html", {
        "check": check, "sheets": [sheet(db, check, r["key"], r["name"]) for r in group["rooms"]],
        "building_name": group["name"], "committee": inv.committee(check),
        "office": settings_store.org_names(db)["report"], "blank_rows": 5,
        "back_url": f"/inventory/{check_id}", "back_label": "หน้าความคืบหน้า",
        "title": f"ใบรายการตรวจสอบพัสดุ {group['name']} ปี {check['fiscal_year']}"})


# ---------------------------------------------------------------- พบของที่ไม่มีในทะเบียน

def found_new_ctx(db, check, form=None, error=None):
    return {"check": check, "form": form or {"name": "", "location_id": "", "note": "", "result": inv.OK},
            "error": error, "locations": places.location_options(db), "RESULTS": (inv.OK, inv.BROKEN),
            "flag": inv.UNREGISTERED_FLAG}


@router.get("/inventory/{check_id}/found-new")
def found_new_form(request: Request, check_id: int, user=Depends(require_editor),
                   db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    if check["closed_at"] is not None:
        return fail(request, "รอบตรวจสอบพัสดุนี้ปิดแล้ว เพิ่มรายการไม่ได้", f"/inventory/{check_id}", 409)
    return templates.TemplateResponse(request, "inventory/found_new.html", found_new_ctx(db, check))


@router.post("/inventory/{check_id}/found-new")
def found_new(request: Request, check_id: int, name: str = Form(""), location_id: str = Form(""),
              note: str = Form(""), result: str = Form(inv.OK), user=Depends(require_editor),
              db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    if check["closed_at"] is not None:
        return fail(request, "รอบตรวจสอบพัสดุนี้ปิดแล้ว เพิ่มรายการไม่ได้", f"/inventory/{check_id}", 409)
    form = {"name": name, "location_id": location_id, "note": note, "result": result}
    loc = int(location_id) if location_id.isdigit() else None
    error = None
    if (location_id and loc is None) or (loc is not None and not db.execute(
            "SELECT 1 FROM locations WHERE id = ? AND active = 1", (loc,)).fetchone()):
        error = "ไม่พบห้องนี้ หรือถูกปิดใช้งานแล้ว"
    elif len(name) > 300 or len(note) > 1000:
        error = "ข้อความยาวเกินไป"
    if not error:
        try:
            asset_id, code = inv.add_unregistered(db, user["id"], check, name, loc, note, result)
        except inv.InventoryError as e:
            error = str(e)
        else:
            return flash_redirect(request, f"/inventory/{check_id}",
                                  f"เพิ่ม “{places.normalize(name)}” เป็น {code} แล้ว (ติดป้ายต้องตรวจสอบไว้ให้)")
    return templates.TemplateResponse(request, "inventory/found_new.html", found_new_ctx(db, check, form, error),
                                      status_code=400)


# ---------------------------------------------------------------- ปิดรอบ และข้อเสนอ

@router.get("/inventory/{check_id}/close")
def close_confirm(request: Request, check_id: int, user=Depends(require_editor),
                  db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    if check["closed_at"] is not None:
        return RedirectResponse(f"/inventory/{check_id}", status_code=303)
    return templates.TemplateResponse(request, "inventory/close.html", {
        "check": check, "rows": inv.unchecked_items(db, check_id), "c": inv.counts(db, check_id)})


@router.post("/inventory/{check_id}/close")
def close_round(request: Request, check_id: int, expected: str = Form(""), confirm_year: str = Form(""),
                user=Depends(require_editor), db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    if check["closed_at"] is not None:
        return fail(request, "รอบตรวจสอบพัสดุนี้ปิดแล้ว", f"/inventory/{check_id}", 409)
    if to_int(confirm_year) != check["fiscal_year"] or not expected.isdigit():
        return flash_redirect(request, f"/inventory/{check_id}/close",
                              f"พิมพ์ปีงบประมาณ {check['fiscal_year']} ให้ตรงเพื่อยืนยันการปิดรอบ ยังไม่ได้ปิดรอบ")
    # สำรอง db ก่อนปิดรอบทุกครั้ง (ปิดผิดแล้วเกิน 7 วัน ต้องกู้จากไฟล์นี้)
    saved = backup.backup(db, f"before_close_inventory_{check_id}")
    try:
        n = inv.close_check(db, user["id"], check, int(expected))
    except inv.InventoryError as e:
        return flash_redirect(request, f"/inventory/{check_id}/close", str(e))
    msg = f"ปิดรอบตรวจสอบพัสดุประจำปี {check['fiscal_year']} แล้ว (สำรองฐานข้อมูลไว้ที่ backups\\{saved.name})"
    if n:
        msg += f" บันทึก {n:,} ชิ้นที่ไม่ได้ตรวจเป็น “ไม่พบ”"
    return flash_redirect(request, f"/inventory/{check_id}#proposals", msg + " ตรวจข้อเสนอด้านล่างแล้วกดยืนยันทีละกลุ่ม")


@router.get("/inventory/{check_id}/reopen")
def reopen_form(request: Request, check_id: int, user=Depends(require_admin), db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    if check["closed_at"] is None:
        return RedirectResponse(f"/inventory/{check_id}", status_code=303)
    return templates.TemplateResponse(request, "inventory/reopen.html", {
        "check": check, "can_reopen": inv.can_reopen(check), "deadline": inv.reopen_deadline(check),
        "c": inv.counts(db, check_id), "applied": inv.applied_proposals(db, check_id), "error": None,
        "n_close": db.execute("SELECT COUNT(*) FROM inventory_check_items WHERE check_id = ? AND method = 'close'",
                              (check_id,)).fetchone()[0], "days": inv.REOPEN_DAYS})


@router.post("/inventory/{check_id}/reopen")
def reopen(request: Request, check_id: int, reason: str = Form(""), user=Depends(require_admin),
           db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    try:
        n = inv.reopen_check(db, user["id"], check, reason)
    except inv.InventoryError as e:
        return fail(request, str(e), f"/inventory/{check_id}", 409)
    except sqlite3.IntegrityError:  # trigger ใน db ปฏิเสธ (เช่น เวลาเครื่องเลย 7 วันพอดี)
        return fail(request, "ฐานข้อมูลไม่อนุญาตให้เปิดรอบนี้กลับ", f"/inventory/{check_id}", 409)
    return flash_redirect(request, f"/inventory/{check_id}",
                          f"เปิดรอบตรวจสอบพัสดุประจำปี {check['fiscal_year']} กลับแล้ว"
                          + (f" · {n:,} ชิ้นที่ถูกบันทึกเป็นไม่พบตอนปิดรอบ กลับเป็นยังไม่ตรวจ" if n else ""))


@router.post("/inventory/{check_id}/proposals/{group}")
async def confirm_proposal(request: Request, check_id: int, group: str, user=Depends(require_editor),
                           db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    if group not in inv.PROPOSALS:
        raise HTTPException(status_code=404, detail="ไม่พบกลุ่มข้อเสนอนี้")
    form = await request.form(max_fields=MAX_FORM_FIELDS)
    ids = [int(i) for i in form.getlist("ids") if str(i).isdigit()]
    try:
        n = inv.apply_proposal(db, user["id"], check, group, ids)
    except inv.InventoryError as e:
        return fail(request, str(e), f"/inventory/{check_id}", 409)
    p = inv.PROPOSALS[group]
    return flash_redirect(request, f"/inventory/{check_id}#proposals", f"{p['action']} {n} ชิ้นแล้ว")


# ---------------------------------------------------------------- รายงาน

@router.get("/inventory/{check_id}/report")
def report(request: Request, check_id: int, user=Depends(require_editor), db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    return templates.TemplateResponse(request, "inventory/report.html", {
        **inv.report(db, check), "RESULT_CLASS": inv.RESULT_CLASS})


@router.get("/inventory/{check_id}/report.xlsx")
def report_xlsx(check_id: int, user=Depends(require_editor), db: sqlite3.Connection = Depends(inv_db)):
    check = get_check_or_404(db, check_id)
    data = inventory_xlsx.build(inv.report(db, check))
    name = f"รายงานผลการตรวจสอบพัสดุ ปีงบประมาณ {check['fiscal_year']}.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=\"inventory-{check['fiscal_year']}.xlsx\"; "
                                                    f"filename*=UTF-8''{quote(name)}"})
