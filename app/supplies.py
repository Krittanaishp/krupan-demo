"""ระบบวัสดุสิ้นเปลือง: หมวด รายการ หน่วย รับเข้า เบิกจ่าย ยอดคงเหลือ

กติกา (ดู docs/SUPPLIES_PLAN.md)
- ไม่มีราคาหรือมูลค่าเลย
- ยอดคงเหลือ = SUM(supply_moves.qty) เท่านั้น เก็บเป็นหน่วยหลัก จำนวนเต็ม ไม่มีช่องยอดที่แก้ได้
- supply_moves แก้/ลบไม่ได้ (trigger) การยกเลิกเอกสาร = เพิ่มรายการกลับยอด (kind 'reversal') วันที่เดียวกับเอกสารเดิม
- ยอดสิ้นวันของทุกวันห้ามติดลบ (trigger supply_moves_no_negative ตรวจตอนบันทึก ภายใน BEGIN IMMEDIATE)
  จำนวนที่จ่ายได้ ณ วันที่ D จึงเท่ากับยอดต่ำสุดของวัน D และทุกวันหลังจากนั้น (ปกติ D = วันนี้ = ยอดคงเหลือ)
- ใบรับ/ใบเบิกที่บันทึกแล้วแก้ไม่ได้ ผิดให้ยกเลิก (มีเหตุผล) แล้วบันทึกใหม่ (คัดลอกจากใบที่ยกเลิกได้)
"""
import json
import re
import sqlite3
from datetime import date

from app import audit, timeutil

MAX_LINES = 50          # บรรทัดต่อใบ
MAX_QTY = 1_000_000     # จำนวนต่อบรรทัด (หน่วยหลัก หรือหน่วยที่รับ)
MAX_FACTOR = 100_000
NAME_MAX = 200
TEXT_MAX = 500
THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
DEFAULT_APPROVER_KEY = "SUPPLY_DEFAULT_APPROVER"

KIND_LABELS = {"opening": "ยอดยกมา", "receipt": "รับเข้า", "issue": "เบิกจ่าย", "adjust": "ปรับยอดหลังตรวจนับ",
               "reversal": "ยกเลิก"}


class SupplyError(Exception):
    """ข้อความแสดงให้ผู้ใช้เห็นได้"""


def clean(text, limit=NAME_MAX):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit] or None


def to_int(text):
    raw = str(text or "").translate(THAI_DIGITS).replace(",", "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"-?\d+", raw):
        raise ValueError(raw)
    return int(raw)


def parse_date(text, label, allow_future=False):
    """'2026-11-24' -> date · ช่องว่างหรือผิดรูปแบบ -> SupplyError"""
    raw = str(text or "").strip()
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise SupplyError(f"กรอก{label}") from None
    if not timeutil.reasonable_year(d):  # เช่น ปี พ.ศ. ถูกส่งมาเป็นปี ค.ศ. (2569 -> ค.ศ. 2569) หรือพิมพ์ปีผิด
        raise SupplyError(f"{label}: ตรวจปี พ.ศ. อีกครั้ง")
    if not allow_future and d > timeutil.today_th():
        raise SupplyError(f"{label}อยู่ในอนาคต")
    return d


# ---------------------------------------------------------------- หน่วย และการแสดงหน่วยใหญ่ประกอบ

def units_by_item(db, active_only=True):
    """{item_id: [{"id", "name", "factor", "active"}]} เรียงจากหน่วยใหญ่ที่สุด"""
    out = {}
    q = "SELECT id, item_id, name, factor, active FROM supply_units"
    if active_only:
        q += " WHERE active = 1"
    for r in db.execute(q + " ORDER BY factor DESC, id"):
        out.setdefault(r["item_id"], []).append(dict(r))
    return out


def breakdown(qty, base_unit, units):
    """23 รีม + [กล่อง=5] -> '4 กล่อง 3 รีม' · None ถ้าไม่มีหน่วยใหญ่ที่ใช้ได้ หรือยอดน้อยกว่าหน่วยใหญ่ที่เล็กที่สุด"""
    if qty is None or qty <= 0 or not units:
        return None
    rest, parts = qty, []
    for u in sorted(units, key=lambda u: -u["factor"]):
        n, rest = divmod(rest, u["factor"])
        if n:
            parts.append(f"{n:,} {u['name']}")
    if not parts:
        return None
    if rest:
        parts.append(f"{rest:,} {base_unit}")
    return " ".join(parts)


# ---------------------------------------------------------------- ยอดคงเหลือ

def balance(db, item_id):
    return db.execute("SELECT COALESCE(SUM(qty), 0) FROM supply_moves WHERE item_id = ?", (item_id,)).fetchone()[0]


def available_on(db, item_id, day):
    """จำนวนที่จ่ายได้ ณ วันที่ day โดยไม่ทำให้ยอดสิ้นวันของวันนั้นหรือวันหลังจากนั้นติดลบ"""
    day = str(day)
    cum, before, later = 0, 0, []
    for d, q in db.execute("SELECT move_date, SUM(qty) FROM supply_moves WHERE item_id = ? GROUP BY move_date "
                           "ORDER BY move_date", (item_id,)):
        cum += q
        if d <= day:
            before = cum
        else:
            later.append(cum)
    return max(0, min([before, *later]))


def level(bal, reorder_point):
    """'out' หมดแล้ว / 'low' เหลือต่ำกว่าจุดสั่งซื้อ (ยอด < จุดสั่งซื้อ: เท่ากับยังไม่เตือน ตามที่ฝ่ายกำหนด) / 'ok'"""
    if bal <= 0:
        return "out"
    if reorder_point is not None and bal < reorder_point:
        return "low"
    return "ok"


LEVEL_LABELS = {"out": "หมดแล้ว", "low": "ต่ำกว่าจุดสั่งซื้อ", "ok": ""}


def balances(db, category_id=None, q="", only_low=False, include_inactive=False, as_of=None):
    """ยอดคงเหลือทุกรายการ [{id, category_id, category, name, base_unit, reorder_point, active, balance, level, breakdown}]
    only_low: เฉพาะหมดแล้ว + เหลือต่ำกว่าจุดสั่งซื้อ (รายการที่ไม่ได้ตั้งจุดสั่งซื้อนับเฉพาะตอนหมด)
    as_of: ยอด ณ สิ้นวันนั้น (นับเฉพาะรายการเคลื่อนไหวที่ลงวันที่ไม่เกินวันนั้น) · None = ยอดปัจจุบัน"""
    where, params = [], []
    upto, upto_params = ("AND m.move_date <= ?", [str(as_of)]) if as_of else ("", [])
    if not include_inactive:
        where.append("i.active = 1")
    if category_id:
        where.append("i.category_id = ?")
        params.append(category_id)
    for word in (q or "").split()[:5]:
        where.append("(i.name LIKE ? OR c.name LIKE ?)")
        params += [f"%{word}%"] * 2
    rows = db.execute(f"""SELECT i.id, i.category_id, c.name AS category, i.name, i.base_unit, i.reorder_point, i.active,
                                 i.note, COALESCE((SELECT SUM(m.qty) FROM supply_moves m WHERE m.item_id = i.id {upto}), 0) AS balance
                          FROM supply_items i JOIN supply_categories c ON c.id = i.category_id
                          {'WHERE ' + ' AND '.join(where) if where else ''}
                          ORDER BY c.sort_order, c.name, i.name""", upto_params + params).fetchall()
    units = units_by_item(db)
    out = []
    for r in rows:
        d = dict(r)
        d["level"] = level(d["balance"], d["reorder_point"])
        d["breakdown"] = breakdown(d["balance"], d["base_unit"], units.get(d["id"]))
        d["units"] = units.get(d["id"], [])
        if only_low and d["level"] == "ok":
            continue
        out.append(d)
    return out


def low_count(db):
    """(หมดแล้ว, ต่ำกว่าจุดสั่งซื้อ) ของรายการที่ใช้งานอยู่"""
    rows = balances(db, only_low=True)
    return sum(r["level"] == "out" for r in rows), sum(r["level"] == "low" for r in rows)


# ---------------------------------------------------------------- หมวดและรายการ

def categories(db, active_only=False):
    q = """SELECT c.*, (SELECT COUNT(*) FROM supply_items i WHERE i.category_id = c.id) AS n_items FROM supply_categories c"""
    if active_only:
        q += " WHERE c.active = 1"
    return db.execute(q + " ORDER BY c.sort_order, c.name").fetchall()


def save_category(db, user_id, name, cat_id=None):
    name = clean(name, 100)
    if not name:
        raise SupplyError("กรอกชื่อหมวด")
    other = db.execute("SELECT id FROM supply_categories WHERE name = ? AND id IS NOT ?", (name, cat_id)).fetchone()
    if other:
        raise SupplyError(f"มีหมวด “{name}” อยู่แล้ว")
    with db:
        if cat_id is None:
            order = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM supply_categories").fetchone()[0]
            cat_id = db.execute("INSERT INTO supply_categories (name, sort_order) VALUES (?, ?)", (name, order)).lastrowid
            audit.log(db, user_id, "supply_categories", cat_id, "insert", {"name": {"old": None, "new": name}})
        else:
            old = db.execute("SELECT name FROM supply_categories WHERE id = ?", (cat_id,)).fetchone()
            if old is None:
                raise SupplyError("ไม่พบหมวดนี้")
            if old["name"] != name:
                db.execute("UPDATE supply_categories SET name = ? WHERE id = ?", (name, cat_id))
                audit.log(db, user_id, "supply_categories", cat_id, "update", {"name": {"old": old["name"], "new": name}})
    return cat_id


def set_category_active(db, user_id, cat_id, active):
    old = db.execute("SELECT active FROM supply_categories WHERE id = ?", (cat_id,)).fetchone()
    if old is None:
        raise SupplyError("ไม่พบหมวดนี้")
    active = 1 if active else 0
    if active == 0 and db.execute("SELECT 1 FROM supply_items WHERE category_id = ? AND active = 1", (cat_id,)).fetchone():
        raise SupplyError("หมวดนี้ยังมีรายการที่ใช้งานอยู่ ปิดใช้งานรายการก่อน")
    if old["active"] != active:
        with db:
            db.execute("UPDATE supply_categories SET active = ? WHERE id = ?", (active, cat_id))
            audit.log(db, user_id, "supply_categories", cat_id, "update", {"active": {"old": old["active"], "new": active}})


def get_item(db, item_id):
    row = db.execute("""SELECT i.*, c.name AS category FROM supply_items i JOIN supply_categories c ON c.id = i.category_id
                        WHERE i.id = ?""", (item_id,)).fetchone()
    if row is None:
        raise SupplyError("ไม่พบรายการวัสดุนี้")
    return row


def parse_item_form(db, form, item_id=None):
    """ฟอร์มรายการวัสดุ -> (ค่า, หน่วยใหม่ [(ชื่อ, อัตรา)], errors)"""
    errors, v = {}, {}
    cat = str(form.get("category_id", "")).strip()
    v["category_id"] = int(cat) if cat.isdigit() else None
    if v["category_id"] is None or not db.execute("SELECT 1 FROM supply_categories WHERE id = ?", (v["category_id"],)).fetchone():
        errors["category_id"] = "เลือกหมวด"
    v["name"] = clean(form.get("name"))
    if not v["name"]:
        errors["name"] = "กรอกชื่อรายการ"
    elif v["category_id"] and db.execute("SELECT 1 FROM supply_items WHERE category_id = ? AND name = ? AND id IS NOT ?",
                                         (v["category_id"], v["name"], item_id)).fetchone():
        errors["name"] = "หมวดนี้มีรายการชื่อนี้อยู่แล้ว"
    v["base_unit"] = clean(form.get("base_unit"), 30)
    if not v["base_unit"]:
        errors["base_unit"] = "กรอกหน่วยหลัก (หน่วยที่จ่ายออก เช่น ด้าม รีม ตลับ)"
    try:
        v["reorder_point"] = to_int(form.get("reorder_point"))
        if v["reorder_point"] is not None and not 0 <= v["reorder_point"] <= MAX_QTY:
            raise ValueError
    except ValueError:
        errors["reorder_point"] = "จุดสั่งซื้อต้องเป็นจำนวนเต็มตั้งแต่ 0 (เว้นว่างได้)"
    v["note"] = clean(form.get("note"), TEXT_MAX)
    new_units = []
    for i in range(1, 4):
        uname = clean(form.get(f"unit_name_{i}"), 30)
        raw = form.get(f"unit_factor_{i}")
        if not uname and not str(raw or "").strip():
            continue
        try:
            factor = to_int(raw)
        except ValueError:
            factor = None
        if not uname:
            errors[f"unit_{i}"] = "กรอกชื่อหน่วย"
        elif factor is None or not 1 < factor <= MAX_FACTOR:
            errors[f"unit_{i}"] = f"1 {uname} = กี่{v['base_unit'] or 'หน่วยหลัก'} (จำนวนเต็มมากกว่า 1)"
        elif uname == v["base_unit"]:
            errors[f"unit_{i}"] = "ชื่อหน่วยใหญ่ต้องไม่ซ้ำกับหน่วยหลัก"
        else:
            new_units.append((uname, factor))
    names = [u[0] for u in new_units]
    if len(set(names)) != len(names):
        errors["unit_1"] = "ชื่อหน่วยซ้ำกัน"
    return v, new_units, errors


def save_item(db, user_id, v, new_units, item_id=None):
    """เพิ่ม/แก้รายการ คืน item_id · หน่วยใหม่ที่ชื่อซ้ำกับหน่วยที่มีอยู่ (และอัตราต่างกัน) ไม่ได้"""
    existing = {u["name"]: u for u in db.execute("SELECT * FROM supply_units WHERE item_id = ?", (item_id,))} if item_id else {}
    for uname, factor in new_units:
        u = existing.get(uname)
        if u and u["factor"] != factor:
            raise SupplyError(f"หน่วย “{uname}” มีอยู่แล้ว (1 {uname} = {u['factor']:,} {v['base_unit']}) "
                              "อัตราแปลงแก้ไม่ได้ ถ้าเปลี่ยนจริงให้ปิดใช้หน่วยเดิมแล้วตั้งชื่อหน่วยใหม่")
    fields = ("category_id", "name", "base_unit", "reorder_point", "note")
    with db:
        if item_id is None:
            item_id = db.execute(f"INSERT INTO supply_items ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
                                 [v[f] for f in fields]).lastrowid
            changes = {f: {"old": None, "new": v[f]} for f in fields}
            action = "insert"
        else:
            old = db.execute("SELECT * FROM supply_items WHERE id = ?", (item_id,)).fetchone()
            if old["base_unit"] != v["base_unit"] and db.execute("SELECT 1 FROM supply_moves WHERE item_id = ?", (item_id,)).fetchone():
                raise SupplyError("เปลี่ยนหน่วยหลักไม่ได้ เพราะมีรายการรับ/จ่ายแล้ว (ยอดเก็บเป็นหน่วยหลักเดิม)")
            changes = audit.diff(old, v, fields)
            if changes:
                db.execute(f"UPDATE supply_items SET {', '.join(f'{c} = ?' for c in changes)} WHERE id = ?",
                           [v[c] for c in changes] + [item_id])
            action = "update"
        added = []
        for uname, factor in new_units:
            u = existing.get(uname)
            if u is None:
                db.execute("INSERT INTO supply_units (item_id, name, factor) VALUES (?, ?, ?)", (item_id, uname, factor))
                added.append(f"{uname} = {factor:,} {v['base_unit']}")
            elif not u["active"]:
                db.execute("UPDATE supply_units SET active = 1 WHERE id = ?", (u["id"],))
                added.append(f"{uname} = {factor:,} {v['base_unit']} (เปิดใช้อีกครั้ง)")
        if added:
            changes["units_added"] = added
        if changes:
            audit.log(db, user_id, "supply_items", item_id, action, changes)
    return item_id


def set_unit_active(db, user_id, unit_id, active):
    u = db.execute("SELECT * FROM supply_units WHERE id = ?", (unit_id,)).fetchone()
    if u is None:
        raise SupplyError("ไม่พบหน่วยนี้")
    active = 1 if active else 0
    if u["active"] != active:
        with db:
            db.execute("UPDATE supply_units SET active = ? WHERE id = ?", (active, unit_id))
            audit.log(db, user_id, "supply_items", u["item_id"], "update",
                      {f"unit {u['name']}": {"old": "ใช้งาน" if u["active"] else "ปิดใช้", "new": "ใช้งาน" if active else "ปิดใช้"}})
    return u["item_id"]


def set_item_active(db, user_id, item_id, active):
    old = get_item(db, item_id)
    active = 1 if active else 0
    if active == 0 and balance(db, item_id) > 0:
        raise SupplyError("ยังมียอดคงเหลืออยู่ ปิดใช้งานได้เมื่อยอดเป็น 0 (จ่ายออกหรือปรับยอดหลังตรวจนับก่อน)")
    if active == 1 and not db.execute("SELECT active FROM supply_categories WHERE id = ?", (old["category_id"],)).fetchone()[0]:
        raise SupplyError("หมวดของรายการนี้ปิดใช้งานอยู่ เปิดใช้หมวดก่อน")
    if old["active"] != active:
        with db:
            db.execute("UPDATE supply_items SET active = ? WHERE id = ?", (active, item_id))
            audit.log(db, user_id, "supply_items", item_id, "update", {"active": {"old": old["active"], "new": active}})


# ---------------------------------------------------------------- ตัวเลือกในฟอร์มรับ/เบิก

def item_label(r):
    """ข้อความที่พิมพ์/เลือกในช่องวัสดุ (ไม่ซ้ำกัน เพราะชื่อไม่ซ้ำในหมวดเดียวกัน)"""
    return f"{r['name']} · {r['category']}"


def item_choices(db):
    """รายการที่ใช้งานอยู่สำหรับช่องเลือกวัสดุ [{id, label, name, category, base_unit, balance, level, note, units}]"""
    out = []
    for r in balances(db):
        out.append({"id": r["id"], "label": item_label(r), "name": r["name"], "category": r["category"],
                    "base_unit": r["base_unit"], "balance": r["balance"], "level": r["level"], "note": r["note"] or "",
                    "units": [{"id": u["id"], "name": u["name"], "factor": u["factor"]} for u in r["units"]]})
    return out


def resolve_item(db, text, choices=None):
    """ข้อความในช่องวัสดุ -> item dict (รับทั้ง label เต็ม และชื่อรายการที่ไม่ซ้ำ) หรือ None"""
    text = clean(text, 300)
    if not text:
        return None
    choices = choices if choices is not None else item_choices(db)
    for c in choices:
        if c["label"] == text:
            return c
    same = [c for c in choices if c["name"] == text]
    return same[0] if len(same) == 1 else None


def issuing_departments(db, include_ids=()):
    ids = [i for i in include_ids if i]
    extra = f" OR id IN ({','.join('?' * len(ids))})" if ids else ""
    return db.execute(f"SELECT id, name FROM departments WHERE (issues_supplies = 1 AND active = 1){extra} "
                      "ORDER BY sort_order, name", ids).fetchall()


def approvers(db, include_id=None):
    return db.execute("""SELECT id, full_name, nickname, position FROM staff WHERE active = 1 OR id IS ?
                         ORDER BY COALESCE(full_name, nickname)""", (include_id,)).fetchall()


def staff_name(r):
    name = r["full_name"] or r["nickname"] or ""
    return f"{name} ({r['position']})" if r["position"] else name


def default_approver(db):
    from app import settings_store
    sid = settings_store.get(db, DEFAULT_APPROVER_KEY)
    if isinstance(sid, int) and db.execute("SELECT 1 FROM staff WHERE id = ? AND active = 1", (sid,)).fetchone():
        return sid
    return None


def requester_names(db):
    """{department_id: [ชื่อผู้เบิกที่เคยบันทึก ล่าสุดก่อน]} สำหรับรายชื่อเสนอ (แยกตามฝ่าย)"""
    out = {}
    for dep, name in db.execute("""SELECT department_id, requester_name FROM supply_issues
                                   GROUP BY department_id, requester_name ORDER BY MAX(id) DESC"""):
        out.setdefault(dep, [])
        if len(out[dep]) < 30:
            out[dep].append(name)
    return out


def sources(db):
    return [r[0] for r in db.execute("SELECT source FROM supply_receipts GROUP BY source ORDER BY MAX(id) DESC LIMIT 30")]


# ---------------------------------------------------------------- รับเข้า

def parse_receipt(db, form):
    """ฟอร์มรับเข้า -> (header, lines, errors, rows สำหรับแสดงซ้ำ)
    บรรทัด: item_N (ข้อความ), unit_N (id หน่วย หรือ 'base'), qty_N"""
    errors = []
    h = {"source": clean(form.get("source")), "doc_no": clean(form.get("doc_no"), 100),
         "note": clean(form.get("note"), TEXT_MAX), "received_date": None}
    try:
        h["received_date"] = parse_date(form.get("received_date"), "วันที่รับ")
    except SupplyError as e:
        errors.append(str(e))
    if not h["source"]:
        errors.append("กรอกแหล่งที่ได้ (เช่น จัดซื้อ ร้าน... / ได้รับจากจังหวัด)")
    choices = item_choices(db)
    lines, rows, seen = [], [], set()
    for i in range(1, MAX_LINES + 1):
        text, unit, raw = form.get(f"item_{i}", ""), str(form.get(f"unit_{i}", "base")), form.get(f"qty_{i}", "")
        if not str(text).strip() and not str(raw).strip():
            continue
        rows.append({"item": text, "unit": unit, "qty": raw})
        n = len(rows)
        item = resolve_item(db, text, choices)
        if item is None:
            errors.append(f"บรรทัด {n}: ไม่พบวัสดุ “{clean(text, 80) or ''}” เลือกจากรายการ (ถ้ายังไม่มี เพิ่มที่หน้ารายการวัสดุก่อน)")
            continue
        try:
            q = to_int(raw)
        except ValueError:
            q = None
        if q is None or not 0 < q <= MAX_QTY:
            errors.append(f"บรรทัด {n} ({item['name']}): จำนวนต้องเป็นจำนวนเต็มมากกว่า 0")
            continue
        if unit in ("", "base"):
            uname, factor = item["base_unit"], 1
        else:
            u = next((u for u in item["units"] if str(u["id"]) == unit), None)
            if u is None:
                errors.append(f"บรรทัด {n} ({item['name']}): เลือกหน่วย")
                continue
            uname, factor = u["name"], u["factor"]
        if q * factor > MAX_QTY:
            errors.append(f"บรรทัด {n} ({item['name']}): จำนวนมากเกินไป")
            continue
        if item["id"] in seen:
            errors.append(f"บรรทัด {n}: {item['name']} ซ้ำกับบรรทัดก่อนหน้า รวมเป็นบรรทัดเดียว")
            continue
        seen.add(item["id"])
        lines.append({"item_id": item["id"], "name": item["name"], "base_unit": item["base_unit"],
                      "unit_name": uname, "unit_qty": q, "factor": factor, "qty": q * factor})
    if not lines and not errors:
        errors.append("ยังไม่มีรายการวัสดุ")
    return h, lines, errors, rows


def begin(db):
    """เริ่ม transaction แบบเขียน (ล็อกก่อนอ่านยอด กันสองคนบันทึกพร้อมกัน)"""
    if db.in_transaction:
        db.commit()
    db.execute("BEGIN IMMEDIATE")


def record_receipt(db, user_id, h, lines, copied_from_id=None):
    begin(db)
    try:
        rid = db.execute("""INSERT INTO supply_receipts (received_date, source, doc_no, note, created_by, copied_from_id)
                            VALUES (?, ?, ?, ?, ?, ?)""",
                         (h["received_date"].isoformat(), h["source"], h["doc_no"], h["note"], user_id, copied_from_id)).lastrowid
        for ln in lines:
            lid = db.execute("""INSERT INTO supply_receipt_lines (receipt_id, item_id, unit_name, unit_qty, factor, qty)
                                VALUES (?, ?, ?, ?, ?, ?)""",
                             (rid, ln["item_id"], ln["unit_name"], ln["unit_qty"], ln["factor"], ln["qty"])).lastrowid
            db.execute("""INSERT INTO supply_moves (item_id, move_date, kind, qty, receipt_line_id, created_by)
                          VALUES (?, ?, 'receipt', ?, ?, ?)""", (ln["item_id"], h["received_date"].isoformat(), ln["qty"], lid, user_id))
        audit.log(db, user_id, "supply_receipts", rid, "insert", {
            "received_date": {"old": None, "new": h["received_date"].isoformat()}, "source": {"old": None, "new": h["source"]},
            "doc_no": {"old": None, "new": h["doc_no"]},
            "lines": [f"{ln['name']} {ln['unit_qty']:,} {ln['unit_name']}" + (f" (= {ln['qty']:,} {ln['base_unit']})" if ln["factor"] > 1 else "")
                      for ln in lines],
            **({"copied_from": copied_from_id} if copied_from_id else {})})
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return rid


# ---------------------------------------------------------------- เบิกจ่าย

def parse_issue(db, form):
    """ฟอร์มใบเบิก -> (header, lines, errors, rows)
    บรรทัด: item_N, req_N (จำนวนที่ขอ), out_N (จำนวนที่จ่ายจริง ว่าง = เท่าที่ขอแต่ไม่เกินที่มี)"""
    errors = []
    h = {"requester_name": clean(form.get("requester_name")), "doc_no": clean(form.get("doc_no"), 100),
         "note": clean(form.get("note"), TEXT_MAX), "issue_date": None, "approved_date": None,
         "department_id": None, "approver_staff_id": None, "approver_name": None}
    try:
        h["issue_date"] = parse_date(form.get("issue_date"), "วันที่เบิก")
    except SupplyError as e:
        errors.append(str(e))
    dep = str(form.get("department_id", "")).strip()
    if dep.isdigit() and db.execute("SELECT 1 FROM departments WHERE id = ? AND issues_supplies = 1 AND active = 1",
                                    (int(dep),)).fetchone():
        h["department_id"] = int(dep)
    else:
        errors.append("เลือกฝ่ายที่เบิก")
    if not h["requester_name"]:
        errors.append("กรอกชื่อผู้เบิก")
    ap = str(form.get("approver_staff_id", "")).strip()
    row = db.execute("SELECT * FROM staff WHERE id = ? AND active = 1", (int(ap),)).fetchone() if ap.isdigit() else None
    if row is None:
        errors.append("เลือกผู้อนุมัติ")
    else:
        h["approver_staff_id"], h["approver_name"] = row["id"], staff_name(row)
    try:
        h["approved_date"] = parse_date(form.get("approved_date"), "วันที่อนุมัติ")
    except SupplyError as e:
        errors.append(str(e))

    choices = item_choices(db)
    lines, rows, seen = [], [], set()
    for i in range(1, MAX_LINES + 1):
        text, raw_req, raw_out = form.get(f"item_{i}", ""), form.get(f"req_{i}", ""), form.get(f"out_{i}", "")
        if not str(text).strip() and not str(raw_req).strip() and not str(raw_out).strip():
            continue
        rows.append({"item": text, "req": raw_req, "out": raw_out})
        n = len(rows)
        item = resolve_item(db, text, choices)
        if item is None:
            errors.append(f"บรรทัด {n}: ไม่พบวัสดุ “{clean(text, 80) or ''}” เลือกจากรายการ")
            continue
        try:
            req = to_int(raw_req)
            out = to_int(raw_out)
        except ValueError:
            errors.append(f"บรรทัด {n} ({item['name']}): จำนวนต้องเป็นจำนวนเต็ม")
            continue
        if req is None or not 0 < req <= MAX_QTY:
            errors.append(f"บรรทัด {n} ({item['name']}): กรอกจำนวนที่ขอ (จำนวนเต็มมากกว่า 0)")
            continue
        if out is not None and not 0 <= out <= req:
            errors.append(f"บรรทัด {n} ({item['name']}): จำนวนที่จ่ายต้องอยู่ระหว่าง 0 ถึงจำนวนที่ขอ ({req:,})")
            continue
        if item["id"] in seen:
            errors.append(f"บรรทัด {n}: {item['name']} ซ้ำกับบรรทัดก่อนหน้า รวมเป็นบรรทัดเดียว")
            continue
        seen.add(item["id"])
        lines.append({"item_id": item["id"], "name": item["name"], "base_unit": item["base_unit"],
                      "requested_qty": req, "issued_qty": out, "row": n})
    if not lines and not errors:
        errors.append("ยังไม่มีรายการวัสดุ")
    if h["issue_date"] and h["approved_date"] and h["approved_date"] > timeutil.today_th():
        errors.append("วันที่อนุมัติอยู่ในอนาคต")
    return h, lines, errors, rows


def check_stock(db, day, lines):
    """เติม issued_qty ที่ว่าง = min(ขอ, ที่มี) และคืนรายการบรรทัดที่จ่ายเกินที่มี [(บรรทัด, ที่มี)]"""
    short = []
    for ln in lines:
        avail = available_on(db, ln["item_id"], day)
        ln["available"] = avail
        if ln["issued_qty"] is None:
            ln["issued_qty"] = min(ln["requested_qty"], avail)
        elif ln["issued_qty"] > avail:
            short.append((ln, avail))
    return short


class StockChanged(SupplyError):
    """ยอดไม่พอ ณ เวลาบันทึก: short = [(line, available)]"""

    def __init__(self, short):
        self.short = short
        super().__init__("ยอดคงเหลือไม่พอ: " + ", ".join(
            f"{ln['name']} จ่ายได้ {a:,} {ln['base_unit']}" for ln, a in short))


def record_issue(db, user_id, h, lines, copied_from_id=None):
    """ตรวจยอดอีกครั้งใน transaction เดียวกับที่บันทึก ถ้าไม่พอ -> StockChanged (ไม่บันทึกอะไรเลย)"""
    begin(db)
    try:
        day = h["issue_date"].isoformat()
        short = check_stock(db, day, lines)
        if short:
            raise StockChanged(short)
        iid = db.execute("""INSERT INTO supply_issues (issue_date, department_id, requester_name, doc_no, approver_staff_id,
                                approver_name, approved_date, note, created_by, copied_from_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                         (day, h["department_id"], h["requester_name"], h["doc_no"], h["approver_staff_id"], h["approver_name"],
                          h["approved_date"].isoformat(), h["note"], user_id, copied_from_id)).lastrowid
        for ln in lines:
            lid = db.execute("INSERT INTO supply_issue_lines (issue_id, item_id, requested_qty, issued_qty) VALUES (?, ?, ?, ?)",
                             (iid, ln["item_id"], ln["requested_qty"], ln["issued_qty"])).lastrowid
            if ln["issued_qty"]:
                db.execute("""INSERT INTO supply_moves (item_id, move_date, kind, qty, issue_line_id, created_by)
                              VALUES (?, ?, 'issue', ?, ?, ?)""", (ln["item_id"], day, -ln["issued_qty"], lid, user_id))
        dep = db.execute("SELECT name FROM departments WHERE id = ?", (h["department_id"],)).fetchone()[0]
        audit.log(db, user_id, "supply_issues", iid, "insert", {
            "issue_date": {"old": None, "new": day}, "department": {"old": None, "new": dep},
            "requester_name": {"old": None, "new": h["requester_name"]}, "doc_no": {"old": None, "new": h["doc_no"]},
            "approver": {"old": None, "new": h["approver_name"]},
            "approved_date": {"old": None, "new": h["approved_date"].isoformat()},
            "lines": [f"{ln['name']} ขอ {ln['requested_qty']:,} จ่าย {ln['issued_qty']:,} {ln['base_unit']}" for ln in lines],
            **({"copied_from": copied_from_id} if copied_from_id else {})})
        db.commit()
    except sqlite3.IntegrityError as e:
        db.rollback()
        if "below zero" in str(e):  # ไม่ควรเกิด (ตรวจแล้วใน transaction เดียวกัน) แต่ trigger เป็นด่านสุดท้าย
            raise SupplyError("ยอดคงเหลือไม่พอ ระบบไม่ได้บันทึก ลองบันทึกอีกครั้ง") from None
        raise
    except BaseException:
        db.rollback()
        raise
    return iid


# ---------------------------------------------------------------- เอกสาร: อ่าน / ยกเลิก

def get_receipt(db, rid):
    r = db.execute("""SELECT r.*, u.display_name AS created_name, u.username AS created_user,
                             cu.display_name AS cancelled_name, cu.username AS cancelled_user
                      FROM supply_receipts r JOIN users u ON u.id = r.created_by LEFT JOIN users cu ON cu.id = r.cancelled_by
                      WHERE r.id = ?""", (rid,)).fetchone()
    if r is None:
        raise SupplyError("ไม่พบใบรับนี้")
    lines = db.execute("""SELECT l.*, i.name, i.base_unit, c.name AS category FROM supply_receipt_lines l
                          JOIN supply_items i ON i.id = l.item_id JOIN supply_categories c ON c.id = i.category_id
                          WHERE l.receipt_id = ? ORDER BY l.id""", (rid,)).fetchall()
    return r, lines


def get_issue(db, iid):
    r = db.execute("""SELECT s.*, d.name AS department, u.display_name AS created_name, u.username AS created_user,
                             cu.display_name AS cancelled_name, cu.username AS cancelled_user
                      FROM supply_issues s JOIN departments d ON d.id = s.department_id JOIN users u ON u.id = s.created_by
                      LEFT JOIN users cu ON cu.id = s.cancelled_by WHERE s.id = ?""", (iid,)).fetchone()
    if r is None:
        raise SupplyError("ไม่พบใบเบิกนี้")
    lines = db.execute("""SELECT l.*, i.name, i.base_unit, c.name AS category FROM supply_issue_lines l
                          JOIN supply_items i ON i.id = l.item_id JOIN supply_categories c ON c.id = i.category_id
                          WHERE l.issue_id = ? ORDER BY l.id""", (iid,)).fetchall()
    return r, lines


def _reverse(db, user_id, moves, note):
    for m in moves:
        db.execute("""INSERT INTO supply_moves (item_id, move_date, kind, qty, receipt_line_id, issue_line_id, reverses_id,
                          note, created_by) VALUES (?, ?, 'reversal', ?, ?, ?, ?, ?, ?)""",
                   (m["item_id"], m["move_date"], -m["qty"], m["receipt_line_id"], m["issue_line_id"], m["id"], note, user_id))


def cancel_receipt(db, user_id, rid, reason):
    reason = clean(reason, TEXT_MAX)
    if not reason:
        raise SupplyError("กรอกเหตุผลที่ยกเลิก")
    begin(db)
    try:
        r = db.execute("SELECT * FROM supply_receipts WHERE id = ?", (rid,)).fetchone()
        if r is None:
            raise SupplyError("ไม่พบใบรับนี้")
        if r["cancelled_at"]:
            raise SupplyError("ใบรับนี้ยกเลิกไปแล้ว")
        moves = db.execute("""SELECT m.* FROM supply_moves m JOIN supply_receipt_lines l ON l.id = m.receipt_line_id
                              WHERE l.receipt_id = ? AND m.kind = 'receipt'""", (rid,)).fetchall()
        short = []
        for m in moves:  # ยอดจะติดลบไหมถ้าเอาของที่รับออก (ของถูกจ่ายไปแล้ว)
            avail = available_on(db, m["item_id"], m["move_date"])
            if avail < m["qty"]:
                name = db.execute("SELECT name, base_unit FROM supply_items WHERE id = ?", (m["item_id"],)).fetchone()
                short.append(f"{name['name']} (รับ {m['qty']:,} {name['base_unit']} แต่เหลือให้ยกเลิกได้ {avail:,})")
        if short:
            raise SupplyError("ยกเลิกไม่ได้ เพราะของจากใบนี้ถูกจ่ายออกไปแล้ว ยอดจะติดลบ: " + ", ".join(short)
                              + " · ยกเลิกใบเบิกที่เกี่ยวข้องก่อน หรือใช้การปรับยอดหลังตรวจนับ")
        _reverse(db, user_id, moves, f"ยกเลิกใบรับ #{rid}: {reason}")
        db.execute("UPDATE supply_receipts SET cancelled_at = CURRENT_TIMESTAMP, cancelled_by = ?, cancel_reason = ? WHERE id = ?",
                   (user_id, reason, rid))
        audit.log(db, user_id, "supply_receipts", rid, "cancel", {"cancel_reason": {"old": None, "new": reason}})
        db.commit()
    except BaseException:
        db.rollback()
        raise


def cancel_issue(db, user_id, iid, reason):
    reason = clean(reason, TEXT_MAX)
    if not reason:
        raise SupplyError("กรอกเหตุผลที่ยกเลิก")
    begin(db)
    try:
        r = db.execute("SELECT * FROM supply_issues WHERE id = ?", (iid,)).fetchone()
        if r is None:
            raise SupplyError("ไม่พบใบเบิกนี้")
        if r["cancelled_at"]:
            raise SupplyError("ใบเบิกนี้ยกเลิกไปแล้ว")
        moves = db.execute("""SELECT m.* FROM supply_moves m JOIN supply_issue_lines l ON l.id = m.issue_line_id
                              WHERE l.issue_id = ? AND m.kind = 'issue'""", (iid,)).fetchall()
        _reverse(db, user_id, moves, f"ยกเลิกใบเบิก #{iid}: {reason}")
        db.execute("UPDATE supply_issues SET cancelled_at = CURRENT_TIMESTAMP, cancelled_by = ?, cancel_reason = ? WHERE id = ?",
                   (user_id, reason, iid))
        audit.log(db, user_id, "supply_issues", iid, "cancel", {"cancel_reason": {"old": None, "new": reason}})
        db.commit()
    except BaseException:
        db.rollback()
        raise


def list_receipts(db, month=None, limit=200):
    where, params = [], []
    if month:
        where.append("substr(r.received_date, 1, 7) = ?")
        params.append(month)
    return db.execute(f"""SELECT r.*, (SELECT COUNT(*) FROM supply_receipt_lines l WHERE l.receipt_id = r.id) AS n_lines
                          FROM supply_receipts r {'WHERE ' + ' AND '.join(where) if where else ''}
                          ORDER BY r.received_date DESC, r.id DESC LIMIT ?""", [*params, limit]).fetchall()


def list_issues(db, month=None, department_id=None, limit=200):
    where, params = [], []
    if month:
        where.append("substr(s.issue_date, 1, 7) = ?")
        params.append(month)
    if department_id:
        where.append("s.department_id = ?")
        params.append(department_id)
    return db.execute(f"""SELECT s.*, d.name AS department,
                                 (SELECT COUNT(*) FROM supply_issue_lines l WHERE l.issue_id = s.id) AS n_lines,
                                 (SELECT COUNT(*) FROM supply_issue_lines l WHERE l.issue_id = s.id
                                                                            AND l.issued_qty < l.requested_qty) AS n_short
                          FROM supply_issues s JOIN departments d ON d.id = s.department_id
                          {'WHERE ' + ' AND '.join(where) if where else ''}
                          ORDER BY s.issue_date DESC, s.id DESC LIMIT ?""", [*params, limit]).fetchall()


# ---------------------------------------------------------------- บัญชีวัสดุ (รายรายการ)

def stock_card(db, item_id, date_from=None, date_to=None):
    """(ยอดยกมาต้นช่วง, [แถว]) แถว: วันที่ / ประเภท / รับจาก–จ่ายให้ / เลขที่เอกสาร / รับ / จ่าย / คงเหลือ / ลิงก์"""
    params = [item_id]
    opening = 0
    if date_from:
        opening = db.execute("SELECT COALESCE(SUM(qty), 0) FROM supply_moves WHERE item_id = ? AND move_date < ?",
                             (item_id, str(date_from))).fetchone()[0]
    where = "m.item_id = ?"
    if date_from:
        where += " AND m.move_date >= ?"
        params.append(str(date_from))
    if date_to:
        where += " AND m.move_date <= ?"
        params.append(str(date_to))
    rows = db.execute(f"""
        SELECT m.*, rl.receipt_id, r.source, r.doc_no AS receipt_doc, rl.unit_name, rl.unit_qty, rl.factor,
               il.issue_id, s.requester_name, s.doc_no AS issue_doc, d.name AS department, il.requested_qty,
               b.file_name AS import_file
        FROM supply_moves m
        LEFT JOIN supply_receipt_lines rl ON rl.id = m.receipt_line_id LEFT JOIN supply_receipts r ON r.id = rl.receipt_id
        LEFT JOIN supply_issue_lines il ON il.id = m.issue_line_id LEFT JOIN supply_issues s ON s.id = il.issue_id
        LEFT JOIN departments d ON d.id = s.department_id
        LEFT JOIN supply_import_batches b ON b.id = m.import_batch_id
        WHERE {where} ORDER BY m.move_date, m.id""", params).fetchall()
    out, bal = [], opening
    for m in rows:
        bal += m["qty"]
        if m["receipt_id"]:
            party = f"รับจาก {m['source']}"
            doc, link = m["receipt_doc"], f"/supplies/receipts/{m['receipt_id']}"
        elif m["issue_id"]:
            party = f"จ่ายให้ {m['department']} ({m['requester_name']})"
            doc, link = m["issue_doc"], f"/supplies/issues/{m['issue_id']}"
        elif m["kind"] == "opening":
            party, doc, link = "ยอดยกมา" + (f" (นำเข้าจาก {m['import_file']})" if m["import_file"] else ""), None, None
        else:
            party, doc, link = KIND_LABELS.get(m["kind"], m["kind"]), None, None
        if m["kind"] == "reversal":
            party = f"ยกเลิก: {party}"
        out.append({"date": m["move_date"], "kind": m["kind"], "party": party, "doc": doc, "link": link,
                    "in": m["qty"] if m["qty"] > 0 else None, "out": -m["qty"] if m["qty"] < 0 else None,
                    "balance": bal, "note": m["note"],
                    "unit_text": f"{m['unit_qty']:,} {m['unit_name']}" if m["receipt_id"] and m["factor"] and m["factor"] > 1 else None})
    return opening, out


def issue_lines_json(lines):
    return json.dumps(lines, ensure_ascii=False)
