"""การตรวจสอบพัสดุประจำปี: เปิดรอบ (เก็บ snapshot) บันทึกผล ปิดรอบ ข้อเสนอเปลี่ยนสถานะ และข้อมูลรายงาน

กติกา
- เปิดได้ทีละ 1 รอบ (db กันไว้ด้วย unique index) รอบที่ปิดแล้วแก้ไม่ได้ (db กันไว้ด้วย trigger)
- ตอนเปิดรอบ สร้างแถวใน inventory_check_items ให้ทุกชิ้นที่สถานะไม่ใช่ "จำหน่ายแล้ว" ณ ตอนนั้น (ไม่รวมรายการซ้ำ)
  ของที่เพิ่มเข้าทะเบียนระหว่างรอบจึงไม่อยู่ในรอบ และนับครบตามจำนวนตอนเปิดรอบเสมอ
- ห้อง "ตามทะเบียน" ของแต่ละชิ้นคือห้อง ณ วันเปิดรอบ (expected_location_id) ใบรายการแต่ละห้องจึงไม่เปลี่ยนระหว่างรอบ
- หลังปิดรอบ ระบบไม่เปลี่ยนสถานะในทะเบียนเอง ต้องกดยืนยันข้อเสนอทีละกลุ่ม
"""
import json
import sqlite3
from datetime import date

from app import audit, flags, places, settings_store, timeutil

OK, BROKEN, MISSING = RESULTS = ("พบ ใช้งานได้", "พบ ชำรุด", "ไม่พบ")
RESULT_CLASS = {OK: "ok", BROKEN: "warn", MISSING: "danger"}
METHOD_LABELS = {"qr": "สแกน QR", "list": "กรอกจากใบรายการ", "close": "ไม่ได้ตรวจ บันทึกตอนปิดรอบ"}
NO_ROOM = "none"
NO_ROOM_NAME = "ยังไม่จัดเข้าห้อง"
UNREGISTERED_FLAG = flags.INVENTORY_UNREGISTERED
DISPOSED = "จำหน่ายแล้ว"
MAX_MEMBERS = 8
REOPEN_DAYS = 7  # admin เปิดรอบที่ปิดแล้วกลับได้ภายในกี่วันหลังปิด (trigger ใน db ตรวจซ้ำ)


class InventoryError(Exception):
    """ทำรายการไม่ได้ตามกติกา (ข้อความแสดงให้ผู้ใช้เห็นได้)"""


def missing_flag(fiscal_year):
    return flags.inventory_missing(fiscal_year)


def schema_ready(db):
    """db ผ่าน migrate_005 แล้วหรือยัง (ยังไม่ผ่าน = หน้าตรวจสอบพัสดุใช้ไม่ได้ แต่หน้าอื่นต้องไม่พัง)"""
    return "method" in {r[1] for r in db.execute("PRAGMA table_info(inventory_check_items)")}


def default_fiscal_year(today):
    """ปีงบประมาณที่เพิ่งสิ้นสุด (ตรวจสอบพัสดุ ณ สิ้นปีงบประมาณ 30 ก.ย. เริ่มตรวจเดือน ต.ค.)"""
    return today.year + 543 if today.month >= 10 else today.year + 542


def now_text():
    return timeutil.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- กรรมการ

def committee_json(chair, members):
    return json.dumps({"chair": chair, "members": members}, ensure_ascii=False)


def committee(row):
    """[(ชื่อ, ตำแหน่งในคณะกรรมการ)] ประธานก่อน"""
    try:
        data = json.loads(row["committee"] or "{}")
    except ValueError:  # ข้อความเดิมก่อน migration
        data = {"chair": row["committee"], "members": []}
    if not isinstance(data, dict):
        data = {}
    out = [(data["chair"], "ประธานกรรมการ")] if data.get("chair") else []
    return out + [(m, "กรรมการ") for m in data.get("members") or [] if m]


def parse_round_form(chair, members_text):
    """คืน (chair, [members], error)"""
    chair = places.normalize(chair)
    members = [places.normalize(m) for m in (members_text or "").splitlines()]
    members = [m for m in members if m]
    if not chair:
        return chair, members, "กรอกชื่อประธานกรรมการ"
    if not members:
        return chair, members, "กรอกชื่อกรรมการอย่างน้อย 1 คน (บรรทัดละ 1 คน)"
    if len(members) > MAX_MEMBERS:
        return chair, members, f"กรรมการได้ไม่เกิน {MAX_MEMBERS} คน"
    if any(len(n) > 120 for n in [chair, *members]):
        return chair, members, "ชื่อยาวเกินไป"
    return chair, members, None


# ---------------------------------------------------------------- รอบตรวจสอบพัสดุ

def open_check(db):
    return db.execute("SELECT * FROM inventory_checks WHERE closed_at IS NULL").fetchone()


def get_check(db, check_id):
    return db.execute("SELECT * FROM inventory_checks WHERE id = ?", (check_id,)).fetchone()


def list_checks(db):
    return db.execute("""SELECT c.*, COUNT(i.asset_id) AS n_items, COUNT(i.result) AS n_checked
                         FROM inventory_checks c LEFT JOIN inventory_check_items i ON i.check_id = c.id
                         GROUP BY c.id ORDER BY c.id DESC""").fetchall()


def create_check(db, user_id, fiscal_year, started_at, chair, members):
    """เปิดรอบใหม่ + snapshot ครุภัณฑ์ทุกชิ้นที่ยังไม่จำหน่าย คืน (id, จำนวนชิ้น)"""
    try:
        with db:
            cid = db.execute("""INSERT INTO inventory_checks (fiscal_year, started_at, committee, created_by, created_at)
                                VALUES (?, ?, ?, ?, ?)""",
                             (fiscal_year, started_at, committee_json(chair, members), user_id, now_text())).lastrowid
            n = db.execute("""INSERT INTO inventory_check_items (check_id, asset_id, expected_location_id, status_at_open)
                              SELECT ?, id, location_id, status FROM assets
                              WHERE status != ? AND duplicate_of_id IS NULL""",
                           (cid, DISPOSED)).rowcount
            audit.log(db, user_id, "inventory_checks", cid, "insert", {
                "fiscal_year": {"old": None, "new": fiscal_year}, "started_at": {"old": None, "new": started_at},
                "committee": {"old": None, "new": ", ".join([chair, *members])}, "items": n})
    except sqlite3.IntegrityError as e:
        if "idx_inventory_checks_one_open" in str(e) or "UNIQUE" in str(e):
            raise InventoryError("มีรอบตรวจสอบพัสดุที่ยังเปิดอยู่ ต้องปิดรอบนั้นก่อนจึงเปิดรอบใหม่ได้") from e
        raise
    return cid, n


def update_check(db, user_id, check, fiscal_year, started_at, chair, members):
    new = {"fiscal_year": fiscal_year, "started_at": started_at, "committee": committee_json(chair, members)}
    changes = audit.diff(check, new, ("fiscal_year", "started_at", "committee"))
    if not changes:
        return {}
    ensure_open(check)
    with db:
        db.execute("UPDATE inventory_checks SET fiscal_year = ?, started_at = ?, committee = ? WHERE id = ?",
                   (fiscal_year, started_at, new["committee"], check["id"]))
        audit.log(db, user_id, "inventory_checks", check["id"], "update", changes)
    return changes


def ensure_open(check):
    if check is None:
        raise InventoryError("ไม่พบรอบตรวจสอบพัสดุนี้")
    if check["closed_at"] is not None:
        raise InventoryError("รอบตรวจสอบพัสดุนี้ปิดแล้ว แก้ไขไม่ได้")


# ---------------------------------------------------------------- รายการในรอบ

ITEM_COLUMNS = """i.*, a.reg_no, a.temp_code, a.name, a.details, a.unit_price, a.status, a.location_id,
                  a.location_text, a.needs_review, el.name AS expected_location_name,
                  fl.name AS found_location_name, cl.name AS current_location_name,
                  COALESCE(u.display_name, u.username) AS checked_by_name,
                  COALESCE(hs.nickname, hs.full_name) AS holder_name, hs.full_name AS holder_full_name,
                  hs.position AS holder_position"""
ITEM_FROM = """FROM inventory_check_items i
               JOIN assets a ON a.id = i.asset_id
               LEFT JOIN locations el ON el.id = i.expected_location_id
               LEFT JOIN locations fl ON fl.id = i.found_location_id
               LEFT JOIN locations cl ON cl.id = a.location_id
               LEFT JOIN users u ON u.id = i.checked_by
               LEFT JOIN staff hs ON hs.id = a.holder_id"""
ITEM_ORDER = "ORDER BY a.reg_no IS NULL, a.reg_no, a.temp_code"
# พบนอกห้องที่บันทึกไว้: ทะเบียนมีห้อง ณ วันเปิดรอบ แต่พบที่ห้องอื่น
OUTSIDE = ("i.found_location_id IS NOT NULL AND i.expected_location_id IS NOT NULL "
           "AND i.found_location_id != i.expected_location_id")


def get_item(db, check_id, asset_id):
    return db.execute(f"SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE i.check_id = ? AND i.asset_id = ?",
                      (check_id, asset_id)).fetchone()


def room_where(room):
    """เงื่อนไข SQL ของห้องตามทะเบียน ณ วันเปิดรอบ (room = id ห้อง หรือ "none")"""
    if room == NO_ROOM:
        return "i.expected_location_id IS NULL AND i.unregistered = 0", ()
    return "i.expected_location_id = ? AND i.unregistered = 0", (int(room),)


def valid_room(room):
    return room == NO_ROOM or str(room).isdigit()


def room_name(db, room):
    if room == NO_ROOM:
        return NO_ROOM_NAME
    return places.location_name(db, int(room))


def room_items(db, check_id, room, only_unchecked=False):
    where, params = room_where(room)
    extra = " AND i.result IS NULL" if only_unchecked else ""
    # ห้อง "ยังไม่จัดเข้าห้อง" เรียงตามข้อความที่ตั้งเดิม จะได้เดินตรวจทีละจุดตามใบรายการ
    order = "ORDER BY a.location_text IS NULL, a.location_text, a.reg_no IS NULL, a.reg_no, a.temp_code" \
        if room == NO_ROOM else ITEM_ORDER
    return db.execute(f"SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE i.check_id = ? AND {where}{extra} {order}",
                      (check_id, *params)).fetchall()


NO_BUILDING = "none"
NO_BUILDING_NAME = "ห้องที่ยังไม่ระบุอาคาร"


def holder_focus(db, room):
    """ห้องนี้อยู่ในอาคารที่ต้องเน้นคอลัมน์ผู้รับผิดชอบในใบรายการ (ของติดตัวบุคคล) หรือไม่"""
    if room == NO_ROOM:
        return False
    row = db.execute("""SELECT b.holder_focus FROM locations l JOIN buildings b ON b.id = l.building_id
                        WHERE l.id = ?""", (int(room),)).fetchone()
    return bool(row and row[0])


def building_rooms(db, check_id):
    """ห้องในรอบ (ตามทะเบียน ณ วันเปิดรอบ) จัดกลุ่มตามอาคารปัจจุบันของห้อง
    [{key, name, holder_focus, rooms: [{key, name, total}]}] เรียงตามลำดับอาคาร ห้องในอาคารเรียงตามชื่อ
    "ยังไม่จัดเข้าห้อง" อยู่ในกลุ่ม "ห้องที่ยังไม่ระบุอาคาร" ท้ายสุด"""
    rows = db.execute("""SELECT i.expected_location_id AS lid, l.name AS room, b.id AS bid, b.name AS building,
                                b.holder_focus, b.sort_order, COUNT(*) AS total
                         FROM inventory_check_items i
                         LEFT JOIN locations l ON l.id = i.expected_location_id
                         LEFT JOIN buildings b ON b.id = l.building_id
                         WHERE i.check_id = ? AND i.unregistered = 0
                         GROUP BY i.expected_location_id
                         ORDER BY b.id IS NULL, b.sort_order, b.name, i.expected_location_id IS NULL, l.name""",
                      (check_id,)).fetchall()
    out = []
    for r in rows:
        key = str(r["bid"]) if r["bid"] is not None else NO_BUILDING
        if not out or out[-1]["key"] != key:
            out.append({"key": key, "name": r["building"] or NO_BUILDING_NAME, "holder_focus": bool(r["holder_focus"]),
                        "rooms": []})
        out[-1]["rooms"].append({"key": NO_ROOM if r["lid"] is None else str(r["lid"]),
                                 "name": r["room"] or NO_ROOM_NAME, "total": r["total"]})
    return out


def rooms(db, check_id):
    """ความคืบหน้ารายห้อง (ห้องตามทะเบียน ณ วันเปิดรอบ) ไม่รวมของที่ไม่มีในทะเบียน"""
    rows = db.execute(f"""
        SELECT i.expected_location_id AS lid, l.name AS name, COUNT(*) AS total, COUNT(i.result) AS checked,
               SUM(i.result = ?) AS ok, SUM(i.result = ?) AS broken, SUM(i.result = ?) AS missing,
               SUM({OUTSIDE}) AS outside
        FROM inventory_check_items i LEFT JOIN locations l ON l.id = i.expected_location_id
        WHERE i.check_id = ? AND i.unregistered = 0
        GROUP BY i.expected_location_id
        ORDER BY i.expected_location_id IS NULL, l.name""", (OK, BROKEN, MISSING, check_id)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["key"] = NO_ROOM if r["lid"] is None else str(r["lid"])
        d["name"] = r["name"] or NO_ROOM_NAME
        d["pct"] = round(100 * r["checked"] / r["total"]) if r["total"] else 0
        out.append(d)
    return out


def counts(db, check_id):
    r = db.execute(f"""
        SELECT COUNT(*) AS total, COUNT(i.result) AS checked,
               COALESCE(SUM(i.result = ?), 0) AS ok, COALESCE(SUM(i.result = ?), 0) AS broken,
               COALESCE(SUM(i.result = ?), 0) AS missing, COALESCE(SUM(i.result IS NULL), 0) AS unchecked,
               COALESCE(SUM({OUTSIDE}), 0) AS outside
        FROM inventory_check_items i WHERE i.check_id = ? AND i.unregistered = 0""",
                   (OK, BROKEN, MISSING, check_id)).fetchone()
    d = dict(r)
    d["unregistered"] = db.execute("SELECT COUNT(*) FROM inventory_check_items WHERE check_id = ? AND unregistered = 1",
                                   (check_id,)).fetchone()[0]
    d["pct"] = round(100 * d["checked"] / d["total"]) if d["total"] else 0
    return d


def unchecked_items(db, check_id):
    return db.execute(f"SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE i.check_id = ? AND i.result IS NULL "
                      f"ORDER BY el.name IS NULL, el.name, a.reg_no IS NULL, a.reg_no, a.temp_code",
                      (check_id,)).fetchall()


# ---------------------------------------------------------------- บันทึกผล

def record(db, user_id, check, asset_id, result, method, note=None, found_location_id=None, keep_found=False):
    """บันทึกผลตรวจ 1 ชิ้น (ต้องเรียกภายใน transaction) คืน changes ที่บันทึกใน audit_log หรือ {} ถ้าเหมือนเดิม

    keep_found=True: ไม่แตะห้องที่พบ (หน้ากรอกจากใบรายการไม่ได้ถามห้อง)"""
    ensure_open(check)
    if result not in RESULTS:
        raise InventoryError("เลือกผลการตรวจ")
    item = get_item(db, check["id"], asset_id)
    if item is None:
        raise InventoryError("ครุภัณฑ์ชิ้นนี้ไม่อยู่ในรอบตรวจสอบพัสดุนี้")
    note = places.normalize(note) or None
    if keep_found:
        found_location_id = item["found_location_id"]
    elif found_location_id == item["expected_location_id"]:
        found_location_id = None  # พบในห้องที่ทะเบียนบันทึกไว้
    changes = {}
    for field, old, new in (("inventory_result", item["result"], result), ("inventory_note", item["note"], note),
                            ("found_location_id", item["found_location_id"], found_location_id)):
        if old != new:
            changes[field] = {"old": old, "new": new}
    if "found_location_id" in changes:
        ch = changes["found_location_id"]
        ch["old_label"], ch["new_label"] = places.location_name(db, ch["old"]), places.location_name(db, ch["new"])
    if not changes:
        return {}
    db.execute("""UPDATE inventory_check_items SET result = ?, note = ?, found_location_id = ?, method = ?,
                         checked_at = ?, checked_by = ? WHERE check_id = ? AND asset_id = ?""",
               (result, note, found_location_id, method, now_text(), user_id, check["id"], asset_id))
    audit.log(db, user_id, "assets", asset_id, "inventory",
              {**changes, "check_id": check["id"], "fiscal_year": check["fiscal_year"], "method": method})
    return changes


def save_room(db, user_id, check, room, entries):
    """บันทึกผลจากใบรายการทั้งห้องในครั้งเดียว entries = {asset_id: (result, note)} คืนจำนวนชิ้นที่เปลี่ยน
    ชิ้นที่ไม่ได้เลือกผลไว้ (result ว่าง) ไม่แตะ"""
    ensure_open(check)
    allowed = {r["asset_id"]: r for r in room_items(db, check["id"], room)}
    n = 0
    with db:
        for asset_id, (result, note) in entries.items():
            item = allowed.get(asset_id)
            if item is None or not result:
                continue
            note = places.normalize(note) or None
            if item["result"] == result and item["note"] == note:
                continue
            if record(db, user_id, check, asset_id, result, "list", note, keep_found=True):
                n += 1
    return n


def move_to_found(db, user_id, asset, item):
    """ย้ายห้องในทะเบียนไปห้องที่พบตอนตรวจสอบพัสดุ (asset_moves เหตุผล "พบระหว่างตรวจสอบพัสดุ") คืน True ถ้าย้าย"""
    new_loc = item["found_location_id"]
    if new_loc is None or asset["location_id"] == new_loc:
        return False
    changes = places.label_changes(db, {"location_id": {"old": asset["location_id"], "new": new_loc}})
    with db:
        db.execute("UPDATE assets SET location_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                   (new_loc, asset["id"]))
        audit.log(db, user_id, "assets", asset["id"], "update", {**changes, "via": "inventory",
                                                                 "check_id": item["check_id"]})
        places.record_move(db, asset["id"], asset["location_id"], new_loc, asset["holder_id"], asset["holder_id"],
                           f"ตรวจสอบพัสดุประจำปี {item_fiscal_year(db, item)}", reason=places.REASON_FOUND)
    return True


def item_fiscal_year(db, item):
    return db.execute("SELECT fiscal_year FROM inventory_checks WHERE id = ?", (item["check_id"],)).fetchone()[0]


def next_temp_code(db):
    n = db.execute("""SELECT MAX(CAST(SUBSTR(temp_code, 5) AS INTEGER)) FROM assets
                      WHERE temp_code LIKE 'TMP-%'""").fetchone()[0] or 0
    return f"TMP-{n + 1:04d}"


def add_unregistered(db, user_id, check, name, location_id, note, result=OK):
    """พบของที่ไม่มีในทะเบียน: สร้างครุภัณฑ์ใหม่ (รหัส TMP) + needs_review และใส่เข้ารอบเป็นรายการที่ตรวจแล้ว"""
    ensure_open(check)
    name = places.normalize(name)
    if not name:
        raise InventoryError("กรอกชื่อรายการ")
    if result not in (OK, BROKEN):
        raise InventoryError("เลือกสภาพที่พบ")
    note = places.normalize(note) or None
    with db:
        code = next_temp_code(db)
        status = "ชำรุด" if result == BROKEN else "ใช้งาน"
        asset_id = db.execute("""INSERT INTO assets (temp_code, name, location_id, status, note, needs_review)
                                 VALUES (?, ?, ?, ?, ?, ?)""",
                              (code, name, location_id, status, note, UNREGISTERED_FLAG)).lastrowid
        audit.log(db, user_id, "assets", asset_id, "insert", {
            "temp_code": {"old": None, "new": code}, "name": {"old": None, "new": name},
            **places.label_changes(db, {"location_id": {"old": None, "new": location_id}}),
            "status": {"old": None, "new": status}, "needs_review": {"old": None, "new": UNREGISTERED_FLAG},
            "via": "inventory", "check_id": check["id"]})
        db.execute("""INSERT INTO inventory_check_items (check_id, asset_id, expected_location_id, status_at_open,
                          unregistered, result, checked_at, checked_by, found_location_id, method, note)
                      VALUES (?, ?, NULL, NULL, 1, ?, ?, ?, ?, 'list', ?)""",
                   (check["id"], asset_id, result, now_text(), user_id, location_id, note))
    return asset_id, code


# ---------------------------------------------------------------- ปิดรอบ

def close_check(db, user_id, check, expected_unchecked):
    """บันทึกชิ้นที่ยังไม่ตรวจเป็น "ไม่พบ" แล้วปิดรอบ คืนจำนวนชิ้นที่บันทึกเป็นไม่พบ
    expected_unchecked = จำนวนที่ผู้ใช้เห็นในหน้ายืนยัน ถ้าไม่ตรงแสดงว่ามีคนตรวจเพิ่มระหว่างนั้น ให้ยืนยันใหม่"""
    ensure_open(check)
    with db:
        ids = [r[0] for r in db.execute("SELECT asset_id FROM inventory_check_items WHERE check_id = ? AND result IS NULL",
                                        (check["id"],))]
        if len(ids) != expected_unchecked:
            raise InventoryError("จำนวนชิ้นที่ยังไม่ได้ตรวจเปลี่ยนไประหว่างนั้น (มีคนกรอกผลเพิ่ม) กรุณาตรวจรายการแล้วยืนยันอีกครั้ง")
        now = now_text()
        db.execute("""UPDATE inventory_check_items SET result = ?, method = 'close', checked_at = ?, checked_by = ?
                      WHERE check_id = ? AND result IS NULL""", (MISSING, now, user_id, check["id"]))
        changes = json.dumps({"inventory_result": {"old": None, "new": MISSING}, "check_id": check["id"],
                              "fiscal_year": check["fiscal_year"], "method": "close"}, ensure_ascii=False)
        db.executemany("INSERT INTO audit_log (user_id, table_name, record_id, action, changes) "
                       "VALUES (?, 'assets', ?, 'inventory', ?)", [(user_id, i, changes) for i in ids])
        closed = timeutil.today_th().isoformat()
        db.execute("UPDATE inventory_checks SET closed_at = ?, closed_by = ? WHERE id = ?", (closed, user_id, check["id"]))
        audit.log(db, user_id, "inventory_checks", check["id"], "close",
                  {"closed_at": {"old": None, "new": closed}, "marked_missing": len(ids)})
    return len(ids)


# ---------------------------------------------------------------- เปิดรอบที่ปิดแล้วกลับ (admin)

def reopen_deadline(check):
    """วันสุดท้ายที่เปิดกลับได้ หรือ None ถ้ายังไม่ปิด"""
    if check["closed_at"] is None:
        return None
    return date.fromordinal(date.fromisoformat(check["closed_at"]).toordinal() + REOPEN_DAYS)


def can_reopen(check, today=None):
    deadline = reopen_deadline(check)
    return deadline is not None and (today or timeutil.today_th()) <= deadline


def reopen_check(db, user_id, check, reason):
    """เปิดรอบที่ปิดแล้วกลับ: ชิ้นที่ถูกบันทึกเป็น "ไม่พบ" ตอนปิดรอบ (method close) กลับเป็นยังไม่ตรวจ
    ข้อเสนอที่กดยืนยันไปแล้วไม่ย้อนกลับ (ดู applied_proposals) คืนจำนวนชิ้นที่กลับเป็นยังไม่ตรวจ"""
    reason = places.normalize(reason)
    if check["closed_at"] is None:
        raise InventoryError("รอบนี้ยังเปิดอยู่")
    if not reason:
        raise InventoryError("กรอกเหตุผลที่ต้องเปิดรอบกลับ")
    if len(reason) > 500:
        raise InventoryError("เหตุผลยาวเกินไป (ไม่เกิน 500 ตัวอักษร)")
    if not can_reopen(check):
        raise InventoryError(f"เปิดกลับได้ภายใน {REOPEN_DAYS} วันหลังปิดรอบเท่านั้น (ปิดเมื่อ {check['closed_at']})")
    other = open_check(db)
    if other is not None:
        raise InventoryError(f"มีรอบตรวจสอบพัสดุประจำปี {other['fiscal_year']} เปิดอยู่ ต้องปิดรอบนั้นก่อน")
    with db:
        db.execute("""INSERT INTO inventory_reopens (check_id, reason, closed_at_before, closed_by_before,
                                                     reopened_by, reopened_at) VALUES (?, ?, ?, ?, ?, ?)""",
                   (check["id"], reason, check["closed_at"], check["closed_by"], user_id, now_text()))
        db.execute("UPDATE inventory_checks SET closed_at = NULL, closed_by = NULL WHERE id = ?", (check["id"],))
        ids = [r[0] for r in db.execute("SELECT asset_id FROM inventory_check_items WHERE check_id = ? AND method = 'close'",
                                        (check["id"],))]
        db.execute("""UPDATE inventory_check_items SET result = NULL, method = NULL, checked_at = NULL, checked_by = NULL
                      WHERE check_id = ? AND method = 'close'""", (check["id"],))
        changes = json.dumps({"inventory_result": {"old": MISSING, "new": None}, "check_id": check["id"],
                              "fiscal_year": check["fiscal_year"], "method": "reopen"}, ensure_ascii=False)
        db.executemany("INSERT INTO audit_log (user_id, table_name, record_id, action, changes) "
                       "VALUES (?, 'assets', ?, 'inventory', ?)", [(user_id, i, changes) for i in ids])
        audit.log(db, user_id, "inventory_checks", check["id"], "reopen",
                  {"closed_at": {"old": check["closed_at"], "new": None}, "reason": reason, "unmarked_missing": len(ids)})
    return len(ids)


def reopens(db, check_id):
    return db.execute("""SELECT r.*, COALESCE(u.display_name, u.username) AS who FROM inventory_reopens r
                         LEFT JOIN users u ON u.id = r.reopened_by WHERE r.check_id = ? ORDER BY r.id""",
                      (check_id,)).fetchall()


def applied_proposals(db, check_id):
    """การเปลี่ยนสถานะ / ป้ายต้องตรวจสอบ ที่กดยืนยันจากข้อเสนอของรอบนี้ไปแล้ว (หลังเปิดรอบกลับ ให้ admin ตัดสินใจเอง)"""
    rows = db.execute("""
        SELECT l.record_id AS asset_id, l.changes, l.created_at, a.reg_no, a.temp_code, a.name, a.status, a.needs_review
        FROM audit_log l JOIN assets a ON a.id = l.record_id
        WHERE l.table_name = 'assets' AND l.action = 'update'
          AND json_extract(l.changes, '$.via') = 'inventory' AND json_extract(l.changes, '$.check_id') = ?
          AND (json_extract(l.changes, '$.status') IS NOT NULL OR json_extract(l.changes, '$.needs_review') IS NOT NULL)
        ORDER BY l.id""", (check_id,)).fetchall()
    out = []
    for r in rows:
        ch = json.loads(r["changes"])
        what = (f"สถานะ {ch['status']['old']} → {ch['status']['new']}" if "status" in ch
                else "ติดป้าย " + missing_flag(ch.get("fiscal_year", "")))
        out.append({**dict(r), "what": what})
    return out


# ---------------------------------------------------------------- ข้อเสนอหลังปิดรอบ (ต้องกดยืนยันทีละกลุ่ม)

PROPOSALS = {
    "damaged": {"title": "พบชำรุด แต่ทะเบียนเป็น “ใช้งาน”", "action": "เปลี่ยนสถานะเป็น “ชำรุด”",
                "result": BROKEN, "status": "ใช้งาน", "new_status": "ชำรุด"},
    "repaired": {"title": "พบใช้งานได้ แต่ทะเบียนเป็น “ชำรุด”", "action": "เปลี่ยนสถานะเป็น “ใช้งาน”",
                 "result": OK, "status": "ชำรุด", "new_status": "ใช้งาน"},
    "missing": {"title": "ไม่พบ", "action": "ติดป้าย “ต้องตรวจสอบ” (ไม่เปลี่ยนสถานะ)", "result": MISSING},
}


def proposal_items(db, check, group):
    p = PROPOSALS[group]
    rows = db.execute(f"SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE i.check_id = ? AND i.result = ? {ITEM_ORDER}",
                      (check["id"], p["result"])).fetchall()
    if group == "missing":
        flag = missing_flag(check["fiscal_year"])
        return [r for r in rows if flag not in flags.split(r["needs_review"])]
    return [r for r in rows if r["status"] == p["status"]]


def proposals(db, check):
    return {g: proposal_items(db, check, g) for g in PROPOSALS}


def apply_proposal(db, user_id, check, group, asset_ids):
    """ทำตามข้อเสนอกลุ่มเดียว เฉพาะชิ้นที่ผู้ใช้เห็นในหน้า (asset_ids) และยังตรงเงื่อนไขอยู่ คืนจำนวนชิ้นที่เปลี่ยน"""
    if check["closed_at"] is None:
        raise InventoryError("ต้องปิดรอบก่อนจึงจะยืนยันข้อเสนอได้")
    if group not in PROPOSALS:
        raise InventoryError("ไม่พบกลุ่มข้อเสนอนี้")
    wanted = set(asset_ids)
    rows = [r for r in proposal_items(db, check, group) if r["asset_id"] in wanted]
    p = PROPOSALS[group]
    with db:
        for r in rows:
            meta = {"via": "inventory", "check_id": check["id"], "fiscal_year": check["fiscal_year"]}
            if group == "missing":
                new_flags = flags.join([*flags.split(r["needs_review"]), missing_flag(check["fiscal_year"])])
                db.execute("UPDATE assets SET needs_review = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                           (new_flags, r["asset_id"]))
                audit.log(db, user_id, "assets", r["asset_id"], "update",
                          {"needs_review": {"old": r["needs_review"], "new": new_flags}, **meta})
            else:
                db.execute("UPDATE assets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                           (p["new_status"], r["asset_id"]))
                audit.log(db, user_id, "assets", r["asset_id"], "update",
                          {"status": {"old": r["status"], "new": p["new_status"]}, **meta})
    return len(rows)


# ---------------------------------------------------------------- รายงาน

def value_summary(rows):
    """{"n", "value" (รวมเฉพาะที่ทราบราคา), "unknown" (จำนวนที่ไม่ทราบราคา)}"""
    known = [float(r["unit_price"]) for r in rows if r["unit_price"] is not None]
    return {"n": len(rows), "value": sum(known), "unknown": len(rows) - len(known)}


def report_row(r):
    d = dict(r)
    d["code"] = r["reg_no"] or r["temp_code"]
    # ห้องตามทะเบียน ณ วันเปิดรอบ ถ้ายังไม่จัดเข้าห้อง ใช้ข้อความ "ใช้ประจำที่" เดิมจาก Excel (ติด * ไว้ในรายงาน)
    d["room_raw"] = not r["expected_location_name"] and bool(r["location_text"])
    d["room_label"] = r["expected_location_name"] or r["location_text"] or "—"
    d["moved"] = r["found_location_id"] is not None and r["location_id"] == r["found_location_id"]
    return d


def report(db, check):
    items = [report_row(r) for r in
             db.execute(f"SELECT {ITEM_COLUMNS} {ITEM_FROM} WHERE i.check_id = ? {ITEM_ORDER}", (check["id"],))]
    snap = [r for r in items if not r["unregistered"]]
    by_result = {res: [r for r in snap if r["result"] == res] for res in RESULTS}
    summary = [(res, value_summary(by_result[res])) for res in RESULTS]
    unchecked = [r for r in snap if r["result"] is None]
    if unchecked:
        summary.append(("ยังไม่ตรวจ", value_summary(unchecked)))
    outside = [r for r in snap if r["found_location_id"] is not None and r["expected_location_id"] is not None
               and r["found_location_id"] != r["expected_location_id"]]
    return {
        "check": check, "office": settings_store.org_names(db)["report"], "committee": committee(check), "summary": summary,
        "total": value_summary(snap),
        "broken": by_result[BROKEN], "missing": by_result[MISSING], "outside": outside,
        "unregistered": [r for r in items if r["unregistered"]], "unchecked": len(unchecked),
    }
