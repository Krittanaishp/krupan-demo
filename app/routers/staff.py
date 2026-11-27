"""ผู้รับผิดชอบครุภัณฑ์: รายการ/เพิ่ม/แก้/ปิดใช้งาน (ลบได้เฉพาะคนที่ไม่มีครุภัณฑ์และประวัติการย้ายอ้างถึง)"""
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import audit, places
from app.auth import can_edit, require_editor
from app.db import get_db
from app.templating import staff_label, templates

router = APIRouter()

FIELDS = ("nickname", "full_name", "position")
MAX_LEN = 200
COUNTS = """(SELECT COUNT(*) FROM assets a WHERE a.holder_id = s.id) AS n,
            (SELECT COUNT(*) FROM asset_moves m WHERE m.from_holder_id = s.id OR m.to_holder_id = s.id) AS n_moves"""


def staff_or_404(db, staff_id):
    row = db.execute(f"SELECT s.*, {COUNTS} FROM staff s WHERE s.id = ?", (staff_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="ไม่พบผู้รับผิดชอบนี้")
    return row


def clean(nickname, full_name, position):
    values = {"nickname": places.normalize(nickname) or None, "full_name": places.normalize(full_name) or None,
              "position": places.normalize(position) or None}
    if not values["nickname"] and not values["full_name"]:
        return values, "ใส่ชื่อเล่นหรือชื่อ-นามสกุลอย่างน้อยหนึ่งอย่าง"
    if any(v and len(v) > MAX_LEN for v in values.values()):
        return values, "ข้อความยาวเกินไป"
    return values, None


@router.get("/staff")
def old_staff_url(request: Request):
    """URL เดิม (ก่อนรวมเมนูห้องและผู้รับผิดชอบ) ยังใช้ได้"""
    return RedirectResponse("/places/staff" + (f"?{request.url.query}" if request.url.query else ""), status_code=301)


@router.get("/places/staff")
def staff_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(f"SELECT s.*, {COUNTS} FROM staff s "
                      "ORDER BY s.active DESC, COALESCE(s.nickname, s.full_name)").fetchall()
    return templates.TemplateResponse(request, "staff/list.html", {"rows": rows, "can_edit": can_edit(request),
                                                                "place_tab": "staff"})


@router.post("/staff")
def add_staff(request: Request, nickname: str = Form(""), full_name: str = Form(""), position: str = Form(""),
              user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    values, error = clean(nickname, full_name, position)
    if not error:
        with db:
            places.create_staff(db, user["id"], **values)
    request.session["flash"] = error or f"เพิ่มผู้รับผิดชอบ “{staff_label(values)}” แล้ว"
    return RedirectResponse("/places/staff", status_code=303)


@router.post("/staff/{staff_id}")
def edit_staff(request: Request, staff_id: int, nickname: str = Form(""), full_name: str = Form(""),
               position: str = Form(""), user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    old = staff_or_404(db, staff_id)
    values, error = clean(nickname, full_name, position)
    changes = {} if error else audit.diff(old, values, FIELDS)
    if changes:
        with db:
            db.execute("UPDATE staff SET nickname = ?, full_name = ?, position = ? WHERE id = ?",
                       (values["nickname"], values["full_name"], values["position"], staff_id))
            audit.log(db, user["id"], "staff", staff_id, "update", changes)
    request.session["flash"] = error or (f"บันทึก “{staff_label(values)}” แล้ว" if changes else "ไม่มีข้อมูลที่เปลี่ยนแปลง")
    return RedirectResponse("/places/staff", status_code=303)


@router.post("/staff/{staff_id}/active")
def set_staff_active(request: Request, staff_id: int, active: int = Form(...), user=Depends(require_editor),
                     db: sqlite3.Connection = Depends(get_db)):
    old = staff_or_404(db, staff_id)
    active = 1 if active else 0
    if active != old["active"]:
        with db:
            db.execute("UPDATE staff SET active = ? WHERE id = ?", (active, staff_id))
            audit.log(db, user["id"], "staff", staff_id, "update", {"active": {"old": old["active"], "new": active}})
    request.session["flash"] = f"{'เปิด' if active else 'ปิด'}ใช้งาน “{staff_label(old)}” แล้ว"
    return RedirectResponse("/places/staff", status_code=303)


@router.post("/staff/{staff_id}/delete")
def delete_staff(request: Request, staff_id: int, user=Depends(require_editor),
                 db: sqlite3.Connection = Depends(get_db)):
    old = staff_or_404(db, staff_id)
    if old["n"] or old["n_moves"]:
        request.session["flash"] = (f"ลบ “{staff_label(old)}” ไม่ได้ เพราะมีครุภัณฑ์ {old['n']} ชิ้นหรือประวัติการย้ายอ้างถึงอยู่"
                                    " ใช้ “ปิดใช้งาน” แทน")
        return RedirectResponse("/places/staff", status_code=303)
    with db:
        db.execute("DELETE FROM staff WHERE id = ?", (staff_id,))
        audit.log(db, user["id"], "staff", staff_id, "delete",
                  {k: {"old": old[k], "new": None} for k in FIELDS if old[k]})
    request.session["flash"] = f"ลบ “{staff_label(old)}” แล้ว"
    return RedirectResponse("/places/staff", status_code=303)
