"""0.8.2: รายงานจัดหน้าและฟอนต์แบบไฟล์ Excel เดิม (TH SarabunIT๙ 16, A4 แนวนอน ขอบตามชีต "14.")"""
import re
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app import reports, reports_xlsx
from tests.test_inventory import open_round, rooms  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
IT9 = "TH SarabunIT๙"


# ---------------------------------------------------------------- ค่าการจัดหน้า

def test_layout_original_matches_sheet_14():
    L = reports.layout("register", "original")
    assert L["orientation"] == "landscape" and L["center"] is True
    assert L["margins_in"] == {"left": 0.039, "right": 0.039, "top": 0.472, "bottom": 0.157}
    assert L["widths_chars"] == [5, 10.875, 40.875, 27.25, 11.5, 15.875, 14.375, 9]
    assert round(sum(L["widths_pt"]), 1) == 759.7 and L["factor"] == 1.0
    assert L["font_pt"] == 16 and L["row_pt"] == 20.25 and L["head_pt"] == 39.95


def test_layout_safe_keeps_font_at_least_15pt():
    safe = reports.layout("register", "safe")
    assert round(safe["margins_in"]["left"] * 25.4, 1) == 5.0 and round(safe["margins_in"]["right"] * 25.4, 1) == 5.0
    assert safe["font_pt"] == 16 and safe["factor"] == 1.0  # A4 แนวนอนขอบ 5 มม. ยังพอสำหรับตาราง 268 มม.
    acq_safe = reports.layout("acquired", "safe")
    assert acq_safe["orientation"] == "portrait" and acq_safe["factor"] < 1 and acq_safe["font_pt"] == 15
    acq = reports.layout("acquired", "original")
    assert acq["margins_in"] == {"left": 0.7, "right": 0.7, "top": 0.75, "bottom": 0.75} and acq["font_pt"] == 16
    assert sum(acq["widths_pt"]) <= reports.A4_PT[0] - 1.4 * 72 + 0.5
    assert reports.layout("register", "อะไรก็ได้")["margin"] == "original"


def test_text_lines_estimate():
    assert reports_xlsx.text_lines(None, 10) == 1 and reports_xlsx.text_lines("สั้น", 40) == 1
    # สระบน/ล่าง วรรณยุกต์ไม่นับความกว้าง
    assert reports_xlsx.text_lines("ที่นั่ง", 9) == reports_xlsx.text_lines("ทนง", 9) == 1
    assert reports_xlsx.text_lines("ก" * 20 + "\nข", 10) == 4


# ---------------------------------------------------------------- หน้าพิมพ์

def test_register_print_page_layout(editor, db):
    html = editor.get("/reports/register?fy=2569&digits=arabic").text
    assert "size: A4 landscape" in html and "padding: 0.472in 0.039in 0.157in 0.039in" in html
    assert "margin: 0;" in html and ".page-top th { height: 0.472in" in html and ".page-bottom td { height: 0.157in" in html
    assert html.count('class="page-top"') == html.count("<section class=\"register\"") == html.count('class="page-bottom"')
    assert "/static/css/report-fonts.css" in html and 'id="font-warn"' in html and "/static/js/font-check.js" in html
    assert "13pt" not in html and "12.5pt" not in html and "font-size: 16pt" in html
    assert "width: 759.7pt" in html and 'class="cat-name">ประเภท โต๊ะ-เก้าอี้' in html
    safe = editor.get("/reports/register?fy=2569&margin=safe").text
    assert "padding: 0.472in 0.197in 0.197in 0.197in" in safe and ".page-bottom td { height: 0.197in" in safe and "ขอบปลอดภัยสำหรับเครื่องพิมพ์" in safe
    acquired = editor.get("/reports/acquired?fy=2563").text
    assert "size: A4 portrait" in acquired and "padding: 0.75in 0.7in 0.75in 0.7in" in acquired
    form = editor.get("/reports").text
    assert 'name="margin"' in form and "ตรงต้นฉบับ" in form and "ปลอดภัยสำหรับเครื่องพิมพ์" in form


def test_all_printed_reports_use_report_font(editor, db, rooms):  # noqa: F811
    cid = open_round(editor)
    pages = ["/reports/register?fy=2569", "/reports/disposals?fy=2569", "/reports/summary",
             f"/inventory/{cid}/report", f"/inventory/{cid}/rooms/{rooms[0]}/print"]
    for url in pages:
        html = editor.get(url).text
        assert "/static/css/report-fonts.css" in html and 'id="font-warn"' in html, url
    assert 'class="report-print"' in editor.get("/reports/summary").text
    # หน้าเว็บปกติไม่ใช้ฟอนต์รายงาน
    assert "report-fonts.css" not in editor.get("/assets").text


def test_report_font_css_does_not_embed_it9():
    css = (ROOT / "app/static/css/report-fonts.css").read_text(encoding="utf-8")
    it9_faces = [b for b in re.findall(r"@font-face\s*{[^}]*}", css) if IT9 in b]
    assert len(it9_faces) == 2 and all("url(" not in b and "local(" in b for b in it9_faces)
    assert css.index('"TH SarabunIT๙", "TH SarabunPSK", "TH Sarabun New"') > 0
    assert not [p for p in (ROOT / "app/static/fonts").iterdir() if "IT" in p.name]
    doc_css = (ROOT / "app/static/css/print-doc.css").read_text(encoding="utf-8")
    assert "var(--report-font" in doc_css and "@font-face" not in doc_css


# ---------------------------------------------------------------- Excel

def test_register_excel_page_setup_matches_original(editor, db):
    wb = load_workbook(BytesIO(editor.get("/reports/register.xlsx?fy=2569").content))
    ws = wb["14. โต๊ะ-เก้าอี้"]
    ps, pm = ws.page_setup, ws.page_margins
    assert ps.paperSize == 9 and ps.orientation == "landscape" and ps.scale == 100
    assert not ws.sheet_properties.pageSetUpPr.fitToPage and ws.print_options.horizontalCentered
    assert (pm.left, pm.right, pm.top, pm.bottom) == (0.039, 0.039, 0.472, 0.157)
    assert tuple(ws.column_dimensions[c].width for c in "ABCDEFGH") == reports.REGISTER_WIDTHS
    assert ws.print_title_rows == "$1:$4"
    assert ws["A1"].font.name == IT9 and ws["A1"].font.sz == 16 and not ws["A1"].font.b
    assert ws["A2"].border.bottom.style == "thin" and ws["H2"].border.bottom.style == "thin"
    head = [ws.cell(3, c) for c in range(1, 9)]
    assert ws.row_dimensions[3].height == 39.95
    assert all(c.font.name == IT9 and c.font.sz == 16 and not c.font.b for c in head)
    assert all(c.border.top.style == c.border.bottom.style == c.border.left.style == "thin" for c in head)
    assert all(c.alignment.horizontal == "center" and c.alignment.vertical == "center" for c in head)
    assert not ws["A3"].alignment.wrap_text  # "ลำดับ" ไม่ตัดบรรทัด
    assert ws["C4"].font.b and ws["C4"].border.bottom.style == "hair"
    data = [r for r in range(5, ws.max_row + 1) if ws.cell(r, 1).value]
    assert data and all(ws.row_dimensions[r].height % 20.25 == 0 for r in data)
    first = data[0]
    assert ws.cell(first, 1).border.top.style == "hair" and ws.cell(first, 1).border.left.style == "thin"
    assert [ws.cell(first, c).alignment.horizontal for c in (1, 2, 3, 4)] == ["center", "center", "left", "center"]
    assert ws.cell(first, 1).font.name == IT9


def test_acquired_and_other_excels_use_it9(editor, db, rooms):  # noqa: F811
    db.execute("UPDATE assets SET fiscal_year = 2569 WHERE id = 1")
    db.commit()
    ws = load_workbook(BytesIO(editor.get("/reports/acquired.xlsx?fy=2569").content)).worksheets[0]
    pm = ws.page_margins
    assert ws.page_setup.orientation == "portrait" and (pm.left, pm.right, pm.top, pm.bottom) == (0.7, 0.7, 0.75, 0.75)
    assert sum(ws.column_dimensions[c].width for c in "ABCDEFGH") < sum(reports.REGISTER_WIDTHS)
    for url in ("/reports/disposals.xlsx?fy=2569", "/reports/summary.xlsx", "/review/export.xlsx"):
        ws = load_workbook(BytesIO(editor.get(url).content)).worksheets[0]
        assert ws["A1"].font.name == IT9, url
    cid = open_round(editor)
    ws = load_workbook(BytesIO(editor.get(f"/inventory/{cid}/report.xlsx").content)).worksheets[0]
    assert ws["A1"].font.name == IT9


# ---------------------------------------------------------------- ราคาแบบรูปแบบบัญชี (ศูนย์ = "-", ไม่ทราบราคา = ว่าง)

def price_rows(html):
    """ข้อความในคอลัมน์ราคา (ช่องที่ 5) ของแถวข้อมูล เรียงตามเลขทะเบียน"""
    rows = re.findall(r"<tr>\s*<td class=\"c\">.*?</tr>", html, re.S)
    out = {}
    for r in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        out[re.sub(r"<[^>]+>", "", tds[3]).strip()] = (tds[4].strip(), re.search(r'<td class="([^"]*)">' + re.escape(tds[4]), r))
    return out


def test_zero_price_shows_dash_in_print_and_accounting_format_in_excel(editor, db):
    db.execute("UPDATE assets SET unit_price = 0 WHERE id = 3")
    db.commit()
    html = editor.get("/reports/register?fy=2569&digits=arabic&group=0&cat=14").text
    prices = price_rows(html)
    assert prices["ปค.99-9999-1-1401-0010"][0] == "-" and "dash" in prices["ปค.99-9999-1-1401-0010"][1].group(1)
    assert prices["ปค.99-9999-1-1401-0011"][0] == "900.00"
    ws = load_workbook(BytesIO(editor.get("/reports/register.xlsx?fy=2569&group=0&cat=14").content)).worksheets[0]
    cells = {ws.cell(r, 4).value: ws.cell(r, 5) for r in range(5, ws.max_row + 1) if ws.cell(r, 4).value}
    zero = cells["ปค.๙๙-๙๙๙๙-๑-๑๔๐๑-๐๐๑๐"]
    assert zero.value == 0 and zero.number_format == reports.ACCOUNTING_FORMAT == '_-* #,##0.00_-;\\-* #,##0.00_-;_-* "-"??_-;_-@_-'
    assert cells["ปค.๙๙-๙๙๙๙-๑-๑๔๐๑-๐๐๑๑"].number_format == reports.ACCOUNTING_FORMAT
    assert reports.accounting_text(0) == "-" and reports.accounting_text(1500) == "1,500.00"


def test_unknown_price_stays_blank_not_zero(editor, db):
    # TMP-0001 (#2) ไม่ทราบราคา
    html = editor.get("/reports/register?fy=2569&digits=arabic&group=0&cat=14").text
    assert price_rows(html)["TMP-0001"][0] == ""
    ws = load_workbook(BytesIO(editor.get("/reports/register.xlsx?fy=2569&digits=arabic&group=0&cat=14").content)).worksheets[0]
    unknown = next(ws.cell(r, 5) for r in range(5, ws.max_row + 1) if ws.cell(r, 4).value == "TMP-0001")
    assert unknown.value is None
    assert reports.accounting_text(None) == ""
    assert db.execute("SELECT unit_price FROM assets WHERE id = 2").fetchone()[0] is None
