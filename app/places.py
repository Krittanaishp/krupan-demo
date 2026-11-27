"""ห้อง (locations) และผู้รับผิดชอบ (staff): สร้าง ค้นหา บันทึกการย้าย และอ่านข้อความ "ใช้ประจำที่" เดิม"""
import hashlib
import re

from app import audit, timeutil
from app.templating import staff_label

# ---------------------------------------------------------------- อ่านข้อความ location_text เดิม

# "ห้องผู้บริหาร 4 ตัว ห้องประชุม 3 ตัว" / "ห้องทะเบียน (3 ชุด) และห้องประชุม": บอกจำนวนแยกตามที่ = หลายห้องในช่องเดียว
QTY_RE = re.compile(r"[0-9]+\s*(ตัว|เครื่อง|ใบ|ชุด|อัน|คัน|หลัง)")
ROOM_WORD_RE = re.compile(r"ห้อง[^\s,/()]+")
# ชื่อคน: "สนง./เอ็ม" (หลัง /), "นายสมมติ ใจดี", "นางสาว...", "ป. สมมติ ตัวอย่าง"
# ไม่นับ "นายอำเภอ" "ห้องนาย" "หน้าห้อง นาย." ซึ่งหมายถึงตำแหน่ง
TITLE_RE = re.compile(r"(นางสาว|นาง|นาย|น\.ส\.)(?!อำเภอ)(?=[ก-๙])([ก-๙]+(?:\s+[ก-๙]+)?)")
SLASH_NAME_RE = re.compile(r"/\s*([ก-๙][^,/]*)")
PALAD_NAME_RE = re.compile(r"ป\.\s+([ก-๙]+\s+[ก-๙]+)")


def normalize(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def text_key(text):
    """id ของแถวในหน้าจัดห้อง (ข้อความเป็นภาษาไทยและมีอักขระพิเศษ ใช้เป็น id ตรง ๆ ไม่ได้)"""
    return "m-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def is_multi_room(text):
    if QTY_RE.search(text):
        return True
    return len(set(ROOM_WORD_RE.findall(text))) >= 2


def person_in(text):
    """คืน (ชื่อเล่น, ชื่อจริง) ที่เดาได้จากข้อความ หรือ None ถ้าไม่พบชื่อคน"""
    m = SLASH_NAME_RE.search(text)
    if m:
        name = normalize(m.group(1))
        if TITLE_RE.match(name):  # "/นายทดลอง"
            return "", name
        return name, ""
    m = TITLE_RE.search(text) or PALAD_NAME_RE.search(text)
    if m:
        return "", normalize(m.group(0))
    return None


def suggest_room(text):
    """ชื่อห้องที่เดาจากข้อความ: ตัดคำซ้ำ ตัดชื่อคน ตัดคำว่า 'ใช้ประจำที่'"""
    s = SLASH_NAME_RE.sub("", text)
    s = TITLE_RE.sub("", s)
    s = PALAD_NAME_RE.sub("", s)
    s = re.sub(r"^ใช้ประจำที่\s*", "", normalize(s))
    words = []
    for w in s.split(" "):
        if w and w not in words:
            words.append(w)
    return " ".join(words).strip(" ,")


# ---------------------------------------------------------------- ห้อง / ผู้รับผิดชอบ

def location_options(db, include_id=None):
    """ห้องที่เลือกได้: ที่ใช้งานอยู่ + ห้องปัจจุบันของรายการ (แม้ถูกปิดใช้งานแล้ว)"""
    return db.execute("SELECT id, name, active FROM locations WHERE active = 1 OR id = ? ORDER BY name",
                      (include_id,)).fetchall()


def staff_options(db, include_id=None):
    return db.execute("""SELECT id, full_name, nickname, position, active FROM staff
                         WHERE active = 1 OR id = ? ORDER BY COALESCE(nickname, full_name)""",
                      (include_id,)).fetchall()


def location_name(db, location_id):
    if location_id is None:
        return None
    row = db.execute("SELECT name FROM locations WHERE id = ?", (location_id,)).fetchone()
    return row["name"] if row else None


def staff_name(db, staff_id):
    if staff_id is None:
        return None
    row = db.execute("SELECT full_name, nickname FROM staff WHERE id = ?", (staff_id,)).fetchone()
    return staff_label(row) if row else None


def create_location(db, user_id, name):
    """คืน (id, สร้างใหม่หรือไม่) ถ้ามีชื่อนี้อยู่แล้วใช้ห้องเดิม (ต้องเรียกภายใน transaction)"""
    name = normalize(name)
    row = db.execute("SELECT id FROM locations WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"], False
    lid = db.execute("INSERT INTO locations (name) VALUES (?)", (name,)).lastrowid
    audit.log(db, user_id, "locations", lid, "insert", {"name": {"old": None, "new": name}})
    return lid, True


def create_staff(db, user_id, nickname, full_name, position=None):
    nickname, full_name, position = normalize(nickname) or None, normalize(full_name) or None, normalize(position) or None
    sid = db.execute("INSERT INTO staff (full_name, nickname, position) VALUES (?, ?, ?)",
                     (full_name, nickname, position)).lastrowid
    audit.log(db, user_id, "staff", sid, "insert",
              {k: {"old": None, "new": v} for k, v in
               (("nickname", nickname), ("full_name", full_name), ("position", position)) if v})
    return sid


def label_changes(db, changes):
    """ใส่ชื่อห้อง/ผู้รับผิดชอบ ณ เวลาที่แก้ ลงใน changes ของ audit_log (เก็บแค่ id จะอ่านไม่รู้เรื่องถ้าชื่อเปลี่ยน)"""
    for field, namer in (("location_id", location_name), ("holder_id", staff_name)):
        if field in changes:
            ch = changes[field]
            ch["old_label"] = namer(db, ch["old"])
            ch["new_label"] = namer(db, ch["new"])
    return changes


# เหตุผลใน asset_moves.reason: รายงาน "การย้ายจริง" ให้นับเฉพาะ REAL_MOVE_REASONS
REASON_FIRST = "จัดห้องครั้งแรก"        # ยังไม่เคยมีห้อง -> ได้ห้อง (ไม่ใช่การย้ายจริง)
REASON_REMAP = "แก้การจัดห้อง"          # เปลี่ยนห้องที่จัดไว้จากหน้าจัดห้อง (แก้การจัดกลุ่ม ไม่ใช่การย้ายของจริง)
REASON_MOVE = "ย้ายห้อง"
REASON_HOLDER = "เปลี่ยนผู้รับผิดชอบ"
REASON_FOUND = "พบระหว่างตรวจสอบพัสดุ"        # ย้ายในทะเบียนตามห้องที่พบตอนตรวจสอบพัสดุประจำปี
REASON_MERGE = "รวมห้องซ้ำ"               # ห้องเดียวกันที่สร้างไว้ 2 ชื่อ รวมเป็นห้องเดียว (ไม่ใช่การย้ายจริง)
REASON_MERGE_UNDO = "ยกเลิกการรวมห้อง"    # คืนกลับห้องเดิมหลังยกเลิกการรวม (ไม่ใช่การย้ายจริง)
REASONS = (REASON_FIRST, REASON_REMAP, REASON_MOVE, REASON_HOLDER, REASON_FOUND, REASON_MERGE, REASON_MERGE_UNDO)
REAL_MOVE_REASONS = (REASON_MOVE, REASON_HOLDER)
NOT_REAL_MOVE_REASONS = tuple(r for r in REASONS if r not in REAL_MOVE_REASONS)
MAPPING_NOTE_PREFIX = "จัดเข้าห้องจากข้อความเดิม"


def move_reason(old_loc, new_loc, from_mapping=False):
    if old_loc is None and new_loc is not None:
        return REASON_FIRST
    if old_loc == new_loc:
        return REASON_HOLDER
    return REASON_REMAP if from_mapping else REASON_MOVE


def record_move(db, asset_id, old_loc, new_loc, old_holder, new_holder, note, from_mapping=False, reason=None):
    """บันทึก asset_moves เมื่อห้องหรือผู้รับผิดชอบเปลี่ยน (วันที่ = วันนี้ตามเวลาไทย)
    reason: ระบุเหตุผลเอง (เช่น พบระหว่างตรวจสอบพัสดุ) ไม่อย่างนั้นเดาจากห้องเดิม/ใหม่"""
    if old_loc == new_loc and old_holder == new_holder:
        return None
    return db.execute(
        """INSERT INTO asset_moves (asset_id, moved_at, from_location_id, to_location_id,
                                    from_holder_id, to_holder_id, note, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (asset_id, timeutil.today_th().isoformat(), old_loc, new_loc, old_holder, new_holder, note,
         reason or move_reason(old_loc, new_loc, from_mapping))).lastrowid
