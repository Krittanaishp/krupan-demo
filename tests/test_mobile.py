"""แถบเตือน BASE_URL สำหรับ admin ทุกหน้า และโครงหน้าสำหรับมือถือ"""
import pytest

from app import network
from tests.conftest import login


@pytest.fixture(autouse=True)
def fresh_network_cache():
    network.clear_cache()
    yield
    network.clear_cache()


def test_check_reports_mismatch_after_ip_change(monkeypatch):
    """IP ของเครื่องเปลี่ยนจาก 192.168.1.5 เป็น 192.168.10.121 แต่ BASE_URL ยังเป็นค่าเดิม"""
    ok, message = network.check("http://192.168.1.5:8000", ["192.168.10.121"])
    assert not ok and "ไม่ตรงกับ IP" in message
    assert network.check("http://192.168.10.121:8000", ["192.168.10.121"])[0]


def test_admin_sees_warning_on_every_page(client, monkeypatch):
    monkeypatch.setattr(network, "local_ipv4s", lambda: ["192.168.10.121"])  # test ตั้ง BASE_URL=192.0.2.10
    login(client, "admin1")
    for url in ("/assets", "/review", "/assets/1", "/q/99-9999-1-1306-0004", "/users"):
        html = client.get(url).text
        assert 'class="net-warning"' in html and "BASE_URL ไม่ตรงกับ IP ของเครื่องนี้" in html, url
        assert "192.168.10.121" in html and "http://192.0.2.10:8000" in html
    page = client.get("/settings/network").text  # หน้าตั้งค่ามีกล่องเตือนของตัวเองอยู่แล้ว
    assert 'class="net-warning"' not in page and "alert-danger" in page


def test_no_warning_for_non_admin_or_when_matching(client, monkeypatch):
    monkeypatch.setattr(network, "local_ipv4s", lambda: ["192.168.10.121"])
    login(client)  # editor
    assert 'class="net-warning"' not in client.get("/assets").text
    client.post("/logout")
    network.clear_cache()
    monkeypatch.setattr(network, "local_ipv4s", lambda: ["192.0.2.10"])
    login(client, "admin1")
    assert 'class="net-warning"' not in client.get("/assets").text


def test_warning_is_cached(client, monkeypatch):
    calls = []
    monkeypatch.setattr(network, "local_ipv4s", lambda: calls.append(1) or ["192.168.10.121"])
    login(client, "admin1")
    for _ in range(3):
        client.get("/assets")
    assert len(calls) == 1


def test_pages_support_iphone_safe_area(client):
    assert "viewport-fit=cover" in client.get("/login").text
    assert "viewport-fit=cover" in client.get("/q/99-9999-1-1306-0004").text
    css = client.get("/static/css/app.css").text
    assert "env(safe-area-inset-left)" in css
    assert ".table-wrap, .table-scroll { position: relative" in css
    assert "overflow-x: hidden" not in css.split("body {")[1].split("}")[0]


def test_review_rows_have_card_labels(editor):
    html = editor.get("/review").text
    assert 'class="flags"' in html and 'class="small facts"' in html and "ชีตต้นทาง: " in html
    assert '<span class="k">ที่ตั้ง:</span> ห้องปกครอง' in html


def test_health_needs_no_login_but_shows_only_ok(client):
    """ไม่ล็อกอิน: ตอบแค่ "ok" (ไม่บอกเวอร์ชันและเวลาสำรองข้อมูล) · ล็อกอินแล้วจึงเห็นรายละเอียด"""
    r = client.get("/health")
    assert r.status_code == 200 and r.text == "ok\n"
    login(client, "viewer1")
    r = client.get("/health")
    assert r.text.splitlines()[0] == "ok" and "last_backup_ok=none" in r.text and "version=" in r.text


def test_server_writes_info_file_for_install_scripts(tmp_path, monkeypatch):
    """install.ps1 / update.ps1 ยืนยันเวอร์ชันที่กำลังทำงานจาก logs/server-info.txt (ไม่ใช่จาก /health)"""
    import os
    from fastapi.testclient import TestClient

    from app import version
    from app.main import app
    monkeypatch.setenv("KRUPAN_LOG_DIR", str(tmp_path / "logs"))
    with TestClient(app):  # lifespan: เขียนตอน server เปิด
        pass
    text = (tmp_path / "logs" / "server-info.txt").read_text(encoding="utf-8")
    assert f"version={version.version()}\n" in text and f"pid={os.getpid()}\n" in text and "started=" in text


def test_assets_rows_have_card_fields(editor):
    html = editor.get("/assets").text
    assert 'class="assets asset-list"' in html
    for cls in ('class="cat"', 'class="num price"', 'class="loc"', 'class="status"', 'class="name"'):
        assert cls in html, cls
    assert "หมวด: " in html and "ที่ตั้ง: " in html and "เลือกเพื่อจำหน่าย" in html
