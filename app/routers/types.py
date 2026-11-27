"""หน้าจัดการประเภทครุภัณฑ์: แก้ชื่อประเภท (แก้แล้ว name_is_draft = 0)"""
import re
import sqlite3

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import audit
from app.auth import get_user, require_editor
from app.db import get_db
from app.templating import templates

router = APIRouter()

TYPE_QUERY = """SELECT t.code, t.name, t.name_is_draft, t.category_code, c.name AS category_name,
                       (SELECT COUNT(*) FROM assets a WHERE a.type_code = t.code) AS n
                FROM asset_types t JOIN categories c ON c.code = t.category_code"""


def type_ctx(request, row, error=None, saved=False):
    return {"t": row, "error": error, "saved": saved,
            "can_edit": bool((get_user(request) or {}).get("can_edit"))}


@router.get("/types")
def types_page(request: Request, draft: str = "", db: sqlite3.Connection = Depends(get_db)):
    only_draft = draft == "1"
    rows = db.execute(TYPE_QUERY + (" WHERE t.name_is_draft = 1" if only_draft else "")
                      + " ORDER BY t.code").fetchall()
    n_draft = db.execute("SELECT COUNT(*) FROM asset_types WHERE name_is_draft = 1").fetchone()[0]
    n_all = db.execute("SELECT COUNT(*) FROM asset_types").fetchone()[0]
    return templates.TemplateResponse(request, "types/list.html", {
        "rows": rows, "only_draft": only_draft, "n_draft": n_draft, "n_all": n_all,
        "can_edit": bool((get_user(request) or {}).get("can_edit"))})


@router.post("/types/{code}")
def rename_type(request: Request, code: str, name: str = Form(""),
                user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    old = db.execute(TYPE_QUERY + " WHERE t.code = ?", (code,)).fetchone()
    if old is None:
        raise HTTPException(status_code=404, detail="ไม่พบประเภทนี้")
    is_htmx = bool(request.headers.get("HX-Request"))

    name = re.sub(r"\s+", " ", name).strip()
    error = "ต้องมีชื่อประเภท" if not name else ("ชื่อยาวเกิน 200 ตัวอักษร" if len(name) > 200 else None)
    if error:
        if is_htmx:  # ส่งแถวเดิมกลับพร้อมข้อความผิดพลาด (status 200 เพื่อให้ htmx แสดงผล)
            return templates.TemplateResponse(request, "types/_row.html", type_ctx(request, old, error))
        request.session["flash"] = f"ประเภท {code}: {error}"
        return RedirectResponse("/types", status_code=303)

    new = {"name": name, "name_is_draft": 0}  # คนยืนยันชื่อแล้ว ไม่ใช่ชื่อร่างอีกต่อไป
    changes = audit.diff(old, new, ("name", "name_is_draft"))
    if changes:
        with db:
            db.execute("UPDATE asset_types SET name = ?, name_is_draft = 0 WHERE code = ?", (name, code))
            # record_id เป็นตัวเลข จึงเก็บรหัสเต็ม (เช่น '0301' ที่มี 0 นำหน้า) ไว้ใน changes ด้วย
            audit.log(db, user["id"], "asset_types", int(code) if code.isdigit() else 0, "update", {"code": code, **changes})

    if is_htmx:
        row = db.execute(TYPE_QUERY + " WHERE t.code = ?", (code,)).fetchone()
        return templates.TemplateResponse(request, "types/_row.html", type_ctx(request, row, saved=True))
    request.session["flash"] = f"บันทึกชื่อประเภท {code} แล้ว" if changes else "ไม่มีข้อมูลที่เปลี่ยนแปลง"
    return RedirectResponse("/types", status_code=303)
