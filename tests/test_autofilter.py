"""ตัวกรองอัตโนมัติ (static/js/autofilter.js) ฝั่งเซิร์ฟเวอร์และเทมเพลต · พฤติกรรมในเบราว์เซอร์อยู่ใน test_autofilter_browser.py"""
import re

from app.filter_history import set_filter_url
from tests.conftest import ROOT

TEMPLATES = ROOT / "app" / "templates"
# หน้าที่มีตัวกรอง: กรองอัตโนมัติทุกหน้า (ยกเว้นรายการใน KEEP_BUTTON)
AUTO = {
    "supplies/issues.html": "issue-filters", "supplies/receipts.html": "receipt-filters",
    "supplies/balances.html": "balance-filters", "supplies/item.html": "card-filters",
    "assets/list.html": "filters", "locations/list.html": "room-filters", "locations/mapping.html": "map-filters",
    "review/list.html": "review-search", "reports/summary.html": "summary-filters", "supplies/monthly.html": "monthly-filters",
    "supplies/items.html": "item-filters",
}
# คงปุ่มไว้: รายงานที่เปิดแท็บใหม่และต้องเลือกหลายช่องให้ครบก่อน / หน้าพิมพ์สติกเกอร์ / ช่องค้นหาในหน้า QR ไม่พบ
KEEP_BUTTON = {"reports/index.html", "labels/setup.html", "q/not_found.html", "assets/_results.html"}


def forms(text):
    return re.findall(r"<form\b[^>]*>.*?</form>", text, re.S)


def test_every_get_filter_form_is_autofilter_or_listed():
    for p in TEMPLATES.rglob("*.html"):
        rel = p.relative_to(TEMPLATES).as_posix()
        for f in forms(p.read_text(encoding="utf-8")):
            head = f[:f.index(">")]
            if 'method="post"' in head or "hx-post" in head:
                continue
            if rel in KEEP_BUTTON:
                assert "data-autofilter" not in head, rel
                continue
            assert rel in AUTO, f"ฟอร์ม GET ใหม่ใน {rel}: ใส่ data-autofilter หรือเพิ่มใน KEEP_BUTTON พร้อมเหตุผล"
            assert f'id="{AUTO[rel]}"' in head and "data-autofilter" in head and 'hx-trigger="krupan:filter"' in head, rel
            assert 'hx-sync="this:replace"' in head and "hx-indicator=" in head, rel
            # ปุ่มกดมีเฉพาะใน noscript (ปิด JavaScript ยังกรองได้) · ไม่มี hx-get ในแต่ละช่องแล้ว
            outside = re.sub(r"<noscript>.*?</noscript>", "", f, flags=re.S)
            assert 'type="submit"' not in outside, rel
            assert re.search(r"<noscript>\s*<button[^>]*type=\"submit\"", f), rel
            assert "hx-get" not in f[len(head):], rel


def test_base_loads_autofilter_and_reloads_on_back():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "static_url('js/autofilter.js')" in base
    assert '"historyCacheSize": 0' in base and '"refreshOnHistoryMiss": true' in base


def test_autofilter_js_rules():
    js = (ROOT / "app" / "static" / "js" / "autofilter.js").read_text(encoding="utf-8")
    assert "var DELAY = 300;" in js
    assert "if (q === s.last) return;" in js  # Enter / ค่าเดิม ไม่ส่งซ้ำ
    assert "e.preventDefault();" in js and '"HX-Filter-History"' in js
    assert "params.delete(k)" in js  # ช่องว่างไม่อยู่ใน URL


class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


class FakeResponse:
    def __init__(self):
        self.headers = {}


def test_set_filter_url_push_or_replace():
    r = set_filter_url(FakeRequest({"HX-Filter-History": "push"}), FakeResponse(), "/places?q=a")
    assert r.headers == {"HX-Push-Url": "/places?q=a"}
    r = set_filter_url(FakeRequest({"HX-Filter-History": "replace"}), FakeResponse(), "/places?q=a")
    assert r.headers == {"HX-Replace-Url": "/places?q=a"}
    r = set_filter_url(FakeRequest({}), FakeResponse(), "/places")
    assert r.headers == {"HX-Replace-Url": "/places"}


def test_partial_only_for_filter_requests(editor):
    r = editor.get("/places?status=active", headers={"HX-Request": "true", "HX-Filter-History": "push"})
    assert "<html" not in r.text and r.headers["HX-Push-Url"] == "/places?status=active"
    # htmx request อื่น (ไม่ได้มาจากตัวกรอง) และตอนกู้ประวัติ ได้ทั้งหน้า
    assert "<html" in editor.get("/places", headers={"HX-Request": "true"}).text
    r = editor.get("/places", headers={"HX-Request": "true", "HX-Filter-History": "push", "HX-History-Restore-Request": "true"})
    assert "<html" in r.text
    r = editor.get("/supplies", headers={"HX-Request": "true", "HX-History-Restore-Request": "true"})
    assert "<html" in r.text


def test_issues_and_receipts_empty_messages(editor):
    assert "ยังไม่มีใบเบิก" in editor.get("/supplies/issues").text
    html = editor.get("/supplies/issues?month=2000-01").text
    assert "ไม่มีใบเบิกตามตัวกรองนี้" in html and "<table" not in html
    assert 'data-filter-clear="#issue-filters"' in html
    assert "ยังไม่มีใบรับ" in editor.get("/supplies/receipts").text
    html = editor.get("/supplies/receipts?month=2000-01").text
    assert "ไม่มีใบรับตามตัวกรองนี้" in html and 'data-filter-clear="#receipt-filters"' in html
    assert 'id="issue-results"' in editor.get("/supplies/issues").text
