"""header ความปลอดภัย (app/security_headers.py), ไม่มี inline script/style ในเทมเพลต (CSP), id ที่เป็นไปไม่ได้ใน URL ตอบ 404"""
import re

import pytest

from app import security_headers
from tests.conftest import ROOT

TEMPLATES = ROOT / "app" / "templates"
import importlib
import pkgutil

import app.routers as routers_pkg
from app.main import app

SAMPLE = {"code": "99-9999-1-1306-0004", "room": "1", "building": "1", "group": "g", "kind": "buildings"}


def all_routes():
    routes = list(app.routes)
    for m in pkgutil.iter_modules(routers_pkg.__path__):
        mod = importlib.import_module(f"app.routers.{m.name}")
        if hasattr(mod, "router"):
            routes += list(mod.router.routes)
    out = set()
    for r in routes:
        for method in getattr(r, "methods", None) or ():
            if method in ("GET", "POST"):
                path = re.sub(r"\{(\w+)(:[^}]*)?\}", lambda m: SAMPLE.get(m.group(1), "1"), r.path)
                out.add((method, r.path, path))
    return sorted(out)


ROUTES = all_routes()


def test_headers_on_pages_static_and_downloads(editor):
    for url in ("/login", "/supplies", "/static/css/app.css", "/supplies/balances.xlsx", "/no-such-page"):
        h = editor.get(url).headers
        assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff", url
        assert h["referrer-policy"] == "same-origin", url
        csp = h["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self';" in csp and "frame-ancestors 'none'" in csp, url
        assert "unsafe-inline" not in csp and "unsafe-eval" not in csp, url


def test_nonce_is_new_every_request_and_matches_style(editor):
    nonces = set()
    for _ in range(3):
        r = editor.get("/reports/register")
        nonce = re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
        assert f'<style nonce="{nonce}">' in r.text
        nonces.add(nonce)
    assert len(nonces) == 3


def test_templates_have_no_inline_script_handler_or_style_attribute():
    for p in TEMPLATES.rglob("*.html"):
        text = p.read_text(encoding="utf-8")
        rel = p.relative_to(TEMPLATES)
        assert not re.search(r"\son[a-z]+=", text), f"{rel}: มี event handler แบบ inline"
        assert 'style="' not in text, f"{rel}: มี style attribute"
        assert "javascript:" not in text, f"{rel}: มีลิงก์ javascript:"
        for m in re.finditer(r"<script\b([^>]*)>", text):
            attrs = m.group(1)
            assert "src=" in attrs or 'type="application/json"' in attrs, f"{rel}: มี inline script"
        for m in re.finditer(r"<style\b([^>]*)>", text):
            assert "csp_nonce(request)" in m.group(1), f"{rel}: <style> ไม่มี nonce"


def test_htmx_does_not_inject_its_own_style():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert '"includeIndicatorStyles": false' in base


def test_server_header_is_off_in_every_launcher():
    assert "server_header=False" in (ROOT / "scripts" / "serve.py").read_text(encoding="utf-8")
    for doc in ("README.md", "README.en.md"):
        assert "--no-server-header" in (ROOT / doc).read_text(encoding="utf-8"), doc


def test_csp_nonce_helper():
    class R:
        class state:
            csp_nonce = "abc"
    assert security_headers.csp_nonce(R) == "abc" and security_headers.csp_nonce(None) == ""


INT_ROUTES = sorted({(m, pattern) for m, pattern, _ in ROUTES if re.search(
    r"\{(asset_id|check_id|preset_id|merge_id|location_id|area_id|repair_id|disposal_id|rule_id|staff_id|item_id|unit_id|"
    r"cat_id|rid|iid|batch_id|user_id)\}", pattern)})
BAD_IDS = ["999999999999999999999999", "9223372036854775808", "-1", "0"]


def fill(pattern, bad):
    sample = {"code": "99-9999-1-1306-0004", "room": "1", "building": "1", "group": "g", "kind": "buildings"}
    return re.sub(r"\{(\w+)\}", lambda m: sample.get(m.group(1), bad), pattern)


def test_int_route_list():
    assert len(INT_ROUTES) > 40


@pytest.mark.parametrize("method,pattern", INT_ROUTES)
def test_impossible_ids_are_404_not_500(admin, method, pattern):
    for bad in BAD_IDS:
        path = fill(pattern, bad)
        r = admin.get(path, follow_redirects=False) if method == "GET" else admin.post(path, data={}, follow_redirects=False)
        assert r.status_code == 404, (method, path, r.status_code)


@pytest.mark.parametrize("path", ["/inventory/1/rooms/999999999999999999999999", "/inventory/1/rooms/999999999999999999999999/print",
                                  "/inventory/1/buildings/999999999999999999999999/print"])
def test_huge_number_inside_text_param_is_not_500(admin, path):
    assert admin.get(path, follow_redirects=False).status_code != 500


def test_demo_menu_for_anonymous_shows_only_open_pages(client):
    """demo: ทะเบียนครุภัณฑ์เปิดดูได้โดยไม่ล็อกอิน เมนูที่คนยังไม่ล็อกอินเห็นต้องเปิดได้จริงทุกเมนู (ไม่เด้งไปหน้าล็อกอิน)"""
    html = client.get("/login").text
    nav = html.split('<nav class="nav"', 1)[1].split("</nav>", 1)[0]
    links = sorted(set(re.findall(r'href="(/[^"#?]*)"', nav)))
    assert "/assets" in links and "/supplies" not in links and "/reports" not in links
    for href in links:
        assert client.get(href, follow_redirects=False).status_code == 200, href
    assert client.get("/health").text == "ok\n"
