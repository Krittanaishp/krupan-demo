"""บันทึก audit_log: ใครแก้อะไร เมื่อไร ค่าเดิม -> ค่าใหม่"""
import json

# ผู้ใช้สมมติสำหรับการแก้ข้อมูลด้วยสคริปต์ (ล็อกอินไม่ได้: hash '!' ไม่ใช่ hash ที่ถูกต้อง และปิดใช้งาน)
SYSTEM_USERNAME = "system-migration"
UNUSABLE_PASSWORD_HASH = "!"


def diff(old, new, fields):
    """คืน {field: {"old": ..., "new": ...}} เฉพาะช่องที่ค่าเปลี่ยน"""
    return {f: {"old": old[f], "new": new[f]} for f in fields if old[f] != new[f]}


def log(db, user_id, table_name, record_id, action, changes):
    cur = db.execute(
        "INSERT INTO audit_log (user_id, table_name, record_id, action, changes) VALUES (?, ?, ?, ?, ?)",
        (user_id, table_name, record_id, action, json.dumps(changes, ensure_ascii=False)),
    )
    return cur.lastrowid


def system_user_id(db, username=SYSTEM_USERNAME, display_name="ระบบ (สคริปต์แก้ข้อมูล)"):
    """id ของผู้ใช้สมมติ (ค่าเริ่มต้น system-migration) สร้างให้ถ้ายังไม่มี เรียกภายใน transaction ของสคริปต์"""
    row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row:
        return row[0]
    has_active = "active" in {r[1] for r in db.execute("PRAGMA table_info(users)")}
    cur = db.execute(
        "INSERT INTO users (username, password_hash, display_name, role) VALUES (?, ?, ?, 'viewer')",
        (username, UNUSABLE_PASSWORD_HASH, display_name))
    if has_active:
        db.execute("UPDATE users SET active = 0 WHERE id = ?", (cur.lastrowid,))
    return cur.lastrowid
