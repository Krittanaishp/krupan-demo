"""Content-Security-Policy ในเบราว์เซอร์จริง (Edge headless): ทุกหน้าไม่มีอะไรถูกบล็อก หน้าพิมพ์ยังใช้ได้ Excel ยังดาวน์โหลดได้
db ชั่วคราว (ข้อมูลสมมติแบบเดียวกับ ui_audit) + server ชั่วคราวที่ 127.0.0.1 port สุ่ม · ไม่แตะ db จริง

ต้องมี playwright:  uv run --with playwright pytest tests/test_csp_browser.py"""
import os
import re

import pytest

pytest.importorskip("playwright")

from tests import ui_audit  # noqa: E402
from tests.test_combobox_browser import base, browser  # noqa: E402,F401  (server + เบราว์เซอร์ชุดเดียวกัน)


@pytest.fixture
def pg(base, browser):  # noqa: F811
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    page = ctx.new_page()
    page.violations = []
    # CSP ที่ถูกบล็อกขึ้นใน console เป็น error "Refused to ... Content Security Policy"
    page.on("console", lambda m: page.violations.append(m.text) if "Content Security Policy" in m.text else None)
    ctx.on("page", lambda p: p.on("console", lambda m: page.violations.append(m.text)
                                  if "Content Security Policy" in m.text else None))
    page.add_init_script("window.__printed = 0; window.print = function () { window.__printed++; };")
    ctx.add_init_script("window.__printed = 0; window.print = function () { window.__printed++; };")
    page.goto(base + "/login")
    page.fill("#username", ui_audit.USER)
    page.fill("#password", ui_audit.PASSWORD)
    page.click("button[type=submit]")
    page.base = base
    yield page
    ctx.close()


def test_no_csp_violation_on_any_page(pg):
    urls = [u for u in ui_audit.pages(os.environ["COMBO_DB"]) if not u.startswith("(")]
    bad = {}
    for u in urls:
        before = len(pg.violations)
        pg.goto(pg.base + u, wait_until="load")
        if len(pg.violations) > before:
            bad[u] = pg.violations[before:]
    assert not bad, bad


def test_print_pages_render_and_print_button_works(pg):
    pg.goto(pg.base + "/reports")
    for action in ("/reports/register", "/reports/acquired", "/reports/disposals"):
        form = pg.locator(f"form[action='{action}']")
        with pg.context.expect_page() as info:
            form.locator("button[type=submit]").first.click()
        doc = info.value
        doc.wait_for_load_state("load")
        assert doc.locator("[data-print]").count() >= 1, action  # ปีที่ไม่มีรายการ: หน้ายังเปิดได้ แต่ไม่มีตาราง
        doc.locator("[data-print]").first.click()
        assert doc.evaluate("window.__printed") == 1, action
        if action == "/reports/register":
            col = doc.locator("col.c1").first
            assert col.evaluate("c => getComputedStyle(c).width") not in ("auto", "0px")  # ความกว้างจาก <style nonce>
        doc.close()
    assert pg.violations == []


def test_label_sheet_positions_come_from_nonce_style(pg):
    r = pg.goto(pg.base + "/labels/sheet?border=1")  # แผ่นทดสอบเส้นขอบ (ทุกดวงไม่มีเนื้อหา)
    if r.status != 200 or not pg.locator(".label").count():
        pytest.skip("ส่วน QR ปิดอยู่ (QR_ENABLED=off) หน้าสติกเกอร์ไม่เปิด · ตำแหน่งดวงตรวจใน test_labels.py แล้ว")
    first = pg.locator(".label").first
    assert first.evaluate("e => getComputedStyle(e).position") == "absolute"
    left = first.evaluate("e => e.getBoundingClientRect().left")
    second = pg.locator(".label").nth(1).evaluate("e => e.getBoundingClientRect().left")
    assert second > left  # ดวงถัดไปอยู่คนละตำแหน่ง (ถ้า CSS ถูกบล็อก ทุกดวงจะซ้อนกันที่เดียว)
    pg.locator("[data-print]").first.click()
    assert pg.evaluate("window.__printed") == 1 and pg.violations == []


def test_inventory_progress_and_room_print(pg):
    """เปิดรอบตรวจสอบพัสดุใน db ชั่วคราว แล้วดูแถบความคืบหน้า (data-pct) และหน้าพิมพ์ใบรายการ/รายงาน"""
    from tests.test_inventory import round_form
    pg.goto(pg.base + "/inventory")
    token = pg.evaluate("JSON.parse(document.body.getAttribute('hx-headers'))['X-CSRF-Token']")
    r = pg.request.post(pg.base + "/inventory", form={**round_form(), "csrf_token": token}, max_redirects=0)
    assert r.status == 303, r.text()
    pg.goto(pg.base + r.headers["location"])
    bar = pg.locator("[data-pct]").first
    if bar.count():
        pct = float(bar.get_attribute("data-pct"))
        assert bar.evaluate("e => e.style.width") == f"{pct:g}%"
    prints = [h for h in pg.locator("a[href$='/print'], a[href$='/report']").evaluate_all("a => a.map(x => x.getAttribute('href'))")]
    assert prints, "ไม่พบลิงก์หน้าพิมพ์"
    for h in prints[:3]:
        pg.goto(pg.base + h)
        assert pg.locator("table").count() >= 1
        pg.locator("[data-print]").first.click()
        assert pg.evaluate("window.__printed") >= 1
    assert pg.violations == []


def test_excel_downloads_still_work(pg):
    for url in ("/supplies", "/supplies/monthly"):
        pg.goto(pg.base + url)
        with pg.expect_download() as d:
            pg.locator("a.xlsx-link").first.click()
        assert d.value.suggested_filename.endswith(".xlsx")
    assert pg.violations == []


def test_autosubmit_and_back_link(pg):
    pg.goto(pg.base + "/settings/backup")
    box = pg.locator("input[data-autosubmit]")
    if box.count():
        with pg.expect_navigation():
            box.first.click()
    pg.goto(pg.base + "/labels")
    pg.goto(pg.base + "/labels/sheet?mode=room&room=999999")  # ไม่พบห้อง: หน้าแจ้งข้อผิดพลาดมีลิงก์ย้อนกลับ
    back = pg.locator("a[data-back]")
    if back.count():
        back.first.click()
        pg.wait_for_load_state("load")
        assert re.search(r"/labels($|\?)", pg.url)
    assert pg.violations == []
