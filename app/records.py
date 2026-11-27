"""ประวัติการซ่อม และการจำหน่ายครุภัณฑ์ (ทุกการเปลี่ยนแปลงบันทึก audit_log ไว้ในประวัติของครุภัณฑ์ชิ้นนั้น)"""
from app import audit, places, timeutil

DISPOSED = "จำหน่ายแล้ว"
METHODS = ("ขาย", "แลกเปลี่ยน", "โอน", "แปรสภาพหรือทำลาย")
MAX_LOT = 1000


class RecordError(Exception):
    """ข้อความแสดงให้ผู้ใช้เห็นได้"""


def now_text():
    return timeutil.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def clean(v, limit, label):
    v = places.normalize(v)
    if len(v) > limit:
        raise RecordError(f"{label}ยาวเกินไป (ไม่เกิน {limit} ตัวอักษร)")
    return v or None


# ---------------------------------------------------------------- ประวัติซ่อม

REPAIR_FIELDS = (("repaired_at", "repaired_at"), ("description", "repair_description"),
                 ("cost", "repair_cost"), ("note", "repair_note"))


def repairs(db, asset_id):
    return db.execute("SELECT * FROM repairs WHERE asset_id = ? ORDER BY repaired_at DESC, id DESC",
                      (asset_id,)).fetchall()


def repair_values(repaired_at, description, cost, note):
    description = clean(description, 500, "รายการซ่อม")
    if not description:
        raise RecordError("กรอกรายการซ่อม")
    return {"repaired_at": repaired_at, "description": description, "cost": cost, "note": clean(note, 1000, "หมายเหตุ")}


def repair_changes(old, new):
    out = {}
    for field, key in REPAIR_FIELDS:
        o = old[field] if old is not None else None
        if o != new.get(field):
            out[key] = {"old": o, "new": new.get(field)}
    return out


def add_repair(db, user_id, asset_id, values):
    with db:
        rid = db.execute("INSERT INTO repairs (asset_id, repaired_at, description, cost, note) VALUES (?, ?, ?, ?, ?)",
                         (asset_id, values["repaired_at"], values["description"], values["cost"], values["note"])).lastrowid
        audit.log(db, user_id, "assets", asset_id, "repair_insert", {"repair_id": rid, **repair_changes(None, values)})
    return rid


def get_repair(db, asset_id, repair_id):
    return db.execute("SELECT * FROM repairs WHERE id = ? AND asset_id = ?", (repair_id, asset_id)).fetchone()


def update_repair(db, user_id, row, values):
    changes = repair_changes(row, values)
    if not changes:
        return {}
    with db:
        db.execute("UPDATE repairs SET repaired_at = ?, description = ?, cost = ?, note = ? WHERE id = ?",
                   (values["repaired_at"], values["description"], values["cost"], values["note"], row["id"]))
        audit.log(db, user_id, "assets", row["asset_id"], "repair_update", {"repair_id": row["id"], **changes})
    return changes


def delete_repair(db, user_id, row):
    with db:
        db.execute("DELETE FROM repairs WHERE id = ?", (row["id"],))
        audit.log(db, user_id, "assets", row["asset_id"], "repair_delete",
                  {"repair_id": row["id"], **{key: {"old": row[f], "new": None} for f, key in REPAIR_FIELDS}})


# ---------------------------------------------------------------- การจำหน่าย

def active_disposal(db, asset_id):
    return db.execute("""SELECT d.*, COALESCE(u.display_name, u.username) AS who,
                                (SELECT COUNT(*) FROM disposals x WHERE x.lot_id = d.lot_id AND x.cancelled_at IS NULL) AS lot_size
                         FROM disposals d LEFT JOIN users u ON u.id = d.created_by
                         WHERE d.asset_id = ? AND d.cancelled_at IS NULL""", (asset_id,)).fetchone()


def disposal_history(db, asset_id):
    return db.execute("""SELECT d.*, COALESCE(u.display_name, u.username) AS who,
                                COALESCE(c.display_name, c.username) AS cancelled_who
                         FROM disposals d LEFT JOIN users u ON u.id = d.created_by
                         LEFT JOIN users c ON c.id = d.cancelled_by
                         WHERE d.asset_id = ? ORDER BY d.id DESC""", (asset_id,)).fetchall()


def dispose(db, user_id, asset_ids, disposed_at, method, doc_ref, amount, note):
    """จำหน่ายหลายชิ้นพร้อมกันภายใต้หนังสืออนุมัติฉบับเดียว คืน (lot_id, จำนวนชิ้น)
    amount = จำนวนเงินที่ได้ทั้งชุด (เก็บไว้ที่แถวแรกของชุด)"""
    doc_ref = clean(doc_ref, 200, "เลขที่หนังสืออนุมัติ")
    if not doc_ref:
        raise RecordError("กรอกเลขที่หนังสืออนุมัติให้จำหน่าย")
    if method not in METHODS:
        raise RecordError("เลือกวิธีจำหน่าย")
    note = clean(note, 1000, "หมายเหตุ")
    ids = sorted(set(asset_ids))
    if not ids:
        raise RecordError("ยังไม่ได้เลือกครุภัณฑ์")
    if len(ids) > MAX_LOT:
        raise RecordError(f"จำหน่ายพร้อมกันได้ไม่เกิน {MAX_LOT} ชิ้น")
    rows = db.execute(f"SELECT id, reg_no, temp_code, status FROM assets WHERE id IN ({','.join('?' * len(ids))})",
                      ids).fetchall()
    if len(rows) != len(ids):
        raise RecordError("ไม่พบครุภัณฑ์บางชิ้นที่เลือก")
    done = [r["reg_no"] or r["temp_code"] for r in rows if r["status"] == DISPOSED]
    if done:
        raise RecordError("จำหน่ายไปแล้ว: " + ", ".join(done[:10]))
    lot_id = None
    now = now_text()
    with db:
        for i, r in enumerate(rows):
            did = db.execute("""INSERT INTO disposals (asset_id, disposed_at, method, doc_ref, amount, note, lot_id,
                                                       status_before, created_by, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                             (r["id"], disposed_at, method, doc_ref, amount if i == 0 else None, note, lot_id,
                              r["status"], user_id, now)).lastrowid
            if lot_id is None:
                lot_id = did
                db.execute("UPDATE disposals SET lot_id = ? WHERE id = ?", (did, did))
            db.execute("UPDATE assets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (DISPOSED, r["id"]))
            audit.log(db, user_id, "assets", r["id"], "dispose", {
                "status": {"old": r["status"], "new": DISPOSED}, "disposal_id": did, "lot_id": lot_id, "lot_size": len(rows),
                "disposed_at": {"old": None, "new": disposed_at}, "disposal_method": {"old": None, "new": method},
                "doc_ref": {"old": None, "new": doc_ref},
                **({"disposal_amount": {"old": None, "new": amount}} if i == 0 and amount is not None else {})})
    return lot_id, len(rows)


def get_disposal(db, disposal_id):
    return db.execute("""SELECT d.*, a.reg_no, a.temp_code, a.name, a.status FROM disposals d
                         JOIN assets a ON a.id = d.asset_id WHERE d.id = ?""", (disposal_id,)).fetchone()


def cancel_disposal(db, user_id, d, reason):
    """ยกเลิกการจำหน่าย (admin) คืนสถานะเดิม ถ้าเป็นแถวที่เก็บยอดเงินของชุด ย้ายยอดไปแถวอื่นในชุดที่ยังไม่ยกเลิก"""
    reason = clean(reason, 500, "เหตุผล")
    if not reason:
        raise RecordError("กรอกเหตุผลที่ยกเลิกการจำหน่าย")
    if d["cancelled_at"] is not None:
        raise RecordError("รายการนี้ถูกยกเลิกไปแล้ว")
    restore = d["status_before"] if d["status_before"] and d["status_before"] != DISPOSED else "ใช้งาน"
    with db:
        db.execute("UPDATE disposals SET cancelled_at = ?, cancelled_by = ?, cancel_reason = ? WHERE id = ?",
                   (now_text(), user_id, reason, d["id"]))
        if d["amount"] is not None:
            nxt = db.execute("""SELECT id FROM disposals WHERE lot_id = ? AND cancelled_at IS NULL ORDER BY id LIMIT 1""",
                             (d["lot_id"],)).fetchone()
            if nxt:
                db.execute("UPDATE disposals SET amount = ? WHERE id = ?", (d["amount"], nxt["id"]))
                db.execute("UPDATE disposals SET amount = NULL WHERE id = ?", (d["id"],))
        db.execute("UPDATE assets SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (restore, d["asset_id"]))
        audit.log(db, user_id, "assets", d["asset_id"], "dispose_cancel", {
            "status": {"old": d["status"], "new": restore}, "disposal_id": d["id"], "doc_ref": d["doc_ref"],
            "cancel_reason": {"old": None, "new": reason}})
    return restore
