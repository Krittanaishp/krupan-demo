"""Excel กลุ่มข้อความ "ใช้ประจำที่" ที่ระบบเสนอว่าน่าจะเป็นห้องเดียวกัน (พิมพ์ไปให้เจ้าหน้าที่ยืนยันก่อนกดจัด)"""
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from app.templating import thai_date

THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
FONT = "TH SarabunPSK"
COLUMNS = [  # (หัวคอลัมน์, ความกว้าง)
    ("กลุ่ม", 7), ("ห้องที่ระบบเสนอ", 24), ("ผู้รับผิดชอบที่เสนอ", 16), ("ข้อความเดิม", 30), ("ชิ้น", 7),
    ("ตอนนี้อยู่ห้อง", 22), ("ข้อสังเกต", 30), ("ตัวอย่างรายการ (3 รายการแรก)", 46),
    ("ยืนยัน (✓ / ✗)", 11), ("ห้องที่ถูกต้อง / หมายเหตุ (เจ้าหน้าที่กรอก)", 36),
]
GROUP_FILL = PatternFill("solid", fgColor="F3F6FB")


def build_workbook(groups, org=""):
    wb = Workbook()
    ws = wb.active
    ws.title = "กลุ่มห้องที่เสนอ"
    today = date.today()
    n_texts = sum(len(g["texts"]) for g in groups)
    ws.append([f"ข้อความ “ใช้ประจำที่” ที่ระบบเสนอว่าน่าจะเป็นห้องเดียวกัน ({len(groups)} กลุ่ม {n_texts} ข้อความ)"])
    ws.append([" · ".join(x for x in (
        "ระบบเสนอเท่านั้น ยังไม่ได้จัดห้องให้ กรุณาติ๊ก ✓ ข้อความที่อยู่ห้องเดียวกันจริง หรือ ✗ ถ้าไม่ใช่",
        org, f"พิมพ์วันที่ {thai_date(today.isoformat())}") if x)])
    ws.append([c[0] for c in COLUMNS])
    for i, g in enumerate(groups, 1):
        first_row = ws.max_row + 1
        for t in g["texts"]:
            r = t["row"]
            now = ", ".join(f"{name} ({n})" for name, n in r["room_counts"].items()) or "ยังไม่จัด"
            ws.append([
                i, g["target_name"] + ("" if g["target_id"] else " (ห้องใหม่)"), g["holder_hint"] or "",
                r["text"], r["n"], now, " · ".join(text for _, text in t["notes"]),
                "\n".join(r["samples"]), "", "",
            ])
        for row in ws.iter_rows(min_row=first_row, max_row=ws.max_row):
            for c in row:
                if i % 2:
                    c.fill = GROUP_FILL
    ws["A1"].font = Font(name=FONT, size=18, bold=True)
    ws["A2"].font = Font(name=FONT, size=14, color="555555")
    for col, (_, width) in enumerate(COLUMNS, 1):
        ws.column_dimensions[ws.cell(row=3, column=col).column_letter].width = width
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for c in row:
            c.font = Font(name=FONT, size=14, bold=c.row == 3)
            c.border = BORDER
            c.alignment = Alignment(wrap_text=True, vertical="top")
            if c.row == 3:
                c.fill = PatternFill("solid", fgColor="E8EFF9")
    ws.freeze_panes = "A4"
    ws.print_title_rows = "3:3"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.oddFooter.center.text = "หน้า &P / &N"
    if not groups:
        ws.append(["ไม่มีกลุ่มที่ระบบเสนอ"])
    return wb
