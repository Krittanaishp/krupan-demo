"""จัดกลุ่มข้อความ "ใช้ประจำที่" เดิมที่น่าจะเป็นห้องเดียวกัน: ระบบเสนอ คนยืนยัน ระบบไม่รวมเอง

ลำดับการตัดสินว่าข้อความไปห้องไหน
1. ตาราง "ข้อความเดิม → ห้องที่ถูกต้อง" (location_text_rules) แบบตรงทุกตัวอักษร (exact = 1)
2. ตารางเดียวกันแบบเทียบหลัง normalize (exact = 0)
3. เดาจากการ normalize (group_key) ข้อความที่ได้ key เดียวกันอยู่กลุ่มเดียวกัน

normalize (group_key)
- ตัดชื่อคน (หลัง / , นาย/นาง/น.ส. ชื่อ, "ป. ชื่อ สกุล"), คำว่า "ใช้ประจำที่" และคำที่ซ้ำกันในข้อความเดียว (places.suggest_room)
- ขยายตัวย่อตามตาราง (ตั้งค่า > ห้อง) ตัวย่อที่ติ๊ก "ท้ายคำ" ขยายเฉพาะเมื่ออยู่ท้ายคำ (เช่น อ. = อำเภอ)
- ตัดช่องว่าง จุด และ / แล้วตัดคำว่า "ห้อง" และ "อำเภอ" ทุกตำแหน่ง
- **เก็บวงเล็บและคำในวงเล็บไว้** ("ห้องมั่นคง (ชั้นบน)" ต้องไม่ชนกับ "ห้องมั่นคง")

ตัวย่อที่อ่านได้หลายแบบ (ภาษาไทยไม่มีช่องว่างระหว่างคำ เช่น "ทะเบียนอ." = ทะเบียน + อ. หรือ ทะเบีย + นอ.)
เลือกแบบที่ได้ key ตรงกับข้อความอื่นที่อ่านได้แบบเดียว หรือตรงกับชื่อห้องที่มีอยู่ ถ้าไม่ตรงใครใช้แบบที่ขยายมากที่สุด
"""
import hashlib
import itertools
import re
from collections import Counter, defaultdict

from app import places, settings_store

ABBR_KEY = "LOCATION_ABBREVIATIONS"
ROLE_KEY = "LOCATION_ROLE_WORDS"
PLACE_KEY = "LOCATION_PLACE_WORDS"

# ค่าเริ่มต้น (ใช้เมื่อยังไม่มีค่าใน app_settings) migration 009 ใส่ค่าเดียวกันลง db
DEFAULT_ABBREVIATIONS = [
    {"short": "ปค.", "full": "ปกครอง", "word_end": False},
    {"short": "มค.", "full": "มั่นคง", "word_end": False},
    {"short": "สมต.", "full": "เสมียนตรา", "word_end": False},
    {"short": "นอ.", "full": "นายอำเภอ", "word_end": False},
    {"short": "อส.", "full": "อาสารักษาดินแดน", "word_end": False},
    {"short": "สนง.", "full": "สำนักงาน", "word_end": False},
    {"short": "ทบ.", "full": "ทะเบียน", "word_end": False},
    {"short": "อ.", "full": "อำเภอ", "word_end": True},
    {"short": "ศูนย์ฯ", "full": "ศูนย์ดำรงธรรม", "word_end": False},
    {"short": "หัอง", "full": "ห้อง", "word_end": False},
    {"short": "อาวุธโส", "full": "อาวุโส", "word_end": False},
]
# คำที่บอกว่าข้อความน่าจะเป็นตำแหน่ง/คน (ต้องขึ้นต้นคำ) เมื่อข้อความไม่มีคำบอกสถานที่เลย
DEFAULT_ROLE_WORDS = ["นายอำเภอ", "ปลัดอำเภอ", "ปลัดอาวุโส", "ปลัด", "ป."]
DEFAULT_PLACE_WORDS = ["ห้อง", "หน้าห้อง", "บ้านพัก", "กองร้อย", "ศูนย์", "หอ"]

WORD_END_CHARS = " /,()"
MAX_ABBR_MATCHES = 8  # กันข้อความแปลก ๆ ที่มีตัวย่อมากจนจำนวนแบบที่อ่านได้ระเบิด


# ---------------------------------------------------------------- ค่าตั้งค่า

def _clean_abbr(items):
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        short, full = str(it.get("short") or "").strip(), str(it.get("full") or "").strip()
        if short and full and short != full:
            out.append({"short": short, "full": full, "word_end": bool(it.get("word_end"))})
    return out


def _clean_words(items):
    return [w for w in (str(x).strip() for x in (items or [])) if w]


def abbreviations(db):
    saved = settings_store.get(db, ABBR_KEY)
    return _clean_abbr(saved) if isinstance(saved, list) else list(DEFAULT_ABBREVIATIONS)


def role_words(db):
    saved = settings_store.get(db, ROLE_KEY)
    return _clean_words(saved) if isinstance(saved, list) else list(DEFAULT_ROLE_WORDS)


def place_words(db):
    saved = settings_store.get(db, PLACE_KEY)
    return _clean_words(saved) if isinstance(saved, list) else list(DEFAULT_PLACE_WORDS)


def rules(db):
    return db.execute("""SELECT id, match_text, exact, room_name, holder_hint, note, active
                         FROM location_text_rules ORDER BY exact DESC, match_text""").fetchall()


# ---------------------------------------------------------------- ตัวแก้ไขตารางในหน้าตั้งค่า (ข้อความหลายบรรทัด)

def abbr_to_text(items):
    return "\n".join(f"{a['short']} = {a['full']}" + (" (ท้ายคำ)" if a["word_end"] else "") for a in items)


def parse_abbr_text(text):
    """'ปค. = ปกครอง' ต่อบรรทัด ต่อท้ายด้วย '(ท้ายคำ)' ได้ คืน (รายการ, [ข้อผิดพลาด])"""
    items, errors, seen = [], [], set()
    for i, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        word_end = False
        m = re.search(r"\(\s*ท้ายคำ\s*\)\s*$", line)
        if m:
            word_end, line = True, line[:m.start()].strip()
        if "=" not in line:
            errors.append(f"บรรทัด {i}: ต้องเป็นรูปแบบ “ตัวย่อ = คำเต็ม”")
            continue
        short, full = (s.strip() for s in line.split("=", 1))
        if not short or not full:
            errors.append(f"บรรทัด {i}: ต้องมีทั้งตัวย่อและคำเต็ม")
        elif short == full:
            errors.append(f"บรรทัด {i}: ตัวย่อกับคำเต็มเหมือนกัน")
        elif short in seen:
            errors.append(f"บรรทัด {i}: ตัวย่อ “{short}” ซ้ำ")
        elif len(short) > 40 or len(full) > 100:
            errors.append(f"บรรทัด {i}: ยาวเกินไป")
        else:
            seen.add(short)
            items.append({"short": short, "full": full, "word_end": word_end})
    return items, errors


def parse_words_text(text):
    words = []
    for line in (text or "").splitlines():
        w = line.strip()
        if w and w not in words and len(w) <= 60:
            words.append(w)
    return words


# ---------------------------------------------------------------- normalize

def squash(s):
    """ตัดช่องว่าง จุด / แล้วตัดคำว่า ห้อง และ อำเภอ ทุกตำแหน่ง (วงเล็บเก็บไว้)"""
    s = re.sub(r"[\s./]+", "", s or "")
    return s.replace("ห้อง", "").replace("อำเภอ", "")


def _matches(s, abbr):
    out = []
    for a in abbr:
        for m in re.finditer(re.escape(a["short"]), s):
            if a["word_end"] and not (m.end() == len(s) or s[m.end()] in WORD_END_CHARS):
                continue
            out.append((m.start(), m.end(), a["full"]))
    out.sort()
    return out[:MAX_ABBR_MATCHES]


def readings(text, abbr):
    """ทุกแบบที่อ่านได้ [(key, จำนวนตัวย่อที่ขยาย, ข้อความเต็ม)] แบบแรก = ขยายมากที่สุด"""
    s = places.suggest_room(text) or places.normalize(text)
    ms = _matches(s, abbr)
    out, seen = [], set()
    for n in range(len(ms), -1, -1):
        for combo in itertools.combinations(ms, n):
            if any(a[1] > b[0] for a, b in zip(combo, combo[1:])):
                continue  # ตัวย่อทับกัน
            full, pos = "", 0
            for st, en, rep in combo:
                full += s[pos:st] + rep
                pos = en
            full = places.normalize(full + s[pos:])
            if full not in seen:
                seen.add(full)
                out.append((squash(full), n, full))
    return out


def expand(text, abbr):
    """ข้อความเต็มแบบที่ขยายมากที่สุด (ใช้ตรวจคำตำแหน่ง และเสนอชื่อห้อง)"""
    return readings(text, abbr)[0][2]


def group_key(text, abbr):
    """key ของข้อความเดี่ยว ๆ (ไม่ดูข้อความอื่น) ใช้กับข้อความในตาราง "ข้อความเดิม → ห้อง" """
    return readings(text, abbr)[0][0]


def looks_like_role(text, abbr, roles, place):
    """ข้อความที่ไม่มีคำบอกสถานที่เลย และมีคำตำแหน่งขึ้นต้นคำ เช่น "นายอำเภอ", "ป.อาวุโส", "ประจำตำแหน่ง ปลัดอำเภอ" """
    full = expand(text, abbr)
    if any(p in full for p in place):
        return False
    return any(tok.startswith(r) for tok in full.split(" ") for r in roles)


def resolve_keys(texts, abbr, room_names=()):
    """{ข้อความ: key} โดยเลือกแบบที่อ่านได้ให้ตรงกับข้อความอื่นถ้าตัวย่อกำกวม"""
    reads = {t: readings(t, abbr) for t in texts}
    sure = {}
    for t, rd in reads.items():
        top = {k for k, n, _ in rd if n == rd[0][1]}
        if len(top) == 1:
            sure[t] = rd[0][0]
    by_key = defaultdict(set)
    for t, k in sure.items():
        by_key[k].add(t)
    room_keys = {squash(expand(name, abbr)) for name in room_names}
    out = {}
    for t, rd in reads.items():
        if t in sure:
            out[t] = sure[t]
            continue
        hit = [k for k, n, _ in rd if n > 0 and ((by_key[k] - {t}) or k in room_keys)]
        out[t] = hit[0] if hit else rd[0][0]
    return out


def full_name_for_key(text, key, abbr):
    for k, _, full in readings(text, abbr):
        if k == key:
            return full
    return expand(text, abbr)


def shorten(name, limit=40):
    name = places.normalize(name)
    return name if len(name) <= limit else name[:limit - 1].rstrip() + "…"


# ---------------------------------------------------------------- จับคู่กับตาราง "ข้อความเดิม → ห้อง"

def match_rule(text, key, rule_rows, abbr):
    """กฎที่ใช้กับข้อความนี้ (ตรงทุกตัวอักษรก่อน แล้วจึงเทียบหลัง normalize) หรือ None"""
    stripped = (text or "").strip()
    active = [r for r in rule_rows if r["active"]]
    for r in active:
        if r["exact"] and r["match_text"].strip() == stripped:
            return r
    for r in active:
        if not r["exact"] and group_key(r["match_text"], abbr) == key:
            return r
    return None


# ---------------------------------------------------------------- สร้างกลุ่มที่เสนอ

def staff_for_hint(db, hint):
    """ผู้รับผิดชอบที่ตรงกับคำใบ้ (ชื่อเล่น ชื่อจริง หรือตำแหน่ง) เฉพาะที่ใช้งานอยู่"""
    if not hint:
        return None
    row = db.execute("""SELECT id FROM staff WHERE active = 1 AND (nickname = ? OR full_name = ? OR position = ?)
                        ORDER BY (nickname = ?) DESC, id LIMIT 1""", (hint, hint, hint, hint)).fetchone()
    return row["id"] if row else None


def room_by_name(db, name):
    """ห้องที่ชื่อนี้ (ถ้าถูกรวมไปแล้ว ใช้ห้องที่รวมเข้าไป) หรือ None"""
    row = db.execute("SELECT id, active, merged_into_id FROM locations WHERE name = ?", (name,)).fetchone()
    seen = set()
    while row is not None and row["merged_into_id"] and row["id"] not in seen:
        seen.add(row["id"])
        row = db.execute("SELECT id, active, merged_into_id FROM locations WHERE id = ?", (row["merged_into_id"],)).fetchone()
    return row["id"] if row and row["active"] else None


def group_id(kind, key):
    return "g-" + hashlib.sha1(f"{kind}|{key}".encode("utf-8")).hexdigest()[:12]


def build_groups(db, rows):
    """กลุ่มที่เสนอจากแถวของหน้าจัดห้อง (locations.mapping_rows) ข้อความที่มีหลายห้องในช่องเดียวไม่เข้ากลุ่ม

    คืนรายการกลุ่ม เรียงจากจำนวนชิ้นมากไปน้อย แต่ละกลุ่ม:
      id, kind ("rule" / "guess"), rule, key, target_id, target_name, holder_hint, holder_id,
      texts [{row, checked, locked, notes}], n, n_todo (ชิ้นที่ยังไม่อยู่ห้องปลายทาง), show
    """
    abbr, roles, place = abbreviations(db), role_words(db), place_words(db)
    rule_rows = rules(db)
    loc_rows = db.execute("SELECT id, name, active, merged_into_id FROM locations").fetchall()
    loc_names = {r["id"]: r["name"] for r in loc_rows}
    usable = [r for r in loc_rows if r["active"] and not r["merged_into_id"]]
    candidates = [r for r in rows if not r["multi"] and r["text"].strip() not in ("", "-")]
    keys = resolve_keys([r["text"] for r in candidates], abbr, [r["name"] for r in usable])

    buckets = defaultdict(list)
    for r in candidates:
        key = keys[r["text"]]
        rule = match_rule(r["text"], key, rule_rows, abbr)
        if rule:
            buckets[("rule", rule["room_name"], rule["holder_hint"] or "")].append((r, rule))
        else:
            buckets[("guess", key, "")].append((r, None))

    room_key_ids = {}
    for r in usable:
        room_key_ids.setdefault(squash(expand(r["name"], abbr)), r["id"])

    groups = []
    for (kind, key, hint), members in buckets.items():
        if kind == "guess" and len(members) < 2:
            continue
        member_rows = [m[0] for m in members]
        if kind == "rule":
            rule = members[0][1]
            target_id = room_by_name(db, rule["room_name"])
            target_name = rule["room_name"]
        else:
            rule = None
            by_room = Counter()
            for r in member_rows:
                for loc_id, n in r["locs"].items():
                    if loc_id is not None:
                        by_room[loc_id] += n
            usable_ids = {u["id"] for u in usable}
            mapped = [lid for lid, _ in by_room.most_common() if lid in usable_ids]
            target_id = mapped[0] if mapped else room_key_ids.get(key)
            # ชื่อเต็มที่ยาวที่สุดในกลุ่ม (ขยายตัวย่อแล้ว) ผู้ใช้แก้ได้ก่อนกด
            target_name = max((full_name_for_key(r["text"], key, abbr) for r in member_rows), key=len)
        if target_id is not None:
            target_name = loc_names[target_id]

        texts = []
        for r, rule_hit in members:
            notes, checked, locked = [], True, False
            in_target = r["locs"].get(target_id, 0) if target_id is not None else 0
            elsewhere = {loc_names[k]: n for k, n in r["locs"].items() if k is not None and k != target_id}
            if target_id is not None and in_target == r["n"]:
                checked, locked = False, True
                notes.append(("ok", "อยู่ห้องนี้แล้ว"))
            elif elsewhere:
                checked = False
                notes.append(("warn", "จัดไว้ที่ " + ", ".join(f"{name} ({n})" for name, n in elsewhere.items()) + " แล้ว"))
            if rule_hit:
                notes.append(("rule", "ตามข้อมูลที่ยืนยันแล้ว" + (" (ตรงทุกตัวอักษร)" if rule_hit["exact"] else "")))
            elif looks_like_role(r["text"], abbr, roles, place):
                checked = False
                notes.append(("role", "อาจเป็นผู้รับผิดชอบ ไม่ใช่ห้อง"))
            if r["person"]:
                who = r["person"][0] or r["person"][1]
                notes.append(("person", f"มีชื่อคน: {who} (เลือกผู้รับผิดชอบรายข้อความได้ในตารางด้านล่าง)"))
            texts.append({"row": r, "checked": checked and not locked, "locked": locked, "notes": notes,
                          "todo": r["n"] - in_target})
        texts.sort(key=lambda t: (-t["row"]["n"], t["row"]["text"]))
        n = sum(t["row"]["n"] for t in texts)
        n_todo = sum(t["todo"] for t in texts)
        hint = rule["holder_hint"] if rule else None
        groups.append({
            "id": group_id(kind, f"{key}|{hint or ''}"), "kind": kind, "rule": rule, "key": key,
            "target_id": target_id, "target_name": target_name,
            "holder_hint": hint, "holder_id": staff_for_hint(db, hint),
            "texts": texts, "n": n, "n_todo": n_todo, "show": n_todo > 0,
        })
    groups.sort(key=lambda g: (-g["n"], g["target_name"]))
    return groups
