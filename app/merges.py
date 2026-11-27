"""รวมห้องที่ซ้ำกัน (ห้องเดียวกันที่สร้างไว้หลายชื่อ) และยกเลิกการรวมภายใน 1 วัน

- ครุภัณฑ์ทุกชิ้นของห้องที่ถูกรวมย้ายไปห้องที่เก็บไว้ บันทึก asset_moves เหตุผล "รวมห้องซ้ำ" (ไม่นับเป็นการย้ายจริง)
- ห้องที่ถูกรวม: ปิดใช้งาน + merged_into_id ห้ามลบ (ประวัติการย้ายและรอบตรวจสอบพัสดุเดิมอ้างถึงด้วย id จึงยังแสดงชื่อเดิม)
- ห้ามรวม/ยกเลิกระหว่างที่มีรอบตรวจสอบพัสดุเปิดอยู่ (ใบรายการของรอบแบ่งตามห้อง)
- ยกเลิกการรวม: คืนเฉพาะชิ้นที่ยังอยู่ห้องที่เก็บไว้ (ชิ้นที่ถูกย้ายต่อหลังรวมแล้วไม่แตะ) แล้วเปิดห้องเดิมกลับตามสถานะเดิม
ทุกฟังก์ชันต้องเรียกภายใน transaction (with db:) ของผู้เรียก
"""
import json

from app import audit, inventory, places

UNDO_HOURS = 24


class MergeError(Exception):
    pass


def blocked_reason(db):
    check = inventory.open_check(db)
    if check:
        return (f"มีรอบตรวจสอบพัสดุปีงบประมาณ {check['fiscal_year']} เปิดอยู่ รวมห้องหรือยกเลิกการรวมไม่ได้ "
                "เพราะใบรายการของรอบแบ่งตามห้อง ปิดรอบก่อนแล้วจึงทำ")
    return None


def _room(db, location_id):
    return db.execute("SELECT id, name, active, merged_into_id FROM locations WHERE id = ?", (location_id,)).fetchone()


def merge(db, user_id, kept_id, merged_ids):
    """รวมห้อง merged_ids เข้าห้อง kept_id คืน (merge_id, จำนวนชิ้นที่ย้าย)"""
    reason = blocked_reason(db)
    if reason:
        raise MergeError(reason)
    merged_ids = list(dict.fromkeys(int(i) for i in merged_ids if int(i) != int(kept_id)))
    if not merged_ids:
        raise MergeError("เลือกห้องอย่างน้อย 2 ห้อง")
    kept = _room(db, kept_id)
    if kept is None or not kept["active"] or kept["merged_into_id"]:
        raise MergeError("ห้องที่จะเก็บไว้ต้องเป็นห้องที่ใช้งานอยู่")
    rooms = []
    for mid in merged_ids:
        r = _room(db, mid)
        if r is None:
            raise MergeError("ไม่พบห้องที่เลือก")
        if r["merged_into_id"]:
            raise MergeError(f"ห้อง “{r['name']}” ถูกรวมไปแล้ว")
        rooms.append(r)

    merge_id = db.execute("INSERT INTO location_merges (kept_id, merged, merged_by) VALUES (?, ?, ?)", (
        kept["id"], json.dumps([{"id": r["id"], "name": r["name"], "active": r["active"]} for r in rooms],
                               ensure_ascii=False), user_id)).lastrowid
    moved = 0
    for r in rooms:
        assets = db.execute("SELECT id, holder_id FROM assets WHERE location_id = ? ORDER BY id", (r["id"],)).fetchall()
        for a in assets:
            db.execute("UPDATE assets SET location_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (kept["id"], a["id"]))
            audit.log(db, user_id, "assets", a["id"], "update", {
                "location_id": {"old": r["id"], "new": kept["id"], "old_label": r["name"], "new_label": kept["name"]},
                "merge_id": merge_id})
            places.record_move(db, a["id"], r["id"], kept["id"], a["holder_id"], a["holder_id"],
                               f"รวมห้อง “{r['name']}” เข้ากับ “{kept['name']}”", reason=places.REASON_MERGE)
            db.execute("INSERT INTO location_merge_items (merge_id, asset_id, from_location_id) VALUES (?, ?, ?)",
                       (merge_id, a["id"], r["id"]))
            moved += 1
        db.execute("UPDATE locations SET active = 0, merged_into_id = ? WHERE id = ?", (kept["id"], r["id"]))
        audit.log(db, user_id, "locations", r["id"], "update", {
            "active": {"old": r["active"], "new": 0}, "merged_into_id": {"old": None, "new": kept["id"]},
            "merged_into_label": kept["name"], "merge_id": merge_id, "assets_moved": len(assets)})
    return merge_id, moved


def get(db, merge_id):
    return db.execute(f"""SELECT m.*, l.name AS kept_name, l.merged_into_id AS kept_merged_into,
                                 datetime(m.merged_at, '+{UNDO_HOURS} hours') > datetime('now') AS undo_open,
                                 (SELECT COUNT(*) FROM location_merge_items i WHERE i.merge_id = m.id) AS n_items
                          FROM location_merges m JOIN locations l ON l.id = m.kept_id WHERE m.id = ?""",
                      (merge_id,)).fetchone()


def recent(db):
    """การรวมที่ยังยกเลิกได้ (ไม่เกิน 1 วัน และยังไม่ได้ยกเลิก) ล่าสุดก่อน"""
    rows = db.execute(f"""SELECT m.id FROM location_merges m
                          WHERE m.undone_at IS NULL AND datetime(m.merged_at, '+{UNDO_HOURS} hours') > datetime('now')
                          ORDER BY m.id DESC""").fetchall()
    out = []
    for r in rows:
        m = dict(get(db, r["id"]))
        m["merged_rooms"] = json.loads(m["merged"])
        out.append(m)
    return out


def undo(db, user_id, merge_id):
    """ยกเลิกการรวม คืน (จำนวนชิ้นที่คืนห้องเดิม, จำนวนชิ้นที่ข้ามเพราะถูกย้ายไปที่อื่นแล้ว)"""
    m = get(db, merge_id)
    if m is None:
        raise MergeError("ไม่พบการรวมห้องนี้")
    if m["undone_at"]:
        raise MergeError("ยกเลิกการรวมนี้ไปแล้ว")
    if not m["undo_open"]:
        raise MergeError(f"เกิน {UNDO_HOURS // 24} วันแล้ว ยกเลิกการรวมไม่ได้ ถ้าจะแยกห้องให้สร้างห้องใหม่แล้วย้ายทีละชิ้น")
    reason = blocked_reason(db)
    if reason:
        raise MergeError(reason)
    if m["kept_merged_into"]:
        raise MergeError(f"ห้อง “{m['kept_name']}” ถูกรวมเข้าห้องอื่นต่อไปแล้ว ยกเลิกการรวมครั้งหลังก่อน")
    rooms = {r["id"]: r for r in json.loads(m["merged"])}
    names = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM locations")}
    back = skipped = 0
    items = db.execute("""SELECT i.asset_id, i.from_location_id, a.location_id, a.holder_id
                          FROM location_merge_items i JOIN assets a ON a.id = i.asset_id
                          WHERE i.merge_id = ? ORDER BY i.asset_id""", (merge_id,)).fetchall()
    for it in items:
        if it["location_id"] != m["kept_id"]:
            skipped += 1
            continue
        frm = it["from_location_id"]
        db.execute("UPDATE assets SET location_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (frm, it["asset_id"]))
        audit.log(db, user_id, "assets", it["asset_id"], "update", {
            "location_id": {"old": m["kept_id"], "new": frm, "old_label": names[m["kept_id"]], "new_label": names[frm]},
            "undo_merge_id": merge_id})
        places.record_move(db, it["asset_id"], m["kept_id"], frm, it["holder_id"], it["holder_id"],
                           f"ยกเลิกการรวมห้อง “{names[frm]}” เข้ากับ “{names[m['kept_id']]}”",
                           reason=places.REASON_MERGE_UNDO)
        back += 1
    for rid, info in rooms.items():
        cur = _room(db, rid)
        if cur is None or cur["merged_into_id"] != m["kept_id"]:
            continue
        db.execute("UPDATE locations SET active = ?, merged_into_id = NULL WHERE id = ?", (info["active"], rid))
        audit.log(db, user_id, "locations", rid, "update", {
            "active": {"old": cur["active"], "new": info["active"]}, "merged_into_id": {"old": m["kept_id"], "new": None},
            "undo_merge_id": merge_id})
    db.execute("UPDATE location_merges SET undone_at = CURRENT_TIMESTAMP, undone_by = ? WHERE id = ?", (user_id, merge_id))
    return back, skipped
