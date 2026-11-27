"""ส่งออก Excel ของระบบวัสดุ: ยอดคงเหลือ ณ วันที่, ต่ำกว่าจุดสั่งซื้อ, ใบเบิก/ใบรับ (1 แถว = 1 บรรทัดวัสดุ),
สรุปการเบิกรายเดือน (วัสดุ × ฝ่าย), สรุปรับ–จ่าย–คงเหลือ, บัญชีวัสดุ · ทุกไฟล์รูปแบบเดียวกันและบันทึก audit_log
ข้อมูลทดสอบอยู่ใน db ชั่วคราวเท่านั้น"""
import json
import re
from datetime import timedelta
from io import BytesIO
from urllib.parse import unquote

import pytest
from openpyxl import load_workbook

from app import supplies_reports as rep
from tests.conftest import login
from tests.test_supplies import PAPER, PEN, TODAY, issue, receive, setup, unit_id  # noqa: F401

PREV = TODAY.replace(day=1) - timedelta(days=1)  # วันสุดท้ายของเดือนก่อน
MONTH, PREV_MONTH = TODAY.strftime("%Y-%m"), PREV.strftime("%Y-%m")
ABBR = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def thai(d):
    return f"{d.day} {ABBR[d.month - 1]} {d.year + 543}"


def doc_id(r):
    return int(r.headers["location"].split("/")[-1].split("?")[0])


@pytest.fixture
def data(editor, setup):  # noqa: F811
    """เดือนก่อน: รับกระดาษ 10 ปากกา 50 · จ่ายกระดาษ 1 (ฝ่าย 2)
    เดือนนี้: รับกระดาษ 2 กล่อง (=10) · รับปากกา 5 แล้วยกเลิก · จ่าย ฝ่าย 1 กระดาษ 3 + ปากกา 10, ฝ่าย 2 กระดาษ 4,
    ฝ่าย 1 ปากกา 2 แล้วยกเลิก"""
    p = PREV.isoformat()
    receive(editor, [(PAPER, 10, "base"), (PEN, 50, "base")], day=p, doc_no="ร1")
    issue(editor, [(PAPER, 1)], day=p, approved=p, dep="2", requester="นางสอง")
    receive(editor, [(PAPER, 2, unit_id(setup, 1))], doc_no="ร2", source="จัดซื้อ ร้านสอง")
    rid = doc_id(receive(editor, [(PEN, 5, "base")], doc_no="ร3"))
    assert editor.post(f"/supplies/receipts/{rid}/cancel", data={"reason": "รับผิดใบ"}).status_code in (200, 303)
    issue(editor, [(PAPER, 3), (PEN, 10)], doc_no="บ1")
    issue(editor, [(PAPER, 4)], dep="2", requester="นางสอง", doc_no="บ2")
    iid = doc_id(issue(editor, [(PEN, 2)], doc_no="บ3"))
    assert editor.post(f"/supplies/issues/{iid}/cancel", data={"reason": "เบิกซ้ำ"}).status_code in (200, 303)
    return setup


def get_xlsx(client, url):
    r = client.get(url)
    assert r.status_code == 200, (url, r.text[:300])
    assert "spreadsheetml" in r.headers["content-type"]
    name = unquote(re.search(r"filename\*=UTF-8''(.+)$", r.headers["content-disposition"]).group(1))
    return load_workbook(BytesIO(r.content)).active, name


def table(ws):
    """แถวข้อมูลใต้หัวตาราง (แถว 6) จนถึงแถวว่างแรก"""
    out = []
    for row in ws.iter_rows(min_row=7, values_only=True):
        if all(v is None for v in row):
            break
        out.append(row)
    return out


def head(ws):
    return [ws.cell(i, 1).value for i in range(1, 5)]


EXPORTS = [
    ("/supplies/balances.xlsx", "ยอดคงเหลือวัสดุ", "ยอดคงเหลือวัสดุ_ณ"),
    ("/supplies/low.xlsx", "รายการวัสดุที่ต้องสั่งซื้อ (หมดแล้ว / ต่ำกว่าจุดสั่งซื้อ)", "วัสดุที่ต้องสั่งซื้อ_ณ"),
    (f"/supplies/issues.xlsx?month={MONTH}", "รายการเบิกวัสดุ", "ใบเบิกวัสดุ_"),
    (f"/supplies/receipts.xlsx?month={MONTH}", "รายการรับวัสดุเข้า", "ใบรับวัสดุ_"),
    (f"/supplies/monthly/issues.xlsx?month={MONTH}", "สรุปการเบิกวัสดุรายเดือน (จำนวนที่จ่าย แยกตามฝ่าย)", "สรุปการเบิกวัสดุ_"),
    (f"/supplies/monthly/movement.xlsx?month={MONTH}", "สรุปรับ–จ่าย–คงเหลือวัสดุประจำเดือน", "สรุปรับจ่ายคงเหลือวัสดุ_"),
    ("/supplies/items/1/card.xlsx", "บัญชีวัสดุ: A4 80 แกรม", "บัญชีวัสดุ_A4 80 แกรม_"),
]


@pytest.mark.parametrize("url, title, prefix", EXPORTS)
def test_every_export_has_same_report_format(editor, data, url, title, prefix):
    ws, name = get_xlsx(editor, url)
    lines = head(ws)
    assert lines[0] == title and lines[1] == "ที่ทำการปกครองอำเภอทดสอบ"  # ชื่อหน่วยงานจากค่าตั้งค่า
    assert re.search(r"\d{1,2} [ก-๙.]+ 25\d\d", lines[2]), lines[2]  # ช่วงวันที่แบบ พ.ศ.
    assert lines[3].startswith("ส่งออกเมื่อ ") and lines[3].endswith(" โดย ผู้ใช้ editor1") and " น." in lines[3]
    assert ws.freeze_panes == "A7" and ws.print_title_rows == "$6:$6"
    assert int(ws.page_setup.paperSize) == int(ws.PAPERSIZE_A4) and ws.page_setup.fitToWidth == 1 and ws.page_setup.fitToHeight == 0
    assert ws.sheet_properties.pageSetUpPr.fitToPage
    for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, 12)):
        for c in row:
            if c.value is not None:
                assert c.font.name == "TH SarabunIT๙" and c.font.sz == 16, (c.coordinate, c.font.name, c.font.sz)
    # ตัวเลขเป็นตัวเลขจริง (ไม่ใช่ข้อความ) และไม่มีราคา/มูลค่า
    values = [v for row in table(ws) for v in row]
    assert any(isinstance(v, int) for v in values)
    assert not any(isinstance(v, str) and v.replace(",", "").isdigit() for v in values)
    assert not any(isinstance(v, str) and ("ราคา" in v or "มูลค่า" in v or "บาท" in v) for row in ws.iter_rows(values_only=True)
                   for v in row)
    assert name.startswith(prefix) and name.endswith(".xlsx") and re.search(r"25\d\d", name), name


def test_every_export_is_audited(editor, data):
    db = data
    before = db.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'export'").fetchone()[0]
    for url, title, prefix in EXPORTS:
        get_xlsx(editor, url)
    rows = db.execute("SELECT * FROM audit_log WHERE action = 'export' ORDER BY id").fetchall()[before:]
    assert len(rows) == len(EXPORTS)
    user = db.execute("SELECT id FROM users WHERE username = 'editor1'").fetchone()[0]
    for r in rows:
        assert r["table_name"] == "exports" and r["user_id"] == user
        ch = json.loads(r["changes"])
        assert ch["report"] and ch["file"].endswith(".xlsx") and isinstance(ch["rows"], int)
    issues = json.loads(rows[2]["changes"])
    assert issues["filters"] == {"month": MONTH} and issues["rows"] == 4
    # การส่งออกไม่นับเป็น "แก้ข้อมูลล่าสุด" ในหน้าผู้ใช้
    db.execute("UPDATE audit_log SET created_at = '2000-01-01 00:00:00' WHERE action != 'export'")
    db.commit()
    login(editor, "admin1")
    assert "แก้ข้อมูลล่าสุด 1 ม.ค. 2543" in editor.get("/users").text


def test_viewer_can_export(client, data):
    login(client, "viewer1")
    ws, _ = get_xlsx(client, f"/supplies/issues.xlsx?month={MONTH}")
    assert head(ws)[3].endswith("โดย ผู้ใช้ viewer1")


def test_balances_as_of_date_screen_and_excel_match(editor, data):
    page = editor.get(f"/supplies?as_of={PREV.isoformat()}").text.split('id="balances"')[1]
    assert f"ยอด ณ วันที่ {thai(PREV)}" in page
    ws, name = get_xlsx(editor, f"/supplies/balances.xlsx?as_of={PREV.isoformat()}")
    assert head(ws)[2] == f"ข้อมูล ณ วันที่ {thai(PREV)}"
    rows = {r[2]: r for r in table(ws)}
    assert rows["A4 80 แกรม"][4] == 9 and rows["ปากกาลูกลื่น สีน้ำเงิน"][4] == 50 and rows["เทปใส"][7] == "หมดแล้ว"
    assert name == f"ยอดคงเหลือวัสดุ_ณ{PREV.day}{ABBR[PREV.month - 1]}{PREV.year + 543}.xlsx"
    # ยอดปัจจุบัน: กระดาษ 9 + 10 − 7 = 12, ปากกา 50 − 10 = 40 (ใบที่ยกเลิกไม่นับ)
    rows = {r[2]: r for r in table(get_xlsx(editor, "/supplies/balances.xlsx")[0])}
    assert rows["A4 80 แกรม"][4] == 12 and rows["ปากกาลูกลื่น สีน้ำเงิน"][4] == 40
    # ตามตัวกรองบนจอ: หมวด + เฉพาะต่ำกว่าจุดสั่งซื้อ
    ws, _ = get_xlsx(editor, "/supplies/balances.xlsx?category=2&low=1")
    assert [r[2] for r in table(ws)] == ["เทปใส"] and "หมวด เครื่องเขียน" in head(ws)[2]
    # วันที่ในอนาคต = ยอดปัจจุบัน
    future = (TODAY + timedelta(days=3)).isoformat()
    assert {r[2]: r[4] for r in table(get_xlsx(editor, f"/supplies/balances.xlsx?as_of={future}")[0])}["A4 80 แกรม"] == 12


def test_low_export_for_purchase_request(editor, data):
    ws, _ = get_xlsx(editor, "/supplies/low.xlsx")
    assert [ws.cell(6, c).value for c in range(1, 10)][-2:] == ["จำนวนที่ขอซื้อ", "หมายเหตุ"]
    rows = table(ws)
    assert [r[2] for r in rows] == ["เทปใส"] and rows[0][4] == 0 and rows[0][6] == "หมดแล้ว" and rows[0][7] is None


def test_issues_export_one_row_per_line_with_cancelled_status(editor, data):
    ws, name = get_xlsx(editor, f"/supplies/issues.xlsx?month={MONTH}")
    cols = [ws.cell(6, c).value for c in range(1, 13)]
    assert cols == ["ลำดับ", "วันที่เบิก", "ใบเบิก", "เลขที่ใบเบิก", "ฝ่าย", "ผู้เบิก", "วัสดุ", "หน่วย", "จำนวนที่ขอ",
                    "จำนวนที่จ่าย", "ผู้อนุมัติ", "สถานะ"]
    rows = table(ws)
    assert [(r[3], r[6], r[8], r[9]) for r in rows] == [
        ("บ1", "A4 80 แกรม", 3, 3), ("บ1", "ปากกาลูกลื่น สีน้ำเงิน", 10, 10), ("บ2", "A4 80 แกรม", 4, 4),
        ("บ3", "ปากกาลูกลื่น สีน้ำเงิน", 2, 2)]
    assert rows[0][1] == thai(TODAY) and rows[0][10].startswith("นายอนุมัติ ใจดี") and rows[0][11] == "จ่ายแล้ว"
    assert rows[3][11] == "ยกเลิกแล้ว (เบิกซ้ำ)"
    assert ws.cell(10, 4).font.color.rgb.endswith("808080")  # ใบที่ยกเลิกตัวอักษรสีเทา
    assert "ยกเลิกแล้ว 1 ใบ" in ws.cell(12, 1).value
    assert name == f"ใบเบิกวัสดุ_{ABBR[TODAY.month - 1]}{TODAY.year + 543}.xlsx"
    assert head(ws)[2].startswith(f"เดือน {ABBR[TODAY.month - 1]} {TODAY.year + 543} (ช่วงวันที่ 1 ")
    # ตามฝ่าย
    ws, name = get_xlsx(editor, f"/supplies/issues.xlsx?month={MONTH}&department=2")
    assert [(r[4], r[9]) for r in table(ws)] == [("การเงิน (เสมียนตรา)", 4)]
    assert name.endswith("_การเงิน (เสมียนตรา).xlsx") and "ฝ่ายการเงิน (เสมียนตรา)" in head(ws)[2]
    # ไม่เลือกเดือน: ทุกใบ ไม่จำกัด 200 ใบเหมือนหน้าจอ
    ws, name = get_xlsx(editor, "/supplies/issues.xlsx")
    assert len(table(ws)) == 5 and name.startswith("ใบเบิกวัสดุ_ทั้งหมดถึง") and head(ws)[2].startswith("ทุกเดือน (ช่วงวันที่")
    ws, _ = get_xlsx(editor, "/supplies/issues.xlsx?month=2000-01")
    assert table(ws)[0][0] == "ไม่มีใบเบิกตามตัวกรองนี้"


def test_receipts_export(editor, data):
    ws, name = get_xlsx(editor, f"/supplies/receipts.xlsx?month={MONTH}")
    rows = table(ws)
    assert [(r[3], r[5], r[6], r[7], r[8], r[10]) for r in rows] == [
        ("ร2", "A4 80 แกรม", 2, "กล่อง", 10, "รับแล้ว"), ("ร3", "ปากกาลูกลื่น สีน้ำเงิน", 5, "ด้าม", 5, "ยกเลิกแล้ว (รับผิดใบ)")]
    assert rows[0][4] == "จัดซื้อ ร้านสอง" and name == f"ใบรับวัสดุ_{ABBR[TODAY.month - 1]}{TODAY.year + 543}.xlsx"
    ws, _ = get_xlsx(editor, f"/supplies/receipts.xlsx?month={PREV_MONTH}")
    assert [(r[5], r[8]) for r in table(ws)] == [("A4 80 แกรม", 10), ("ปากกาลูกลื่น สีน้ำเงิน", 50)]


def test_monthly_issue_matrix_screen_and_excel(editor, data):
    m = rep.issue_matrix(data, MONTH)
    assert [d["name"] for d in m["departments"]] == ["ปกครอง", "การเงิน (เสมียนตรา)"]
    assert [(r["name"], r["qty"], r["total"]) for r in m["rows"]] == [("A4 80 แกรม", [3, 4], 7),
                                                                       ("ปากกาลูกลื่น สีน้ำเงิน", [10, 0], 10)]
    assert m["n_docs"] == [1, 1] and m["n_docs_total"] == 2  # ใบที่ยกเลิกไม่นับ
    assert m["n_items"] == [2, 1] and m["n_items_total"] == 2  # ปกครอง: กระดาษ + ปากกา · การเงิน: กระดาษ
    assert "dept_totals" not in m and "total" not in m  # ไม่รวมจำนวนข้ามรายการ (หน่วยต่างกัน)
    ws, name = get_xlsx(editor, f"/supplies/monthly/issues.xlsx?month={MONTH}")
    assert [ws.cell(6, c).value for c in range(1, 8)] == ["ลำดับ", "หมวด", "รายการ", "หน่วย", "ปกครอง", "การเงิน (เสมียนตรา)", "รวมทุกฝ่าย"]
    rows = table(ws)
    assert [r[2:] for r in rows[:2]] == [("A4 80 แกรม", "รีม", 3, 4, 7), ("ปากกาลูกลื่น สีน้ำเงิน", "ด้าม", 10, None, 10)]
    assert rows[2][0] == "จำนวนใบเบิก (ใบ)" and rows[2][4:] == (1, 1, 2)
    assert rows[3][0] == "จำนวนรายการที่เบิก (รายการ)" and rows[3][4:] == (2, 1, 2)
    assert len(rows) == 4  # ไม่มีแถวรวมจำนวนข้ามรายการ
    flat = [v for r in ws.iter_rows(values_only=True) for v in r if isinstance(v, str)]
    assert not any("รวมจำนวนที่จ่าย" in v or "หน่วยที่ต่างกัน" in v for v in flat) and 17 not in [v for r in rows for v in r]
    page = editor.get(f"/supplies/monthly?month={MONTH}").text.split('id="matrix-title"')[1].split('id="move-title"')[0]
    assert "<strong>7</strong> รีม" in page and "<strong>10</strong> ด้าม" in page
    assert "จำนวนใบเบิก (ใบ)" in page and "จำนวนรายการที่เบิก (รายการ)" in page
    assert "รวมจำนวนที่จ่าย" not in page and "หน่วยที่ต่างกัน" not in page and "<strong>17</strong>" not in page
    prev = rep.issue_matrix(data, PREV_MONTH)
    assert [(r["name"], r["qty"]) for r in prev["rows"]] == [("A4 80 แกรม", [0, 1])]


def test_monthly_movement_balances(editor, data):
    m = {r["name"]: r for r in rep.movement(data, MONTH)["rows"]}
    paper, pen, tape = m["A4 80 แกรม"], m["ปากกาลูกลื่น สีน้ำเงิน"], m["เทปใส"]
    assert (paper["opening"], paper["received"], paper["issued"], paper["adjusted"], paper["closing"]) == (9, 10, 7, 0, 12)
    assert (pen["opening"], pen["received"], pen["issued"], pen["closing"]) == (50, 0, 10, 40)  # ยกเลิกหักออกแล้ว
    assert (tape["closing"], tape["level"]) == (0, "out")
    ws, name = get_xlsx(editor, f"/supplies/monthly/movement.xlsx?month={MONTH}")
    rows = {r[2]: r for r in table(ws)}
    assert rows["A4 80 แกรม"][4:9] == (9, 10, 7, 0, 12) and rows["ปากกาลูกลื่น สีน้ำเงิน"][4:9] == (50, 0, 10, 0, 40)
    assert name == f"สรุปรับจ่ายคงเหลือวัสดุ_{ABBR[TODAY.month - 1]}{TODAY.year + 543}.xlsx"
    prev = {r["name"]: r for r in rep.movement(data, PREV_MONTH)["rows"]}
    assert prev["A4 80 แกรม"]["closing"] == 9 == paper["opening"]  # คงเหลือเดือนก่อน = ยกมาเดือนนี้
    # ไม่เลือกเดือน = เดือนนี้
    assert "สรุปรับจ่ายคงเหลือวัสดุ_" + ABBR[TODAY.month - 1] in get_xlsx(editor, "/supplies/monthly/movement.xlsx")[1]


def test_opening_import_counts_as_brought_forward(editor, setup):  # noqa: F811
    db = setup
    uid = db.execute("SELECT id FROM users WHERE username = 'editor1'").fetchone()[0]
    db.execute("INSERT INTO supply_moves (item_id, move_date, kind, qty, created_by) VALUES (1, ?, 'opening', 8, ?)",
               (TODAY.isoformat(), uid))
    db.commit()
    paper = {r["name"]: r for r in rep.movement(db, MONTH)["rows"]}["A4 80 แกรม"]
    assert (paper["opening"], paper["received"], paper["closing"]) == (8, 0, 8)


def test_stock_card_export_follows_date_range(editor, data):
    ws, name = get_xlsx(editor, f"/supplies/items/1/card.xlsx?date_from={TODAY.replace(day=1).isoformat()}")
    rows = table(ws)
    assert rows[0][1] == "ยอดยกมา" and rows[0][5] == 9 and rows[-1][5] == 12
    assert name.endswith(f"_1{ABBR[TODAY.month - 1]}{TODAY.year + 543}-{TODAY.day}{ABBR[TODAY.month - 1]}{TODAY.year + 543}.xlsx")


PAGES = [
    ("/supplies?category=2", "/supplies/balances.xlsx?category=2"),
    (f"/supplies?as_of={PREV.isoformat()}", f"/supplies/balances.xlsx?as_of={PREV.isoformat()}"),
    ("/supplies/low", "/supplies/low.xlsx"),
    (f"/supplies/issues?month={MONTH}&department=1", f"/supplies/issues.xlsx?month={MONTH}&amp;department=1"),
    (f"/supplies/receipts?month={MONTH}", f"/supplies/receipts.xlsx?month={MONTH}"),
    (f"/supplies/monthly?month={MONTH}", f"/supplies/monthly/issues.xlsx?month={MONTH}"),
    (f"/supplies/monthly?month={MONTH}", f"/supplies/monthly/movement.xlsx?month={MONTH}"),
    ("/supplies/items/1?date_from=2000-01-01", "/supplies/items/1/card.xlsx?date_from=2000-01-01"),
]


@pytest.mark.parametrize("page, href", PAGES)
def test_export_button_same_place_and_follows_filters(editor, data, page, href):
    html = editor.get(page).text
    links = re.findall(r'<div class="results-meta">(?:(?!</div>).)*?<span class="results-links">(?:(?!</span>).)*?'
                       r'<a class="xlsx-link"[^>]*href="([^"]+)"[^>]*>ดาวน์โหลด Excel</a>', html, re.S)
    assert href in links, (page, links)


def test_monthly_tab_and_defaults_to_this_month(editor, data):
    html = editor.get("/supplies/monthly").text
    assert 'href="/supplies/monthly" aria-current="page"' in html
    assert f'value="{MONTH}"' in html and 'data-empty-label="เดือนนี้"' in html
    assert "ไม่มีการเบิกจ่ายในเดือนนี้" in editor.get("/supplies/monthly?month=2000-01").text
