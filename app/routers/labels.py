"""พิมพ์สติกเกอร์ QR (editor ขึ้นไป): เลือกครุภัณฑ์ ตั้งค่าแผ่นสติกเกอร์ (preset) และหน้าสำหรับสั่งพิมพ์"""
import sqlite3
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from app import audit, labels, network, qr, settings_store
from app.auth import require_editor
from app.config import qr_enabled
from app.db import get_db
from app.routers.assets import (ASSET_COLUMNS, ASSET_FROM, ASSET_ORDER, NO_LOCATION, filter_options,
                                parse_filters, query_assets)
from app.templating import templates

def qr_on():
    """หน้า /labels* ใช้ได้เฉพาะเมื่อเปิด QR_ENABLED (ปิดอยู่ = 404 ไม่ลบโค้ด)"""
    if not qr_enabled():
        raise HTTPException(status_code=404)


router = APIRouter(dependencies=[Depends(qr_on)])

MODES = {"room": "ตามห้อง", "filter": "ตามตัวกรอง (เหมือนหน้าทะเบียน)", "pick": "เลือกเอง"}
FILTER_KEYS = ("q", "category", "status", "fy", "review", "loc", "loc_text", "unprinted", "building", "dept")
MAX_PICK = 2000


def presets(db):
    return db.execute("SELECT * FROM label_presets ORDER BY builtin DESC, name").fetchall()


def preset_or_default(db, preset_id):
    row = None
    if str(preset_id or "").isdigit():
        row = db.execute("SELECT * FROM label_presets WHERE id = ?", (int(preset_id),)).fetchone()
    return row or db.execute("SELECT * FROM label_presets ORDER BY builtin DESC, id LIMIT 1").fetchone()


def selection(db, params, ids):
    """ครุภัณฑ์ที่จะพิมพ์ตามวิธีเลือก คืน (mode, rows, ข้อความอธิบาย, error)"""
    mode = params.get("mode") if params.get("mode") in MODES else "room"
    if mode == "room":
        room = params.get("room", "")
        if not (room == NO_LOCATION or room.isdigit()):
            return mode, [], "", "เลือกห้อง"
        f = parse_filters(loc=room)
        name = "ยังไม่จัดเข้าห้อง" if room == NO_LOCATION else (
            (db.execute("SELECT name FROM locations WHERE id = ?", (int(room),)).fetchone() or ["?"])[0])
        return mode, query_assets(db, f), f"ห้อง {name}", None
    if mode == "filter":
        f = parse_filters(**{k: params.get(k, "") for k in FILTER_KEYS})
        return mode, query_assets(db, f), "ตามตัวกรอง", None
    picked = sorted({int(i) for i in ids if str(i).isdigit()})[:MAX_PICK]
    if not picked:
        return mode, [], "", "ยังไม่ได้ติ๊กเลือกครุภัณฑ์"
    rows = db.execute(f"SELECT {ASSET_COLUMNS} {ASSET_FROM} WHERE a.id IN ({','.join('?' * len(picked))}) "
                      f"{ASSET_ORDER}", picked).fetchall()
    return mode, rows, "เลือกเอง", None


def read_params(request):
    p = request.query_params
    return {k: p.get(k, "") for k in ("mode", "room", "preset", "start", *FILTER_KEYS, *labels.FIELDS)}, \
        p.getlist("ids")


def start_of(params, layout):
    raw = str(params.get("start") or "1").strip()
    start = int(raw) if raw.isdigit() else 0
    if not 1 <= start <= layout.per_page:
        return 1, f"เริ่มพิมพ์ที่ดวงต้องอยู่ระหว่าง 1-{layout.per_page}"
    return start, None


def summary_ctx(request, db, params, ids):
    preset = preset_or_default(db, params.get("preset"))
    layout, layout_errors = labels.parse(params, labels.from_row(preset))
    start, start_error = (1, None) if layout_errors else start_of(params, layout)
    mode, rows, desc, sel_error = selection(db, params, ids)
    n = len(rows)
    n_pages = len(labels.paginate(range(n), layout, start)) if n and not layout_errors else 0
    url = network.base_url()
    ok, net_message = network.check(url, network.local_ipv4s())
    return {"mode": mode, "n": n, "desc": desc, "n_pages": n_pages, "layout": layout, "start": start,
            "layout_errors": layout_errors, "start_error": start_error, "sel_error": sel_error,
            "preset": preset, "base_url": url, "net_ok": ok, "net_message": net_message,
            "qr_example": qr.url_for(qr.code_for(rows[0]), request) if rows else ""}


@router.get("/labels")
def labels_page(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    params, ids = read_params(request)
    if not params["mode"]:
        params["mode"] = "filter" if any(params[k] for k in FILTER_KEYS) else "room"
    ctx = summary_ctx(request, db, params, ids)
    preset = ctx["preset"]
    form_layout = {f: params[f] if params[f] != "" else preset[f] for f in labels.FIELDS}
    return templates.TemplateResponse(request, "labels/setup.html", {
        **ctx, **filter_options(db), "params": params, "picked": {int(i) for i in ids if str(i).isdigit()},
        "f": parse_filters(**{k: params[k] for k in FILTER_KEYS}), "modes": MODES, "presets": presets(db),
        "form_layout": form_layout, "field_labels": labels.FIELD_LABELS,
        "all_assets": query_assets(db, parse_filters()),
    })


@router.get("/labels/summary")
def labels_summary(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    params, ids = read_params(request)
    return templates.TemplateResponse(request, "labels/_summary.html", summary_ctx(request, db, params, ids))


@router.get("/labels/layout-fields")
def layout_fields(request: Request, preset: str = "", user=Depends(require_editor),
                  db: sqlite3.Connection = Depends(get_db)):
    """เลือก preset แล้วเติมค่าในช่องตั้งค่าแผ่นสติกเกอร์ (htmx)"""
    row = preset_or_default(db, preset)
    return templates.TemplateResponse(request, "labels/_layout_fields.html", {
        "form_layout": {f: row[f] for f in labels.FIELDS}, "field_labels": labels.FIELD_LABELS,
        "layout_errors": {}, "oob": False})


@router.get("/labels/sheet")
def labels_sheet(request: Request, border: str = "", user=Depends(require_editor),
                 db: sqlite3.Connection = Depends(get_db)):
    params, ids = read_params(request)
    ctx = summary_ctx(request, db, params, ids)
    test = border == "1"
    errors = list(ctx["layout_errors"].values())
    if not test:
        errors += [e for e in (ctx["start_error"], ctx["sel_error"]) if e]
        if not errors and not ctx["n"]:
            errors.append("ไม่มีครุภัณฑ์ที่ตรงกับที่เลือก")
    if errors:
        return templates.TemplateResponse(request, "labels/error.html", {"errors": errors}, status_code=400)

    layout = ctx["layout"]
    d = labels.design(layout)
    if test:
        pages, rows = labels.border_page(layout), []
    else:
        _, rows, _, _ = selection(db, params, ids)
        items = []
        for r in rows:
            code = qr.code_for(r)
            text = r["reg_no"] or r["temp_code"]
            items.append({"id": r["id"], "text": text, "lines": labels.reg_lines(text), "reg_pt": labels.reg_pt(text, d),
                          "name": r["name"],
                          "room": r["location_name"] or r["location_text"] or "",
                          "svg": qr.svg(qr.url_for(code, request))})
        pages = labels.paginate(items, layout, ctx["start"])
    back = "/labels?" + urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "border"])
    return templates.TemplateResponse(request, "labels/sheet.html", {
        **ctx, "pages": pages, "d": d, "test": test, "ids": [r["id"] for r in rows], "office": settings_store.org_names(db)["display"],
        "back": back})


@router.post("/labels/mark")
async def mark_printed(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """ผู้ใช้ยืนยันว่าพิมพ์สติกเกอร์เรียบร้อยแล้ว: บันทึก label_printed_at และ audit_log ทีละชิ้น"""
    form = await request.form()
    ids = sorted({int(i) for i in form.getlist("ids") if str(i).isdigit()})
    rows = db.execute(f"SELECT id, label_printed_at FROM assets WHERE id IN ({','.join('?' * len(ids))})",
                      ids).fetchall() if ids else []
    with db:
        now = db.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
        for r in rows:
            db.execute("UPDATE assets SET label_printed_at = ? WHERE id = ?", (now, r["id"]))
            audit.log(db, user["id"], "assets", r["id"], "label",
                      {"label_printed_at": {"old": r["label_printed_at"], "new": now}})
    request.session["flash"] = f"บันทึกแล้วว่าพิมพ์สติกเกอร์ {len(rows)} ชิ้น"
    back = str(form.get("back") or "/labels")
    return RedirectResponse(back if back.startswith("/labels") else "/labels", status_code=303)


# ---------------------------------------------------------------- preset

@router.post("/labels/presets")
async def save_preset(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """บันทึกค่าในฟอร์มเป็น preset (ชื่อซ้ำกับของที่บันทึกไว้ = แก้ทับ, preset เริ่มต้นของระบบแก้ไม่ได้)"""
    form = await request.form()
    name = " ".join(str(form.get("preset_name", "")).split())
    base = labels.from_row(preset_or_default(db, form.get("preset")))
    layout, errors = labels.parse(form, base)
    error = None
    if not name:
        error = "ตั้งชื่อ preset ก่อนบันทึก"
    elif len(name) > 100:
        error = "ชื่อ preset ยาวเกินไป"
    elif errors:
        error = "บันทึกไม่ได้: " + " / ".join(errors.values())
    existing = db.execute("SELECT * FROM label_presets WHERE name = ?", (name,)).fetchone() if name else None
    if not error and existing and existing["builtin"]:
        error = "preset เริ่มต้นของระบบแก้ไม่ได้ ตั้งชื่อใหม่"
    if error:
        return templates.TemplateResponse(request, "labels/_preset_msg.html", {"error": error})

    values = layout.as_dict()
    with db:
        if existing:
            changes = audit.diff(existing, values, labels.FIELDS)
            db.execute(f"UPDATE label_presets SET {', '.join(f'{f} = ?' for f in labels.FIELDS)} WHERE id = ?",
                       [values[f] for f in labels.FIELDS] + [existing["id"]])
            preset_id = existing["id"]
            if changes:
                audit.log(db, user["id"], "label_presets", preset_id, "update", {"name": name, **changes})
        else:
            preset_id = db.execute(
                f"INSERT INTO label_presets (name, {', '.join(labels.FIELDS)}) VALUES (?{', ?' * len(labels.FIELDS)})",
                [name] + [values[f] for f in labels.FIELDS]).lastrowid
            audit.log(db, user["id"], "label_presets", preset_id, "insert", {"name": {"old": None, "new": name}})
    request.session["flash"] = f"บันทึก preset “{name}” แล้ว"
    keep = [(k, v) for k, v in form.multi_items()
            if k in ("mode", "room", "start", "ids", *FILTER_KEYS) and v]
    return Response(status_code=204, headers={"HX-Redirect": "/labels?" + urlencode(keep + [("preset", preset_id)])})


@router.post("/labels/presets/{preset_id}/delete")
def delete_preset(request: Request, preset_id: int, user=Depends(require_editor),
                  db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM label_presets WHERE id = ?", (preset_id,)).fetchone()
    if row is None or row["builtin"]:
        request.session["flash"] = "ลบ preset นี้ไม่ได้"
    else:
        with db:
            db.execute("DELETE FROM label_presets WHERE id = ?", (preset_id,))
            audit.log(db, user["id"], "label_presets", preset_id, "delete", {"name": {"old": row["name"], "new": None}})
        request.session["flash"] = f"ลบ preset “{row['name']}” แล้ว"
    return RedirectResponse("/labels", status_code=303)
