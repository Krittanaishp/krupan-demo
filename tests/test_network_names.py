"""BASE_URL แบบชื่อ (เช่น http://krupan.klongyai:8000 จาก local DNS ของ router): แปลงชื่อเป็น IP แล้วเทียบกับ IP ของเครื่อง
ไม่ใช้ DNS จริง ส่งตัวแปลงชื่อสมมติเข้าไปแทน"""
import pytest

from app import network
from tests.conftest import login

IPS = ["192.168.10.123"]


def fake_dns(table):
    return lambda host: table.get(host, [host] if network.is_ip(host) else [])


@pytest.fixture(autouse=True)
def fresh_network_cache():
    network.clear_cache()
    yield
    network.clear_cache()


def test_name_resolving_to_this_machine_is_ok():
    r = network.inspect("http://krupan.klongyai:8000", IPS, fake_dns({"krupan.klongyai": ["192.168.10.123"]}))
    assert r["ok"] and r["is_name"] and r["host"] == "krupan.klongyai" and r["resolved"] == ["192.168.10.123"]
    assert "แปลงเป็น IP ของเครื่องนี้ (192.168.10.123)" in r["message"] and r["note"] == ""


def test_name_that_cannot_be_resolved_is_red():
    r = network.inspect("http://krupan.klongyai:8000", IPS, fake_dns({}))
    assert not r["ok"] and r["resolved"] == []
    assert "แปลงชื่อ krupan.klongyai ใน BASE_URL เป็น IP ไม่ได้" in r["message"]


def test_name_resolving_elsewhere_is_red():
    r = network.inspect("http://krupan.klongyai:8000", IPS, fake_dns({"krupan.klongyai": ["192.168.10.99"]}))
    assert not r["ok"] and "แปลงเป็น 192.168.10.99 ซึ่งไม่ใช่ IP ของเครื่องนี้" in r["message"]


def test_dot_local_name_gets_mdns_note_even_when_ok():
    r = network.inspect("http://krupan.local:8000", IPS, fake_dns({"krupan.local": ["192.168.10.123"]}))
    assert r["ok"] and "Android" in r["note"] and ".local" in r["note"]


def test_ip_messages_unchanged():
    assert network.inspect("http://192.168.10.123:8000", IPS)["message"] == "BASE_URL ตรงกับ IP ของเครื่องนี้"
    r = network.inspect("http://192.168.10.121:8000", IPS)
    assert not r["ok"] and r["message"] == "BASE_URL ไม่ตรงกับ IP ของเครื่องนี้" and not r["is_name"]
    assert not network.inspect("http://localhost:8000", IPS)["ok"]


def test_network_page_shows_resolved_name(client, monkeypatch):
    monkeypatch.setenv("BASE_URL", "http://krupan.klongyai:8000")
    monkeypatch.setattr(network, "local_ipv4s", lambda: IPS)
    monkeypatch.setattr(network, "resolve_host", fake_dns({"krupan.klongyai": ["192.168.10.99"]}))
    login(client, "admin1")
    html = client.get("/settings/network").text
    assert "alert-danger" in html and "ซึ่งไม่ใช่ IP ของเครื่องนี้" in html
    assert "<code>krupan.klongyai</code> แปลงเป็น IP" in html and "<code>192.168.10.99</code>" in html
    assert "ตั้ง local DNS ให้ชื่อ <code>krupan.klongyai</code> ชี้ไปที่ <code>192.168.10.123</code>" in html
    # แถบเตือนทุกหน้าของ admin ใช้ข้อความเดียวกัน
    assert "ซึ่งไม่ใช่ IP ของเครื่องนี้" in client.get("/assets").text


def test_network_page_ok_with_name(client, monkeypatch):
    monkeypatch.setenv("BASE_URL", "http://krupan.klongyai:8000")
    monkeypatch.setattr(network, "local_ipv4s", lambda: IPS)
    monkeypatch.setattr(network, "resolve_host", fake_dns({"krupan.klongyai": ["192.168.10.123"]}))
    login(client, "admin1")
    html = client.get("/settings/network").text
    assert "alert-danger" not in html and "แปลงเป็น IP ของเครื่องนี้" in html and "(ตรงกับเครื่องนี้)" in html
