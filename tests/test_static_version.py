"""ไฟล์ CSS/JS ทุกไฟล์ในหน้าต่อท้ายด้วย ?v=<hash ของเนื้อไฟล์> (app/static_version.py)
หลังอัปเดตระบบ เบราว์เซอร์ของเจ้าหน้าที่โหลดไฟล์ใหม่เอง ไม่ต้องกด Ctrl+F5 · เนื้อไฟล์เดิม = URL เดิม (ยังใช้ที่เก็บไว้ได้)"""
import hashlib
import re

import pytest

from app import static_version
from app.static_version import STATIC_DIR, static_url
from tests.conftest import ROOT

TEMPLATES = ROOT / "app" / "templates"
ASSET = re.compile(r'<(?:link[^>]*rel="stylesheet"[^>]*href|script[^>]*src)="([^"]+)"')
PAGES = ["/", "/supplies", "/supplies/receive", "/supplies/issue", "/supplies/import", "/supplies/issues",
         "/assets", "/reports", "/reports/summary", "/reports/register", "/reports/disposals", "/labels/sheet?border=1",
         "/inventory", "/settings", "/account/password"]


def assets_of(html):
    return ASSET.findall(html)


def check_versioned(url):
    m = re.fullmatch(r"/static/((?:css|js)/[\w.-]+)\?v=([0-9a-f]{12})", url)
    assert m, f"ไม่มี ?v= หรือรูปแบบผิด: {url}"
    path, v = m.groups()
    assert v == hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:12], url


def test_templates_never_link_static_css_js_directly():
    """เทมเพลตอ้าง CSS/JS ผ่าน static_url() เท่านั้น (ไม่มี /static/css/... หรือ /static/js/... ตรง ๆ)"""
    bad = []
    for p in TEMPLATES.rglob("*.html"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if re.search(r'(href|src)="/static/(css|js)/', line):
                bad.append(f"{p.relative_to(TEMPLATES)}: {line.strip()}")
    assert not bad, bad


def test_every_css_js_on_every_page_has_content_hash(admin, db):
    seen = set()
    for page in PAGES:
        r = admin.get(page)
        assert r.status_code == 200, page
        urls = assets_of(r.text)
        assert urls, page
        for u in urls:
            check_versioned(u)
            seen.add(u.split("?")[0])
    assert {"/static/css/app.css", "/static/js/app.js", "/static/js/datepicker.js", "/static/js/htmx.min.js",
            "/static/js/supplies.js", "/static/css/print-doc.css"} <= seen


def test_login_page_without_login_also_versioned(client, db):
    urls = assets_of(client.get("/login").text)
    assert urls
    for u in urls:
        check_versioned(u)


def test_versioned_url_is_served_and_keeps_csp(admin, db):
    url = static_url("css/app.css")
    r = admin.get(url)
    assert r.status_code == 200 and r.content == (STATIC_DIR / "css/app.css").read_bytes()
    page = admin.get("/supplies")
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp  # ?v= เป็นไฟล์จากเครื่องเดิม ผ่าน 'self'


def test_hash_changes_when_content_changes(tmp_path):
    d = tmp_path / "css"
    d.mkdir()
    f = d / "x.css"
    f.write_text("body { color: red; }", encoding="utf-8")
    a = static_url("css/x.css", root=tmp_path)
    assert a == static_url("css/x.css", root=tmp_path)  # เนื้อเดิม = URL เดิม
    f.write_text("body { color: blue; }", encoding="utf-8")
    b = static_url("css/x.css", root=tmp_path)
    assert a != b and a.split("?")[0] == b.split("?")[0] == "/static/css/x.css"
    assert b.endswith("?v=" + hashlib.sha256(b"body { color: blue; }").hexdigest()[:12])
    f.write_text("body { color: red; }", encoding="utf-8")  # แก้กลับเป็นเนื้อเดิม = URL เดิม
    assert static_url("css/x.css", root=tmp_path) == a


def test_hash_is_computed_once_per_file_version(tmp_path, monkeypatch):
    (tmp_path / "a.js").write_text("1", encoding="utf-8")
    calls = []
    real = static_version.hashlib.sha256
    monkeypatch.setattr(static_version.hashlib, "sha256", lambda b: calls.append(1) or real(b))
    for _ in range(5):
        static_url("a.js", root=tmp_path)
    assert len(calls) == 1


def test_missing_file_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        static_url("css/nope.css", root=tmp_path)
