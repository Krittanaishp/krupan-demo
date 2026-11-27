"""ช่องเลือกวัสดุแบบรายการที่ระบบวาดเอง (static/js/combobox.js) ทดสอบในเบราว์เซอร์จริง (Edge headless)
db ชั่วคราว (ข้อมูลสมมติแบบเดียวกับ ui_audit) + server ชั่วคราวที่ 127.0.0.1 port สุ่ม · ไม่แตะ db จริง

ต้องมี playwright:  uv run --with playwright pytest tests/test_combobox_browser.py
(รันทั้งชุดแบบไม่มี playwright ไฟล์นี้จะถูกข้าม)"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest

pytest.importorskip("playwright")

from tests import ui_audit  # noqa: E402
from tests.conftest import ROOT  # noqa: E402


# ชื่อแบบเดียวกับรายการจริงของฝ่าย (ใช้ทดสอบการค้นหาที่พิมพ์ผิด) · ใส่ใน db ชั่วคราวเท่านั้น
TYPO_ITEMS = [
    ("เครื่องเขียน", "กระดาษโน้ต ขนาด 2x2 นิ้ว", "อัน", None),
    ("เครื่องเขียน", "ปากกาไฮไลท์ แบบหัวตัด", "แท่ง", None),
    ("คลิป ลวด ลูกแม็ก", "ลูกแม็ก เบอร์ 35", "กล่อง", None),
    ("หมึกพิมพ์", "หมึก Printo PX-410", "กล่อง", "ชื่อเดิมในไฟล์: หมึก printo PX-410"),
    ("หมึกพิมพ์", "หมึก INKO 009 IK100 (สีดำ)", "กล่อง", None),
    ("หมึกพิมพ์", "หมึก INKO 009 IK200 (สีน้ำเงิน)", "กล่อง", None),
    ("หมึกพิมพ์", "หมึกเลเซอร์ทดสอบ", "ตลับ", "ชื่อเดิมในไฟล์: ตลับ zx-99 รุ่นเก่า"),
]


def add_typo_items(db):
    import sqlite3
    conn = sqlite3.connect(db)
    for cat, name, unit, note in TYPO_ITEMS:
        row = conn.execute("SELECT id FROM supply_categories WHERE name = ?", (cat,)).fetchone()
        if row is None:
            order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM supply_categories").fetchone()[0]
            cid = conn.execute("INSERT INTO supply_categories (name, sort_order) VALUES (?, ?)", (cat, order)).lastrowid
        else:
            cid = row[0]
        conn.execute("INSERT INTO supply_items (category_id, name, base_unit, note) VALUES (?, ?, ?, ?)", (cid, name, unit, note))
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("combo")
    # prepare_db ตั้ง environment ของ process: ทำใน process แยก ไม่ให้กระทบ test อื่น
    code = ("import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from tests import ui_audit; "
            "print(ui_audit.prepare_db(None, Path(sys.argv[2])))")
    out = subprocess.run([sys.executable, "-c", code, str(ROOT), str(tmp)], capture_output=True, text=True, cwd=ROOT,
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"), encoding="utf-8")
    assert out.returncode == 0, out.stderr
    db = out.stdout.strip().splitlines()[-1]
    add_typo_items(db)
    os.environ["COMBO_DB"] = db
    port = ui_audit.free_port()
    env = dict(os.environ, KRUPAN_DB=db, KRUPAN_BACKUP_DIR=str(tmp / "b"), KRUPAN_LOG_DIR=str(tmp / "logs"),
               KRUPAN_USB_WATCH="off", KRUPAN_SECRET_KEY="combo-" + "x" * 40, QR_ENABLED="off")
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except Exception:
            time.sleep(0.25)
    yield url
    subprocess.run(["taskkill", "/PID", str(srv.pid), "/T", "/F"], capture_output=True)


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        yield b
        b.close()


@pytest.fixture
def page(base, browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    pg.goto(base + "/login")
    pg.fill("#username", ui_audit.USER)
    pg.fill("#password", ui_audit.PASSWORD)
    pg.click("button[type=submit]")
    pg.goto(base + "/supplies/issue")
    pg.base = base
    yield pg
    ctx.close()


def field(pg, n=1):
    return pg.locator(f"#item_{n}").locator("xpath=ancestor::span[contains(@class,'cb-field')]")


def options(pg):
    return json.loads(pg.locator("#supply-options").text_content())


def shown(pg):
    """ชื่อรายการที่ตรงกับคำค้น (ไม่รวมรายการ "หมายถึง…ไหม")"""
    return pg.locator("#cb-listbox li.cb-opt:not(.cb-suggest)").evaluate_all(
        "els => els.map(e => e.querySelector('.cb-name').textContent)")


def test_click_opens_full_list_with_aria(page):
    page.click("#item_1")
    box = page.locator("#cb-listbox")
    assert box.is_visible() and box.get_attribute("role") == "listbox"
    assert page.get_attribute("#item_1", "role") == "combobox"
    assert page.get_attribute("#item_1", "aria-expanded") == "true"
    assert page.get_attribute("#item_1", "aria-controls") == "cb-listbox"
    assert len(shown(page)) == len(options(page))
    page.keyboard.press("ArrowDown")
    act = page.get_attribute("#item_1", "aria-activedescendant")
    assert act and page.locator(f"#{act}").get_attribute("class").count("cb-active") == 1
    page.keyboard.press("Escape")
    assert not box.is_visible() and page.get_attribute("#item_1", "aria-expanded") == "false"


def test_search_contains_ignores_case_and_extra_spaces_and_highlights(page):
    page.click("#item_1")
    page.keyboard.type("ปากกา")
    names = shown(page)
    assert names and all("ปากกา" in n for n in names)
    assert len(names) == sum("ปากกา" in o["name"] for o in options(page))
    assert page.locator("#cb-listbox mark").first.text_content() == "ปากกา"
    page.fill("#item_1", "")
    page.keyboard.type("  a4   80 ")
    assert shown(page) == ["A4 80 แกรม"]
    page.fill("#item_1", "")
    page.keyboard.type("ไม่มีวัสดุนี้แน่นอน")
    assert shown(page) == [] and page.locator("#cb-listbox .cb-none").text_content() == "ไม่พบวัสดุ"


def test_option_layout_name_category_balance_and_tags(page):
    page.click("#item_1")
    by = {o["name"]: o for o in options(page)}
    li = page.locator("#cb-listbox li.cb-opt", has_text="Canon 337")
    assert by["Canon 337"]["right"].startswith("0 ") and "หมด" in li.locator(".badge-danger").text_content()
    assert "cb-dim" in li.get_attribute("class")
    first = page.locator("#cb-listbox li.cb-opt").first
    name_box = first.locator(".cb-name").bounding_box()
    sub_box = first.locator(".cb-sub").bounding_box()
    right_box = first.locator(".cb-right").bounding_box()
    assert sub_box["y"] > name_box["y"] and right_box["x"] > name_box["x"] + name_box["width"]
    assert page.locator("li.cb-opt:not(.cb-dim) .cb-name").first.evaluate("e => getComputedStyle(e).fontWeight") in ("600", "700")
    low = [o for o in options(page) if o["tag"] == "ใกล้หมด"]
    for o in low:
        assert o["tagKind"] == "warn"


def test_enter_selects_fills_balance_and_moves_to_next_field(page):
    page.click("#item_1")
    page.keyboard.type("ยางลบ")
    page.keyboard.press("Enter")
    opt = next(o for o in options(page) if o["name"] == "ยางลบ")
    assert page.input_value("#item_1") == opt["value"]
    assert page.evaluate("document.activeElement.id") == "req_1"
    assert page.locator("[data-line]").first.locator("[data-balance]").inner_text().strip() == opt["right"]
    assert not page.locator("#cb-listbox").is_visible()


def test_arrow_keys_then_enter_and_mouse_click(page):
    page.click("#item_1")
    page.keyboard.type("ปากกา")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    second = shown(page)[1]
    page.keyboard.press("Enter")
    assert page.input_value("#item_1").startswith(second + " · ")
    page.click("#item_2")
    page.keyboard.type("A4")
    page.locator("#cb-listbox li.cb-opt", has_text="A4 70 แกรม").click()
    assert page.input_value("#item_2").startswith("A4 70 แกรม · ")
    assert page.locator("[data-line]").nth(1).locator("[data-balance]").inner_text().strip()


def test_must_choose_from_list_blocks_submit(page):
    page.click("#item_1")
    page.keyboard.type("ปากกา")
    page.keyboard.press("Tab")
    assert page.get_attribute("#item_1", "aria-invalid") == "true"
    assert page.locator("#item_1-cb-error").text_content() == "เลือกวัสดุจากรายการ"
    assert page.get_attribute("#item_1", "aria-describedby").split()[-1] == "item_1-cb-error"
    assert page.locator("[data-line]").first.locator("[data-balance]").inner_text().strip() == ""
    url = page.url
    page.focus("#req_1")
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(300)
    assert page.url == url and page.evaluate("document.activeElement.id") == "item_1"  # ไม่ได้ส่ง กลับมาช่องที่ผิด
    page.keyboard.press("Enter")  # Enter ตอนยังไม่ได้เลือก: อยู่ช่องเดิม
    assert page.evaluate("document.activeElement.id") == "item_1"
    page.fill("#item_1", "")
    page.keyboard.type("ปากกาลูกลื่น สีแดง")
    page.keyboard.press("Enter")
    assert page.get_attribute("#item_1", "aria-invalid") is None and not page.locator("#item_1-cb-error").count()


def test_exact_name_is_selected_automatically_on_blur(page):
    page.click("#item_1")
    page.keyboard.type("ยางลบ")
    page.keyboard.press("Escape")
    page.click("h1")
    opt = next(o for o in options(page) if o["name"] == "ยางลบ")
    assert page.input_value("#item_1") == opt["value"]
    assert page.locator("[data-line]").first.locator("[data-balance]").inner_text().strip() == opt["right"]


def test_out_of_stock_item_still_selectable(page):
    page.click("#item_1")
    page.keyboard.type("Canon 337")
    page.keyboard.press("Enter")
    assert page.input_value("#item_1").startswith("Canon 337 · ")
    assert page.locator("[data-line]").first.locator("[data-balance]").inner_text().strip().startswith("0 ")


def test_same_item_twice_is_refused(page):
    page.click("#item_1")
    page.keyboard.type("ยางลบ")
    page.keyboard.press("Enter")
    page.click("#item_2")
    page.keyboard.type("ยางลบ")
    li = page.locator("#cb-listbox li.cb-opt", has_text="ยางลบ")
    assert li.get_attribute("aria-disabled") == "true" and "อยู่บรรทัด 1 แล้ว" in li.inner_text()
    page.keyboard.press("Tab")
    assert "เลือกซ้ำ" in page.locator("#item_2-cb-error").text_content()


def test_list_matches_input_width_and_opens_upward_near_bottom(page):
    page.set_viewport_size({"width": 1280, "height": 1600})  # ใต้ช่องมีที่พอ: เปิดลงล่าง ชิดใต้ช่อง
    page.click("#item_1")
    assert "cb-up" not in page.get_attribute("#cb-popup", "class")
    inp, box = field(page).bounding_box(), page.locator("#cb-popup").bounding_box()
    assert abs(inp["width"] - box["width"]) <= 1 and abs(inp["x"] - box["x"]) <= 1
    assert 0 <= box["y"] - (inp["y"] + inp["height"]) <= 4
    item_h = page.locator("#cb-listbox li.cb-opt").first.bounding_box()["height"]
    assert page.locator("#cb-listbox").bounding_box()["height"] <= item_h * 8 + 12
    page.keyboard.press("Escape")
    page.set_viewport_size({"width": 1280, "height": 420})
    page.evaluate("document.querySelector('#item_1').scrollIntoView({block: 'end'})")
    page.click("#item_1")
    inp, box = field(page).bounding_box(), page.locator("#cb-popup").bounding_box()
    assert "cb-up" in page.get_attribute("#cb-popup", "class")
    assert box["y"] + box["height"] <= inp["y"] + 1


def test_mobile_390_full_width_and_tappable(base, browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    pg = ctx.new_page()
    pg.goto(base + "/login")
    pg.fill("#username", ui_audit.USER)
    pg.fill("#password", ui_audit.PASSWORD)
    pg.click("button[type=submit]")
    pg.goto(base + "/supplies/issue")
    pg.locator("#item_1").scroll_into_view_if_needed()
    pg.tap("#item_1")
    inp, box = field(pg).bounding_box(), pg.locator("#cb-popup").bounding_box()
    assert abs(inp["width"] - box["width"]) <= 1
    assert pg.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    first = pg.locator("#cb-listbox li.cb-opt").first
    assert first.bounding_box()["height"] >= 44
    name = first.locator(".cb-name").text_content()
    first.tap()
    assert pg.input_value("#item_1").startswith(name + " · ")
    ctx.close()


def test_receive_page_uses_same_picker(page):
    page.goto(page.base + "/supplies/receive")
    page.click("#item_1")
    page.keyboard.type("A4 80")
    page.keyboard.press("Enter")
    assert page.input_value("#item_1").startswith("A4 80 แกรม · ")
    assert page.locator("#unit_1 option").count() >= 2  # หน่วยใหญ่ของรายการที่เลือกขึ้นทันที
    field(page).locator("[data-combobox-toggle]").click()  # หน้ารับเข้ามีปุ่ม ▼ และปุ่มกรองหมวดเหมือนกัน
    assert len(shown(page)) == len(options(page)) and chips(page)[0] == "ทั้งหมด"


# ---------------------------------------------------------------- ปุ่ม ▼ / หัวหมวด / กรองหมวด / ค้นหาที่พิมพ์ผิด

def group_heads(pg):
    return pg.locator("#cb-listbox li.cb-group").all_text_contents()


def chips(pg):
    return pg.locator("#cb-popup .cb-chip").all_text_contents()


def test_toggle_button_big_with_tooltip_opens_all_even_with_text_and_closes(page):
    btn = field(page).locator("[data-combobox-toggle]")
    b = btn.bounding_box()
    assert b["width"] >= 44 and b["height"] >= 44
    assert btn.get_attribute("title") == "ดูวัสดุทั้งหมด" and btn.get_attribute("aria-label") == "ดูวัสดุทั้งหมด"
    page.click("#item_1")
    page.keyboard.type("ปากกา")  # มีข้อความค้างอยู่: ปุ่มยังเปิดทั้งหมด
    page.keyboard.press("Escape")
    btn.click()
    assert page.locator("#cb-popup").is_visible() and len(shown(page)) == len(options(page))
    assert page.input_value("#item_1") == "ปากกา"  # ไม่ลบข้อความที่พิมพ์ไว้
    assert btn.get_attribute("aria-expanded") == "true"
    btn.click()
    assert not page.locator("#cb-popup").is_visible() and btn.get_attribute("aria-expanded") == "false"


def test_toggle_then_choose_with_arrows_or_mouse_without_typing(page):
    field(page).locator("[data-combobox-toggle]").click()
    assert page.evaluate("document.activeElement.id") == "item_1"
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    second = shown(page)[1]
    page.keyboard.press("Enter")
    assert page.input_value("#item_1").startswith(second + " · ")
    assert page.evaluate("document.activeElement.id") == "req_1"  # เลือกแล้วไปช่องจำนวนที่ขอเหมือนเดิม
    field(page, 2).locator("[data-combobox-toggle]").click()
    page.locator("#cb-listbox li.cb-opt", has_text="Canon 337").click()
    assert page.input_value("#item_2").startswith("Canon 337 · ")
    page.focus("#item_3")
    page.keyboard.press("Escape")
    page.keyboard.press("Alt+ArrowDown")  # คีย์บอร์ด: Alt+↓ = ปุ่ม ▼
    assert page.locator("#cb-popup").is_visible() and len(shown(page)) == len(options(page))


def test_group_headers_in_system_order_and_arrows_skip_them(page):
    page.click("#item_1")
    groups = []
    for o in options(page):
        if o["group"] not in groups:
            groups.append(o["group"])
    assert group_heads(page) == groups  # ข้อมูลส่งมาเรียงตามลำดับหมวดในระบบ (sort_order) แล้ว
    head = page.locator("#cb-listbox li.cb-group").first
    assert head.get_attribute("role") == "presentation" and head.get_attribute("data-index") is None
    for _ in range(len(options(page)) + 3):  # ลูกศรวนทั้งรายการ ไม่เคยหยุดที่หัวหมวด
        page.keyboard.press("ArrowDown")
        act = page.get_attribute("#item_1", "aria-activedescendant")
        assert "cb-opt" in page.locator(f"#{act}").get_attribute("class")
    head.click()  # กดหัวหมวด: ไม่เลือกอะไร
    assert page.input_value("#item_1") == ""


def test_group_order_follows_category_sort_order(page):
    import sqlite3
    db = os.environ.get("COMBO_DB")
    page.click("#item_1")
    if db:
        conn = sqlite3.connect(db)
        order = [r[0] for r in conn.execute("SELECT name FROM supply_categories ORDER BY sort_order, name")]
        conn.close()
        heads = group_heads(page)
        assert heads == [c for c in order if c in heads]


def test_category_chips_filter_and_combine_with_search(page):
    page.click("#item_1")
    groups = group_heads(page)
    assert chips(page) == ["ทั้งหมด"] + groups
    assert page.locator(".cb-chip[aria-pressed=true]").text_content() == "ทั้งหมด"
    page.locator(".cb-chip", has_text="หมึกพิมพ์").click()
    assert group_heads(page) == ["หมึกพิมพ์"]
    ink = [x["name"] for x in options(page) if x["group"] == "หมึกพิมพ์"]
    assert sorted(shown(page)) == sorted(ink)
    assert page.evaluate("document.activeElement.id") == "item_1"  # กดปุ่มหมวดแล้วช่องยังพิมพ์ต่อได้
    page.keyboard.type("inko")
    inko = sorted(n for n in ink if "inko" in n.lower())
    assert len(inko) >= 2 and sorted(shown(page)) == inko
    assert page.locator(".cb-chip[aria-pressed=true]").text_content() == "หมึกพิมพ์"
    page.locator(".cb-chip", has_text="เครื่องเขียน").click()
    assert shown(page) == [] and page.locator(".cb-none").is_visible()
    page.locator(".cb-chip", has_text="ทั้งหมด").click()
    assert sorted(shown(page)) == sorted(o["name"] for o in options(page) if "inko" in (o["name"] + o["keys"]).lower())


def test_search_keeps_group_headers(page):
    page.click("#item_1")
    page.keyboard.type("ปากกา")
    assert group_heads(page) == ["เครื่องเขียน"]
    page.fill("#item_1", "")
    page.keyboard.type("inko")
    assert group_heads(page) == ["หมึกพิมพ์"]


@pytest.mark.parametrize("typed, want", [
    ("โน๊ต", "กระดาษโน้ต ขนาด 2x2 นิ้ว"),          # วรรณยุกต์ต่างกัน
    ("ไฮไลต์", "ปากกาไฮไลท์ แบบหัวตัด"),                     # ต/ท + การันต์
    ("ลูกแม็ค", "ลูกแม็ก เบอร์ 35"),                  # ค/ก
    ("ลูกแมก", "ลูกแม็ก เบอร์ 35"),                  # ไม่มีไม้ไต่คู้
    ("printo", "หมึก Printo PX-410"),              # ตัวพิมพ์เล็ก
    ("zx-99", "หมึกเลเซอร์ทดสอบ"),                  # อยู่ในหมายเหตุ (ชื่อเดิม) เท่านั้น
    ("inko ดำ", "หมึก INKO 009 IK100 (สีดำ)"),   # หลายคำ
    ("ดำ inko", "หมึก INKO 009 IK100 (สีดำ)"),   # หลายคำ สลับลำดับ
    ("2 x 2", "กระดาษโน้ต ขนาด 2x2 นิ้ว"),           # ช่องว่าง
    ("ปากกาไฮ ไลท์", "ปากกาไฮไลท์ แบบหัวตัด"),
    ("INKO", "หมึก INKO 009 IK200 (สีน้ำเงิน)"),
])
def test_typo_tolerant_search(page, typed, want):
    page.click("#item_1")
    page.keyboard.type(typed)
    assert want in shown(page), (typed, shown(page))
    if typed in ("inko ดำ", "ดำ inko"):  # ทุกรายการที่ได้ต้องมีทั้งสองคำ (สีน้ำเงินไม่ขึ้น)
        assert "หมึก INKO 009 IK200 (สีน้ำเงิน)" not in shown(page)
        assert all("inko" in n.lower() and "ดำ" in n for n in shown(page))
    if typed in ("โน๊ต", "printo", "ไฮไลต์", "inko ดำ"):
        assert page.locator("#cb-listbox li.cb-opt mark").count() >= 1  # เน้นส่วนที่ตรง


def test_typo_search_still_must_choose(page):
    page.click("#item_1")
    page.keyboard.type("inko")  # ตรง 2 รายการ: Enter ไม่เลือกให้
    page.keyboard.press("Enter")
    assert page.input_value("#item_1") == "inko" and page.evaluate("document.activeElement.id") == "item_1"
    page.fill("#item_1", "")
    page.keyboard.type("ไฮไลต์")  # ตรงรายการเดียว: Enter เลือก แล้วไปช่องจำนวนที่ขอ
    page.keyboard.press("Enter")
    assert page.input_value("#item_1").startswith("ปากกาไฮไลท์ แบบหัวตัด · ")
    assert page.evaluate("document.activeElement.id") == "req_1"


def test_no_match_suggests_three_nearest_and_show_all(page):
    page.click("#item_1")
    page.keyboard.type("ยางลอบ")
    assert shown(page) == [] and page.locator(".cb-none").text_content() == "ไม่พบวัสดุ"
    assert page.locator(".cb-did-you-mean").text_content() == "หมายถึง…ไหม"
    sugg = page.locator("#cb-listbox li.cb-suggest .cb-name").all_text_contents()
    assert 1 <= len(sugg) <= 3 and sugg[0] == "ยางลบ"
    page.keyboard.press("Enter")  # ยังไม่ได้เลือก: Enter ไม่เลือกรายการที่เสนอให้เอง
    assert page.input_value("#item_1") == "ยางลอบ"
    show_all = page.locator("#cb-listbox li.cb-show-all")
    assert "ดูวัสดุทั้งหมด" in show_all.text_content()
    show_all.click()
    assert len(shown(page)) == len(options(page))
    page.fill("#item_1", "")
    page.keyboard.type("ยางลอบ")
    page.locator("#cb-listbox li.cb-suggest", has_text="ยางลบ").click()
    assert page.input_value("#item_1").startswith("ยางลบ · ")


def test_name_matches_win_over_category_matches_and_category_name_still_searchable(page):
    page.click("#item_1")
    page.keyboard.type("ลูกแม็ค")  # หมวด "คลิป ลวด ลูกแม็ก" ก็ตรง แต่ชื่อรายการตรงมาก่อน: แสดงเฉพาะที่ชื่อตรง
    assert shown(page) == ["ลูกแม็ก เบอร์ 35"]
    page.fill("#item_1", "")
    page.keyboard.type("เครื่องเขียน")  # ไม่มีชื่อไหนตรง: ค้นในหมวดแทน
    want = [o["name"] for o in options(page) if o["group"] == "เครื่องเขียน"]
    assert shown(page) == want and group_heads(page) == ["เครื่องเขียน"]
