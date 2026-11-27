"""หน้าตั้งค่า (admin): ชื่อหน่วยงาน, ที่อยู่ที่ใช้ใน QR และการสำรองข้อมูล"""
import re
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse

from app import audit, backup, network, room_groups, settings_store, usb
from app.auth import require_admin
from app.db import get_db
from app.templating import templates

router = APIRouter()


ORG_FIELDS = [
    (settings_store.ORG_DISPLAY_KEY, "ชื่อหน่วยงาน (หัวเว็บ)", "แสดงที่หัวเว็บมุมบนซ้าย บนสติกเกอร์ QR และหน้าที่เปิดจากการสแกน QR"),
    (settings_store.ORG_REPORT_KEY, "ชื่อหน่วยงานในรายงาน",
     "หัวรายงานทะเบียนครุภัณฑ์ รายงานผลการตรวจสอบพัสดุ และเอกสารที่พิมพ์ออก (รายงานทะเบียนต่อท้ายด้วย “(งานการเงินและบัญชี)” ให้เอง)"),
]


def general_page(request, db, values, errors, status_code=200):
    return templates.TemplateResponse(request, "settings/general.html", {
        "fields": ORG_FIELDS, "values": values, "errors": errors,
        "preview_line": f"ของ{values[settings_store.ORG_REPORT_KEY]} (งานการเงินและบัญชี)"}, status_code=status_code)


@router.get("/settings")
def settings_home(request: Request, user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    org = settings_store.org_names(db)
    return general_page(request, db, {settings_store.ORG_DISPLAY_KEY: org["display"],
                                      settings_store.ORG_REPORT_KEY: org["report"]}, {})


@router.post("/settings/org")
def save_org(request: Request, display: str = Form(""), report: str = Form(""),
             user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    values = {settings_store.ORG_DISPLAY_KEY: re.sub(r"\s+", " ", display).strip(),
              settings_store.ORG_REPORT_KEY: re.sub(r"\s+", " ", report).strip()}
    errors = {}
    for key, value in values.items():
        if not value:
            errors[key] = "กรอกชื่อหน่วยงาน"
        elif len(value) > settings_store.ORG_MAX_LEN:
            errors[key] = f"ยาวเกิน {settings_store.ORG_MAX_LEN} ตัวอักษร"
    if errors:
        return general_page(request, db, values, errors, 400)
    old = settings_store.org_names(db)
    old = {settings_store.ORG_DISPLAY_KEY: old["display"], settings_store.ORG_REPORT_KEY: old["report"]}
    changes = {k: {"old": old[k] or None, "new": v} for k, v in values.items() if old[k] != v}
    if changes:
        with db:
            for key, value in values.items():
                settings_store.put(db, key, value)
            audit.log(db, user["id"], "app_settings", 0, "update", changes)
        settings_store.clear_org_cache()
    request.session["flash"] = "บันทึกชื่อหน่วยงานแล้ว" if changes else "ไม่มีข้อมูลที่เปลี่ยนแปลง"
    return RedirectResponse("/settings", status_code=303)


@router.get("/settings/network")
def network_page(request: Request, user=Depends(require_admin)):
    url = network.base_url()
    ips = network.local_ipv4s()
    r = network.inspect(url, ips)
    port = request.url.port or 8000
    return templates.TemplateResponse(request, "settings/network.html", {
        "base_url": url, "ips": ips, "ok": r["ok"], "message": r["message"], "port": port,
        "host": r["host"], "is_name": r["is_name"], "resolved": r["resolved"], "note": r["note"],
        "suggested": f"http://{ips[0]}:{port}" if ips else "", "seen_as": str(request.base_url).rstrip("/")})


@router.get("/settings/backup")
def backup_page(request: Request, user=Depends(require_admin)):
    st = backup.read_status()
    t = backup.parse_time
    return templates.TemplateResponse(request, "settings/backup.html", {
        "files": backup.list_backups(), "status": st, "fmt": backup.fmt,
        "last_success": t(st.get("last_success")), "last_failure": t(st.get("last_failure")),
        "last_copy": t(st.get("last_copy")), "last_download": t(st.get("last_download")),
        "last_copy_error_at": t(st.get("last_copy_error_at")),
        "copy_dir": backup.copy_dir_setting(), "backup_dir": backup.backup_dir(),
        "warnings_enabled": backup.warnings_enabled(), "same_disk": backup.same_disk_warning(),
        "gdrive_at": t(st.get("gdrive_confirmed_at")), "usb_label": usb.label_setting(),
        "usb_last": t(st.get("usb_last_copy")), "usb_error_at": t(st.get("usb_last_error_at")),
        "offsite_days": backup.OFFSITE_WARN_DAYS})


@router.post("/settings/backup/gdrive")
def set_gdrive(request: Request, confirmed: str = Form(""), user=Depends(require_admin),
               db: sqlite3.Connection = Depends(get_db)):
    """admin ยืนยันว่าใช้ Google Drive สำรองโฟลเดอร์ BACKUP_COPY_DIR แล้ว (ระบบตรวจเองไม่ได้)"""
    on = confirmed == "1"
    was = bool(backup.read_status().get("gdrive_confirmed_at"))
    if on != was:
        backup.set_gdrive(on, user["username"])
        with db:
            audit.log(db, user["id"], "app_settings", 0, "update", {"gdrive_confirmed": {"old": was, "new": on}})
    request.session["flash"] = ("บันทึกแล้ว: ใช้ Google Drive สำรองโฟลเดอร์นี้" if on
                                else "บันทึกแล้ว: ไม่ได้ใช้ Google Drive")
    return RedirectResponse("/settings/backup", status_code=303)


@router.post("/settings/backup/run")
def backup_now(request: Request, user=Depends(require_admin)):
    try:
        r = backup.make_backup(f"manual:{user['username']}")
    except backup.BackupError as e:
        request.session["flash"] = f"สำรองข้อมูลไม่สำเร็จ: {e}"
    else:
        msg = f"สำรองข้อมูลแล้ว: {r['file'].name} ({r['check']})"
        if r["copied"]:
            msg += " · คัดลอกไปที่ BACKUP_COPY_DIR แล้ว"
        elif r["copy_error"]:
            msg += f" · คัดลอกไปที่ BACKUP_COPY_DIR ไม่ได้: {r['copy_error']}"
        request.session["flash"] = msg
    return RedirectResponse("/settings/backup", status_code=303)


@router.get("/settings/backup/latest")
def download_latest(request: Request, user=Depends(require_admin)):
    path = backup.latest_daily()
    if path is None:
        request.session["flash"] = "ยังไม่มีไฟล์สำรอง กด “สำรองตอนนี้” ก่อน"
        return RedirectResponse("/settings/backup", status_code=303)
    backup.mark_downloaded(path.name)
    return FileResponse(path, media_type="application/octet-stream", filename=path.name)


# ---------------------------------------------------------------- จัดห้อง: ตัวย่อ คำตรวจ และ "ข้อความเดิม → ห้อง"

RULE_MAX = 200


def rooms_page(request, db, values=None, errors=None, status_code=200):
    values = values or {
        "abbr": room_groups.abbr_to_text(room_groups.abbreviations(db)),
        "roles": "\n".join(room_groups.role_words(db)),
        "places": "\n".join(room_groups.place_words(db)),
    }
    return templates.TemplateResponse(request, "settings/rooms.html", {
        "values": values, "errors": errors or [], "rules": room_groups.rules(db)}, status_code=status_code)


@router.get("/settings/rooms")
def rooms_settings(request: Request, user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    return rooms_page(request, db)


@router.post("/settings/rooms/lists")
def save_room_lists(request: Request, abbr: str = Form(""), roles: str = Form(""), places_text: str = Form(""),
                    user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    items, errors = room_groups.parse_abbr_text(abbr)
    new = {room_groups.ABBR_KEY: items, room_groups.ROLE_KEY: room_groups.parse_words_text(roles),
           room_groups.PLACE_KEY: room_groups.parse_words_text(places_text)}
    if errors:
        return rooms_page(request, db, {"abbr": abbr, "roles": roles, "places": places_text}, errors, 400)
    old = {room_groups.ABBR_KEY: room_groups.abbreviations(db), room_groups.ROLE_KEY: room_groups.role_words(db),
           room_groups.PLACE_KEY: room_groups.place_words(db)}
    changes = {k: {"old": old[k], "new": v} for k, v in new.items() if old[k] != v}
    if changes:
        with db:
            for key in changes:
                settings_store.put(db, key, new[key])
            audit.log(db, user["id"], "app_settings", 0, "update", changes)
    request.session["flash"] = "บันทึกตารางตัวย่อและคำที่ใช้ตรวจแล้ว" if changes else "ไม่มีข้อมูลที่เปลี่ยนแปลง"
    return RedirectResponse("/settings/rooms", status_code=303)


def clean_rule(match_text, exact, room_name, holder_hint, note):
    values = {"match_text": (match_text or "").strip(), "exact": 1 if exact else 0,
              "room_name": re.sub(r"\s+", " ", room_name or "").strip(),
              "holder_hint": re.sub(r"\s+", " ", holder_hint or "").strip() or None,
              "note": re.sub(r"\s+", " ", note or "").strip() or None}
    if not values["match_text"] or not values["room_name"]:
        return values, "ต้องมีทั้งข้อความเดิมและห้องที่ถูกต้อง"
    if any(len(v or "") > RULE_MAX for k, v in values.items() if k != "exact"):
        return values, f"ข้อความยาวเกิน {RULE_MAX} ตัวอักษร"
    return values, None


@router.post("/settings/rooms/rules")
def add_rule(request: Request, match_text: str = Form(""), exact: str = Form(""), room_name: str = Form(""),
             holder_hint: str = Form(""), note: str = Form(""),
             user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    values, error = clean_rule(match_text, exact, room_name, holder_hint, note)
    if not error and db.execute("SELECT 1 FROM location_text_rules WHERE match_text = ? AND exact = ?",
                                (values["match_text"], values["exact"])).fetchone():
        error = f"มีข้อความ “{values['match_text']}” แบบนี้อยู่แล้ว แก้ในแถวเดิมแทน"
    if not error:
        with db:
            rid = db.execute("""INSERT INTO location_text_rules (match_text, exact, room_name, holder_hint, note)
                                VALUES (:match_text, :exact, :room_name, :holder_hint, :note)""", values).lastrowid
            audit.log(db, user["id"], "location_text_rules", rid, "insert",
                      {k: {"old": None, "new": v} for k, v in values.items() if v not in (None, "")})
    request.session["flash"] = error or f"เพิ่ม “{values['match_text']}” → “{values['room_name']}” แล้ว"
    return RedirectResponse("/settings/rooms#rules", status_code=303)


@router.post("/settings/rooms/rules/{rule_id}")
def edit_rule(request: Request, rule_id: int, match_text: str = Form(""), exact: str = Form(""),
              room_name: str = Form(""), holder_hint: str = Form(""), note: str = Form(""), active: str = Form(""),
              user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    old = db.execute("SELECT * FROM location_text_rules WHERE id = ?", (rule_id,)).fetchone()
    if old is None:
        request.session["flash"] = "ไม่พบแถวนี้"
        return RedirectResponse("/settings/rooms#rules", status_code=303)
    values, error = clean_rule(match_text, exact, room_name, holder_hint, note)
    values["active"] = 1 if active else 0
    if not error and db.execute("SELECT 1 FROM location_text_rules WHERE match_text = ? AND exact = ? AND id != ?",
                                (values["match_text"], values["exact"], rule_id)).fetchone():
        error = f"มีข้อความ “{values['match_text']}” แบบนี้อยู่แล้ว"
    changes = {} if error else audit.diff(old, values, tuple(values))
    if changes:
        with db:
            db.execute("""UPDATE location_text_rules SET match_text = :match_text, exact = :exact, room_name = :room_name,
                          holder_hint = :holder_hint, note = :note, active = :active WHERE id = :id""",
                       {**values, "id": rule_id})
            audit.log(db, user["id"], "location_text_rules", rule_id, "update", changes)
    request.session["flash"] = error or ("บันทึกแล้ว" if changes else "ไม่มีข้อมูลที่เปลี่ยนแปลง")
    return RedirectResponse(f"/settings/rooms#rule-{rule_id}", status_code=303)


# ---------------------------------------------------------------- วัสดุ: ผู้อนุมัติเริ่มต้น และฝ่ายที่เบิกได้ (0.9.0)

def supplies_page(request, db, error=None, status_code=200):
    from app import supplies as sp
    deps = db.execute("SELECT id, name, active, issues_supplies FROM departments ORDER BY sort_order, name").fetchall()
    return templates.TemplateResponse(request, "settings/supplies.html", {
        "deps": deps, "staff": sp.approvers(db), "approver": sp.default_approver(db), "error": error},
        status_code=status_code)


@router.get("/settings/supplies")
def supplies_settings(request: Request, user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    return supplies_page(request, db)


@router.post("/settings/supplies")
async def save_supplies_settings(request: Request, user=Depends(require_admin), db: sqlite3.Connection = Depends(get_db)):
    from app import supplies as sp
    data = await request.form()
    raw = str(data.get("approver", "")).strip()
    approver = int(raw) if raw.isdigit() else None
    if approver is not None and not db.execute("SELECT 1 FROM staff WHERE id = ? AND active = 1", (approver,)).fetchone():
        return supplies_page(request, db, "ไม่พบเจ้าหน้าที่ที่เลือก หรือถูกปิดใช้งานแล้ว", status_code=400)
    picked = {int(v) for v in data.getlist("issues") if str(v).isdigit()}
    old_approver = sp.default_approver(db)
    with db:
        if approver != old_approver:
            settings_store.put(db, sp.DEFAULT_APPROVER_KEY, approver)
            audit.log(db, user["id"], "app_settings", 0, "update",
                      {sp.DEFAULT_APPROVER_KEY: {"old": old_approver, "new": approver}})
        for d in db.execute("SELECT id, issues_supplies FROM departments").fetchall():
            new = 1 if d["id"] in picked else 0
            if new != d["issues_supplies"]:
                db.execute("UPDATE departments SET issues_supplies = ? WHERE id = ?", (new, d["id"]))
                audit.log(db, user["id"], "departments", d["id"], "update", {"issues_supplies": {"old": d["issues_supplies"], "new": new}})
    request.session["flash"] = "บันทึกการตั้งค่าวัสดุแล้ว"
    return RedirectResponse("/settings/supplies", status_code=303)
