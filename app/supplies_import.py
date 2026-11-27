"""นำเข้ารายการวัสดุและยอดยกมาจาก Excel (แบบฟอร์มมาตรฐานเท่านั้น)

ขั้นตอน (บทเรียนจากการย้ายข้อมูลครุภัณฑ์: ห้ามบันทึกโดยไม่มีคนตรวจ ห้ามเขียนทับเอง)
1. ดาวน์โหลดแบบฟอร์ม (1 แถว = 1 รายการ) → กรอก → อัปโหลด (.xlsx ไม่เกิน 2 MB)
2. แสดงตัวอย่าง 5 กลุ่ม: ใหม่ / มีอยู่แล้วและตรงกัน / มีอยู่แล้วแต่ต่าง (เลือกทีละช่อง ค่าเริ่มต้น = ใช้ค่าเดิม)
   / ชื่อซ้ำกันเองในไฟล์ / แถวที่อ่านไม่ได้ (บอกแถวและช่อง)
3. ยืนยัน: สำรอง db ก่อน → บันทึกทั้งชุดใน transaction เดียว + audit_log + supply_import_batches
4. ยกเลิกทั้งชุดได้ถ้ายังไม่มีรับ/จ่ายของรายการในชุดหลังนำเข้า (ปิดใช้งานรายการใหม่ + กลับยอดยกมา ไม่ลบแถว)

จับคู่กับของเดิมด้วย หมวด + ชื่อรายการ (ตัดช่องว่างซ้ำ) · ยอดยกมาใส่ได้เฉพาะรายการที่ยังไม่มีรายการเคลื่อนไหว
"""
import hashlib
import json
import re
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

from app import audit, backup
from app import supplies as sp
from app.reports_xlsx import save

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000
SHEET = "รายการวัสดุ"
COLUMNS = ["หมวด", "ชื่อรายการ", "หน่วยหลัก", "จุดสั่งซื้อ", "หน่วยใหญ่ 1", "อัตรา 1", "หน่วยใหญ่ 2", "อัตรา 2", "ยอดยกมา", "หมายเหตุ"]
KEYS = ["category", "name", "base_unit", "reorder_point", "unit1", "factor1", "unit2", "factor2", "opening", "note"]
FIELD_LABELS = {"active": "สถานะรายการ", "base_unit": "หน่วยหลัก", "reorder_point": "จุดสั่งซื้อ", "note": "หมายเหตุ", "units": "หน่วยใหญ่",
                "opening": "ยอดยกมา"}
EXAMPLES = [
    ["กระดาษ", "A4 80 แกรม", "รีม", 10, "กล่อง", 5, None, None, 23, None],
    ["เครื่องเขียน", "ปากกาลูกลื่น สีน้ำเงิน", "ด้าม", 20, "กล่อง", 50, None, None, 120, None],
    ["หมึกพิมพ์", "Printo PX-410", "ตลับ", 2, None, None, None, None, 3, "ใช้กับเครื่อง Printo ห้องเสมียนตรา"],
]


class ImportError_(Exception):
    """ไฟล์ทั้งไฟล์ใช้ไม่ได้ (ข้อความแสดงให้ผู้ใช้เห็น)"""


# ---------------------------------------------------------------- แบบฟอร์ม

def template():
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    head_fill = PatternFill("solid", fgColor="DDE7F5")
    for col, name in enumerate(COLUMNS, 1):
        c = ws.cell(1, col, name)
        c.font = Font(bold=True)
        c.fill = head_fill
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for col, w in enumerate((16, 36, 12, 12, 12, 9, 12, 9, 12, 30), 1):
        ws.column_dimensions[chr(64 + col)].width = w
    ws.freeze_panes = "A2"
    info = wb.create_sheet("คำอธิบาย")
    lines = [
        "แบบฟอร์มนำเข้ารายการวัสดุและยอดยกมา (ระบบรับเฉพาะไฟล์ตามแบบฟอร์มนี้ ห้ามเปลี่ยนชื่อชีตหรือหัวตาราง)",
        "",
        "กรอกในชีต “รายการวัสดุ” 1 แถว = 1 รายการ เริ่มแถวที่ 2",
        "หมวด (บังคับ): เช่น เครื่องเขียน กระดาษ หมึกพิมพ์ ถ้ายังไม่มีในระบบ ระบบจะสร้างให้",
        "ชื่อรายการ (บังคับ): ชื่อในหมวดเดียวกันห้ามซ้ำ ระบบใช้ หมวด + ชื่อรายการ จับคู่กับรายการที่มีอยู่แล้ว",
        "หน่วยหลัก (บังคับ): หน่วยที่จ่ายออกและเก็บยอด เช่น ด้าม รีม ตลับ (กระดาษนับเป็นรีม ไม่ใช่แผ่น)",
        "จุดสั่งซื้อ: จำนวนเต็ม (หน่วยหลัก) เตือนเมื่อเหลือต่ำกว่าค่านี้ (เท่ากับพอดียังไม่เตือน) เว้นว่างได้",
        "หน่วยใหญ่ / อัตรา: หน่วยที่ใช้ตอนรับเข้า เช่น กล่อง = 5 (1 กล่อง = 5 รีม) ใส่ได้ 2 หน่วย เว้นว่างได้",
        "ยอดยกมา: จำนวนเต็ม (หน่วยหลัก) ที่นับได้ ณ วันที่เริ่มใช้ระบบ (เลือกวันที่ในหน้าจอตอนนำเข้า) เว้นว่าง = 0",
        "ไม่มีช่องราคาหรือมูลค่า (ระบบวัสดุไม่เก็บราคา)",
        "",
        "ตัวอย่าง (อย่าคัดลอกแถวตัวอย่างไปใช้ถ้าไม่ใช่ของจริง):",
    ]
    for i, text in enumerate(lines, 1):
        info.cell(i, 1, text).font = Font(bold=(i == 1))
    start = len(lines) + 1
    for col, name in enumerate(COLUMNS, 1):
        info.cell(start, col, name).font = Font(bold=True)
    for r, row in enumerate(EXAMPLES, start + 1):
        for col, v in enumerate(row, 1):
            info.cell(r, col, v)
    info.column_dimensions["A"].width = 18
    info.column_dimensions["B"].width = 30
    return save(wb)


# ---------------------------------------------------------------- อ่านไฟล์

def norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def cell_int(v, label, allow_none=True, minimum=0):
    """ค่าในเซลล์ -> int · คืน (ค่า, ข้อความผิดพลาด)"""
    if v is None or (isinstance(v, str) and not v.strip()):
        return (None, None) if allow_none else (None, f"ช่อง “{label}” ว่าง")
    if isinstance(v, bool):
        return None, f"ช่อง “{label}” ต้องเป็นจำนวนเต็ม"
    if isinstance(v, (int, float)):
        if float(v) != int(v):
            return None, f"ช่อง “{label}” ต้องเป็นจำนวนเต็ม (ได้ {v})"
        n = int(v)
    else:
        raw = str(v).translate(sp.THAI_DIGITS).replace(",", "").strip()
        if not re.fullmatch(r"-?\d+", raw):
            return None, f"ช่อง “{label}” ต้องเป็นจำนวนเต็ม (ได้ “{v}”)"
        n = int(raw)
    if n < minimum or n > sp.MAX_QTY:
        return None, f"ช่อง “{label}” ต้องอยู่ระหว่าง {minimum:,} ถึง {sp.MAX_QTY:,}"
    return n, None


def read_file(data, filename):
    """bytes -> [{"row", "values"... หรือ "errors"}] · ImportError_ ถ้าไฟล์ทั้งไฟล์ใช้ไม่ได้"""
    if not (filename or "").lower().endswith(".xlsx"):
        raise ImportError_("รับเฉพาะไฟล์ .xlsx (Excel) ตามแบบฟอร์มของระบบ")
    if len(data) > MAX_BYTES:
        raise ImportError_(f"ไฟล์ใหญ่เกิน {MAX_BYTES // (1024 * 1024)} MB")
    if not data.startswith(b"PK"):
        raise ImportError_("ไฟล์นี้ไม่ใช่ไฟล์ .xlsx ที่ถูกต้อง")
    try:
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception:  # noqa: BLE001 - ไฟล์เสีย/รูปแบบอื่น
        raise ImportError_("เปิดไฟล์ไม่ได้ ตรวจว่าเป็นไฟล์ Excel (.xlsx) ที่บันทึกจากแบบฟอร์มของระบบ") from None
    try:
        if SHEET not in wb.sheetnames:
            raise ImportError_(f"ไม่พบชีต “{SHEET}” ใช้แบบฟอร์มที่ดาวน์โหลดจากหน้านี้ (ห้ามเปลี่ยนชื่อชีต)")
        ws = wb[SHEET]
        rows = ws.iter_rows(values_only=True)
        head = [norm(v) for v in (next(rows, None) or [])]
        head = head[:len(COLUMNS)] + [""] * (len(COLUMNS) - len(head))
        wrong = [f"คอลัมน์ {chr(65 + i)} ต้องเป็น “{want}” (ในไฟล์เป็น “{got}”)" for i, (want, got) in enumerate(zip(COLUMNS, head))
                 if want != got]
        if wrong:
            raise ImportError_("หัวตารางไม่ตรงกับแบบฟอร์ม: " + " · ".join(wrong))
        out = []
        for n, values in enumerate(rows, 2):
            values = list(values or [])[:len(COLUMNS)]
            values += [None] * (len(COLUMNS) - len(values))
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
                continue
            if len(out) >= MAX_ROWS:
                raise ImportError_(f"ไฟล์มีมากกว่า {MAX_ROWS:,} แถว แบ่งเป็นหลายไฟล์")
            out.append(parse_row(n, dict(zip(KEYS, values))))
        if not out:
            raise ImportError_("ไม่มีข้อมูลในชีต “รายการวัสดุ” (กรอกตั้งแต่แถวที่ 2)")
        return out
    finally:
        wb.close()


def parse_row(n, raw):
    errors, v = [], {"row": n}
    for key, label, limit in (("category", "หมวด", 100), ("name", "ชื่อรายการ", sp.NAME_MAX), ("base_unit", "หน่วยหลัก", 30)):
        text = norm(raw[key])
        if not text:
            errors.append(f"ช่อง “{label}” ว่าง")
        elif len(text) > limit:
            errors.append(f"ช่อง “{label}” ยาวเกิน {limit} ตัวอักษร")
        v[key] = text
    v["reorder_point"], e = cell_int(raw["reorder_point"], "จุดสั่งซื้อ")
    errors += [e] if e else []
    v["opening"], e = cell_int(raw["opening"], "ยอดยกมา")
    errors += [e] if e else []
    v["note"] = norm(raw["note"])[:sp.TEXT_MAX] or None
    units = []
    for i in (1, 2):
        uname = norm(raw[f"unit{i}"])
        factor, e = cell_int(raw[f"factor{i}"], f"อัตรา {i}", minimum=2)
        if not uname and raw[f"factor{i}"] in (None, ""):
            continue
        if e:
            errors.append(e)
        elif not uname:
            errors.append(f"ช่อง “หน่วยใหญ่ {i}” ว่าง แต่มีอัตรา")
        elif factor is None:
            errors.append(f"ช่อง “อัตรา {i}” ว่าง (1 {uname} = กี่{v.get('base_unit') or 'หน่วยหลัก'})")
        elif len(uname) > 30:
            errors.append(f"ช่อง “หน่วยใหญ่ {i}” ยาวเกิน 30 ตัวอักษร")
        elif uname == v.get("base_unit"):
            errors.append(f"ช่อง “หน่วยใหญ่ {i}” ซ้ำกับหน่วยหลัก")
        else:
            units.append((uname, factor))
    if len({u[0] for u in units}) != len(units):
        errors.append("หน่วยใหญ่ 1 และ 2 ชื่อซ้ำกัน")
    v["units"] = sorted(units)
    if errors:
        return {"row": n, "errors": errors, "raw": {k: raw[k] for k in ("category", "name")}}
    return v


# ---------------------------------------------------------------- เทียบกับของเดิม

def classify(db, parsed):
    """แบ่ง 5 กลุ่ม {"new", "same", "diff", "dup", "bad"} · diff มี "fields": [{"field", "label", "db", "file", "can"}]"""
    groups = {"new": [], "same": [], "diff": [], "dup": [], "bad": []}
    good = [p for p in parsed if "errors" not in p]
    groups["bad"] = [p for p in parsed if "errors" in p]
    seen = {}
    for p in good:
        seen.setdefault((p["category"], p["name"]), []).append(p)
    existing = {}
    # รายการเคลื่อนไหวที่นับว่า "มีแล้ว" ไม่รวมยอดยกมา/การกลับยอดของชุดนำเข้าที่ยกเลิกไปแล้ว (นำเข้าใหม่ได้)
    for r in db.execute("""SELECT i.*, c.name AS category,
                                  (SELECT COUNT(*) FROM supply_moves m WHERE m.item_id = i.id AND (m.import_batch_id IS NULL OR
                                   m.import_batch_id NOT IN (SELECT id FROM supply_import_batches WHERE undone_at IS NOT NULL))) AS n_moves
                           FROM supply_items i JOIN supply_categories c ON c.id = i.category_id"""):
        existing[(r["category"], r["name"])] = r
    units = {}
    all_units = {}
    for u in db.execute("SELECT item_id, name, factor, active FROM supply_units"):
        all_units.setdefault(u["item_id"], {})[u["name"]] = u["factor"]
        if u["active"]:
            units.setdefault(u["item_id"], []).append((u["name"], u["factor"]))
    for key, rows in seen.items():
        if len(rows) > 1:
            for p in rows:
                groups["dup"].append({**p, "others": [q["row"] for q in rows if q is not p]})
            continue
        p = rows[0]
        old = existing.get(key)
        if old is None:
            groups["new"].append(p)
            continue
        p["item_id"] = old["id"]
        fields = []
        if not old["active"]:
            fields.append({"field": "active", "db": "ปิดใช้งาน", "file": "ใช้งาน", "can": True, "why": None})
        if p["base_unit"] != old["base_unit"]:
            fields.append({"field": "base_unit", "db": old["base_unit"], "file": p["base_unit"], "can": not old["n_moves"],
                           "why": None if not old["n_moves"] else "มีรายการรับ/จ่ายแล้ว เปลี่ยนหน่วยหลักไม่ได้"})
        if p["reorder_point"] != old["reorder_point"] and p["reorder_point"] is not None:
            fields.append({"field": "reorder_point", "db": old["reorder_point"], "file": p["reorder_point"], "can": True, "why": None})
        if p["note"] and p["note"] != old["note"]:
            fields.append({"field": "note", "db": old["note"], "file": p["note"], "can": True, "why": None})
        have = dict(units.get(old["id"], []))
        add = [u for u in p["units"] if u[0] not in have]
        known = all_units.get(old["id"], {})  # รวมหน่วยที่ปิดใช้แล้ว: ชื่อเดิมอัตราต่างใช้ไม่ได้
        clash = [u for u in p["units"] if u[0] in known and known[u[0]] != u[1]]
        if add or clash:
            fields.append({"field": "units", "db": ", ".join(f"{n} = {f:,}" for n, f in sorted(have.items())) or "—",
                           "file": ", ".join(f"{n} = {f:,}" for n, f in p["units"]), "can": bool(add) and not clash,
                           "add": add, "why": ("อัตราแปลงของหน่วยที่มีอยู่แก้ไม่ได้: " + ", ".join(n for n, _ in clash)) if clash else None})
        if p["opening"]:
            fields.append({"field": "opening", "db": "ไม่มี" if not old["n_moves"] else f"มีรายการรับ/จ่ายแล้ว {old['n_moves']} รายการ",
                           "file": p["opening"], "can": not old["n_moves"],
                           "why": None if not old["n_moves"] else "มีรายการรับ/จ่ายแล้ว ใส่ยอดยกมาไม่ได้ ใช้การตรวจนับปรับยอดแทน"})
        for f in fields:
            f["label"] = FIELD_LABELS[f["field"]]
        if fields:
            groups["diff"].append({**p, "fields": fields, "old": dict(old)})
        else:
            groups["same"].append(p)
    for g in groups.values():
        g.sort(key=lambda p: p["row"])
    return groups


def digest(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- บันทึก

def parse_openings(form_items, groups):
    """ยอดยกมาที่แก้ในหน้าตัวอย่าง (ช่อง open_<แถว>) -> ({แถว: จำนวน}, {แถว: ข้อความผิดพลาด})
    รับเฉพาะแถวที่ใส่ยอดยกมาได้: กลุ่มใหม่ และกลุ่มต่างที่ยังไม่มีรายการรับ/จ่าย · ช่องว่าง = 0"""
    allowed = {p["row"] for p in groups["new"]}
    allowed |= {p["row"] for p in groups["diff"] for f in p["fields"] if f["field"] == "opening" and f["can"]}
    values, errors = {}, {}
    for key, raw in form_items:
        if not key.startswith("open_") or not key[5:].isdigit() or int(key[5:]) not in allowed:
            continue
        row = int(key[5:])
        n, e = cell_int(raw, "ยอดยกมา")
        if e:
            errors[row] = "ยอดยกมาต้องเป็นจำนวนเต็มตั้งแต่ 0"
        else:
            values[row] = n or 0
    return values, errors


def apply(db, user_id, data, filename, opening_date, picks, openings=None):
    """picks: {(row, field)} ที่ผู้ใช้เลือก "ใช้ค่าจากไฟล์" ในกลุ่มมีอยู่แล้วแต่ต่าง
    openings: {row: จำนวน} ยอดยกมาที่แก้ในหน้าตัวอย่าง (แทนค่าในไฟล์ เช่น ยอดที่นับใหม่วันติดตั้ง) ไม่มี = ใช้ค่าในไฟล์
    คืน (batch_id, summary dict, ไฟล์สำรอง) · บันทึกเฉพาะกลุ่มใหม่ + ช่องที่เลือกในกลุ่มต่าง (กลุ่มซ้ำ/อ่านไม่ได้ไม่บันทึก)"""
    openings = openings or {}
    parsed = read_file(data, filename)
    groups = classify(db, parsed)
    saved = backup.backup(db, "before_supply_import")
    sp.begin(db)
    try:
        bid = db.execute("INSERT INTO supply_import_batches (file_name, file_sha256, opening_date, created_by) VALUES (?, ?, ?, ?)",
                         (filename[:200], digest(data), opening_date.isoformat(), user_id)).lastrowid
        cats = {r["name"]: r["id"] for r in db.execute("SELECT id, name FROM supply_categories")}
        summary = {"new_categories": [], "new_items": 0, "updated_items": 0, "opening_rows": 0, "skipped_dup": len(groups["dup"]),
                   "skipped_bad": len(groups["bad"]), "items": [],
                   # รายการเคลื่อนไหวที่ id มากกว่านี้ (และไม่ใช่ของชุดนี้) = เกิดหลังนำเข้า -> ยกเลิกทั้งชุดไม่ได้
                   "max_move_id": db.execute("SELECT COALESCE(MAX(id), 0) FROM supply_moves").fetchone()[0]}

        def category_id(name):
            if name not in cats:
                order = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM supply_categories").fetchone()[0]
                cats[name] = db.execute("INSERT INTO supply_categories (name, sort_order) VALUES (?, ?)", (name, order)).lastrowid
                audit.log(db, user_id, "supply_categories", cats[name], "insert",
                          {"name": {"old": None, "new": name}, "import_batch": bid})
                summary["new_categories"].append(name)
            return cats[name]

        def opening(item_id, qty):
            db.execute("""INSERT INTO supply_moves (item_id, move_date, kind, qty, import_batch_id, note, created_by)
                          VALUES (?, ?, 'opening', ?, ?, ?, ?)""",
                       (item_id, opening_date.isoformat(), qty, bid, f"ยอดยกมาจาก {filename[:100]}", user_id))
            summary["opening_rows"] += 1

        for p in groups["new"]:
            item_id = db.execute("""INSERT INTO supply_items (category_id, name, base_unit, reorder_point, note)
                                    VALUES (?, ?, ?, ?, ?)""",
                                 (category_id(p["category"]), p["name"], p["base_unit"], p["reorder_point"], p["note"])).lastrowid
            for uname, factor in p["units"]:
                db.execute("INSERT INTO supply_units (item_id, name, factor) VALUES (?, ?, ?)", (item_id, uname, factor))
            qty = openings.get(p["row"], p["opening"] or 0)
            if qty:
                opening(item_id, qty)
            audit.log(db, user_id, "supply_items", item_id, "insert", {
                "name": {"old": None, "new": p["name"]}, "category": {"old": None, "new": p["category"]},
                "base_unit": {"old": None, "new": p["base_unit"]}, "reorder_point": {"old": None, "new": p["reorder_point"]},
                "units_added": [f"{u} = {f:,} {p['base_unit']}" for u, f in p["units"]], "opening": qty,
                **({"opening_in_file": p["opening"] or 0} if qty != (p["opening"] or 0) else {}),
                "import_batch": bid, "row": p["row"]})
            summary["new_items"] += 1
            summary["items"].append({"item_id": item_id, "new": True})

        for p in groups["diff"]:
            changes = {}
            for f in p["fields"]:
                if not f["can"] or (p["row"], f["field"]) not in picks:
                    continue
                if f["field"] == "active":
                    db.execute("UPDATE supply_items SET active = 1 WHERE id = ?", (p["item_id"],))
                    db.execute("UPDATE supply_categories SET active = 1 WHERE id = (SELECT category_id FROM supply_items WHERE id = ?)",
                               (p["item_id"],))
                    changes["active"] = {"old": 0, "new": 1}
                elif f["field"] in ("base_unit", "reorder_point", "note"):
                    db.execute(f"UPDATE supply_items SET {f['field']} = ? WHERE id = ?", (f["file"], p["item_id"]))
                    changes[f["field"]] = {"old": f["db"], "new": f["file"]}
                elif f["field"] == "units":
                    for uname, factor in f["add"]:
                        if not db.execute("UPDATE supply_units SET active = 1 WHERE item_id = ? AND name = ? AND factor = ?",
                                          (p["item_id"], uname, factor)).rowcount:
                            db.execute("INSERT INTO supply_units (item_id, name, factor) VALUES (?, ?, ?)",
                                       (p["item_id"], uname, factor))
                    changes["units_added"] = [f"{u} = {n:,}" for u, n in f["add"]]
                elif f["field"] == "opening":
                    qty = openings.get(p["row"], f["file"])
                    if qty:
                        opening(p["item_id"], qty)
                        changes["opening"] = qty
                        if qty != f["file"]:
                            changes["opening_in_file"] = f["file"]
            if changes:
                audit.log(db, user_id, "supply_items", p["item_id"], "update", {**changes, "import_batch": bid, "row": p["row"]})
                summary["updated_items"] += 1
                summary["items"].append({"item_id": p["item_id"], "new": False, "changes": changes})

        db.execute("UPDATE supply_import_batches SET summary = ? WHERE id = ?",
                   (json.dumps({k: v for k, v in summary.items() if k != "items"}, ensure_ascii=False), bid))
        audit.log(db, user_id, "supply_import_batches", bid, "insert", {
            "file_name": {"old": None, "new": filename[:200]}, "opening_date": {"old": None, "new": opening_date.isoformat()},
            "summary": {k: v for k, v in summary.items() if k != "items"}, "items": summary["items"]})
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return bid, summary, saved


def batches(db):
    return db.execute("""SELECT b.*, u.display_name, u.username,
                                (SELECT COUNT(*) FROM supply_moves m WHERE m.import_batch_id = b.id AND m.kind = 'opening') AS n_opening
                         FROM supply_import_batches b JOIN users u ON u.id = b.created_by ORDER BY b.id DESC""").fetchall()


def batch_items(db, bid):
    """[{item_id, new, changes}] จาก audit_log ของชุดนี้"""
    row = db.execute("""SELECT changes FROM audit_log WHERE table_name = 'supply_import_batches' AND record_id = ?
                        AND action = 'insert' ORDER BY id LIMIT 1""", (bid,)).fetchone()
    return json.loads(row[0]).get("items", []) if row else []


def undo_blockers(db, bid):
    """รายการในชุดที่มีรับ/จ่าย/ปรับยอดหลังนำเข้า (ยกเลิกทั้งชุดไม่ได้)"""
    ids = [it["item_id"] for it in batch_items(db, bid)]
    if not ids:
        return []
    row = db.execute("SELECT summary FROM supply_import_batches WHERE id = ?", (bid,)).fetchone()
    after = (json.loads(row[0] or "{}") if row else {}).get("max_move_id", 0)
    return db.execute(f"""SELECT DISTINCT i.name FROM supply_moves m JOIN supply_items i ON i.id = m.item_id
                          WHERE m.item_id IN ({','.join('?' * len(ids))}) AND m.id > ? AND m.import_batch_id IS NOT ?
                          ORDER BY i.name""", [*ids, after, bid]).fetchall()


def undo(db, user_id, bid, reason):
    """ยกเลิกการนำเข้าทั้งชุด: กลับยอดยกมา, ปิดใช้งานรายการ/หน่วย/หมวดที่ชุดนี้สร้าง, คืนค่าช่องที่ชุดนี้แก้ (ไม่ลบแถว)"""
    reason = sp.clean(reason, sp.TEXT_MAX)
    if not reason:
        raise sp.SupplyError("กรอกเหตุผลที่ยกเลิกการนำเข้า")
    b = db.execute("SELECT * FROM supply_import_batches WHERE id = ?", (bid,)).fetchone()
    if b is None:
        raise sp.SupplyError("ไม่พบชุดนำเข้านี้")
    if b["undone_at"]:
        raise sp.SupplyError("ชุดนี้ยกเลิกไปแล้ว")
    blockers = undo_blockers(db, bid)
    if blockers:
        raise sp.SupplyError("ยกเลิกทั้งชุดไม่ได้ เพราะมีรายการรับ/จ่ายหลังนำเข้าแล้ว: " + ", ".join(r[0] for r in blockers[:10])
                             + (" …" if len(blockers) > 10 else "") + " · ยกเลิกใบรับ/ใบเบิกเหล่านั้นก่อน หรือแก้ทีละรายการ")
    items = batch_items(db, bid)
    sp.begin(db)
    try:
        for m in db.execute("SELECT * FROM supply_moves WHERE import_batch_id = ? AND kind = 'opening'", (bid,)).fetchall():
            db.execute("""INSERT INTO supply_moves (item_id, move_date, kind, qty, import_batch_id, reverses_id, note, created_by)
                          VALUES (?, ?, 'reversal', ?, ?, ?, ?, ?)""",
                       (m["item_id"], m["move_date"], -m["qty"], bid, m["id"], f"ยกเลิกการนำเข้าชุด #{bid}: {reason}", user_id))
        for it in items:
            if it.get("new"):
                db.execute("UPDATE supply_items SET active = 0 WHERE id = ?", (it["item_id"],))
                db.execute("UPDATE supply_units SET active = 0 WHERE item_id = ?", (it["item_id"],))
                audit.log(db, user_id, "supply_items", it["item_id"], "update",
                          {"active": {"old": 1, "new": 0}, "undo_import_batch": bid})
            else:
                ch = it.get("changes") or {}
                back = {}
                for field in ("active", "base_unit", "reorder_point", "note"):
                    if field in ch:
                        db.execute(f"UPDATE supply_items SET {field} = ? WHERE id = ?", (ch[field]["old"], it["item_id"]))
                        back[field] = {"old": ch[field]["new"], "new": ch[field]["old"]}
                for text in ch.get("units_added", []):
                    uname = text.split(" = ")[0]
                    db.execute("UPDATE supply_units SET active = 0 WHERE item_id = ? AND name = ?", (it["item_id"], uname))
                if back or ch.get("units_added"):
                    audit.log(db, user_id, "supply_items", it["item_id"], "update",
                              {**back, **({"units_disabled": ch["units_added"]} if ch.get("units_added") else {}),
                               "undo_import_batch": bid})
        # หมวดที่ชุดนี้สร้างและไม่มีรายการใช้งานเหลือ: ปิดใช้งาน
        for (cid,) in db.execute("""SELECT record_id FROM audit_log WHERE table_name = 'supply_categories' AND action = 'insert'
                                    AND json_extract(changes, '$.import_batch') = ?""", (bid,)).fetchall():
            if not db.execute("SELECT 1 FROM supply_items WHERE category_id = ? AND active = 1", (cid,)).fetchone():
                db.execute("UPDATE supply_categories SET active = 0 WHERE id = ?", (cid,))
        db.execute("UPDATE supply_import_batches SET undone_at = CURRENT_TIMESTAMP, undone_by = ?, undo_reason = ? WHERE id = ?",
                   (user_id, reason, bid))
        audit.log(db, user_id, "supply_import_batches", bid, "undo", {"undo_reason": {"old": None, "new": reason}})
        db.commit()
    except BaseException:
        db.rollback()
        raise
