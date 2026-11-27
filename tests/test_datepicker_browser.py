"""ช่องวันที่และช่องเดือนของระบบ (static/js/datepicker.js) ทดสอบในเบราว์เซอร์จริง (Edge headless)
พิมพ์ พ.ศ. / ค.ศ. / เลขไทย / ปีผิด, ปฏิทิน (หัวเดือนไทย + ปี พ.ศ., จ–อา, วันนี้, ล้าง), คีย์บอร์ด และ ARIA
db ชั่วคราว + server ชั่วคราว 127.0.0.1 (ชุดเดียวกับ test_combobox_browser) · ไม่แตะ db จริง

uv run --with playwright pytest tests/test_datepicker_browser.py"""
import re
from datetime import date, timedelta

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import expect  # noqa: E402

from tests.test_autofilter_browser import ctx, params, type_date  # noqa: E402,F401
from tests.test_combobox_browser import base, browser  # noqa: E402,F401

MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
          "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def today(pg):
    return date.fromisoformat(pg.evaluate("() => { const t = new Date(); return t.getFullYear() + '-' + "
                                          "String(t.getMonth() + 1).padStart(2, '0') + '-' + String(t.getDate()).padStart(2, '0'); }"))


def short(d):
    return f"{d.day} {SHORT[d.month - 1]} {d.year + 543}"


def value(pg, name):
    return pg.locator(f"input[type=hidden][name={name}]").input_value()


def popup(pg):
    return pg.locator("#dp-popup")


# ---------------------------------------------------------------- การอ่านสิ่งที่พิมพ์ (ตัวแปลงใน datepicker.js)
PARSE_CASES = [
    # (ข้อความ, ช่องเดือน, ผลที่ควรได้)
    ("27/11/2569", False, {"iso": "2026-11-27", "ce": None}),
    ("1/2/2569", False, {"iso": "2026-02-01", "ce": None}),
    ("๒๗/๑๑/๒๕๖๙", False, {"iso": "2026-11-27", "ce": None}),
    ("27-11-2569", False, {"iso": "2026-11-27", "ce": None}),
    ("27.11.2569", False, {"iso": "2026-11-27", "ce": None}),
    ("27 11 2569", False, {"iso": "2026-11-27", "ce": None}),
    ("27112569", False, {"iso": "2026-11-27", "ce": None}),
    ("27/11/69", False, {"iso": "2026-11-27", "ce": None}),
    ("27/11/2026", False, {"iso": "2026-11-27", "ce": 2026}),  # ปี ค.ศ. แปลงให้
    ("2026-11-27", False, {"iso": "2026-11-27", "ce": 2026}),
    ("29/2/2567", False, {"iso": "2024-02-29", "ce": None}),
    ("29/2/2569", False, {"error": "format"}),  # ไม่มีวันที่นี้
    ("32/1/2569", False, {"error": "format"}),
    ("1/13/2569", False, {"error": "format"}),
    ("abc", False, {"error": "format"}),
    ("1/1/3000", False, {"error": "year"}),  # ห่างเกิน 100 ปี
    ("1/1/1569", False, {"error": "year"}),
    ("1/1/2400", False, {"error": "year"}),
    ("", False, {"empty": True}),
    ("11/2569", True, {"iso": "2026-11", "ce": None}),
    ("๑๑/๒๕๖๙", True, {"iso": "2026-11", "ce": None}),
    ("11/2026", True, {"iso": "2026-11", "ce": 2026}),
    ("112569", True, {"iso": "2026-11", "ce": None}),
    ("13/2569", True, {"error": "format"}),
    ("11/3100", True, {"error": "year"}),
]


def test_parse_thai_buddhist_and_gregorian_years_and_thai_digits(ctx):
    ctx.goto(ctx.base + "/supplies")
    got = ctx.evaluate("""cases => cases.map(([t, m]) => {
        const r = window.krupanDate.parse(t, m, {y: 2026, m: 11, d: 27});
        return r.empty ? {empty: true} : r.error ? {error: r.error} : {iso: r.iso, ce: r.ce};
      })""", [[t, m] for t, m, _ in PARSE_CASES])
    for (text, month, want), g in zip(PARSE_CASES, got):
        assert g == want, (text, month, g)


# ---------------------------------------------------------------- พิมพ์ในช่อง
def test_typing_buddhist_year_without_leading_zero(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    t = today(ctx)
    d = t - timedelta(days=3)
    type_date(ctx, "#received_date", f"{d.day}/{d.month}/{d.year + 543}")
    assert value(ctx, "received_date") == d.isoformat()
    expect(ctx.locator("#received_date")).to_have_value(short(d))
    expect(ctx.locator("#received_date")).to_have_attribute("aria-invalid", "false")
    ctx.focus("#received_date")  # กลับเข้าไปกรอก: เห็นแบบที่พิมพ์ (วว/ดด/ปปปป พ.ศ.)
    expect(ctx.locator("#received_date")).to_have_value(f"{d.day:02d}/{d.month:02d}/{d.year + 543}")


def test_typing_thai_digits(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    d = today(ctx) - timedelta(days=1)
    th = str.maketrans("0123456789", "๐๑๒๓๔๕๖๗๘๙")
    type_date(ctx, "#received_date", f"{d.day}/{d.month}/{d.year + 543}".translate(th))
    assert value(ctx, "received_date") == d.isoformat()
    expect(ctx.locator("#received_date")).to_have_value(short(d))


def test_typing_gregorian_year_is_converted_and_says_so(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    d = today(ctx) - timedelta(days=2)
    type_date(ctx, "#received_date", f"{d.day}/{d.month}/{d.year}")
    assert value(ctx, "received_date") == d.isoformat()
    expect(ctx.locator("#received_date")).to_have_value(short(d))
    msg = ctx.locator("#received_date-dp-msg")
    expect(msg).to_be_visible()
    expect(msg).to_have_text(f"แปลงปี ค.ศ. {d.year} เป็น พ.ศ. {d.year + 543} แล้ว")
    assert "dp-msg-error" not in (msg.get_attribute("class") or "")


@pytest.mark.parametrize("text", ["1/1/3000", "1/1/1569", "27/11/2700"])
def test_unreasonable_year_red_border_and_cannot_save(ctx, text):
    ctx.goto(ctx.base + "/supplies/receive")
    before = value(ctx, "received_date")
    box = ctx.locator("#received_date")
    type_date(ctx, "#received_date", text)
    expect(box).to_have_attribute("aria-invalid", "true")
    expect(ctx.locator("#received_date-dp-msg")).to_have_text("ตรวจปี พ.ศ. อีกครั้ง")
    assert box.evaluate("e => getComputedStyle(e).borderTopColor") == "rgb(161, 38, 42)"  # --danger-fg
    assert value(ctx, "received_date") == before  # ค่าที่จะส่งไม่เปลี่ยนเป็นปีผิด
    url = ctx.url
    ctx.focus("#source")
    ctx.keyboard.press("Control+Enter")  # สั่งบันทึก: ฟอร์มไม่ถูกส่ง กลับไปที่ช่องวันที่
    ctx.wait_for_timeout(300)
    assert ctx.url == url
    assert ctx.evaluate("document.activeElement.id") == "received_date"
    type_date(ctx, "#received_date", "1/1/2569")  # แก้แล้วขอบแดงหาย
    expect(box).to_have_attribute("aria-invalid", "false")
    expect(ctx.locator("#received_date-dp-msg")).to_be_hidden()


def test_future_date_is_refused_where_max_is_today(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    d = today(ctx) + timedelta(days=1)
    type_date(ctx, "#received_date", f"{d.day}/{d.month}/{d.year + 543}")
    expect(ctx.locator("#received_date")).to_have_attribute("aria-invalid", "true")
    expect(ctx.locator("#received_date-dp-msg")).to_have_text("เลือกวันในอนาคตไม่ได้")
    assert value(ctx, "received_date") == today(ctx).isoformat()


def test_bad_text_stays_in_field_on_enter(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    ctx.fill("#received_date", "32/1/2569")
    ctx.press("#received_date", "Enter")
    expect(ctx.locator("#received_date")).to_have_attribute("aria-invalid", "true")
    expect(ctx.locator("#received_date-dp-msg")).to_contain_text("ตรวจวันที่อีกครั้ง")
    assert ctx.evaluate("document.activeElement.id") == "received_date"


# ---------------------------------------------------------------- ปฏิทิน
def test_calendar_header_weekdays_and_choose_by_click(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    t = today(ctx)
    ctx.click("#received_date")
    pop = popup(ctx)
    expect(pop).to_be_visible()
    expect(ctx.locator("#received_date")).to_have_attribute("aria-expanded", "true")
    assert pop.locator(".dp-month option:checked").inner_text() == MONTHS[t.month - 1]
    assert pop.locator(".dp-year option:checked").inner_text() == str(t.year + 543)
    assert pop.locator(".dp-wd").all_inner_texts() == ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]
    assert pop.locator(".dp-cell").count() == 42
    now = pop.locator(f"[data-dp-cell='{t.isoformat()}']")
    expect(now).to_have_attribute("aria-current", "date")
    expect(now).to_have_attribute("aria-selected", "true")
    # วันจันทร์อยู่คอลัมน์แรก
    first = pop.locator(".dp-row:not(.dp-weekdays)").first.locator(".dp-cell").first.get_attribute("data-dp-cell")
    assert date.fromisoformat(first).weekday() == 0
    # วันในอนาคตกดไม่ได้ (ช่องนี้ max = วันนี้)
    tomorrow = t + timedelta(days=1)
    nxt = pop.locator(f"[data-dp-cell='{tomorrow.isoformat()}']")
    if nxt.count():
        expect(nxt).to_have_attribute("aria-disabled", "true")
    # เปลี่ยนเดือนด้วยปุ่ม ‹ แล้วคลิกวันที่ 15
    pop.locator("[data-dp-step='-1']").click()
    prev = (t.replace(day=1) - timedelta(days=1)).replace(day=15)
    assert pop.locator(".dp-month option:checked").inner_text() == MONTHS[prev.month - 1]
    pop.locator(f"[data-dp-cell='{prev.isoformat()}']").click()
    expect(pop).to_be_hidden()
    assert value(ctx, "received_date") == prev.isoformat()
    expect(ctx.locator("#received_date")).to_have_attribute("aria-expanded", "false")


def test_calendar_month_and_year_selects(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    ctx.click("#received_date")
    pop = popup(ctx)
    pop.locator(".dp-year").select_option("2567")
    pop.locator(".dp-month").select_option("2")
    expect(pop.locator("[data-dp-cell='2024-02-29']")).to_be_visible()
    pop.locator("[data-dp-cell='2024-02-29']").click()
    assert value(ctx, "received_date") == "2024-02-29"
    expect(ctx.locator("#received_date")).to_have_value("29/02/2567")  # ยังอยู่ในช่อง: แบบที่พิมพ์
    ctx.locator("h2").first.click()
    expect(ctx.locator("#received_date")).to_have_value("29 ก.พ. 2567")


def test_calendar_today_and_clear_buttons(ctx):
    ctx.goto(ctx.base + "/supplies?as_of=2026-01-05")
    box = ctx.locator("#as_of")
    expect(box).to_have_value("5 ม.ค. 2569")
    box.click()
    popup(ctx).locator("[data-dp-clear]").click()
    expect(box).to_have_value("")
    expect(box).to_have_attribute("placeholder", "วันนี้")  # ตอนว่างบอกความหมายของช่อง
    expect(ctx).not_to_have_url(re.compile("as_of="))
    box.click()
    popup(ctx).locator("[data-dp-today]").click()
    t = today(ctx)
    expect(box).to_have_value(f"{t.day:02d}/{t.month:02d}/{t.year + 543}")
    ctx.locator("h1").click()
    expect(box).to_have_value(short(t))
    assert value(ctx, "as_of") == today(ctx).isoformat()


def test_month_field_picker(ctx):
    ctx.goto(ctx.base + "/supplies/issues")
    box = ctx.locator("#month")
    box.click()
    pop = popup(ctx)
    expect(pop).to_be_visible()
    assert pop.locator(".dp-cell").all_inner_texts() == SHORT
    expect(pop.locator(".dp-month")).to_be_hidden()
    y = today(ctx).year
    assert pop.locator(".dp-year option:checked").inner_text() == str(y + 543)
    pop.locator("[data-dp-step='-1']").click()  # ปีก่อน
    assert pop.locator(".dp-year option:checked").inner_text() == str(y - 1 + 543)
    pop.locator(f"[data-dp-cell='{y - 1}-03']").click()
    expect(box).to_have_value(f"03/{y - 1 + 543}")
    ctx.locator("h1").click()
    expect(box).to_have_value(f"มี.ค. {y - 1 + 543}")
    expect(ctx).to_have_url(re.compile(f"month={y - 1}-03"))
    assert params(ctx) == {"month": f"{y - 1}-03"}


# ---------------------------------------------------------------- คีย์บอร์ด
def test_keyboard_arrows_enter_escape(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    t = today(ctx)
    box = ctx.locator("#received_date")
    box.focus()
    expect(popup(ctx)).to_be_hidden()  # Tab/โฟกัสเฉย ๆ ไม่เปิดปฏิทิน
    ctx.keyboard.press("ArrowDown")
    expect(popup(ctx)).to_be_visible()
    active = lambda: ctx.evaluate("document.getElementById(document.activeElement.getAttribute('aria-activedescendant'))"
                                  ".getAttribute('data-dp-cell')")
    assert active() == t.isoformat()
    ctx.keyboard.press("ArrowLeft")
    assert active() == (t - timedelta(days=1)).isoformat()
    ctx.keyboard.press("ArrowUp")
    assert active() == (t - timedelta(days=8)).isoformat()
    ctx.keyboard.press("ArrowRight")  # ไปข้างหน้าได้จนถึงวันนี้
    assert active() == (t - timedelta(days=7)).isoformat()
    ctx.keyboard.press("PageUp")
    pm = t - timedelta(days=7)
    pm = pm.replace(month=pm.month - 1 or 12, year=pm.year - (pm.month == 1), day=1)
    assert active()[:7] == pm.isoformat()[:7]
    ctx.keyboard.press("PageDown")
    assert active() == (t - timedelta(days=7)).isoformat()
    ctx.keyboard.press("Escape")  # ปิดโดยไม่เปลี่ยนค่า
    expect(popup(ctx)).to_be_hidden()
    assert value(ctx, "received_date") == t.isoformat()
    ctx.keyboard.press("ArrowDown")
    ctx.keyboard.press("ArrowLeft")
    ctx.keyboard.press("Enter")  # Enter ตอนปฏิทินเปิด = เลือก (ยังอยู่ช่องเดิม)
    expect(popup(ctx)).to_be_hidden()
    assert value(ctx, "received_date") == (t - timedelta(days=1)).isoformat()
    assert ctx.evaluate("document.activeElement.id") == "received_date"
    ctx.keyboard.press("Enter")  # Enter ตอนปฏิทินปิด = ไปช่องถัดไปเหมือนเดิม
    assert ctx.evaluate("document.activeElement.id") == "source"


def test_typed_then_enter_moves_to_next_field(ctx):
    ctx.goto(ctx.base + "/supplies/issue")
    d = today(ctx) - timedelta(days=4)
    ctx.fill("#issue_date", f"{d.day}/{d.month}/{d.year + 543}")
    ctx.press("#issue_date", "Enter")
    assert value(ctx, "issue_date") == d.isoformat()
    assert ctx.evaluate("document.activeElement.id") != "issue_date"


def test_ctrl_enter_still_submits_from_date_field(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    d = today(ctx) - timedelta(days=1)
    ctx.fill("#received_date", f"{d.day}/{d.month}/{d.year + 543}")
    with ctx.expect_navigation():
        ctx.press("#received_date", "Control+Enter")
    # ฟอร์มยังไม่ครบ: เซิร์ฟเวอร์ตอบพร้อม error และวันที่ที่พิมพ์ (ค.ศ. ในช่องซ่อน) ยังอยู่
    expect(ctx.locator(".form-error")).to_be_visible()
    assert value(ctx, "received_date") == d.isoformat()
    expect(ctx.locator("#received_date")).to_have_value(short(d))


def test_aria_attributes(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    box = ctx.locator("#received_date")
    assert box.get_attribute("role") == "combobox"
    assert box.get_attribute("aria-haspopup") == "grid"
    assert box.get_attribute("aria-controls") == "dp-grid"
    assert ctx.locator("label[for=received_date]").count() == 1  # ป้ายชื่อยังผูกกับช่องที่เห็น
    described = box.get_attribute("aria-describedby").split()
    assert "received_date-dp-hint" in described and "received_date-dp-msg" in described
    assert box.get_attribute("name") is None and value(ctx, "received_date")  # ชื่อช่องอยู่ที่ช่องซ่อน
    box.click()
    grid = ctx.locator("#dp-grid")
    assert grid.get_attribute("role") == "grid"
    assert grid.get_attribute("aria-labelledby") == "dp-title"
    assert ctx.locator("#dp-grid [role=row]").count() == 7
    assert ctx.locator("#dp-grid [role=columnheader]").count() == 7
    assert ctx.locator("#dp-grid [role=gridcell]").count() == 42
    act = box.get_attribute("aria-activedescendant")
    cell = ctx.locator(f"#{act}")
    assert cell.get_attribute("role") == "gridcell"
    assert re.fullmatch(r"วัน\S+ที่ \d+ \S+ 25\d\d", cell.get_attribute("aria-label"))
    for sel, label in ((".dp-month", "เดือน"), (".dp-year", "ปี พ.ศ."), ("[data-dp-step='-1']", "เดือนก่อน"),
                       ("[data-dp-step='1']", "เดือนถัดไป")):
        assert ctx.locator("#dp-popup " + sel).get_attribute("aria-label") == label
    assert ctx.locator("[data-dp-toggle=received_date]").get_attribute("aria-label") == "เปิดปฏิทิน"


def test_box_same_height_as_other_fields_and_toggle_opens(ctx):
    ctx.goto(ctx.base + "/supplies/receive")
    d = ctx.locator("#received_date").bounding_box()
    s = ctx.locator("#source").bounding_box()
    assert abs(d["height"] - s["height"]) <= 1 and abs(d["y"] - s["y"]) <= 2
    ctx.click("[data-dp-toggle=received_date]")
    expect(popup(ctx)).to_be_visible()
    ctx.click("[data-dp-toggle=received_date]")
    expect(popup(ctx)).to_be_hidden()


def test_mobile_calendar_fits_screen_and_cells_are_big(base, browser):  # noqa: F811
    c = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    pg = c.new_page()
    from tests import ui_audit
    pg.goto(base + "/login")
    pg.fill("#username", ui_audit.USER)
    pg.fill("#password", ui_audit.PASSWORD)
    pg.click("button[type=submit]")
    pg.goto(base + "/supplies/receive")
    pg.tap("[data-dp-toggle=received_date]")
    pop = pg.locator("#dp-popup")
    expect(pop).to_be_visible()
    b = pop.bounding_box()
    assert b["x"] >= 0 and b["x"] + b["width"] <= 390
    cell = pop.locator(".dp-cell").first.bounding_box()
    assert cell["height"] >= 44 and cell["width"] >= 40
    c.close()


def test_submit_without_touching_date_keeps_server_value(ctx):
    """ไม่ได้แตะช่องวันที่ (ข้อความในช่องเป็นแบบแสดงผล "27 พ.ย. 2569") ส่งฟอร์มได้ ค่าที่ส่งเป็นค่าเดิม"""
    ctx.goto(ctx.base + "/supplies/receive")
    t = today(ctx)
    expect(ctx.locator("#received_date")).to_have_value(short(t))
    with ctx.expect_navigation():
        ctx.press("#source", "Control+Enter")
    expect(ctx.locator(".form-error")).to_be_visible()
    assert value(ctx, "received_date") == t.isoformat()
    expect(ctx.locator("#received_date")).not_to_have_attribute("aria-invalid", "true")


def test_without_javascript_native_date_input_remains(base, browser):  # noqa: F811
    c = browser.new_context(java_script_enabled=False)
    pg = c.new_page()
    from tests import ui_audit
    pg.goto(base + "/login")
    pg.fill("#username", ui_audit.USER)
    pg.fill("#password", ui_audit.PASSWORD)
    pg.click("button[type=submit]")
    pg.goto(base + "/supplies/receive")
    assert pg.locator("#received_date").get_attribute("type") == "date"
    assert pg.locator("#received_date").get_attribute("name") == "received_date"
    c.close()
