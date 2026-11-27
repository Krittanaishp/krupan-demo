"""ข้อมูลรายงาน: รายละเอียดทรัพย์สิน (แบบชีตเดิม แยกหมวด), ครุภัณฑ์ได้มาในปี, สรุป, จำหน่ายในปี, export ทั้งหมด"""
import re
from collections import Counter

from app import settings_store, thai
from app.records import DISPOSED

# ชื่อหน่วยงานในหัวรายงานมาจากค่าตั้งค่า ORG_REPORT_NAME (settings_store.office_line) ไม่ฝังในโค้ด
NO_CATEGORY = "none"
NO_CATEGORY_NAME = "ไม่ระบุหมวด"
# ชื่อที่บอกจำนวนอยู่แล้ว ("จำนวน 100 ตัว", "15 ตัว") ไม่ต้องเติม "จำนวน n รายการ" ซ้ำ
QTY_RE = re.compile(r"จำนวน|\d+\s*(ตัว|เครื่อง|ชุด|ใบ|อัน|คัน|หลัง|ตู้|ชิ้น|เล่ม|ผืน)")
REG_RE = re.compile(r"^(.*-)(\d+)$")
# ชิ้นที่รวมแถวกันได้ต้องเหมือนกันทุกช่องนี้ และเลขทะเบียนต่อเนื่องกัน
GROUP_KEYS = ("name", "details", "acquired_date", "acquired_date_precision", "unit_price", "funding_source")

REGISTER_COLUMNS = ("ลำดับ", "วัน,เดือน,ปี", "ประเภทครุภัณฑ์", "หมายเลขทะเบียน", "ราคาต่อหน่วย", "วิธีการได้มา",
                    "ใช้ประจำที่", "หมายเหตุ")
# ---------------------------------------------------------------- การจัดหน้าแบบไฟล์ต้นฉบับ
# วัดจากชีต "14." ของไฟล์ Excel เดิม (data/raw อ่านด้วย xlrd + record ตั้งหน้ากระดาษ) และ PDF ที่ Excel พิมพ์ชีตนั้นเป็น A4:
# ฟอนต์ TH SarabunIT๙ 16 ทุกช่อง (หัวรายงาน/หัวตารางไม่หนา แถว "ประเภท ..." หนา), A4 แนวนอน สเกล 100% กึ่งกลางแนวนอน
# ความกว้างคอลัมน์ (หน่วยตัวอักษรของ Excel) ใช้ใน Excel
REGISTER_WIDTHS = (5, 10.875, 40.875, 27.25, 11.5, 15.875, 14.375, 9)
# ความกว้างเดียวกันตอน Excel พิมพ์ออกมาจริง (pt วัดจาก PDF) ใช้ในหน้าพิมพ์ รวม 759.7pt = 268 มม.
REGISTER_PRINT_PT = (28.2, 61.5, 230.6, 153.5, 64.6, 89.7, 80.8, 50.8)
REGISTER_FONT_PT = 16
MIN_SAFE_FONT_PT = 15          # แบบ "ปลอดภัยสำหรับเครื่องพิมพ์" ย่อได้แต่ตัวอักษรต้องไม่เล็กกว่านี้
TITLE_ROW_PT = 20.25           # หัวรายงาน 2 บรรทัด และแถวข้อมูล
HEAD_ROW_PT = 39.95            # หัวตาราง
A4_PT = (595.28, 841.89)       # กว้าง x สูง แนวตั้ง
SAFE_SIDE_IN = round(5 / 25.4, 3)  # ขอบซ้าย/ขวา (และล่าง) 5 มม. = 0.197 นิ้ว
# ขอบกระดาษ (นิ้ว) ซ้าย ขวา บน ล่าง: ชีตหมวดเดิม ("14." "13.") และชีต "เพิ่ม 70" (แทนด้วยรายงานได้มาในปี)
MARGINS_IN = {"register": (0.039, 0.039, 0.472, 0.157), "acquired": (0.7, 0.7, 0.75, 0.75)}
ORIENTATION = {"register": "landscape", "acquired": "portrait"}
HEADER_FOOTER_IN = {"register": (0.197, 0.118), "acquired": (0.3, 0.3)}
MARGIN_CHOICES = {"original": "ตรงต้นฉบับ", "safe": "ปลอดภัยสำหรับเครื่องพิมพ์"}


def layout(kind="register", margin="original"):
    """ขนาดกระดาษ ขอบ ความกว้างคอลัมน์ และขนาดตัวอักษรของรายงานทะเบียน
    original: ขอบตามต้นฉบับ ตัวอักษร 16pt (ได้มาในปีเป็นแนวตั้ง คอลัมน์แคบลงให้พอดีหน้า ข้อความตัดบรรทัด)
    safe: ขอบซ้าย/ขวา/ล่าง 5 มม. ย่อทั้งหน้าให้พอดีความกว้าง แต่ตัวอักษรไม่เล็กกว่า 15pt"""
    kind = "acquired" if kind == "acquired" else "register"
    left, right, top, bottom = MARGINS_IN[kind]
    if margin == "safe":
        left = right = SAFE_SIDE_IN
        bottom = max(bottom, SAFE_SIDE_IN)
    else:
        margin = "original"
    landscape = ORIENTATION[kind] == "landscape"
    page_w, page_h = (A4_PT[1], A4_PT[0]) if landscape else A4_PT
    avail = page_w - (left + right) * 72
    factor = min(1.0, avail / sum(REGISTER_PRINT_PT))
    font_pt = REGISTER_FONT_PT if margin == "original" else max(MIN_SAFE_FONT_PT, round(REGISTER_FONT_PT * factor, 1))
    return {"kind": kind, "margin": margin, "margin_label": MARGIN_CHOICES[margin], "orientation": ORIENTATION[kind],
            "page_mm": (round(page_w / 72 * 25.4), round(page_h / 72 * 25.4)),
            "margins_in": {"left": left, "right": right, "top": top, "bottom": bottom},
            "header_footer_in": HEADER_FOOTER_IN[kind], "center": kind == "register", "factor": factor,
            "widths_pt": [round(w * factor, 1) for w in REGISTER_PRINT_PT],
            "widths_chars": [round(w * factor, 3) for w in REGISTER_WIDTHS],
            "font_pt": font_pt, "row_pt": round(TITLE_ROW_PT * font_pt / REGISTER_FONT_PT, 2),
            "head_pt": HEAD_ROW_PT}


def money_text(v):
    return "" if v is None else f"{float(v):,.2f}"


# รูปแบบราคาในชีตหมวดเดิม (รูปแบบบัญชีของ Excel): ศูนย์แสดง "-" ส่วนราคาที่ไม่ทราบ (NULL) เว้นว่าง
ACCOUNTING_FORMAT = '_-* #,##0.00_-;\\-* #,##0.00_-;_-* "-"??_-;_-@_-'


def accounting_text(v):
    """ข้อความราคาในหน้าพิมพ์แบบรูปแบบบัญชี: 0 -> "-", ไม่ทราบราคา -> "" """
    if v is None:
        return ""
    return "-" if float(v) == 0 else money_text(v)


# ---------------------------------------------------------------- ตัวเลือกจากฟอร์ม

def parse_options(params, categories):
    """ค่าจาก query string -> ตัวเลือกของรายงาน (ค่าเริ่มต้น: รวมเลขต่อเนื่อง, เลขไทย, ไม่รวมที่จำหน่ายแล้ว, ทุกหมวด)"""
    fy = thai.to_int(params.get("fy")) or thai.current_fiscal_year()
    if not 2400 <= fy <= 2700:
        fy = thai.current_fiscal_year()
    codes = {c["code"] for c in categories}
    picked = [c for c in params.getlist("cat") if c in codes] if hasattr(params, "getlist") else []
    return {"fy": fy, "cats": picked or sorted(codes), "all_cats": not picked or set(picked) == codes,
            "group": params.get("group", "1") != "0", "thai": params.get("digits", "thai") != "arabic",
            "disposed": params.get("disposed", "0") == "1",
            "margin": "safe" if params.get("margin") == "safe" else "original"}


def categories(db):
    """หมวดที่มีครุภัณฑ์อยู่ (+ ไม่ระบุหมวด) [{code, name, n}]"""
    rows = db.execute("""SELECT COALESCE(t.category_code, ?) AS code, COALESCE(c.name, ?) AS name, COUNT(*) AS n
                         FROM assets a LEFT JOIN asset_types t ON t.code = a.type_code
                         LEFT JOIN categories c ON c.code = t.category_code
                         GROUP BY 1 ORDER BY t.category_code IS NULL, t.category_code""",
                      (NO_CATEGORY, NO_CATEGORY_NAME)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- รวมเลขทะเบียนต่อเนื่อง

def reg_parts(reg_no):
    m = REG_RE.match(reg_no or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def continues(prev, row):
    """row ต่อจาก prev ได้ไหม: ช่องสำคัญเหมือนกันทุกช่อง และเลขลำดับท้ายเลขทะเบียน = เลขเดิม + 1 (prefix และจำนวนหลักเท่ากัน)"""
    if any(prev[k] != row[k] for k in GROUP_KEYS):
        return False
    p_pre, p_seq = reg_parts(prev["reg_no"])
    r_pre, r_seq = reg_parts(row["reg_no"])
    if p_pre is None or r_pre is None or p_pre != r_pre or len(p_seq) != len(r_seq):
        return False
    return int(r_seq) == int(p_seq) + 1


def group_runs(rows, enabled=True):
    """[[row, ...], ...] แต่ละกลุ่มคือชิ้นที่รวมเป็นแถวเดียวได้ (enabled=False = กลุ่มละชิ้น)"""
    runs = []
    for r in rows:
        if enabled and runs and continues(runs[-1][-1], r):
            runs[-1].append(r)
        else:
            runs.append([r])
    return runs


def location_label(r):
    return r["location_name"] or r["location_text"] or ""


def entry(run, number, thai_digits):
    """แถวในรายงานจากกลุ่มชิ้นที่รวมกัน (ค่าเป็นข้อความพร้อมแสดง เลขไทยตามตัวเลือก)"""
    first, last = run[0], run[-1]
    n = len(run)
    code = first["reg_no"] or first["temp_code"]
    if n > 1:
        prefix, seq = reg_parts(first["reg_no"])
        _, last_seq = reg_parts(last["reg_no"])
        reg_lines = [prefix, f"{seq}-{last_seq}"]  # แบบชีตเดิม: "ปค.99-9999-1-1422-" / "0001-0100"
        reg_full = f"{first['reg_no']}-{last_seq}"
    else:
        reg_lines, reg_full = [code], code
    # ชิ้นเดียว: บรรทัดเดียว แต่หน้าพิมพ์ให้ตัดบรรทัดหลังขีดที่ 4 ได้ถ้าคอลัมน์แคบ (ใส่ <wbr>)
    split = reg_parts(first["reg_no"]) if n == 1 else (None, None)
    name = first["name"]
    if n > 1 and not QTY_RE.search(name):
        name = f"{name} จำนวน {n} รายการ"
    places = Counter(location_label(r) for r in run)
    if len(places) == 1:
        place = next(iter(places))
    else:
        place = ", ".join(f"{k or 'ไม่ระบุ'} ({v})" for k, v in places.most_common())
    notes = []
    for r in run:
        if r["note"] and r["note"] not in notes:
            notes.append(r["note"])
    d = lambda s: thai.digits(s, thai_digits)  # noqa: E731
    return {
        "no": d(number), "date": d(thai.short_date(first["acquired_date"], first["acquired_date_precision"])),
        "name": d(name), "details": d(first["details"]), "reg_lines": [d(x) for x in reg_lines], "reg": d(reg_full),
        "reg_split": [d(x) for x in split] if split[0] else None,
        "price": d(accounting_text(first["unit_price"])), "price_value": first["unit_price"],
        "funding": d(first["funding_source"]), "place": d(place), "note": d(" / ".join(notes)), "count": n,
        "ids": [r["id"] for r in run], "disposed": all(r["status"] == DISPOSED for r in run),
    }


# ---------------------------------------------------------------- รายละเอียดทรัพย์สิน / ได้มาในปี

REGISTER_SQL = """SELECT a.*, COALESCE(t.category_code, ?) AS cat_code, COALESCE(c.name, ?) AS cat_name,
                         l.name AS location_name
                  FROM assets a LEFT JOIN asset_types t ON t.code = a.type_code
                  LEFT JOIN categories c ON c.code = t.category_code
                  LEFT JOIN locations l ON l.id = a.location_id"""
# ในหมวดเรียงตามวันที่ได้มาแบบชีตเดิม (ชิ้นที่รวมแถวกันได้วันที่เดียวกันอยู่แล้ว จึงยังติดกัน)
REGISTER_ORDER = ("ORDER BY t.category_code IS NULL, t.category_code, a.acquired_date IS NULL, a.acquired_date, "
                  "a.reg_no IS NULL, a.reg_no, a.temp_code")


def register(db, opts, kind="register"):
    """kind = register (ทุกชิ้นที่ได้มาไม่เกินสิ้นปีงบประมาณ) | acquired (ได้มาในปีงบประมาณนั้น)"""
    where, params = [], [NO_CATEGORY, NO_CATEGORY_NAME]
    if kind == "acquired":
        where.append("a.fiscal_year = ?")
        params.append(opts["fy"])
    else:
        where.append("(a.acquired_date IS NULL OR a.acquired_date <= ?)")
        params.append(thai.fiscal_year_bounds(opts["fy"])[1])
    if not opts["disposed"]:
        where.append("a.status != ?")
        params.append(DISPOSED)
    where.append("a.duplicate_of_id IS NULL")  # รายการซ้ำไม่นับในรายงาน (app/duplicates.py)
    rows = db.execute(f"{REGISTER_SQL} WHERE {' AND '.join(where)} {REGISTER_ORDER}", params).fetchall()
    wanted = set(opts["cats"])
    sections, number = [], 0
    for r in rows:
        if r["cat_code"] not in wanted:
            continue
        if not sections or sections[-1]["code"] != r["cat_code"]:
            sections.append({"code": r["cat_code"], "name": r["cat_name"], "rows": []})
        sections[-1]["rows"].append(r)
    for s in sections:
        s["entries"] = []
        for run in group_runs(s.pop("rows"), opts["group"]):
            number += 1
            s["entries"].append(entry(run, number, opts["thai"]))
        s["n_items"] = sum(e["count"] for e in s["entries"])
    t = opts["thai"]
    fy = thai.digits(opts["fy"], t)
    if kind == "acquired":
        title, line2 = f"รายการครุภัณฑ์ได้มาในปีงบประมาณ {fy}", settings_store.office_line(db, extra=True)
    else:
        title, line2 = f"รายละเอียดทรัพย์สิน ประจำปีงบประมาณ {fy}", settings_store.office_line(db)
    return {"kind": kind, "title": title, "line2": line2, "sections": sections, "opts": opts,
            "signers": signer_lines(db, t), "n_items": sum(s["n_items"] for s in sections)}


def signer_lines(db, thai_digits=True):
    return [{"name": thai.digits(s["name"], thai_digits), "role": s["role"]}
            for s in settings_store.signers(db) if s["role"] or s["name"]]


# ---------------------------------------------------------------- สรุปครุภัณฑ์

def summary(db, include_disposed=False, building="", dept=""):
    """building / dept: id หรือ "none" (ห้องที่ยังไม่ระบุ) หรือ "" (ทั้งหมด) · รายการซ้ำไม่นับเสมอ"""
    where, params = ["a.duplicate_of_id IS NULL"], []
    if not include_disposed:
        where.append("a.status != 'จำหน่ายแล้ว'")
    for value, col in ((building, "l.building_id"), (dept, "l.department_id")):
        if value == "none":
            where.append(f"{col} IS NULL")
        elif str(value).isdigit():
            where.append(f"{col} = ?")
            params.append(int(value))
    base = f"""FROM assets a LEFT JOIN asset_types t ON t.code = a.type_code
               LEFT JOIN categories c ON c.code = t.category_code
               LEFT JOIN locations l ON l.id = a.location_id
               LEFT JOIN buildings b ON b.id = l.building_id
               LEFT JOIN departments dp ON dp.id = l.department_id
               WHERE {' AND '.join(where)}"""
    agg = "COUNT(*) AS n, COALESCE(SUM(a.unit_price), 0) AS value, SUM(a.unit_price IS NULL) AS unknown"
    by_cat = db.execute(f"""SELECT COALESCE(c.name, '{NO_CATEGORY_NAME}') AS label, {agg} {base}
                            GROUP BY t.category_code ORDER BY t.category_code IS NULL, t.category_code""", params).fetchall()
    by_building = db.execute(f"""SELECT COALESCE(b.name, CASE WHEN a.location_id IS NULL THEN 'ยังไม่จัดเข้าห้อง'
                                                         ELSE 'ห้องที่ยังไม่ระบุอาคาร' END) AS label, {agg} {base}
                                 GROUP BY b.id, a.location_id IS NULL
                                 ORDER BY b.id IS NULL, b.sort_order, b.name, a.location_id IS NULL""", params).fetchall()
    by_dept = db.execute(f"""SELECT COALESCE(dp.name, CASE WHEN a.location_id IS NULL THEN 'ยังไม่จัดเข้าห้อง'
                                                      ELSE 'ห้องที่ยังไม่ระบุฝ่าย' END) AS label, {agg} {base}
                             GROUP BY dp.id, a.location_id IS NULL
                             ORDER BY dp.id IS NULL, dp.sort_order, dp.name, a.location_id IS NULL""", params).fetchall()
    by_room = db.execute(f"""SELECT COALESCE(l.name, 'ยังไม่จัดเข้าห้อง') AS label, {agg} {base}
                             GROUP BY a.location_id ORDER BY a.location_id IS NULL, l.name""", params).fetchall()
    by_status = db.execute(f"""SELECT a.status AS label, {agg} {base} GROUP BY a.status
                               ORDER BY CASE a.status WHEN 'ใช้งาน' THEN 1 WHEN 'ชำรุด' THEN 2 WHEN 'รอจำหน่าย' THEN 3
                                        WHEN 'สูญหาย' THEN 4 ELSE 5 END""", params).fetchall()
    total = db.execute(f"SELECT {agg} {base}", params).fetchone()
    n_duplicates = db.execute("SELECT COUNT(*) FROM assets WHERE duplicate_of_id IS NOT NULL").fetchone()[0]
    return {"tables": [("แยกตามหมวด", by_cat), ("แยกตามอาคาร", by_building), ("แยกตามฝ่าย", by_dept),
                       ("แยกตามห้อง", by_room), ("แยกตามสถานะ", by_status)],
            "total": total, "include_disposed": include_disposed, "office_line": settings_store.office_line(db),
            "building": building, "dept": dept, "n_duplicates": n_duplicates}


# ---------------------------------------------------------------- จำหน่ายในปีงบประมาณ

def disposals(db, fy, thai_digits=True):
    start, end = thai.fiscal_year_bounds(fy)
    rows = db.execute("""SELECT d.*, a.reg_no, a.temp_code, a.name, a.unit_price
                         FROM disposals d JOIN assets a ON a.id = d.asset_id
                         WHERE d.cancelled_at IS NULL AND d.disposed_at BETWEEN ? AND ?
                         ORDER BY d.disposed_at, d.lot_id, a.reg_no IS NULL, a.reg_no, a.temp_code""",
                      (start, end)).fetchall()
    d = lambda s: thai.digits(s, thai_digits)  # noqa: E731
    lot_sizes = Counter(r["lot_id"] for r in rows)
    out = []
    for i, r in enumerate(rows, start=1):
        amount = money_text(r["amount"])
        if r["amount"] is not None and lot_sizes[r["lot_id"]] > 1:
            amount += f" (รวม {lot_sizes[r['lot_id']]} รายการ)"
        out.append({"no": d(i), "date": d(thai.short_date(r["disposed_at"])), "code": d(r["reg_no"] or r["temp_code"]),
                    "name": d(r["name"]), "price": d(money_text(r["unit_price"])), "price_value": r["unit_price"],
                    "method": r["method"], "doc_ref": d(r["doc_ref"]), "amount": d(amount), "amount_value": r["amount"],
                    "note": d(r["note"]), "asset_id": r["asset_id"]})
    known = [r["unit_price"] for r in rows if r["unit_price"] is not None]
    return {"fy": fy, "fy_text": d(fy), "rows": out, "n": len(rows), "value": sum(float(x) for x in known),
            "unknown": len(rows) - len(known), "amount": sum(float(r["amount"]) for r in rows if r["amount"] is not None),
            "signers": signer_lines(db, thai_digits), "thai": thai_digits,
            "office_line": d(settings_store.office_line(db))}
