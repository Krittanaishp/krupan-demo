"""รายงานผลการตรวจสอบพัสดุเป็นไฟล์ Excel (.xlsx) ฟอนต์ TH SarabunIT๙ 16 ตั้งหน้ากระดาษ A4

ช่องลงชื่อท้ายชีตใช้รูปแบบเดียวกับไฟล์ Excel เดิมของสำนักงาน:
    ลงชื่อ ........................ ประธานกรรมการ    ลงชื่อ ........................ กรรมการ   ...
               (ชื่อ-นามสกุล)                                  (ชื่อ-นามสกุล)
"""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, Side
from openpyxl.utils import get_column_letter

from app.templating import thai_date

FONT = "TH SarabunIT๙"
SIZE = 16
NCOLS = 6
THIN = Side(style="thin", color="000000")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY = "#,##0.00"
DOTS = "." * 40
RAW_NOTE = "* ยังไม่ได้จัดเข้าห้องในระบบ แสดงข้อความ “ใช้ประจำที่” ตามทะเบียนเดิม (Excel)"


def font(bold=False, size=SIZE):
    return Font(name=FONT, size=size, bold=bold)


def title_rows(ws, data, subtitle=None):
    check = data["check"]
    lines = [(f"รายงานผลการตรวจสอบพัสดุประจำปีงบประมาณ {check['fiscal_year']}", True),
             (data["office"], True), (period(check), False)]
    if subtitle:
        lines.append((subtitle, True))
    for i, (text, bold) in enumerate(lines, start=1):
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=NCOLS)
        c = ws.cell(i, 1, text)
        c.font = font(bold, SIZE + (2 if i == 1 else 0))
        c.alignment = Alignment(horizontal="center")
    return len(lines) + 2


def period(check):
    if check["closed_at"]:
        return f"ตรวจระหว่างวันที่ {thai_date(check['started_at'])} ถึง {thai_date(check['closed_at'])}"
    return f"เริ่มตรวจวันที่ {thai_date(check['started_at'])} (ฉบับร่าง ยังไม่ปิดรอบ)"


def header(ws, row, names):
    for col, name in enumerate(names, start=1):
        c = ws.cell(row, col, name)
        c.font = font(True)
        c.border = BOX
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def body_cell(ws, row, col, value, money=False, center=False):
    c = ws.cell(row, col, value)
    c.font = font()
    c.border = BOX
    c.alignment = Alignment(vertical="top", wrap_text=True, horizontal="center" if center else None)
    if money and isinstance(value, (int, float)):
        c.number_format = MONEY
        c.alignment = Alignment(vertical="top", horizontal="right")
    return c


def signatures(ws, row, committee):
    """ช่องลงชื่อกรรมการ แถวละ 3 คน (คนละ 2 คอลัมน์) เหมือนท้ายชีตในไฟล์เดิม"""
    row += 2
    for start in range(0, len(committee), 3):
        group = committee[start:start + 3]
        span = NCOLS // 3
        for k, (name, role) in enumerate(group):
            col = 1 + k * span
            for r, text in ((row, f"ลงชื่อ{DOTS[:24]}{role}"), (row + 1, f"({name})")):
                ws.merge_cells(start_row=r, start_column=col, end_row=r, end_column=col + span - 1)
                c = ws.cell(r, col, text)
                c.font = font()
                c.alignment = Alignment(horizontal="center")
        row += 4
    return row


def setup_page(ws, widths, title_rows_n):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.5
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.print_options.horizontalCentered = True
    ws.print_title_rows = f"{title_rows_n}:{title_rows_n}"  # หัวตารางพิมพ์ซ้ำทุกหน้า


def summary_sheet(ws, data):
    row = title_rows(ws, data)
    header(ws, row, ["ผลการตรวจ", "จำนวน (รายการ)", "มูลค่า (บาท)", "ไม่ทราบมูลค่า (รายการ)", "", ""])
    ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=NCOLS)
    head = row
    for label, s in [*data["summary"], ("รวม", data["total"])]:
        row += 1
        body_cell(ws, row, 1, label).font = font(label == "รวม")
        body_cell(ws, row, 2, s["n"], center=True)
        body_cell(ws, row, 3, s["value"], money=True)
        body_cell(ws, row, 4, f"ไม่ทราบมูลค่า {s['unknown']} รายการ" if s["unknown"] else "-", center=True)
        for col in (5, 6):
            body_cell(ws, row, col, None)
        ws.merge_cells(start_row=row, start_column=4, end_row=row, end_column=NCOLS)
    notes = ["มูลค่ารวมเฉพาะรายการที่ทราบราคา รายการที่ไม่ทราบราคาแสดงจำนวนแยกไว้ ไม่ได้นับเป็น 0 บาท",
             f"จำนวนครุภัณฑ์ในรอบ = ทุกรายการที่สถานะไม่ใช่ “จำหน่ายแล้ว” และไม่ใช่รายการซ้ำ ณ วันเปิดรอบ ({data['total']['n']:,} รายการ)",
             f"พบนอกห้องที่บันทึกไว้ {len(data['outside'])} รายการ"]
    if data["unregistered"]:
        notes.append(f"พบของที่ไม่มีในทะเบียน {len(data['unregistered'])} รายการ (ไม่นับรวมในตารางข้างบน)")
    row += 1
    for text in notes:
        row += 1
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NCOLS)
        ws.cell(row, 1, "หมายเหตุ: " + text if text is notes[0] else text).font = font(size=14)
    signatures(ws, row, data["committee"])
    setup_page(ws, [26, 16, 18, 14, 10, 10], head)


def table_sheet(ws, data, subtitle, columns, rows, widths):
    row = title_rows(ws, data, subtitle)
    header(ws, row, [c[0] for c in columns])
    head = row
    if not rows:
        row += 1
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NCOLS)
        body_cell(ws, row, 1, "ไม่มีรายการ", center=True)
    for i, r in enumerate(rows, start=1):
        row += 1
        for col, (_, get, kind) in enumerate(columns, start=1):
            body_cell(ws, row, col, get(i, r), money=kind == "money", center=kind == "center")
    if any(r.get("room_raw") for r in rows) and any(c[1] is room for c in columns):
        row += 1
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=NCOLS)
        ws.cell(row, 1, RAW_NOTE).font = font(size=14)
    signatures(ws, row, data["committee"])
    setup_page(ws, widths, head)


def room(i, r):
    return r["room_label"] + (" *" if r["room_raw"] else "")


def price(r):
    return float(r["unit_price"]) if r["unit_price"] is not None else "ไม่ทราบ"


def build(data):
    wb = Workbook()
    summary_sheet(wb.active, data)
    wb.active.title = "สรุป"
    base = [("ลำดับ", lambda i, r: i, "center"), ("เลขทะเบียน", lambda i, r: r["code"], None),
            ("รายการ", lambda i, r: r["name"], None)]
    for title, sub, rows in (("ชำรุด", "ตารางแนบ 1 รายการที่พบชำรุด", data["broken"]),
                             ("ไม่พบ", "ตารางแนบ 2 รายการที่ไม่พบ", data["missing"])):
        table_sheet(wb.create_sheet(title), data, f"{sub} ({len(rows)} รายการ)", base + [
            ("ห้องตามทะเบียน", room, None),
            ("ราคา (บาท)", lambda i, r: price(r), "money"),
            ("หมายเหตุ", lambda i, r: r["note"], None)], rows, [7, 26, 34, 24, 14, 22])
    table_sheet(wb.create_sheet("พบนอกห้อง"), data,
                f"ตารางแนบ 3 รายการที่พบนอกห้องที่บันทึกไว้ ({len(data['outside'])} รายการ)", base + [
                    ("ห้องตามทะเบียน", room, None),
                    ("ห้องที่พบ", lambda i, r: r["found_location_name"], None),
                    ("ย้ายในทะเบียนแล้ว", lambda i, r: "ย้ายแล้ว" if r["moved"] else "ยังไม่ย้าย", "center")],
                data["outside"], [7, 26, 32, 22, 22, 14])
    if data["unregistered"]:
        table_sheet(wb.create_sheet("ไม่มีในทะเบียน"), data,
                    f"รายการที่พบแต่ไม่มีในทะเบียน ({len(data['unregistered'])} รายการ)", base + [
                        ("ห้องที่พบ", lambda i, r: r["found_location_name"] or "—", None),
                        ("สภาพ", lambda i, r: r["result"], "center"),
                        ("หมายเหตุ", lambda i, r: r["note"], None)],
                    data["unregistered"], [7, 16, 34, 22, 16, 26])
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
