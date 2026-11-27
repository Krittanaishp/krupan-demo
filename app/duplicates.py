"""รายการซ้ำ: ครุภัณฑ์ชิ้นเดียวกันที่ลงทะเบียนไว้ 2 ครั้ง (admin ตั้ง/ยกเลิก ต้องใส่เหตุผล บันทึก audit_log)

- เก็บใน assets.duplicate_of_id (ซ้ำกับรายการไหน) + duplicate_reason ไม่เปลี่ยน assets.status
  (status มี CHECK ใน schema และยกเลิกแล้วต้องกลับเป็นสถานะเดิม) หน้าเว็บแสดงสถานะเป็น "รายการซ้ำ"
- รายการซ้ำไม่นับในรายงานทะเบียน สรุป Export (แยกชีต) และไม่เข้ารอบตรวจสอบพัสดุที่เปิดใหม่
- ตั้งแล้วล้างป้าย "ลงทะเบียนซ้ำกับ <อีกรายการ> ..." ของทั้งสองรายการให้เอง (เลือกเลขที่ถูกต้องแล้ว)
ทุกฟังก์ชันต้องเรียกภายใน transaction (with db:) ของผู้เรียก
"""
from app import audit, flags

LABEL = "รายการซ้ำ"
MAX_REASON = 500
NOT_DUPLICATE = "a.duplicate_of_id IS NULL"  # เงื่อนไข SQL ของรายการที่นับในรายงาน (alias ของ assets = a)


class DuplicateError(Exception):
    pass


def code_of(row):
    return row["reg_no"] or row["temp_code"]


def find_by_code(db, code):
    code = (code or "").strip()
    if not code:
        return None
    return db.execute("SELECT * FROM assets WHERE reg_no = ? OR temp_code = ? ORDER BY reg_no = ? DESC LIMIT 1",
                      (code, code, code)).fetchone()


def pointing_to(db, asset_id):
    """รายการที่ถูกตั้งว่าซ้ำกับรายการนี้"""
    return db.execute("SELECT id, reg_no, temp_code, name FROM assets WHERE duplicate_of_id = ? ORDER BY id",
                      (asset_id,)).fetchall()


def _drop_dup_flags(db, user_id, asset, other_code):
    keep = [f for f in flags.split(asset["needs_review"])
            if not (f.startswith(flags.DUP_REG_PREFIX) and other_code in f)]
    new = flags.join(keep)
    if new != asset["needs_review"]:
        db.execute("UPDATE assets SET needs_review = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (new, asset["id"]))
        return {"needs_review": {"old": asset["needs_review"], "new": new}}
    return {}


def mark(db, user_id, asset, target_code, reason):
    """ตั้ง asset เป็นรายการซ้ำของรายการที่มีรหัส target_code คืนรายการหลัก"""
    reason = " ".join((reason or "").split())
    if asset["duplicate_of_id"]:
        raise DuplicateError("รายการนี้เป็นรายการซ้ำอยู่แล้ว ยกเลิกก่อนถ้าจะเปลี่ยน")
    if not reason:
        raise DuplicateError("ใส่เหตุผลที่ตั้งเป็นรายการซ้ำ")
    if len(reason) > MAX_REASON:
        raise DuplicateError(f"เหตุผลยาวเกิน {MAX_REASON} ตัวอักษร")
    target = find_by_code(db, target_code)
    if target is None:
        raise DuplicateError(f"ไม่พบรายการ “{target_code.strip()}” ใส่เลขทะเบียนหรือรหัสชั่วคราวของรายการที่ถูกต้อง")
    if target["id"] == asset["id"]:
        raise DuplicateError("เลือกรายการอื่น (ซ้ำกับตัวเองไม่ได้)")
    if target["duplicate_of_id"]:
        raise DuplicateError(f"“{code_of(target)}” เป็นรายการซ้ำอยู่แล้ว ให้เลือกรายการหลักแทน")
    if pointing_to(db, asset["id"]):
        raise DuplicateError("มีรายการอื่นตั้งว่าซ้ำกับรายการนี้อยู่ ยกเลิกรายการเหล่านั้นก่อน")
    db.execute("UPDATE assets SET duplicate_of_id = ?, duplicate_reason = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
               (target["id"], reason, asset["id"]))
    changes = {"duplicate_of_id": {"old": None, "new": target["id"], "new_label": code_of(target)},
               "duplicate_reason": {"old": None, "new": reason}}
    changes.update(_drop_dup_flags(db, user_id, asset, code_of(target)))
    audit.log(db, user_id, "assets", asset["id"], "update", changes)
    other = _drop_dup_flags(db, user_id, target, code_of(asset))
    if other:
        audit.log(db, user_id, "assets", target["id"], "update", {**other, "via": f"ตั้ง {code_of(asset)} เป็นรายการซ้ำ"})
    return target


def unmark(db, user_id, asset, reason):
    reason = " ".join((reason or "").split())
    if not asset["duplicate_of_id"]:
        raise DuplicateError("รายการนี้ไม่ได้เป็นรายการซ้ำ")
    if not reason:
        raise DuplicateError("ใส่เหตุผลที่ยกเลิก")
    if len(reason) > MAX_REASON:
        raise DuplicateError(f"เหตุผลยาวเกิน {MAX_REASON} ตัวอักษร")
    target = db.execute("SELECT reg_no, temp_code FROM assets WHERE id = ?", (asset["duplicate_of_id"],)).fetchone()
    db.execute("UPDATE assets SET duplicate_of_id = NULL, duplicate_reason = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
               (asset["id"],))
    audit.log(db, user_id, "assets", asset["id"], "update", {
        "duplicate_of_id": {"old": asset["duplicate_of_id"], "new": None, "old_label": code_of(target) if target else None},
        "duplicate_reason": {"old": asset["duplicate_reason"], "new": None}, "cancel_reason": reason})
