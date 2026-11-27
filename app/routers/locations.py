"""ห้อง: รายการ/เพิ่ม/แก้/ปิดใช้งาน/รวมห้อง และหน้าจัดข้อความ "ใช้ประจำที่" เดิมเข้าห้องจริง (/places/mapping)"""
import io
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from urllib.parse import parse_qsl, quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from app import audit, merges, places, room_groups, room_groups_xlsx, settings_store
from app.auth import can_edit, is_admin, require_admin, require_editor
from app.db import get_db
from app.filter_history import is_filter_request, set_filter_url
from app.routers.assets import NO_CATEGORY, normalize_query
from app.templating import templates

router = APIRouter()

NEW = "__new__"  # ค่าใน dropdown สำหรับ "สร้างใหม่"
MAX_NAME = 200
SAMPLES = 3  # ชื่อครุภัณฑ์ตัวอย่างต่อข้อความในส่วนกลุ่มที่เสนอ

# ตัวกรองหน้าจัดห้อง (ค่าใน URL, ข้อความที่แสดง)
MAP_STATUSES = [("", "ทั้งหมด"), ("todo", "ยังไม่จัด"), ("done", "จัดแล้ว"), ("multi", "ต้องแยกรายชิ้น"),
                ("person", "มีชื่อคนปน")]
ROOM_STATUSES = [("", "ทั้งหมด"), ("active", "ใช้งาน"), ("inactive", "ปิดใช้งาน"), ("merged", "ถูกรวมแล้ว")]
NO_AREA = "none"  # ห้องที่ยังไม่ระบุอาคาร/ฝ่าย
AREA_KINDS = {"buildings": ("อาคาร/พื้นที่", "building_id"), "departments": ("ฝ่ายที่ดูแล", "department_id")}


# ---------------------------------------------------------------- ตัวกรอง (ใช้ร่วมกันสองแท็บ)

def clean_filters(q="", status="", cat="", statuses=MAP_STATUSES, building=None, dept=None):
    status = status if status in {s for s, _ in statuses} else ""
    f = {"q": normalize_query(q), "status": status, "cat": (cat or "").strip()[:20]}
    if building is not None:  # เฉพาะแท็บห้อง
        area = lambda v: v if v == NO_AREA or (v or "").isdigit() else ""  # noqa: E731
        f.update(building=area(building), dept=area(dept))
    return f


def filter_query(f):
    return {k: v for k, v in f.items() if v}


def page_url(path, f):
    qs = urlencode(filter_query(f))
    return path + ("?" + qs if qs else "")


def back_filters(back, statuses=MAP_STATUSES):
    """ค่าตัวกรองที่ฟอร์มส่งกลับมา (hidden field) ใช้เฉพาะคีย์ที่รู้จัก"""
    d = dict(parse_qsl(back or ""))
    return clean_filters(d.get("q", ""), d.get("status", ""), d.get("cat", ""), statuses)


def text_matches(text, q):
    return not q or q.lower() in normalize_query(text).lower()


def category_options(db, counts):
    """ตัวเลือกหมวดที่มีครุภัณฑ์อยู่จริง [{code, name, n}] counts = Counter(รหัสหมวด -> จำนวนชิ้น)"""
    names = {r["code"]: r["name"] for r in db.execute("SELECT code, name FROM categories")}
    out = [{"code": c, "name": names.get(c, c), "n": n} for c, n in counts.items() if c != NO_CATEGORY]
    out.sort(key=lambda o: o["code"])
    if counts.get(NO_CATEGORY):
        out.append({"code": NO_CATEGORY, "name": "ไม่มีรหัสประเภท", "n": counts[NO_CATEGORY]})
    return out


# ---------------------------------------------------------------- หน้าจัดห้องจากข้อความเดิม

def mapping_rows(db, only_text=None):
    """1 แถวต่อ location_text 1 ค่า เรียงจากจำนวนชิ้นมากไปน้อย"""
    where = "WHERE a.location_text IS NOT NULL AND a.location_text != ''"
    params = []
    if only_text is not None:
        where += " AND a.location_text = ?"
        params.append(only_text)
    by_text = defaultdict(list)
    for r in db.execute(f"""SELECT a.location_text, a.location_id, a.holder_id, COUNT(*) AS n FROM assets a {where}
                            GROUP BY a.location_text, a.location_id, a.holder_id""", params):
        by_text[r["location_text"]].append(r)
    cats = defaultdict(Counter)
    for r in db.execute(f"""SELECT a.location_text, t.category_code, COUNT(*) AS n FROM assets a
                            LEFT JOIN asset_types t ON t.code = a.type_code {where}
                            GROUP BY a.location_text, t.category_code""", params):
        cats[r["location_text"]][r["category_code"] or NO_CATEGORY] += r["n"]
    samples = defaultdict(list)
    for r in db.execute(f"""SELECT a.location_text, a.name FROM assets a {where}
                            ORDER BY a.location_text, a.reg_no IS NULL, a.reg_no, a.temp_code, a.id""", params):
        if len(samples[r["location_text"]]) < SAMPLES:
            samples[r["location_text"]].append(room_groups.shorten(r["name"] or "(ไม่มีชื่อ)"))

    loc_names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM locations")}
    loc_by_name = {v: k for k, v in loc_names.items()}
    usable = {r["id"] for r in db.execute("SELECT id FROM locations WHERE active = 1")}
    staff_names = {r["id"]: places.staff_label(r) for r in db.execute("SELECT id, full_name, nickname FROM staff")}

    rows = []
    for text, parts in by_text.items():
        n = sum(p["n"] for p in parts)
        locs = Counter({p["location_id"]: 0 for p in parts})
        holders = Counter()
        for p in parts:
            locs[p["location_id"]] += p["n"]
            holders[p["holder_id"]] += p["n"]
        mapped = n - locs.get(None, 0)
        mapped_locs = [k for k in locs if k is not None]
        loc_mode = max(mapped_locs, key=lambda k: locs[k]) if mapped_locs else None
        held = [k for k in holders if k is not None]
        holder_mode = max(held, key=lambda k: holders[k]) if held else None
        suggestion = places.suggest_room(text)
        preselect = loc_mode or loc_by_name.get(suggestion)
        rows.append({
            "text": text, "key": places.text_key(text), "n": n, "mapped": mapped,
            "location_id": loc_mode, "location_name": loc_names.get(loc_mode),
            "locs": dict(locs), "cats": cats[text], "samples": samples[text],
            "room_counts": {loc_names[k]: locs[k] for k in sorted(mapped_locs, key=lambda k: -locs[k])},
            "mixed": len(mapped_locs) > 1,
            "holder_id": holder_mode, "holder_name": staff_names.get(holder_mode),
            "holder_count": n - holders.get(None, 0),
            "multi": places.is_multi_room(text),
            "person": places.person_in(text),
            "suggest_room": suggestion,
            # เลือกห้องให้ก่อน: ห้องที่จัดไว้แล้ว หรือห้องที่ชื่อตรงกับชื่อที่เดาได้ (เฉพาะห้องที่ยังใช้งาน)
            "preselect": preselect if preselect in usable else None,
            "assets_url": "/assets?" + urlencode({"loc_text": text}),
        })
    rows.sort(key=lambda r: (-r["n"], r["text"]))
    return rows


def row_matches(r, f):
    if not text_matches(r["text"], f["q"]):
        return False
    st = f["status"]
    if st == "todo" and r["mapped"] >= r["n"]:
        return False
    if st == "done" and r["mapped"] < r["n"]:
        return False
    if st == "multi" and not r["multi"]:
        return False
    if st == "person" and not r["person"]:
        return False
    return not f["cat"] or r["cats"].get(f["cat"], 0) > 0


def with_cat(r, f):
    r["n_cat"] = r["cats"].get(f["cat"], 0) if f["cat"] else None
    return r


def mapping_ctx(db):
    return {"locations": places.location_options(db), "staff": places.staff_options(db), "NEW": NEW}


def mapping_view(db, f):
    rows = [with_cat(r, f) for r in mapping_rows(db)]
    shown = [r for r in rows if row_matches(r, f)]
    groups = [g for g in room_groups.build_groups(db, rows) if g["show"]]
    groups = [g for g in groups if any(row_matches(t["row"], f) for t in g["texts"])]
    all_cats = Counter()
    for r in rows:
        all_cats.update(r["cats"])
    no_text = db.execute("SELECT COUNT(*) FROM assets WHERE location_text IS NULL OR location_text = ''").fetchone()[0]
    return {
        **mapping_ctx(db), "rows": shown, "groups": groups, "f": f, "statuses": MAP_STATUSES,
        "categories": category_options(db, all_cats), "back": urlencode(filter_query(f)),
        "filtered": bool(filter_query(f)), "n_texts": len(rows),
        "n_todo": sum(1 for r in rows if r["mapped"] < r["n"]), "no_text": no_text, "place_tab": "mapping",
        "export_url": "/places/mapping/groups.xlsx",
    }


@router.get("/locations/mapping")
def old_mapping_url(request: Request):
    """URL เดิม (ก่อนรวมเมนูห้องและผู้รับผิดชอบ) ยังใช้ได้"""
    return RedirectResponse("/places/mapping" + (f"?{request.url.query}" if request.url.query else ""), status_code=301)


@router.get("/places/mapping")
def mapping_page(request: Request, q: str = "", status: str = "", cat: str = "", todo: str = "",
                 user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    if todo == "1" and not status:  # URL เดิม ?todo=1
        status = "todo"
    f = clean_filters(q, status, cat)
    ctx = mapping_view(db, f)
    if is_filter_request(request):
        resp = templates.TemplateResponse(request, "locations/_mapping_results.html", ctx)
        set_filter_url(request, resp, page_url("/places/mapping", f))
        return resp
    return templates.TemplateResponse(request, "locations/mapping.html", ctx)


def mapping_result(request, db, text, message=None, error=None, events=None, back=""):
    f = back_filters(back)
    rows = mapping_rows(db, only_text=text)
    if request.headers.get("HX-Request") and rows:
        resp = templates.TemplateResponse(request, "locations/_mapping_row.html", {
            **mapping_ctx(db), "r": with_cat(rows[0], f), "message": message, "error": error,
            "back": urlencode(filter_query(f))})
        if events:
            # ให้ dropdown ของแถวอื่นมีห้อง/ผู้รับผิดชอบที่เพิ่งสร้างด้วย (ดู static/js/app.js)
            resp.headers["HX-Trigger"] = json.dumps(events)  # ensure_ascii: header ต้องเป็น ASCII
        return resp
    request.session["flash"] = error or message
    anchor = f"#{rows[0]['key']}" if rows else ""
    return RedirectResponse(page_url("/places/mapping", f) + anchor, status_code=303)


def check_location_choice(db, location, new_location):
    """คืน (ชื่อห้องใหม่ที่ normalize แล้ว, ข้อผิดพลาด)"""
    new_location = places.normalize(new_location)
    if location == NEW and not new_location:
        return new_location, "พิมพ์ชื่อห้องใหม่"
    if location == NEW and len(new_location) > MAX_NAME:
        return new_location, "ชื่อห้องยาวเกินไป"
    if location != NEW and not db.execute("SELECT 1 FROM locations WHERE id = ? AND active = 1",
                                          (location if location.isdigit() else -1,)).fetchone():
        return new_location, "เลือกห้อง"
    return new_location, None


def check_holder_choice(db, holder, new_nickname, new_full_name):
    if holder == NEW and not (places.normalize(new_nickname) or places.normalize(new_full_name)):
        return "ใส่ชื่อเล่นหรือชื่อจริงของผู้รับผิดชอบคนใหม่"
    if holder not in ("", NEW) and not db.execute("SELECT 1 FROM staff WHERE id = ? AND active = 1",
                                                  (holder if holder.isdigit() else -1,)).fetchone():
        return "เลือกผู้รับผิดชอบ"
    return None


def resolve_choices(db, user_id, location, new_location, holder, new_nickname, new_full_name, new_position=""):
    """สร้างห้อง/ผู้รับผิดชอบใหม่ถ้าเลือก "สร้างใหม่" (ภายใน transaction) คืน (loc_id, holder_id, events)"""
    events = {}
    if location == NEW:
        loc_id, created = places.create_location(db, user_id, new_location)
        if created:
            events["krupan:location-added"] = {"id": loc_id, "name": places.normalize(new_location)}
    else:
        loc_id = int(location)
    holder_id = None
    if holder == NEW:
        holder_id = places.create_staff(db, user_id, new_nickname, new_full_name, new_position)
        events["krupan:staff-added"] = {"id": holder_id, "name": places.staff_name(db, holder_id)}
    elif holder:
        holder_id = int(holder)
    return loc_id, holder_id, events


def apply_text(db, user_id, row, loc_id, holder_id):
    """ทุกชิ้นที่มี location_text = row.text ได้ห้องนี้ (location_text เดิมเก็บไว้เสมอ) คืน (เปลี่ยน, ข้าม)
    ชิ้นที่เคยย้ายแยกไปห้องอื่นแล้วจะไม่ถูกเปลี่ยน (เปลี่ยนเฉพาะชิ้นที่ยังไม่มีห้อง หรืออยู่ห้องเดียวกับกลุ่ม)"""
    text = row["text"]
    changed = skipped = 0
    assets = db.execute("SELECT id, location_id, holder_id FROM assets WHERE location_text = ? ORDER BY id",
                        (text,)).fetchall()
    for a in assets:
        new_loc, new_holder = a["location_id"], a["holder_id"]
        moved_elsewhere = a["location_id"] not in (None, row["location_id"])
        if not moved_elsewhere:
            new_loc = loc_id
        if holder_id is not None and a["holder_id"] in (None, row["holder_id"]):
            new_holder = holder_id
        changes = audit.diff(a, {"location_id": new_loc, "holder_id": new_holder}, ("location_id", "holder_id"))
        if moved_elsewhere:
            skipped += 1
        if not changes:
            continue
        db.execute("UPDATE assets SET location_id = ?, holder_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (new_loc, new_holder, a["id"]))
        places.label_changes(db, changes)
        audit.log(db, user_id, "assets", a["id"], "update", {**changes, "from_location_text": text})
        places.record_move(db, a["id"], a["location_id"], new_loc, a["holder_id"], new_holder,
                           f"{places.MAPPING_NOTE_PREFIX} “{text}”", from_mapping=True)
        changed += 1
    return changed, skipped


@router.post("/locations/mapping")
def save_mapping(request: Request, text: str = Form(...), location: str = Form(""), new_location: str = Form(""),
                 holder: str = Form(""), new_nickname: str = Form(""), new_full_name: str = Form(""),
                 back: str = Form(""), user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    rows = mapping_rows(db, only_text=text)
    if not rows:
        raise HTTPException(status_code=404, detail="ไม่พบข้อความที่ตั้งนี้")
    row = rows[0]
    result = lambda **kw: mapping_result(request, db, text, back=back, **kw)  # noqa: E731
    if row["multi"]:
        return result(error="ข้อความนี้มีหลายห้อง ต้องแยกจัดทีละชิ้นในหน้าแก้ไข")
    new_location, error = check_location_choice(db, location, new_location)
    error = error or check_holder_choice(db, holder, new_nickname, new_full_name)
    if error:
        return result(error=error)

    with db:  # สร้างห้อง/คน + แก้ทุกชิ้น + audit + asset_moves ใน transaction เดียว
        loc_id, holder_id, events = resolve_choices(db, user["id"], location, new_location, holder,
                                                    new_nickname, new_full_name)
        changed, skipped = apply_text(db, user["id"], row, loc_id, holder_id)

    message = f"บันทึกแล้ว {changed} ชิ้น" if changed else "ไม่มีข้อมูลที่เปลี่ยนแปลง"
    if skipped:
        message += f" (ข้าม {skipped} ชิ้นที่ย้ายไปห้องอื่นแล้ว)"
    return result(message=message, events=events)


@router.post("/places/mapping/group")
def save_group(request: Request, texts: list[str] = Form([]), location: str = Form(""), new_location: str = Form(""),
               holder: str = Form(""), new_nickname: str = Form(""), new_full_name: str = Form(""),
               new_position: str = Form(""), group: str = Form(""), back: str = Form(""),
               user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """จัดทุกข้อความที่ติ๊กในกลุ่มที่เสนอเข้าห้องเดียวกัน (ระบบไม่จัดเอง ต้องกดยืนยัน)"""
    url = page_url("/places/mapping", back_filters(back))

    def done(msg):
        request.session["flash"] = msg
        return RedirectResponse(url + (f"#{group}" if group.startswith("g-") else ""), status_code=303)

    texts = list(dict.fromkeys(t for t in texts if t))
    if not texts:
        return done("ติ๊กเลือกข้อความอย่างน้อย 1 ข้อความ")
    rows = []
    for t in texts:
        found = mapping_rows(db, only_text=t)
        if not found:
            return done(f"ไม่พบข้อความ “{t}” (อาจถูกแก้ไปแล้ว) ลองโหลดหน้าใหม่")
        if found[0]["multi"]:
            return done(f"ข้อความ “{t}” มีหลายห้อง ต้องแยกจัดทีละชิ้นในหน้าแก้ไข")
        rows.append(found[0])
    new_location, error = check_location_choice(db, location, new_location)
    error = error or check_holder_choice(db, holder, new_nickname, new_full_name)
    if error:
        return done(error)
    with db:
        loc_id, holder_id, _ = resolve_choices(db, user["id"], location, new_location, holder,
                                               new_nickname, new_full_name, new_position)
        changed = skipped = 0
        for row in rows:
            c, s = apply_text(db, user["id"], row, loc_id, holder_id)
            changed, skipped = changed + c, skipped + s
    msg = f"จัด {len(rows)} ข้อความเข้า “{places.location_name(db, loc_id)}” แล้ว เปลี่ยน {changed} ชิ้น"
    if skipped:
        msg += f" (ข้าม {skipped} ชิ้นที่ย้ายไปห้องอื่นแล้ว)"
    return done(msg)


@router.get("/places/mapping/groups.xlsx")
def export_groups(user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """กลุ่มที่เสนอทั้งหมด สำหรับพิมพ์ไปให้เจ้าหน้าที่ยืนยันก่อนกดจัด"""
    groups = room_groups.build_groups(db, mapping_rows(db))
    buf = io.BytesIO()
    room_groups_xlsx.build_workbook(groups, settings_store.org_names(db)["report"]).save(buf)
    name = f"กลุ่มห้องที่ระบบเสนอ_{date.today():%Y%m%d}.xlsx"
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=\"room-groups.xlsx\"; filename*=UTF-8''{quote(name)}"})


# ---------------------------------------------------------------- รายการห้อง

def location_or_404(db, location_id):
    row = db.execute("""SELECT l.*, (SELECT COUNT(*) FROM assets a WHERE a.location_id = l.id) AS n,
                               (SELECT COUNT(*) FROM asset_moves m
                                WHERE m.from_location_id = l.id OR m.to_location_id = l.id) AS n_moves
                        FROM locations l WHERE l.id = ?""", (location_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="ไม่พบห้องนี้")
    return row


def area_lists(db):
    """อาคาร/ฝ่ายทั้งหมด (รวมที่ปิดใช้งาน) เรียงตามลำดับ พร้อมจำนวนห้อง"""
    out = {}
    for kind, (_, col) in AREA_KINDS.items():
        out[kind] = db.execute(f"""SELECT x.*, (SELECT COUNT(*) FROM locations l WHERE l.{col} = x.id) AS n_rooms
                                   FROM {kind} x ORDER BY x.sort_order, x.name""").fetchall()
    return out


def room_rows(db, f):
    rows = db.execute("""SELECT l.*, m.name AS merged_into_name, b.name AS building_name, d.name AS department_name,
                                (SELECT COUNT(*) FROM assets a WHERE a.location_id = l.id) AS n,
                                (SELECT COUNT(*) FROM asset_moves mv
                                 WHERE mv.from_location_id = l.id OR mv.to_location_id = l.id) AS n_moves
                         FROM locations l LEFT JOIN locations m ON m.id = l.merged_into_id
                         LEFT JOIN buildings b ON b.id = l.building_id LEFT JOIN departments d ON d.id = l.department_id
                         ORDER BY l.active DESC, l.merged_into_id IS NOT NULL, l.name""").fetchall()
    cats = defaultdict(Counter)
    for r in db.execute("""SELECT a.location_id, t.category_code, COUNT(*) AS n FROM assets a
                           LEFT JOIN asset_types t ON t.code = a.type_code
                           WHERE a.location_id IS NOT NULL GROUP BY a.location_id, t.category_code"""):
        cats[r["location_id"]][r["category_code"] or NO_CATEGORY] += r["n"]
    all_cats = Counter()
    for c in cats.values():
        all_cats.update(c)
    out = []
    for r in rows:
        st = f["status"]
        if st == "active" and not r["active"]:
            continue
        if st == "inactive" and (r["active"] or r["merged_into_id"]):
            continue
        if st == "merged" and not r["merged_into_id"]:
            continue
        if not text_matches(r["name"], f["q"]):
            continue
        if any((f.get(k) == NO_AREA and r[c] is not None) or (f.get(k, "").isdigit() and r[c] != int(f[k]))
               for k, c in (("building", "building_id"), ("dept", "department_id"))):
            continue
        n_cat = cats[r["id"]].get(f["cat"], 0) if f["cat"] else None
        if f["cat"] and not n_cat:
            continue
        out.append({**dict(r), "n_cat": n_cat})
    return out, all_cats, len(rows)


@router.get("/locations")
def old_locations_url(request: Request):
    return RedirectResponse("/places" + (f"?{request.url.query}" if request.url.query else ""), status_code=301)


@router.get("/places")
def locations_page(request: Request, q: str = "", status: str = "", cat: str = "", building: str = "", dept: str = "",
                   db: sqlite3.Connection = Depends(get_db)):
    f = clean_filters(q, status, cat, ROOM_STATUSES, building, dept)
    rows, all_cats, n_all = room_rows(db, f)
    editor = can_edit(request)
    ctx = {"rows": rows, "n_all": n_all, "f": f, "statuses": ROOM_STATUSES, "filtered": bool(filter_query(f)),
           "areas": area_lists(db), "NO_AREA": NO_AREA, "is_admin": is_admin(request),
           "back": urlencode(filter_query(f)),
           "categories": category_options(db, all_cats), "can_edit": editor, "place_tab": "rooms",
           "unassigned": db.execute("SELECT COUNT(*) FROM assets WHERE location_id IS NULL").fetchone()[0],
           "recent_merges": merges.recent(db) if editor else [], "merge_blocked": merges.blocked_reason(db)}
    if is_filter_request(request):
        resp = templates.TemplateResponse(request, "locations/_rooms_results.html", ctx)
        set_filter_url(request, resp, page_url("/places", f))
        return resp
    return templates.TemplateResponse(request, "locations/list.html", ctx)


def clean_name(name):
    name = places.normalize(name)
    if not name:
        return name, "ต้องมีชื่อห้อง"
    if len(name) > MAX_NAME:
        return name, "ชื่อห้องยาวเกินไป"
    return name, None


@router.post("/locations")
def add_location(request: Request, name: str = Form(""), user=Depends(require_editor),
                 db: sqlite3.Connection = Depends(get_db)):
    name, error = clean_name(name)
    if not error and db.execute("SELECT 1 FROM locations WHERE name = ?", (name,)).fetchone():
        error = f"มีห้อง “{name}” อยู่แล้ว"
    if not error:
        with db:
            places.create_location(db, user["id"], name)
    request.session["flash"] = error or f"เพิ่มห้อง “{name}” แล้ว"
    return RedirectResponse("/places", status_code=303)


# ---------------------------------------------------------------- รวมห้อง (ต้องอยู่ก่อน /locations/{location_id})

def merge_candidates(db, ids):
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    marks = ",".join("?" * len(ids))
    return db.execute(f"""SELECT l.*, (SELECT COUNT(*) FROM assets a WHERE a.location_id = l.id) AS n
                          FROM locations l WHERE l.id IN ({marks}) ORDER BY n DESC, l.name""", ids).fetchall()


@router.post("/places/merge/start")
def merge_start(ids: list[int] = Form([]), user=Depends(require_editor)):
    """ปุ่ม “รวมห้องที่ติ๊ก” อยู่ในฟอร์มเดียวกับการตั้งอาคาร/ฝ่าย (POST) ส่งต่อไปหน้ายืนยันโดยไม่มี token ใน URL"""
    return RedirectResponse("/places/merge?" + urlencode([("ids", i) for i in dict.fromkeys(ids)]), status_code=303)


@router.get("/places/merge")
def merge_page(request: Request, ids: list[int] = Query([]), user=Depends(require_editor),
               db: sqlite3.Connection = Depends(get_db)):
    rooms = merge_candidates(db, ids)
    error = None
    if len(rooms) < 2:
        error = "ติ๊กเลือกห้องที่จะรวมอย่างน้อย 2 ห้อง"
    elif any(r["merged_into_id"] for r in rooms):
        error = "มีห้องที่ถูกรวมไปแล้วอยู่ในรายการที่เลือก"
    elif not any(r["active"] for r in rooms):
        error = "ต้องมีห้องที่ใช้งานอยู่อย่างน้อย 1 ห้องเพื่อเก็บไว้"
    if error:
        request.session["flash"] = error
        return RedirectResponse("/places", status_code=303)
    keep = next(r["id"] for r in rooms if r["active"])  # ห้องที่มีครุภัณฑ์มากที่สุด (เรียงไว้แล้ว)
    return templates.TemplateResponse(request, "locations/merge.html", {
        "rooms": rooms, "keep": keep, "blocked": merges.blocked_reason(db), "place_tab": "rooms",
        "total": sum(r["n"] for r in rooms)})


@router.post("/places/merge")
def do_merge(request: Request, ids: list[int] = Form([]), keep: int = Form(...), user=Depends(require_editor),
             db: sqlite3.Connection = Depends(get_db)):
    if keep not in ids:
        request.session["flash"] = "เลือกห้องที่จะเก็บไว้จากห้องที่ติ๊ก"
        return RedirectResponse("/places/merge?" + urlencode([("ids", i) for i in ids]), status_code=303)
    try:
        with db:
            merge_id, moved = merges.merge(db, user["id"], keep, ids)
    except merges.MergeError as e:
        request.session["flash"] = str(e)
        return RedirectResponse("/places", status_code=303)
    request.session["flash"] = (f"รวม {len(set(ids)) - 1} ห้องเข้ากับ “{places.location_name(db, keep)}” แล้ว "
                                f"ย้ายครุภัณฑ์ {moved} ชิ้น (ยกเลิกได้ภายใน 1 วัน)")
    return RedirectResponse("/places", status_code=303)


@router.post("/places/merges/{merge_id}/undo")
def undo_merge(request: Request, merge_id: int, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    try:
        with db:
            back, skipped = merges.undo(db, user["id"], merge_id)
    except merges.MergeError as e:
        request.session["flash"] = str(e)
        return RedirectResponse("/places", status_code=303)
    msg = f"ยกเลิกการรวมห้องแล้ว คืนครุภัณฑ์ {back} ชิ้นกลับห้องเดิม"
    if skipped:
        msg += f" (ข้าม {skipped} ชิ้นที่ถูกย้ายไปห้องอื่นหลังรวม)"
    request.session["flash"] = msg
    return RedirectResponse("/places", status_code=303)


# ---------------------------------------------------------------- แก้ / ปิดใช้งาน / ลบห้อง

def area_value(db, kind, raw, current):
    """ค่าจากฟอร์ม -> (id ใหม่, ข้อผิดพลาด) ไม่ได้ส่งมา = คงค่าเดิม, "none" = ไม่ระบุ
    (ใช้ "none" ไม่ใช่ค่าว่าง เพราะ FastAPI แปลงช่องฟอร์มที่ว่างเป็น None ซึ่งแยกไม่ออกจาก “ไม่ได้ส่งมา”)"""
    if raw is None or raw == "":
        return current, None
    if raw == NO_AREA:
        return None, None
    if not raw.isdigit() or not db.execute(f"SELECT 1 FROM {kind} WHERE id = ?", (int(raw),)).fetchone():
        return current, f"เลือก{AREA_KINDS[kind][0]}"
    return int(raw), None


def area_name(db, kind, area_id):
    if area_id is None:
        return None
    row = db.execute(f"SELECT name FROM {kind} WHERE id = ?", (area_id,)).fetchone()
    return row["name"] if row else None


def room_area_changes(db, old, building_id, department_id):
    """audit changes ของอาคาร/ฝ่าย (พร้อมชื่อ ณ ตอนแก้)"""
    changes = {}
    for col, kind, new in (("building_id", "buildings", building_id), ("department_id", "departments", department_id)):
        if old[col] != new:
            changes[col] = {"old": old[col], "new": new, "old_label": area_name(db, kind, old[col]),
                            "new_label": area_name(db, kind, new)}
    return changes


def back_to_places(back):
    f = back_filters_rooms(back)
    return RedirectResponse(page_url("/places", f), status_code=303)


def back_filters_rooms(back):
    d = dict(parse_qsl(back or ""))
    return clean_filters(d.get("q", ""), d.get("status", ""), d.get("cat", ""), ROOM_STATUSES,
                         d.get("building", ""), d.get("dept", ""))


@router.post("/locations/{location_id}")
def rename_location(request: Request, location_id: int, name: str = Form(""), building: str | None = Form(None),
                    department: str | None = Form(None), back: str = Form(""), user=Depends(require_editor),
                    db: sqlite3.Connection = Depends(get_db)):
    """แก้ชื่อ อาคาร และฝ่ายของห้องในแถวเดียว (ไม่ส่งช่องอาคาร/ฝ่ายมา = คงค่าเดิม) บันทึก audit_log"""
    old = location_or_404(db, location_id)
    name, error = clean_name(name)
    if not error and db.execute("SELECT 1 FROM locations WHERE name = ? AND id != ?", (name, location_id)).fetchone():
        error = f"มีห้อง “{name}” อยู่แล้ว"
    building_id, e1 = area_value(db, "buildings", building, old["building_id"])
    department_id, e2 = area_value(db, "departments", department, old["department_id"])
    error = error or e1 or e2
    changes = {}
    if not error:
        if name != old["name"]:
            changes["name"] = {"old": old["name"], "new": name}
        changes.update(room_area_changes(db, old, building_id, department_id))
    if changes:
        with db:
            db.execute("UPDATE locations SET name = ?, building_id = ?, department_id = ? WHERE id = ?",
                       (name, building_id, department_id, location_id))
            audit.log(db, user["id"], "locations", location_id, "update", changes)
    if error:
        request.session["flash"] = error
    elif not changes:
        request.session["flash"] = "ไม่มีข้อมูลที่เปลี่ยนแปลง"
    elif set(changes) == {"name"}:
        request.session["flash"] = f"เปลี่ยนชื่อเป็น “{name}” แล้ว"
    else:
        request.session["flash"] = f"บันทึกห้อง “{name}” แล้ว"
    return back_to_places(back)


@router.post("/places/bulk")
def bulk_set_areas(request: Request, ids: list[int] = Form([]), building: str = Form(""), department: str = Form(""),
                   back: str = Form(""), user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """ตั้งอาคาร/ฝ่ายให้หลายห้องพร้อมกัน ("" = ไม่เปลี่ยน, "none" = ล้างเป็นไม่ระบุ)"""
    ids = list(dict.fromkeys(ids))
    if not ids:
        request.session["flash"] = "ติ๊กเลือกห้องก่อน"
        return back_to_places(back)
    if not building and not department:
        request.session["flash"] = "เลือกอาคารหรือฝ่ายที่จะตั้ง"
        return back_to_places(back)
    n = 0
    with db:
        for lid in ids:
            old = db.execute("SELECT * FROM locations WHERE id = ?", (lid,)).fetchone()
            if old is None:
                continue
            values = {}
            for kind, raw, col in (("buildings", building, "building_id"), ("departments", department, "department_id")):
                if raw == "":
                    values[col] = old[col]
                elif raw == NO_AREA:
                    values[col] = None
                else:
                    values[col], err = area_value(db, kind, raw, old[col])
                    if err:
                        request.session["flash"] = err
                        return back_to_places(back)
            changes = room_area_changes(db, old, values["building_id"], values["department_id"])
            if changes:
                db.execute("UPDATE locations SET building_id = ?, department_id = ? WHERE id = ?",
                           (values["building_id"], values["department_id"], lid))
                audit.log(db, user["id"], "locations", lid, "update", {**changes, "bulk": len(ids)})
                n += 1
    request.session["flash"] = f"ตั้งอาคาร/ฝ่ายให้ {n} ห้องแล้ว" if n else "ไม่มีข้อมูลที่เปลี่ยนแปลง"
    return back_to_places(back)


# ---------------------------------------------------------------- รายการอาคาร / ฝ่าย (admin)

def clean_area_name(name):
    name = places.normalize(name)
    if not name:
        return name, "ต้องมีชื่อ"
    if len(name) > MAX_NAME:
        return name, "ชื่อยาวเกินไป"
    return name, None


@router.post("/places/areas/{kind}")
def add_area(request: Request, kind: str, name: str = Form(""), user=Depends(require_admin),
             db: sqlite3.Connection = Depends(get_db)):
    if kind not in AREA_KINDS:
        raise HTTPException(status_code=404)
    name, error = clean_area_name(name)
    if not error and db.execute(f"SELECT 1 FROM {kind} WHERE name = ?", (name,)).fetchone():
        error = f"มี “{name}” อยู่แล้ว"
    if not error:
        with db:
            order = db.execute(f"SELECT COALESCE(MAX(sort_order), 0) + 1 FROM {kind}").fetchone()[0]
            aid = db.execute(f"INSERT INTO {kind} (name, sort_order) VALUES (?, ?)", (name, order)).lastrowid
            audit.log(db, user["id"], kind, aid, "insert", {"name": {"old": None, "new": name}})
    request.session["flash"] = error or f"เพิ่ม{AREA_KINDS[kind][0]} “{name}” แล้ว"
    return RedirectResponse("/places#areas", status_code=303)


@router.post("/places/areas/{kind}/{area_id}")
def edit_area(request: Request, kind: str, area_id: int, name: str = Form(""), active: str = Form(""),
              holder_focus: str = Form(""), sort_order: str = Form(""), user=Depends(require_admin),
              db: sqlite3.Connection = Depends(get_db)):
    """แก้ชื่อ / ลำดับ / ใช้งาน (และ “เน้นผู้รับผิดชอบ” ของอาคาร) บันทึก audit_log · ไม่มีการลบ (ห้องอ้างถึงอยู่)"""
    if kind not in AREA_KINDS:
        raise HTTPException(status_code=404)
    old = db.execute(f"SELECT * FROM {kind} WHERE id = ?", (area_id,)).fetchone()
    if old is None:
        raise HTTPException(status_code=404)
    name, error = clean_area_name(name)
    if not error and db.execute(f"SELECT 1 FROM {kind} WHERE name = ? AND id != ?", (name, area_id)).fetchone():
        error = f"มี “{name}” อยู่แล้ว"
    new = {"name": name, "active": 1 if active else 0,
           "sort_order": int(sort_order) if sort_order.strip().isdigit() else old["sort_order"]}
    if kind == "buildings":
        new["holder_focus"] = 1 if holder_focus else 0
    changes = {} if error else audit.diff(old, new, tuple(new))
    if changes:
        with db:
            sets = ", ".join(f"{k} = ?" for k in new)
            db.execute(f"UPDATE {kind} SET {sets} WHERE id = ?", (*new.values(), area_id))
            audit.log(db, user["id"], kind, area_id, "update", changes)
    request.session["flash"] = error or ("บันทึกแล้ว" if changes else "ไม่มีข้อมูลที่เปลี่ยนแปลง")
    return RedirectResponse("/places#areas", status_code=303)


@router.post("/locations/{location_id}/active")
def set_location_active(request: Request, location_id: int, active: int = Form(...), user=Depends(require_editor),
                        db: sqlite3.Connection = Depends(get_db)):
    old = location_or_404(db, location_id)
    active = 1 if active else 0
    if active and old["merged_into_id"]:
        target = places.location_name(db, old["merged_into_id"])
        request.session["flash"] = (f"ห้อง “{old['name']}” ถูกรวมเข้ากับ “{target}” แล้ว เปิดใช้งานเองไม่ได้ "
                                    "ใช้ปุ่ม “ยกเลิกการรวม” (ภายใน 1 วันหลังรวม)")
        return RedirectResponse("/places", status_code=303)
    if active != old["active"]:
        with db:
            db.execute("UPDATE locations SET active = ? WHERE id = ?", (active, location_id))
            audit.log(db, user["id"], "locations", location_id, "update", {"active": {"old": old["active"], "new": active}})
    request.session["flash"] = f"{'เปิด' if active else 'ปิด'}ใช้งานห้อง “{old['name']}” แล้ว"
    return RedirectResponse("/places", status_code=303)


@router.post("/locations/{location_id}/delete")
def delete_location(request: Request, location_id: int, user=Depends(require_editor),
                    db: sqlite3.Connection = Depends(get_db)):
    old = location_or_404(db, location_id)
    used = db.execute("""SELECT (SELECT COUNT(*) FROM location_merges WHERE kept_id = ?)
                              + (SELECT COUNT(*) FROM location_merge_items WHERE from_location_id = ?)""",
                      (location_id, location_id)).fetchone()[0]
    if old["n"] or old["n_moves"] or old["merged_into_id"] or used:
        request.session["flash"] = (f"ลบห้อง “{old['name']}” ไม่ได้ เพราะมีครุภัณฑ์ {old['n']} ชิ้นหรือประวัติการย้าย/การรวมห้องอ้างถึงอยู่"
                                    " ใช้ “ปิดใช้งาน” แทน")
        return RedirectResponse("/places", status_code=303)
    with db:
        db.execute("DELETE FROM locations WHERE id = ?", (location_id,))
        audit.log(db, user["id"], "locations", location_id, "delete", {"name": {"old": old["name"], "new": None}})
    request.session["flash"] = f"ลบห้อง “{old['name']}” แล้ว"
    return RedirectResponse("/places", status_code=303)
