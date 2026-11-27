"""รายงานเป็น Excel: รายละเอียดทรัพย์สิน (หน้าตาแบบชีตเดิม ชีตละหมวด), จำหน่ายในปี, export ทั้งหมด
ฟอนต์ TH SarabunIT๙ 16 แบบไฟล์เดิม (ถ้าเครื่องที่เปิดไม่มีฟอนต์นี้ Excel จะใช้ฟอนต์อื่นแทนเอง)"""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from app import reports

FONT = "TH SarabunIT๙"
THIN = Side(style="thin", color="000000")
HAIR = Side(style="hair", color="000000")   # เส้นประบาง ๆ ระหว่างแถวแบบชีตเดิม
HEAD = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BODY = Border(left=THIN, right=THIN, top=HAIR, bottom=HAIR)
MONEY = "#,##0.00"
# ประมาณจำนวนตัวอักษรไทย (ไม่นับสระบน/ล่าง วรรณยุกต์) ต่อความกว้าง 1 หน่วยของคอลัมน์ ที่ฟอนต์ 16pt ใช้คำนวณความสูงแถว
CHARS_PER_UNIT = 0.75  # วัดจากการพิมพ์ด้วย Excel: น้อยกว่านี้ข้อความในคอลัมน์แคบ (วิธีการได้มา หมายเหตุ) ถูกตัด
COMBINING = {0x0E31, *range(0x0E34, 0x0E3B), *range(0x0E47, 0x0E4F)}
INVALID_SHEET = str.maketrans({c: " " for c in "[]:*?/\\"})


def font(bold=False, size=16):
    return Font(name=FONT, size=size, bold=bold)


def save(wb):
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def sheet_title(text, used):
    base = text.translate(INVALID_SHEET).strip()[:28] or "ชีต"
    name, i = base, 2
    while name in used:
        name, i = f"{base[:25]} ({i})", i + 1
    used.add(name)
    return name


def page_setup(ws, widths, repeat_rows):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.page_margins.top = ws.page_margins.bottom = 0.5
    ws.print_title_rows = repeat_rows


def titles(ws, lines, ncols):
    for i, text in enumerate(lines, start=1):
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=ncols)
        for col in range(2, ncols + 1):  # ช่องที่ถูก merge ใช้ฟอนต์เดียวกัน (ไม่ค้างเป็น Calibri)
            ws.cell(i, col).font = font()
        c = ws.cell(i, 1, text)
        c.font = font()
        c.alignment = Alignment(horizontal="center")
        ws.row_dimensions[i].height = 20.25


def header(ws, row, names):
    for col, name in enumerate(names, start=1):
        c = ws.cell(row, col, name)
        c.font = font()
        c.border = HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 39.95


def cell(ws, row, col, value, align=None, money=False, border=BODY):
    c = ws.cell(row, col, value)
    c.font = font()
    c.border = border
    c.alignment = Alignment(horizontal=align, vertical="top", wrap_text=True)
    if money and isinstance(value, (int, float)):
        c.number_format = MONEY
    return c


def text_lines(value, width_chars):
    """จำนวนบรรทัดโดยประมาณเมื่อข้อความตัดบรรทัดในคอลัมน์กว้าง width_chars (ใช้ตั้งความสูงแถว 20.25pt ต่อบรรทัด)"""
    if value is None or value == "":
        return 1
    cap = max(4, int(width_chars * CHARS_PER_UNIT))
    lines = 0
    for seg in str(value).split("\n"):
        visible = sum(1 for ch in seg if ord(ch) not in COMBINING)
        lines += max(1, -(-visible // cap))
    return lines


def register_page_setup(ws, layout):
    """ตั้งหน้ากระดาษแบบชีตหมวดเดิม: A4 สเกล 100% ไม่ย่อให้พอดีหน้า ขอบตามต้นฉบับ กึ่งกลางแนวนอน หัวรายงาน+หัวตารางพิมพ์ซ้ำ"""
    for i, w in enumerate(layout["widths_chars"], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = layout["orientation"]
    ws.page_setup.scale = 100
    ws.sheet_properties.pageSetUpPr.fitToPage = False
    m = layout["margins_in"]
    ws.page_margins.left, ws.page_margins.right = m["left"], m["right"]
    ws.page_margins.top, ws.page_margins.bottom = m["top"], m["bottom"]
    ws.page_margins.header, ws.page_margins.footer = layout["header_footer_in"]
    ws.print_options.horizontalCentered = layout["center"]
    ws.print_title_rows = "1:4"


def signatures(ws, row, signers, ncols):
    """ช่องลงชื่อ 3 คนเรียงกันในแถวเดียว แล้วชื่อในวงเล็บแถวถัดไป (แบบท้ายชีตเดิม)"""
    if not signers:
        return row
    row += 2
    spans = [(2, 3), (4, 5), (6, ncols)] if ncols >= 8 else [(1, 2), (3, 4), (5, ncols)]
    for start in range(0, len(signers), 3):
        for (c1, c2), s in zip(spans, signers[start:start + 3]):
            for r, text in ((row, f"ลงชื่อ{'.' * 26}{s['role']}"), (row + 1, f"({s['name'] or '.' * 30})")):
                ws.merge_cells(start_row=r, start_column=c1, end_row=r, end_column=c2)
                c = ws.cell(r, c1, text)
                c.font = font()
                c.alignment = Alignment(horizontal="center")
        row += 4
    return row


# ---------------------------------------------------------------- รายละเอียดทรัพย์สิน (ชีตละหมวด)

def register(data):
    layout = data.get("layout") or reports.layout(data.get("kind", "register"))
    widths = layout["widths_chars"]
    wb = Workbook()
    wb.remove(wb.active)
    used = set()
    if not data["sections"]:
        ws = wb.create_sheet("ไม่มีรายการ")
        ws.cell(1, 1, "ไม่มีครุภัณฑ์ตามเงื่อนไขที่เลือก").font = font()
    for s in data["sections"]:
        ws = wb.create_sheet(sheet_title(f"{s['code']}. {s['name']}" if s["code"] != reports.NO_CATEGORY else s["name"], used))
        titles(ws, [data["title"], data["line2"]], 8)
        for col in range(1, 9):  # เส้นล่าง thin ใต้หัวรายงานบรรทัดที่ 2
            ws.cell(2, col).border = Border(bottom=THIN)
        cols = list(reports.REGISTER_COLUMNS)
        cols[2] = f"ประเภทครุภัณฑ์ {s['name']}"
        header(ws, 3, cols)
        for col in range(1, 9):  # หัวตารางชีตเดิมไม่ตัดบรรทัด ("ลำดับ" ในคอลัมน์กว้าง 5)
            ws.cell(3, col).alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
        # แถว "ประเภท ..." ตัวหนาในคอลัมน์รายการ (แบบชีตเดิม) เส้นล่าง hair
        for col in range(1, 9):
            c = ws.cell(4, col)
            c.font = font()
            c.border = Border(left=THIN, right=THIN, top=THIN, bottom=HAIR)
        c = ws.cell(4, 3, f"ประเภท {s['name']}")
        c.font = font(bold=True)
        c.alignment = Alignment(horizontal="center", vertical="bottom")
        ws.row_dimensions[4].height = reports.TITLE_ROW_PT
        row = 4
        for e in s["entries"]:
            row += 1
            name = e["name"] + (f"\n{e['details']}" if e["details"] else "")
            values = [(e["no"], "center"), (e["date"], "center"), (name, "left"), ("\n".join(e["reg_lines"]), "center"),
                      (e["price_value"] if e["price_value"] is not None else None, "right"),
                      (e["funding"], "center"), (e["place"], "center"), (e["note"], "center")]
            lines = 1
            for col, (v, align) in enumerate(values, start=1):
                c = cell(ws, row, col, v, align, money=col == 5)
                if col == 5 and isinstance(v, (int, float)):
                    c.number_format = reports.ACCOUNTING_FORMAT  # 0 แสดง "-" ไม่ทราบราคาเว้นว่าง (ไม่ใส่ค่า)
                if col != 5:
                    lines = max(lines, text_lines(v, widths[col - 1]))
            ws.row_dimensions[row].height = reports.TITLE_ROW_PT * lines
        for col in range(1, 9):  # เส้นปิดท้ายตาราง
            ws.cell(row, col).border = Border(left=THIN, right=THIN, top=HAIR, bottom=THIN)
        signatures(ws, row, data["signers"], 8)
        register_page_setup(ws, layout)
    return save(wb)


# ---------------------------------------------------------------- จำหน่ายในปีงบประมาณ

DISPOSAL_COLUMNS = ("ลำดับ", "วันที่จำหน่าย", "หมายเลขทะเบียน", "รายการ", "ราคาต่อหน่วย", "วิธีจำหน่าย",
                    "เลขที่หนังสืออนุมัติ", "จำนวนเงินที่ได้", "หมายเหตุ")


def disposals(data):
    wb = Workbook()
    ws = wb.active
    ws.title = "จำหน่าย"
    titles(ws, [f"รายการครุภัณฑ์ที่จำหน่ายในปีงบประมาณ {data['fy_text']}", data["office_line"]], len(DISPOSAL_COLUMNS))
    header(ws, 3, DISPOSAL_COLUMNS)
    row = 3
    for r in data["rows"]:
        row += 1
        for col, (v, align, m) in enumerate([(r["no"], "center", False), (r["date"], "center", False), (r["code"], None, False),
                                             (r["name"], None, False), (r["price_value"], "right", True),
                                             (r["method"], None, False), (r["doc_ref"], None, False),
                                             (r["amount_value"], "right", True), (r["note"], None, False)], start=1):
            cell(ws, row, col, v, align, money=m, border=HEAD)
    if not data["rows"]:
        row += 1
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(DISPOSAL_COLUMNS))
        cell(ws, row, 1, "ไม่มีรายการ", "center", border=HEAD)
    row += 2
    unknown = f" (ไม่ทราบมูลค่า {data['unknown']} รายการ)" if data["unknown"] else ""
    ws.cell(row, 1, f"รวม {data['n']} รายการ มูลค่าตามทะเบียน {data['value']:,.2f} บาท{unknown} "
                    f"จำนวนเงินที่ได้ {data['amount']:,.2f} บาท").font = font()
    signatures(ws, row, data["signers"], len(DISPOSAL_COLUMNS))
    page_setup(ws, (5, 11, 26, 34, 12, 14, 22, 14, 14), "3:3")
    ws.page_setup.orientation = "landscape"
    return save(wb)


# ---------------------------------------------------------------- สรุปครุภัณฑ์

SUMMARY_COLUMNS = ["", "จำนวน (ชิ้น)", "มูลค่า (บาท)", "ไม่ทราบมูลค่า (รายการ)"]


def summary(data, date_text):
    """ตารางสรุป 3 ชุด (หมวด ห้อง สถานะ) ต่อกันในชีตเดียว ตัวเลขเป็นตัวเลขจริง (รวมต่อใน Excel ได้)"""
    wb = Workbook()
    ws = wb.active
    ws.title = "สรุป"
    ncol = len(SUMMARY_COLUMNS)
    scope = "รวม" if data["include_disposed"] else "ไม่รวม"
    titles(ws, ["สรุปครุภัณฑ์", data["office_line"], f"ข้อมูล ณ วันที่ {date_text} ({scope}รายการที่จำหน่ายแล้ว)"], ncol)
    t = data["total"]
    row = 5
    ws.cell(row, 1, f"ทั้งหมด {t['n']:,} ชิ้น มูลค่า {t['value']:,.2f} บาท"
                    + (f" ไม่ทราบมูลค่า {t['unknown']:,} รายการ" if t["unknown"] else "")).font = font(bold=True)
    ws.cell(row + 1, 1, "มูลค่ารวมเฉพาะชิ้นที่ทราบราคา ชิ้นที่ไม่ทราบราคานับแยกไว้ ไม่ได้นับเป็น 0 บาท").font = font(size=14)
    row += 3
    for title, rows in data["tables"]:
        ws.cell(row, 1, title).font = font(bold=True)
        row += 1
        header(ws, row, [title[6:] or title, *SUMMARY_COLUMNS[1:]])
        for r in rows:
            row += 1
            cell(ws, row, 1, r["label"], border=HEAD)
            cell(ws, row, 2, r["n"], "right", border=HEAD).number_format = "#,##0"
            cell(ws, row, 3, float(r["value"]), "right", money=True, border=HEAD)
            cell(ws, row, 4, r["unknown"] or 0, "right", border=HEAD).number_format = "#,##0"
        row += 1
        cell(ws, row, 1, "รวม", border=HEAD).font = font(bold=True)
        for col, v, fmt in ((2, sum(r["n"] for r in rows), "#,##0"), (3, sum(float(r["value"]) for r in rows), MONEY),
                            (4, sum(r["unknown"] or 0 for r in rows), "#,##0")):
            c = cell(ws, row, col, v, "right", border=HEAD)
            c.number_format = fmt
            c.font = font(bold=True)
        row += 3
    page_setup(ws, (40, 14, 20, 22), None)
    return save(wb)


# ---------------------------------------------------------------- export ทั้งหมด (ทางสำรองถ้าระบบมีปัญหา)

ASSET_EXTRA = [("category_name", "หมวด"), ("type_name", "ชื่อประเภท"), ("location_name", "ห้อง"),
               ("holder_name", "ผู้รับผิดชอบ"), ("holder_position", "ตำแหน่งผู้รับผิดชอบ")]
EXPORT_SHEETS = [
    ("ห้อง", """SELECT l.*, b.name AS building_name, d.name AS department_name FROM locations l
                LEFT JOIN buildings b ON b.id = l.building_id LEFT JOIN departments d ON d.id = l.department_id
                ORDER BY l.id"""),
    ("ผู้รับผิดชอบ", "SELECT * FROM staff ORDER BY id"),
    ("ประวัติการย้าย", """SELECT m.*, a.reg_no, a.temp_code, fl.name AS from_location, tl.name AS to_location
                          FROM asset_moves m JOIN assets a ON a.id = m.asset_id
                          LEFT JOIN locations fl ON fl.id = m.from_location_id
                          LEFT JOIN locations tl ON tl.id = m.to_location_id ORDER BY m.id"""),
    ("ประวัติซ่อม", """SELECT r.*, a.reg_no, a.temp_code, a.name FROM repairs r JOIN assets a ON a.id = r.asset_id
                       ORDER BY r.id"""),
    ("การจำหน่าย", """SELECT d.*, a.reg_no, a.temp_code, a.name FROM disposals d JOIN assets a ON a.id = d.asset_id
                      ORDER BY d.id"""),
    ("หมวดและประเภท", """SELECT t.code, t.category_code, c.name AS category_name, t.name, t.name_is_draft,
                                t.useful_life_years FROM asset_types t LEFT JOIN categories c ON c.code = t.category_code
                         ORDER BY t.code"""),
    ("อาคาร", "SELECT * FROM buildings ORDER BY sort_order, id"),
    ("ฝ่าย", "SELECT * FROM departments ORDER BY sort_order, id"),
    # ระบบวัสดุ (0.9.0): ข้อมูลดิบทุกตารางไว้เปิดดูถ้าระบบมีปัญหา (ไม่มีราคา) ยอดคงเหลือ = ผลรวม qty ในชีตรายการเคลื่อนไหว
    ("วัสดุ-ยอดคงเหลือ", """SELECT i.id, c.name AS category_name, i.name, i.base_unit, i.reorder_point, i.active,
                                 COALESCE((SELECT SUM(m.qty) FROM supply_moves m WHERE m.item_id = i.id), 0) AS balance
                          FROM supply_items i JOIN supply_categories c ON c.id = i.category_id
                          ORDER BY c.sort_order, c.name, i.name"""),
    ("วัสดุ-หมวด", "SELECT * FROM supply_categories ORDER BY sort_order, id"),
    ("วัสดุ-หน่วย", """SELECT u.*, i.name AS item_name, i.base_unit FROM supply_units u JOIN supply_items i ON i.id = u.item_id
                       ORDER BY u.item_id, u.factor"""),
    ("วัสดุ-รายการเคลื่อนไหว", """SELECT m.*, i.name AS item_name, i.base_unit FROM supply_moves m
                                  JOIN supply_items i ON i.id = m.item_id ORDER BY m.id"""),
    ("วัสดุ-ใบรับ", "SELECT * FROM supply_receipts ORDER BY id"),
    ("วัสดุ-บรรทัดใบรับ", """SELECT l.*, i.name AS item_name FROM supply_receipt_lines l JOIN supply_items i ON i.id = l.item_id
                             ORDER BY l.id"""),
    ("วัสดุ-ใบเบิก", """SELECT s.*, d.name AS department_name FROM supply_issues s JOIN departments d ON d.id = s.department_id
                        ORDER BY s.id"""),
    ("วัสดุ-บรรทัดใบเบิก", """SELECT l.*, i.name AS item_name, i.base_unit FROM supply_issue_lines l
                              JOIN supply_items i ON i.id = l.item_id ORDER BY l.id"""),
    ("วัสดุ-รอบนับ", "SELECT * FROM supply_counts ORDER BY id"),
    ("วัสดุ-ผลการนับ", "SELECT * FROM supply_count_lines ORDER BY id"),
    ("วัสดุ-ประวัตินำเข้า", "SELECT * FROM supply_import_batches ORDER BY id"),
]


def write_table(ws, cursor, labels=None):
    names = [d[0] for d in cursor.description]
    head = [(labels or {}).get(n, n) for n in names]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in cursor:
        ws.append([v for v in r])
    ws.freeze_panes = "A2"
    for i, n in enumerate(names, start=1):
        ws.column_dimensions[get_column_letter(i)].width = 14 if n not in ("name", "details", "note", "location_text") else 36
    return len(names)


def export_all(db):
    from app.templating import FIELD_LABELS
    labels = {**FIELD_LABELS, "id": "id (ห้ามแก้)", "created_at": "บันทึกเข้าระบบ (UTC)", "updated_at": "แก้ไขล่าสุด (UTC)",
              "source_sheet": "ชีตต้นทาง", "source_row": "แถวต้นทาง", "acquired_date_raw": "วันที่ตามต้นฉบับ",
              **dict(ASSET_EXTRA)}
    wb = Workbook()
    ws = wb.active
    ws.title = "ครุภัณฑ์"
    sql = """SELECT a.*, c.name AS category_name, t.name AS type_name, l.name AS location_name,
                    COALESCE(s.full_name, s.nickname) AS holder_name, s.position AS holder_position,
                    COALESCE(dup.reg_no, dup.temp_code) AS duplicate_of_code
             FROM assets a LEFT JOIN asset_types t ON t.code = a.type_code
             LEFT JOIN categories c ON c.code = t.category_code
             LEFT JOIN locations l ON l.id = a.location_id
             LEFT JOIN staff s ON s.id = a.holder_id
             LEFT JOIN assets dup ON dup.id = a.duplicate_of_id
             WHERE {} ORDER BY a.reg_no IS NULL, a.reg_no, a.temp_code"""
    labels["duplicate_of_code"] = "ซ้ำกับรายการ"
    write_table(ws, db.execute(sql.format("a.duplicate_of_id IS NULL")), labels)
    for title, other in EXPORT_SHEETS:
        write_table(wb.create_sheet(title), db.execute(other))
    # รายการซ้ำแยกชีตท้ายสุด (ไม่นับรวมในชีตครุภัณฑ์)
    write_table(wb.create_sheet("รายการซ้ำ"), db.execute(sql.format("a.duplicate_of_id IS NOT NULL")), labels)
    return save(wb)
