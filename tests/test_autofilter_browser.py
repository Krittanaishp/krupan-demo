"""ตัวกรองอัตโนมัติ (static/js/autofilter.js) ทดสอบในเบราว์เซอร์จริง (Edge headless)
db ชั่วคราว (ข้อมูลสมมติแบบเดียวกับ ui_audit) + server ชั่วคราวที่ 127.0.0.1 port สุ่ม · ไม่แตะ db จริง

ต้องมี playwright:  uv run --with playwright pytest tests/test_autofilter_browser.py
(รันทั้งชุดแบบไม่มี playwright ไฟล์นี้จะถูกข้าม)"""
import os
import re
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402

from tests import ui_audit  # noqa: E402
from tests.test_combobox_browser import base, browser  # noqa: E402,F401  (server + เบราว์เซอร์ชุดเดียวกัน)

THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


@pytest.fixture
def ctx(base, browser):  # noqa: F811
    c = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = c.new_page()
    pg.goto(base + "/login")
    pg.fill("#username", ui_audit.USER)
    pg.fill("#password", ui_audit.PASSWORD)
    pg.click("button[type=submit]")
    pg.base = base
    # นับ request ของตัวกรอง (htmx) และจำว่าหน้าไม่ได้โหลดใหม่ทั้งหน้า
    pg.filter_requests = []
    pg.on("request", lambda r: pg.filter_requests.append(r.url) if r.headers.get("hx-filter-history") else None)
    yield pg
    c.close()


def mark(pg):
    """ใส่เครื่องหมายใน window: ถ้าหน้าโหลดใหม่ทั้งหน้าเครื่องหมายจะหาย"""
    pg.evaluate("window.__noReload = 1")


def not_reloaded(pg):
    return pg.evaluate("window.__noReload === 1")


def params(pg):
    return {k: v[0] for k, v in parse_qs(urlparse(pg.url).query).items()}


def db_ids():
    conn = sqlite3.connect(os.environ["COMBO_DB"])
    used = conn.execute("SELECT DISTINCT department_id FROM supply_issues").fetchone()[0]
    other = conn.execute("SELECT id FROM departments WHERE issues_supplies = 1 AND id != ? ORDER BY sort_order LIMIT 1",
                         (used,)).fetchone()[0]
    month = conn.execute("SELECT substr(MAX(issue_date), 1, 7) FROM supply_issues").fetchone()[0]
    item = conn.execute("SELECT item_id FROM supply_issue_lines LIMIT 1").fetchone()[0]
    conn.close()
    return {"used": str(used), "other": str(other), "month": month, "item": item}


def type_date(pg, sel, text):
    """พิมพ์ในช่องวันที่/เดือนของระบบ (datepicker.js) แล้วออกจากช่อง (Tab) = ยืนยันค่า"""
    pg.fill(sel, text)
    pg.press(sel, "Tab")


def issue_rows(pg):
    return pg.locator("#issue-results table tbody tr")


def test_issues_no_button_and_department_filters_without_reload(ctx):
    ids = db_ids()
    ctx.goto(ctx.base + "/supplies/issues")
    assert ctx.locator("#issue-filters button[type=submit]:visible").count() == 0
    expect(issue_rows(ctx)).to_have_count(2)
    mark(ctx)
    ctx.select_option("#department", ids["other"])
    expect(ctx.locator("#issue-results .empty")).to_have_text("ไม่มีใบเบิกตามตัวกรองนี้")
    assert ctx.locator("#issue-results table").count() == 0
    assert params(ctx) == {"department": ids["other"]}  # ช่องเดือนที่ว่างไม่อยู่ใน URL
    assert not_reloaded(ctx) and len(ctx.filter_requests) == 1
    ctx.select_option("#department", ids["used"])
    expect(issue_rows(ctx)).to_have_count(2)
    assert params(ctx) == {"department": ids["used"]} and not_reloaded(ctx)


def test_issues_back_forward_and_reload_keep_filter_and_results_in_sync(ctx):
    ids = db_ids()
    ctx.goto(ctx.base + "/supplies/issues")
    ctx.select_option("#department", ids["other"])
    expect(ctx.locator("#issue-results .empty")).to_be_visible()
    ctx.go_back()
    expect(issue_rows(ctx)).to_have_count(2)
    expect(ctx.locator("#department")).to_have_value("")
    assert params(ctx) == {}
    ctx.go_forward()
    expect(ctx.locator("#issue-results .empty")).to_have_text("ไม่มีใบเบิกตามตัวกรองนี้")
    expect(ctx.locator("#department")).to_have_value(ids["other"])
    ctx.reload()
    expect(ctx.locator("#department")).to_have_value(ids["other"])
    expect(ctx.locator("#issue-results .empty")).to_have_text("ไม่มีใบเบิกตามตัวกรองนี้")


def test_issues_clear_filter_updates_table_immediately(ctx):
    ids = db_ids()
    ctx.goto(ctx.base + f"/supplies/issues?department={ids['other']}&month=2000-01")
    expect(ctx.locator("#month")).to_have_value("ม.ค. 2543")
    mark(ctx)
    ctx.click("#issue-results a[data-filter-clear]")
    expect(issue_rows(ctx)).to_have_count(2)
    expect(ctx.locator("#department")).to_have_value("")
    expect(ctx.locator("#month")).to_have_value("")
    expect(ctx.locator("input[name=month]")).to_have_value("")
    expect(ctx.locator("#month")).to_have_attribute("placeholder", "ทุกเดือน")
    assert params(ctx) == {} and not_reloaded(ctx)
    assert ctx.locator("#issue-results a[data-filter-clear]").count() == 0  # ไม่มีตัวกรองแล้ว ลิงก์หายไป
    ctx.go_back()  # ล้างแล้วกดย้อนกลับ ได้ตัวกรองเดิม
    expect(ctx.locator("#department")).to_have_value(ids["other"])


def test_month_field_shows_thai_label_and_filters(ctx):
    ids = db_ids()
    ctx.goto(ctx.base + "/supplies/issues")
    box = ctx.locator("#month")
    expect(box).to_have_value("")
    expect(box).to_have_attribute("placeholder", "ทุกเดือน")
    y, m = map(int, ids["month"].split("-"))
    mark(ctx)
    type_date(ctx, "#month", f"{m}/{y + 543}")
    expect(box).to_have_value(f"{THAI_MONTHS[m - 1]} {y + 543}")
    expect(ctx).to_have_url(re.compile(r"month=" + ids["month"]))
    expect(issue_rows(ctx)).to_have_count(2)
    type_date(ctx, "#month", "1/2543")
    expect(box).to_have_value("ม.ค. 2543")
    expect(ctx.locator("#issue-results .empty")).to_have_text("ไม่มีใบเบิกตามตัวกรองนี้")
    assert not_reloaded(ctx)
    # ข้อความเห็นครบในช่อง ไม่ถูกปุ่มปฏิทินบัง
    assert ctx.locator("#month").evaluate("e => e.scrollWidth <= e.clientWidth")


def test_receipts_month_and_empty_message(ctx):
    ctx.goto(ctx.base + "/supplies/receipts")
    expect(ctx.locator("#receipt-results table tbody tr")).to_have_count(1)
    type_date(ctx, "#month", "01/2543")
    expect(ctx.locator("#receipt-results .empty")).to_have_text("ไม่มีใบรับตามตัวกรองนี้")
    assert params(ctx) == {"month": "2000-01"}
    ctx.click("#receipt-results a[data-filter-clear]")
    expect(ctx.locator("#receipt-results table tbody tr")).to_have_count(1)


def test_search_waits_for_typing_pause_and_enter_does_not_resend(ctx):
    ctx.goto(ctx.base + "/supplies")
    mark(ctx)
    ctx.click("#q")
    ctx.keyboard.type("ปากกา", delay=40)
    ctx.wait_for_timeout(150)
    assert ctx.filter_requests == []  # ยังพิมพ์อยู่ (ไม่ถึง 0.3 วินาที) ยังไม่กรอง
    expect(ctx).to_have_url(re.compile(r"q=%E0%B8%9B"))
    assert len(ctx.filter_requests) == 1
    rows = ctx.locator("#balances tbody tr")
    expect(rows.first).to_contain_text("ปากกา")
    ctx.keyboard.press("Enter")
    ctx.wait_for_timeout(600)
    assert len(ctx.filter_requests) == 1 and not_reloaded(ctx)  # Enter ไม่ส่งซ้ำ ไม่โหลดทั้งหน้า
    # Enter ระหว่างรอ: กรองทันทีครั้งเดียว
    ctx.keyboard.type(" แดง")
    ctx.keyboard.press("Enter")
    ctx.wait_for_timeout(600)
    assert len(ctx.filter_requests) == 2 and not_reloaded(ctx)
    expect(ctx.locator("#q")).to_have_value("ปากกา แดง")


def test_typing_replaces_history_but_select_adds_entry(ctx):
    ctx.goto(ctx.base + "/supplies")
    ctx.click("#q")
    ctx.keyboard.type("ปา")
    expect(ctx).to_have_url(re.compile(r"q="))
    ctx.keyboard.type("กกา")
    expect(ctx).to_have_url(re.compile(r"q=%E0%B8%9B%E0%B8%B2%E0%B8%81%E0%B8%81%E0%B8%B2"))
    ctx.check("#balance-filters input[name=low]")
    expect(ctx).to_have_url(re.compile(r"low=1"))
    ctx.go_back()  # ถอยจากการติ๊ก → คำค้นเต็ม
    expect(ctx.locator("#q")).to_have_value("ปากกา")
    expect(ctx.locator("#balance-filters input[name=low]")).not_to_be_checked()
    ctx.go_back()  # การพิมพ์ทั้งหมดเป็นประวัติเดียว → ถอยกลับไปหน้าไม่มีคำค้น
    expect(ctx.locator("#q")).to_have_value("")
    assert params(ctx) == {}


def test_balances_clear_link_inside_results(ctx):
    ctx.goto(ctx.base + "/supplies?q=zzzz-ไม่มี")
    expect(ctx.locator("#balances .empty")).to_be_visible()
    mark(ctx)
    ctx.click("#balances a[data-filter-clear]")
    expect(ctx.locator("#q")).to_have_value("")
    expect(ctx.locator("#balances tbody tr").first).to_be_visible()
    assert params(ctx) == {} and not_reloaded(ctx)


def test_assets_register_select_pushes_url(ctx):
    ctx.goto(ctx.base + "/assets")
    first = ctx.locator("#status option").nth(1).get_attribute("value")
    mark(ctx)
    ctx.select_option("#status", first)
    expect(ctx).to_have_url(re.compile(r"status="))
    assert params(ctx) == {"status": first} and not_reloaded(ctx)
    ctx.go_back()
    expect(ctx.locator("#status")).to_have_value("")


def test_places_and_mapping_filters_push_url(ctx):
    for url, sel in (("/places", "#room-status"), ("/places/mapping", "#map-status")):
        ctx.goto(ctx.base + url)
        mark(ctx)
        ctx.select_option(sel, index=1)
        value = ctx.locator(sel).input_value()
        expect(ctx).to_have_url(re.compile(r"status=" + value))
        assert not_reloaded(ctx)
        ctx.go_back()
        expect(ctx.locator(sel)).to_have_value("")


def test_review_search_types_and_checkbox(ctx):
    ctx.goto(ctx.base + "/review")
    mark(ctx)
    ctx.fill("#review-q", "ตู้")
    expect(ctx).to_have_url(re.compile(r"q=%E0%B8%95"))
    ctx.check("#review-all")
    expect(ctx).to_have_url(re.compile(r"all=1"))
    assert not_reloaded(ctx)


def test_summary_report_checkbox_updates_numbers_and_excel_link(ctx):
    ctx.goto(ctx.base + "/reports/summary")
    expect(ctx.locator("#summary-sub")).to_contain_text("ไม่รวมรายการที่จำหน่ายแล้ว")
    mark(ctx)
    ctx.check("#summary-filters input[name=disposed]")
    expect(ctx.locator("#summary-sub")).to_contain_text("รวมรายการที่จำหน่ายแล้ว")
    expect(ctx.locator("#summary-xlsx")).to_have_attribute("href", re.compile(r"disposed=1"))
    assert params(ctx) == {"disposed": "1"} and not_reloaded(ctx)
    assert ctx.locator("#summary-filters button[type=submit]:visible").count() == 0


def test_item_stock_card_date_range(ctx):
    item = db_ids()["item"]
    ctx.goto(ctx.base + f"/supplies/items/{item}")
    mark(ctx)
    type_date(ctx, "#date_from", "1/1/2543")
    expect(ctx.locator("#card-xlsx")).to_have_attribute("href", re.compile(r"date_from=2000-01-01"))
    expect(ctx.locator("#card-results .results-meta")).to_contain_text("1 ม.ค. 2543")
    assert params(ctx) == {"date_from": "2000-01-01"} and not_reloaded(ctx)
    ctx.click("#card-results a[data-filter-clear]")
    expect(ctx.locator("#date_from")).to_have_value("")
    expect(ctx.locator("#card-xlsx")).to_have_attribute("href", re.compile(r"card\.xlsx$"))


def test_without_javascript_button_still_filters(base, browser):  # noqa: F811
    c = browser.new_context(java_script_enabled=False)
    pg = c.new_page()
    pg.goto(base + "/login")
    pg.fill("#username", ui_audit.USER)
    pg.fill("#password", ui_audit.PASSWORD)
    pg.click("button[type=submit]")
    ids = db_ids()
    pg.goto(base + "/supplies/issues")
    button = pg.locator("#issue-filters button[type=submit]")
    expect(button).to_be_visible()
    pg.select_option("#department", ids["other"])
    button.click()
    pg.wait_for_load_state("load")
    assert parse_qs(urlparse(pg.url).query)["department"] == [ids["other"]]
    expect(pg.locator("#issue-results .empty")).to_have_text("ไม่มีใบเบิกตามตัวกรองนี้")
    c.close()


def test_reports_index_keeps_buttons(ctx):
    """รายงานทะเบียน/ได้มา/จำหน่ายเปิดแท็บใหม่และต้องเลือกหลายช่องให้ครบก่อน: คงปุ่มไว้"""
    ctx.goto(ctx.base + "/reports")
    for action in ("/reports/register", "/reports/acquired", "/reports/disposals"):
        form = ctx.locator(f"form[action='{action}']")
        assert form.get_attribute("data-autofilter") is None
        expect(form.locator("button[type=submit]").first).to_be_visible()


def test_excel_button_follows_filters_after_autofilter(ctx):
    """ปุ่ม "ดาวน์โหลด Excel" อยู่มุมขวาบนของตาราง และเปลี่ยนตามตัวกรองบนจอทันที (อยู่ในส่วนที่ htmx แทนที่)"""
    ids = db_ids()
    ctx.goto(ctx.base + "/supplies/issues")
    link = ctx.locator("#issue-results .results-meta a.xlsx-link")
    expect(link).to_have_text("ดาวน์โหลด Excel")
    expect(link).to_have_attribute("href", "/supplies/issues.xlsx")
    ctx.select_option("#department", ids["other"])
    expect(link).to_have_attribute("href", f"/supplies/issues.xlsx?department={ids['other']}")
    table = ctx.locator("#issue-results").bounding_box()
    box = link.bounding_box()
    assert box["x"] + box["width"] > table["x"] + table["width"] - 40  # ชิดขวา
    ctx.goto(ctx.base + "/supplies")
    ctx.check("#balance-filters input[name=low]")
    expect(ctx.locator("#balances a.xlsx-link")).to_have_attribute("href", "/supplies/balances.xlsx?low=1")
    type_date(ctx, "#as_of", "1/11/2569")
    expect(ctx.locator("#balances a.xlsx-link")).to_have_attribute("href", "/supplies/balances.xlsx?as_of=2026-11-01&low=1")
    with ctx.expect_download() as d:
        ctx.locator("#balances a.xlsx-link").click()
    assert d.value.suggested_filename == "ยอดคงเหลือวัสดุ_ณ1พ.ย.2569.xlsx"


def test_filter_row_boxes_line_up_and_date_shows_meaning_when_empty(ctx):
    """แถวตัวกรอง: ป้ายชื่อแนวเดียวกัน กล่องขอบบน/ล่างตรงกัน ช่องติ๊กอยู่กึ่งกลางแนวกล่อง · ช่องวันที่ว่างขึ้น "วันนี้" """
    ctx.goto(ctx.base + "/supplies")
    label = ctx.locator("#as_of")
    expect(label).to_have_value("")
    expect(label).to_have_attribute("placeholder", "วันนี้")
    boxes = [ctx.locator(s).bounding_box() for s in ("#q", "#category", "#as_of")]
    assert max(b["y"] for b in boxes) - min(b["y"] for b in boxes) <= 2
    assert max(b["y"] + b["height"] for b in boxes) - min(b["y"] + b["height"] for b in boxes) <= 2
    labels = [ctx.locator(f"label[for={i}]").bounding_box()["y"] for i in ("q", "category", "as_of")]
    assert max(labels) - min(labels) <= 2
    check = ctx.locator("#balance-filters input[name=low]").bounding_box()
    mid = boxes[0]["y"] + boxes[0]["height"] / 2
    assert abs(check["y"] + check["height"] / 2 - mid) <= 3
    type_date(ctx, "#as_of", "20/11/2569")
    expect(label).to_have_value("20 พ.ย. 2569")
    assert label.evaluate("e => getComputedStyle(e).visibility") == "visible"
