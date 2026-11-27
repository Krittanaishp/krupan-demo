"""Excel ของระบบวัสดุ (ไม่มีราคา/มูลค่า) · แบบฟอร์มนำเข้าอยู่ใน supplies_import.py

ทุกไฟล์ใช้แบบเดียวกัน (report_head):
- ฟอนต์ TH SarabunIT๙ 16 แบบรายงานครุภัณฑ์ (reports_xlsx) · วันที่แบบ พ.ศ.
- หัวรายงาน 4 บรรทัด: ชื่อรายงาน / ชื่อหน่วยงาน (ค่าตั้งค่า ไม่ hardcode) / ช่วงวันที่ / ส่งออกเมื่อ ... โดย ...
- ตรึงแถวหัวตาราง · ตัวเลขเป็นตัวเลขจริง (รวมต่อใน Excel ได้) · A4 พอดีความกว้างหน้า หัวตารางพิมพ์ซ้ำทุกหน้า
"""
from openpyxl import Workbook
from openpyxl.styles import Font

from app import settings_store
from app.supplies_reports import doc_count
from app.reports_xlsx import FONT, HEAD, cell, font, header, page_setup, save, titles
from app.templating import thai_date

INT = "#,##0"
HEAD_ROW = 6  # บรรทัด 1–4 หัวรายงาน · 5 ว่าง · 6 หัวตาราง
GREY = Font(name=FONT, size=16, color="808080")
LEVELS = {"out": "หมดแล้ว", "low": "ต่ำกว่าจุดสั่งซื้อ", "ok": ""}


def office(db):
    return settings_store.org_names(db)["report"] or settings_store.org_names(db)["display"]


def report_head(ws, db, title, span, meta, cols, sheet_title=None):
    """หัวรายงาน + หัวตาราง คืนเลขแถวหัวตาราง · meta = {"by": ชื่อผู้ส่งออก, "at": วันเวลาที่ส่งออก}"""
    ws.title = (sheet_title or title)[:31]
    titles(ws, [title, office(db), span, f"ส่งออกเมื่อ {meta['at']} โดย {meta['by']}"], len(cols))
    header(ws, HEAD_ROW, cols)
    ws.freeze_panes = ws.cell(HEAD_ROW + 1, 1).coordinate
    return HEAD_ROW


def finish(ws, widths, landscape=True):
    page_setup(ws, widths, f"{HEAD_ROW}:{HEAD_ROW}")
    ws.page_setup.orientation = "landscape" if landscape else "portrait"


def num(ws, row, col, value, grey=False):
    c = cell(ws, row, col, value, "right", border=HEAD)
    c.number_format = INT
    if grey:
        c.font = GREY
    return c


def text(ws, row, col, value, align=None, grey=False):
    c = cell(ws, row, col, value, align, border=HEAD)
    if grey:
        c.font = GREY
    return c


def note(ws, row, value):
    ws.cell(row, 1, value).font = font()


def empty(ws, row, ncols, message="ไม่มีรายการ"):
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
    text(ws, row, 1, message, "center")
    return row


# ---------------------------------------------------------------- ยอดคงเหลือ / ต่ำกว่าจุดสั่งซื้อ

def balances(db, rows, as_of, meta, category=None, only_low=False, q=""):
    """ยอดคงเหลือ ณ วันที่ (ทุกรายการหรือเฉพาะหมด/ต่ำกว่าจุดสั่งซื้อ) ตามตัวกรองในหน้าจอ"""
    wb = Workbook()
    ws = wb.active
    cols = ["ลำดับ", "หมวด", "รายการ", "หน่วยหลัก", "คงเหลือ (หน่วยหลัก)", "คิดเป็นหน่วยใหญ่", "เตือนเมื่อเหลือต่ำกว่า", "สถานะ"]
    scope = " · ".join(x for x in (f"หมวด {category}" if category else "", f"ค้นหา “{q}”" if q else "",
                                   "เฉพาะหมดแล้ว / ต่ำกว่าจุดสั่งซื้อ" if only_low else "") if x)
    row = report_head(ws, db, "ยอดคงเหลือวัสดุ", f"ข้อมูล ณ วันที่ {thai_date(as_of.isoformat())}"
                      + (f" ({scope})" if scope else ""), meta, cols)
    for i, r in enumerate(rows, 1):
        row += 1
        text(ws, row, 1, i, "center")
        text(ws, row, 2, r["category"])
        text(ws, row, 3, r["name"] + ("" if r["active"] else " (ปิดใช้งาน)"))
        text(ws, row, 4, r["base_unit"], "center")
        num(ws, row, 5, r["balance"])
        text(ws, row, 6, r["breakdown"] or "")
        num(ws, row, 7, r["reorder_point"])
        text(ws, row, 8, LEVELS[r["level"]])
    if not rows:
        row = empty(ws, row + 1, len(cols))
    n_out = sum(r["level"] == "out" for r in rows)
    n_low = sum(r["level"] == "low" for r in rows)
    note(ws, row + 2, f"ทั้งหมด {len(rows):,} รายการ · หมดแล้ว {n_out:,} · ต่ำกว่าจุดสั่งซื้อ {n_low:,} "
                      "· ยอดเป็นหน่วยหลักของแต่ละรายการ (ต่างหน่วยกันจึงไม่รวมยอด)")
    finish(ws, (8, 18, 40, 12, 14, 22, 16, 18))
    return save(wb)


def low(db, rows, today, meta):
    """รายการที่หมด/ต่ำกว่าจุดสั่งซื้อ ใช้ประกอบการขอซื้อ (ช่องจำนวนที่ขอซื้อ/หมายเหตุว่างไว้ให้กรอก)"""
    wb = Workbook()
    ws = wb.active
    cols = ["ลำดับ", "หมวด", "รายการ", "หน่วยหลัก", "คงเหลือ", "เตือนเมื่อเหลือต่ำกว่า", "สถานะ", "จำนวนที่ขอซื้อ", "หมายเหตุ"]
    row = report_head(ws, db, "รายการวัสดุที่ต้องสั่งซื้อ (หมดแล้ว / ต่ำกว่าจุดสั่งซื้อ)",
                      f"ข้อมูล ณ วันที่ {thai_date(today.isoformat())}", meta, cols, sheet_title="ต่ำกว่าจุดสั่งซื้อ")
    for i, r in enumerate(rows, 1):
        row += 1
        text(ws, row, 1, i, "center")
        text(ws, row, 2, r["category"])
        text(ws, row, 3, r["name"])
        text(ws, row, 4, r["base_unit"], "center")
        num(ws, row, 5, r["balance"])
        num(ws, row, 6, r["reorder_point"])
        text(ws, row, 7, LEVELS[r["level"]])
        num(ws, row, 8, None)
        text(ws, row, 9, None)
    if not rows:
        row = empty(ws, row + 1, len(cols), "ไม่มีรายการที่ต้องสั่งซื้อ")
    n_out = sum(r["level"] == "out" for r in rows)
    note(ws, row + 2, f"ทั้งหมด {len(rows):,} รายการ (หมดแล้ว {n_out:,} · ต่ำกว่าจุดสั่งซื้อ {len(rows) - n_out:,}) "
                      "· จำนวนเป็นหน่วยหลักของแต่ละรายการ")
    finish(ws, (8, 18, 40, 12, 11, 14, 18, 14, 24))
    return save(wb)


# ---------------------------------------------------------------- ใบเบิก / ใบรับ (1 แถว = 1 บรรทัดวัสดุ)

def issues(db, lines, span, meta, department=None):
    wb = Workbook()
    ws = wb.active
    cols = ["ลำดับ", "วันที่เบิก", "ใบเบิก", "เลขที่ใบเบิก", "ฝ่าย", "ผู้เบิก", "วัสดุ", "หน่วย", "จำนวนที่ขอ",
            "จำนวนที่จ่าย", "ผู้อนุมัติ", "สถานะ"]
    row = report_head(ws, db, "รายการเบิกวัสดุ", span + (f" · ฝ่าย{department}" if department else ""), meta, cols,
                      sheet_title="ใบเบิก")
    for i, r in enumerate(lines, 1):
        row += 1
        g = bool(r["cancelled_at"])
        text(ws, row, 1, i, "center", g)
        text(ws, row, 2, thai_date(r["issue_date"]), grey=g)
        text(ws, row, 3, f"#{r['issue_id']}", "center", g)
        text(ws, row, 4, r["doc_no"] or "", grey=g)
        text(ws, row, 5, r["department"], grey=g)
        text(ws, row, 6, r["requester_name"], grey=g)
        text(ws, row, 7, r["name"], grey=g)
        text(ws, row, 8, r["base_unit"], "center", g)
        num(ws, row, 9, r["requested_qty"], g)
        num(ws, row, 10, r["issued_qty"], g)
        text(ws, row, 11, r["approver_name"], grey=g)
        text(ws, row, 12, r["status"] + (f" ({r['cancel_reason']})" if g and r["cancel_reason"] else ""), grey=g)
    if not lines:
        row = empty(ws, row + 1, len(cols), "ไม่มีใบเบิกตามตัวกรองนี้")
    n, n_cancel = doc_count(lines, "issue_id")
    note(ws, row + 2, f"ใบเบิก {n:,} ใบ {len(lines):,} บรรทัด" + (f" · ยกเลิกแล้ว {n_cancel:,} ใบ (ตัวอักษรสีเทา "
                      "ไม่ได้ตัดยอดคงเหลือ)" if n_cancel else "") + " · จำนวนเป็นหน่วยหลักของแต่ละรายการ")
    finish(ws, (7, 14, 9, 14, 18, 24, 34, 9, 10, 10, 24, 22))
    return save(wb)


def receipts(db, lines, span, meta):
    wb = Workbook()
    ws = wb.active
    cols = ["ลำดับ", "วันที่รับ", "ใบรับ", "เลขที่เอกสาร", "แหล่งที่ได้", "วัสดุ", "จำนวนที่รับ", "หน่วยที่รับ",
            "จำนวน (หน่วยหลัก)", "หน่วยหลัก", "สถานะ"]
    row = report_head(ws, db, "รายการรับวัสดุเข้า", span, meta, cols, sheet_title="ใบรับ")
    for i, r in enumerate(lines, 1):
        row += 1
        g = bool(r["cancelled_at"])
        text(ws, row, 1, i, "center", g)
        text(ws, row, 2, thai_date(r["received_date"]), grey=g)
        text(ws, row, 3, f"#{r['receipt_id']}", "center", g)
        text(ws, row, 4, r["doc_no"] or "", grey=g)
        text(ws, row, 5, r["source"], grey=g)
        text(ws, row, 6, r["name"], grey=g)
        num(ws, row, 7, r["unit_qty"], g)
        text(ws, row, 8, r["unit_name"], "center", g)
        num(ws, row, 9, r["qty"], g)
        text(ws, row, 10, r["base_unit"], "center", g)
        text(ws, row, 11, r["status"] + (f" ({r['cancel_reason']})" if g and r["cancel_reason"] else ""), grey=g)
    if not lines:
        row = empty(ws, row + 1, len(cols), "ไม่มีใบรับตามตัวกรองนี้")
    n, n_cancel = doc_count(lines, "receipt_id")
    note(ws, row + 2, f"ใบรับ {n:,} ใบ {len(lines):,} บรรทัด" + (f" · ยกเลิกแล้ว {n_cancel:,} ใบ (ตัวอักษรสีเทา "
                      "ไม่ได้เพิ่มยอดคงเหลือ)" if n_cancel else ""))
    finish(ws, (7, 14, 9, 16, 30, 34, 11, 11, 12, 11, 22))
    return save(wb)


# ---------------------------------------------------------------- สรุปรายเดือน

def issue_matrix(db, m, span, meta):
    """วัสดุ × ฝ่าย (จำนวนที่จ่ายรวม) รวมท้ายแถว (รายการเดียวกัน หน่วยเดียวกัน) + จำนวนใบเบิก/จำนวนรายการต่อฝ่าย"""
    wb = Workbook()
    ws = wb.active
    deps = [d["name"] for d in m["departments"]]
    cols = ["ลำดับ", "หมวด", "รายการ", "หน่วย", *deps, "รวมทุกฝ่าย"]
    row = report_head(ws, db, "สรุปการเบิกวัสดุรายเดือน (จำนวนที่จ่าย แยกตามฝ่าย)", span, meta, cols,
                      sheet_title="สรุปการเบิก")
    first = 5
    for i, r in enumerate(m["rows"], 1):
        row += 1
        text(ws, row, 1, i, "center")
        text(ws, row, 2, r["category"])
        text(ws, row, 3, r["name"])
        text(ws, row, 4, r["base_unit"], "center")
        for j, q in enumerate(r["qty"]):
            num(ws, row, first + j, q or None)
        num(ws, row, first + len(deps), r["total"]).font = font(bold=True)
    if not m["rows"]:
        row = empty(ws, row + 1, len(cols), "ไม่มีการเบิกจ่ายในเดือนนี้")
    for label, values, total in (("จำนวนใบเบิก (ใบ)", m["n_docs"], m["n_docs_total"]),
                                 ("จำนวนรายการที่เบิก (รายการ)", m["n_items"], m["n_items_total"])):
        row += 1
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
        for col in range(2, 5):
            text(ws, row, col, None)
        text(ws, row, 1, label, "right").font = font(bold=True)
        for j, q in enumerate(values):
            num(ws, row, first + j, q).font = font(bold=True)
        num(ws, row, first + len(deps), total).font = font(bold=True)
    note(ws, row + 2, "ไม่นับใบเบิกที่ยกเลิก · จำนวนเป็นหน่วยหลักของแต่ละรายการ (ดูคอลัมน์หน่วย)")
    finish(ws, (7, 16, 34, 9, *[11] * len(deps), 11))
    return save(wb)


def movement(db, m, span, meta):
    """ยกมา รับ จ่าย ปรับยอด คงเหลือ ของทุกรายการ"""
    wb = Workbook()
    ws = wb.active
    cols = ["ลำดับ", "หมวด", "รายการ", "หน่วย", "ยกมา", "รับ", "จ่าย", "ปรับยอด", "คงเหลือ", "สถานะ"]
    row = report_head(ws, db, "สรุปรับ–จ่าย–คงเหลือวัสดุประจำเดือน", span, meta, cols, sheet_title="รับ-จ่าย-คงเหลือ")
    for i, r in enumerate(m["rows"], 1):
        row += 1
        text(ws, row, 1, i, "center")
        text(ws, row, 2, r["category"])
        text(ws, row, 3, r["name"] + ("" if r["active"] else " (ปิดใช้งาน)"))
        text(ws, row, 4, r["base_unit"], "center")
        for col, key in ((5, "opening"), (6, "received"), (7, "issued"), (8, "adjusted"), (9, "closing")):
            num(ws, row, col, r[key])
        text(ws, row, 10, LEVELS[r["level"]])
    if not m["rows"]:
        row = empty(ws, row + 1, len(cols))
    note(ws, row + 2, "คงเหลือ = ยกมา + รับ − จ่าย ± ปรับยอด · ยกมารวมยอดยกมาที่นำเข้าระบบในเดือนนี้ "
                      "· เอกสารที่ยกเลิกหักออกจากรับ/จ่ายแล้ว · จำนวนเป็นหน่วยหลักของแต่ละรายการ")
    finish(ws, (7, 18, 40, 10, 11, 11, 11, 11, 11, 18))
    return save(wb)


# ---------------------------------------------------------------- บัญชีวัสดุรายรายการ

def stock_card(db, item, opening, rows, span, meta, opening_label="เริ่มต้น"):
    wb = Workbook()
    ws = wb.active
    cols = ["วันที่", "รับจาก / จ่ายให้", "เลขที่เอกสาร", "รับ", "จ่าย", "คงเหลือ", "หมายเหตุ"]
    row = report_head(ws, db, f"บัญชีวัสดุ: {item['name']}",
                      f"{span} · หมวด {item['category']} · หน่วยนับ {item['base_unit']}", meta, cols, sheet_title="บัญชีวัสดุ")
    row += 1
    text(ws, row, 1, "")
    text(ws, row, 2, opening_label)
    for col in (3, 4, 5, 7):
        text(ws, row, col, None)
    num(ws, row, 6, opening)
    for r in rows:
        row += 1
        g = r["kind"] == "reversal"
        text(ws, row, 1, thai_date(r["date"]), grey=g)
        text(ws, row, 2, r["party"] + (f" ({r['unit_text']})" if r["unit_text"] else ""), grey=g)
        text(ws, row, 3, r["doc"] or "", grey=g)
        num(ws, row, 4, r["in"], g)
        num(ws, row, 5, r["out"], g)
        num(ws, row, 6, r["balance"], g)
        text(ws, row, 7, r["note"] or "", grey=g)
    finish(ws, (16, 44, 16, 11, 11, 12, 30))
    return save(wb)


# ---------------------------------------------------------------- รายการวัสดุ (แบบฟอร์มนำเข้า)

def items_export(db, rows, as_of, meta, scope=""):
    """รายการวัสดุตามตัวกรอง ในรูปแบบแบบฟอร์มนำเข้า (นำเข้ากลับเข้าระบบอื่นได้ทันที)
    ชีต "รายการวัสดุ" หัวตารางเหมือนแบบฟอร์มทุกตัวอักษร ยอดยกมา = ยอดคงเหลือ ณ วันที่ส่งออก (หน่วยหลัก)
    หัวรายงาน (หน่วยงาน วันที่ ผู้ส่งออก ตัวกรอง) อยู่ในชีต "ข้อมูลการส่งออก" แยกจากชีตนำเข้า
    แบบฟอร์มรับหน่วยใหญ่ได้ 2 หน่วย: รายการที่มีมากกว่านั้นใส่ 2 หน่วยแรก (อัตรามากก่อน) และแจ้งไว้ในชีตข้อมูล"""
    from io import BytesIO

    from openpyxl import load_workbook

    from app import supplies_import as si
    wb = load_workbook(BytesIO(si.template()))
    ws = wb[si.SHEET]
    extra_units = []
    for r in rows:
        units = sorted(r["units"], key=lambda u: (-u["factor"], u["name"]))
        if len(units) > 2:
            extra_units.append(f"{r['name']}: " + ", ".join(f"{u['name']} = {u['factor']:,} {r['base_unit']}" for u in units[2:]))
        u1, u2 = (units + [None, None])[:2]
        ws.append([r["category"], r["name"], r["base_unit"], r["reorder_point"],
                   u1["name"] if u1 else None, u1["factor"] if u1 else None,
                   u2["name"] if u2 else None, u2["factor"] if u2 else None,
                   r["balance"], r["note"] or None])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, int):
                c.number_format = INT
    info = wb.create_sheet("ข้อมูลการส่งออก", 1)
    n_inactive = sum(1 for r in rows if not r["active"])
    lines = [
        "รายการวัสดุ (แบบฟอร์มนำเข้า)",
        office(db),
        f"ยอดยกมา = ยอดคงเหลือ ณ วันที่ {thai_date(as_of.isoformat())}" + (f" · ตัวกรอง: {scope}" if scope else ""),
        f"ส่งออกเมื่อ {meta['at']} โดย {meta['by']}",
        "",
        f"ทั้งหมด {len(rows):,} รายการ" + (f" (ปิดใช้งานแล้ว {n_inactive:,} รายการ: ถ้านำเข้าระบบอื่นจะเป็นรายการที่ใช้งาน)" if n_inactive else ""),
        "นำเข้าระบบอื่นได้ทันทีที่หน้า วัสดุ > นำเข้าจาก Excel (ใช้ชีต “รายการวัสดุ” ห้ามเปลี่ยนชื่อชีตหรือหัวตาราง)",
        "ตอนนำเข้า เลือกวันที่ยอดยกมาเป็นวันที่ข้างบน (วันที่ส่งออก) ยอดจะตรงกับระบบนี้",
    ]
    if extra_units:
        lines += ["", "รายการที่มีหน่วยใหญ่เกิน 2 หน่วย (แบบฟอร์มรับได้ 2 หน่วย หน่วยที่เหลือต้องเพิ่มเองในหน้ารายการ):"]
        lines += [f"- {t}" for t in extra_units]
    for i, text in enumerate(lines, 1):
        c = info.cell(i, 1, text)
        c.font = font(bold=(i == 1))
    info.column_dimensions["A"].width = 110
    wb.active = 0
    return save(wb)
