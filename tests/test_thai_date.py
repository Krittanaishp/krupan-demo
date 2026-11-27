"""ช่องวันที่และช่องเดือนทุกหน้าใช้ช่องและปฏิทินของระบบ (static/js/datepicker.js) ที่พิมพ์/แสดงเป็น พ.ศ.
พฤติกรรมในเบราว์เซอร์ตรวจใน tests/test_datepicker_browser.py และ tests/ui_audit.py (ทุกช่องทุกหน้าทุกความกว้าง)"""
import re
from datetime import date

from tests.conftest import ROOT

TEMPLATES = ROOT / "app" / "templates"
JS = ROOT / "app" / "static" / "js"
DP_JS = (JS / "datepicker.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def test_picker_covers_every_date_and_month_input():
    assert 'querySelectorAll("input[type=date], input[type=month]")' in DP_JS
    assert "htmx.onLoad(setup)" in DP_JS  # รวมเนื้อหาที่ htmx ใส่เข้ามา
    assert "+ BE" in DP_JS and "var BE = 543" in DP_JS
    assert '"พฤศจิกายน"' in DP_JS and '"พ.ย."' in DP_JS
    assert 'var DAYS = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]' in DP_JS  # สัปดาห์เริ่มวันจันทร์
    assert "YEAR_RANGE = 100" in DP_JS
    # ไม่ใช้ปฏิทินของเบราว์เซอร์อีก (แบบเดิมซ้อนป้ายบนช่อง type=date)
    app_js = (JS / "app.js").read_text(encoding="utf-8")
    assert "enhanceDate" not in app_js and "showPicker" not in app_js and "showPicker" not in DP_JS
    assert ".date-label" not in CSS and ".date-box" not in CSS


def test_every_page_with_date_input_loads_datepicker():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "static_url('js/datepicker.js')" in base
    # โหลดก่อน autofilter.js: ตรวจวันที่ก่อนตัวกรองรับ submit
    assert base.index("js/datepicker.js") < base.index("js/autofilter.js")
    pages = [p for p in TEMPLATES.rglob("*.html") if re.search(r"type=[\"']?(date|month)", p.read_text(encoding="utf-8"))]
    assert pages, "ควรมีหน้าที่มีช่องวันที่"
    for p in pages:
        text = p.read_text(encoding="utf-8")
        assert '{% extends "base.html" %}' in text or p.name.startswith("_"), p.relative_to(TEMPLATES)
        assert "data-no-thai-date" not in text, p.relative_to(TEMPLATES)


def test_pages_render_date_inputs_with_iso_value(editor, db):
    """HTML ยังเป็น input type=date ค่า yyyy-mm-dd (ค.ศ.): ปิด JavaScript ก็ใช้ได้ · JS เปลี่ยนเป็นช่อง พ.ศ. + ช่องซ่อน"""
    for url in ("/supplies/issue", "/supplies/receive", "/supplies/import"):
        html = editor.get(url).text
        values = re.findall(r'type="date"[^>]*value="([^"]*)"', html)
        assert values and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in values), url


def test_optional_date_filters_say_what_empty_means():
    for rel, names in (("supplies/balances.html", ("as_of",)), ("supplies/item.html", ("date_from", "date_to")),
                       ("supplies/monthly.html", ("month",))):
        text = (TEMPLATES / rel).read_text(encoding="utf-8")
        for n in names:
            tag = re.search(rf'<input[^>]*name="{n}"[^>]*>', text).group(0)
            assert "data-empty-label=" in tag, (rel, n)
    assert '"ทุกเดือน"' in DP_JS and '"เลือกวันที่"' in DP_JS  # ค่าเริ่มต้นตอนไม่ระบุ


def test_box_height_and_message_below_box():
    """กล่องสูงเท่าช่องอื่น (ช่องพิมพ์ปกติ + ปุ่มปฏิทินในกล่อง) · ข้อความแจ้งอยู่ใต้ช่องเฉพาะตอนมี (ไม่ลอยทับช่องถัดไป)"""
    assert ".dp-field > input[data-dp]" in CSS and ".dp-msg {" in CSS and ".dp-msg[hidden] { display: none; }" in CSS
    msg = re.search(r"\.dp-msg \{(.*?)\}", CSS, re.S).group(1)
    assert "position: absolute" not in msg
    assert re.search(r"\.dp-cell \{[^}]*min-height: 44px", CSS)  # กดง่ายบนมือถือ
    # ช่องในแถวตัวกรองได้หน้าตาแบบเดียวกับช่องอื่น
    assert ".filters input[data-dp]" in CSS and ".range-form input[data-dp]" in CSS


def test_server_rejects_unreasonable_years(editor, db):
    """ฝั่ง server: ปีห่างจากปีนี้เกิน 100 ปี (เช่น ปี พ.ศ. ที่ส่งมาเป็น ค.ศ.) ไม่รับ ไม่พึ่ง JS อย่างเดียว"""
    from app import timeutil
    from app.routers import supplies as r
    y = timeutil.today_th().year
    assert timeutil.reasonable_year(date(y - 100, 1, 1)) and timeutil.reasonable_year(date(y + 100, 12, 31))
    assert not timeutil.reasonable_year(date(y + 101, 1, 1)) and not timeutil.reasonable_year(date(y + 543, 1, 1))
    assert r.month_or_none(f"{y + 543}-11") is None and r.month_or_none(f"{y}-11") == f"{y}-11"
    assert r.as_of_or_none(f"{y + 543}-01-01") is None
    # ตัวกรองที่ปีผิด: เหมือนไม่ได้กรอง (ไม่ error)
    for url in (f"/supplies?as_of={y + 543}-01-01", f"/supplies/issues?month={y + 543}-11",
                f"/supplies/monthly?month={y + 543}-11", f"/supplies/items/1?date_from={y + 543}-01-01"):
        assert editor.get(url).status_code in (200, 404), url
