"""QR_ENABLED (0.9.0): ค่าเริ่มต้นปิด ซ่อนส่วน QR แต่ไม่ลบโค้ด และหน้า /q/{code} ยังเปิดได้"""
import pytest

from app import config
from tests.test_inventory import open_round


@pytest.fixture
def qr_off(monkeypatch):
    monkeypatch.setenv("QR_ENABLED", "off")


def test_default_is_off(monkeypatch, tmp_path):
    monkeypatch.delenv("QR_ENABLED", raising=False)
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "none.env")  # ไม่อ่าน .env ของเครื่อง
    assert config.qr_enabled() is False
    for value, expected in (("on", True), ("1", True), ("off", False), ("no", False)):
        monkeypatch.setenv("QR_ENABLED", value)
        assert config.qr_enabled() is expected


def test_labels_hidden_when_off(qr_off, editor):
    assert editor.get("/labels").status_code == 404
    assert editor.get("/labels/sheet").status_code == 404
    html = editor.get("/assets").text
    assert 'href="/labels"' not in html and "สติกเกอร์ QR" not in html and "ยังไม่ได้พิมพ์สติกเกอร์" not in html
    detail = editor.get("/assets/1").text
    assert "หน้าที่เปิดจาก QR" not in detail


def test_labels_visible_when_on(editor):
    assert editor.get("/labels").status_code == 200
    assert 'href="/labels"' in editor.get("/assets").text


def test_q_page_still_opens_without_inventory_box(qr_off, editor, db):
    open_round(editor)
    page = editor.get("/q/99-9999-1-1306-0004")
    assert page.status_code == 200 and "ตู้เหล็ก 4 ลิ้นชัก" in page.text
    assert "/inventory\"" not in page.text and 'action="/q/99-9999-1-1306-0004/inventory"' not in page.text
    r = editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": "ok"}, follow_redirects=True)
    assert "ปิดอยู่" in r.text
    assert db.execute("SELECT COUNT(*) FROM inventory_check_items WHERE checked_at IS NOT NULL").fetchone()[0] == 0
    # ตรวจแบบใบรายการห้องยังเข้าได้
    assert editor.get("/inventory").status_code == 200


def test_q_inventory_box_when_on(editor):
    open_round(editor)
    assert 'action="/q/99-9999-1-1306-0004/inventory"' in editor.get("/q/99-9999-1-1306-0004").text


def test_network_warning_and_tab_hidden_when_off(qr_off, admin, monkeypatch):
    monkeypatch.setenv("BASE_URL", "http://203.0.113.99:8000")  # ไม่ตรงกับเครื่องแน่นอน
    html = admin.get("/settings").text
    assert 'class="net-warning"' not in html and 'href="/settings/network"' not in html
    monkeypatch.setenv("QR_ENABLED", "on")
    html = admin.get("/settings").text
    assert 'class="net-warning"' in html and 'href="/settings/network"' in html
