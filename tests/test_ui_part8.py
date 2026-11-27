"""ชื่อหน่วยงานจากค่าตั้งค่า, เมนูห้องและผู้รับผิดชอบ, คำว่า "ตรวจสอบพัสดุ", ค้นหาในหน้ารอตรวจสอบ, เมนู "เพิ่มเติม" """
import json
import sqlite3
from io import BytesIO

from openpyxl import load_workbook


from tests.conftest import ROOT, login, make_db

APP = ROOT / "app"


# ---------------------------------------------------------------- ชื่อหน่วยงาน

def test_no_org_name_hardcoded_in_app():
    """ห้ามฝังชื่อหน่วยงานในโค้ดหรือเทมเพลต (ค่ามาจาก app_settings เท่านั้น)"""
    for p in list(APP.rglob("*.py")) + list(APP.rglob("*.html")) + list(APP.rglob("*.js")) + list(APP.rglob("*.css")):
        text = p.read_text(encoding="utf-8")
        for word in ("อำเภอตัวอย่าง", "ที่ทำการปกครองอำเภอ", "ที่ว่าการอำเภอ"):
            assert word not in text, f"{p.relative_to(ROOT)} มี “{word}”"


def test_header_q_and_labels_use_display_name(admin):
    html = admin.get("/assets").text
    assert '<span class="brand-sub">ที่ว่าการอำเภอทดสอบ</span>' in html
    assert "<title>ทะเบียนครุภัณฑ์ · ที่ว่าการอำเภอทดสอบ</title>" in html
    assert "ทะเบียนครุภัณฑ์ · ที่ว่าการอำเภอทดสอบ" in admin.get("/q/99-9999-1-1306-0004").text
    sheet = admin.get("/labels/sheet?mode=ids&ids=1").text
    assert "ที่ว่าการอำเภอทดสอบ" in sheet and "ที่ทำการปกครองอำเภอทดสอบ" not in sheet


def test_reports_use_report_name(editor):
    assert "ของที่ทำการปกครองอำเภอทดสอบ (งานการเงินและบัญชี)" in editor.get("/reports/register").text
    ws = load_workbook(BytesIO(editor.get("/reports/summary.xlsx").content)).active
    assert ws["A2"].value == "ของที่ทำการปกครองอำเภอทดสอบ (งานการเงินและบัญชี)"


def test_admin_changes_org_names(admin, db):
    page = admin.get("/settings").text
    assert 'value="ที่ว่าการอำเภอทดสอบ"' in page and 'value="ที่ทำการปกครองอำเภอทดสอบ"' in page
    r = admin.post("/settings/org", data={"display": "  ที่ว่าการอำเภอใหม่ ", "report": "ที่ทำการปกครองอำเภอใหม่"},
                   follow_redirects=True)
    assert "บันทึกชื่อหน่วยงานแล้ว" in r.text
    assert '<span class="brand-sub">ที่ว่าการอำเภอใหม่</span>' in r.text  # หัวเว็บเปลี่ยนทันที (ล้าง cache)
    assert "ของที่ทำการปกครองอำเภอใหม่ (งานการเงินและบัญชี)" in admin.get("/reports/register").text
    log = db.execute("SELECT changes FROM audit_log WHERE table_name = 'app_settings'").fetchone()
    assert json.loads(log[0])["ORG_DISPLAY_NAME"] == {"old": "ที่ว่าการอำเภอทดสอบ", "new": "ที่ว่าการอำเภอใหม่"}


def test_org_names_validation_and_admin_only(client):
    login(client)
    assert client.get("/settings").status_code == 403
    assert client.post("/settings/org", data={"display": "x", "report": "y"}).status_code == 403
    client.post("/logout")
    login(client, "admin1")
    r = client.post("/settings/org", data={"display": "", "report": "ก" * 121})
    assert r.status_code == 400 and "กรอกชื่อหน่วยงาน" in r.text and "ยาวเกิน 120" in r.text


# ---------------------------------------------------------------- migration 008

def db_at_007(path):
    make_db(path)
    conn = sqlite3.connect(path)
    conn.executescript("""
        DELETE FROM app_settings WHERE key LIKE 'ORG_%';
        DELETE FROM schema_migrations WHERE name = '008';
        UPDATE assets SET needs_review = 'ไม่มีราคา, ไม่พบในการตรวจนับปี 2569' WHERE id = 3;
        UPDATE assets SET needs_review = 'พบระหว่างตรวจนับ ไม่มีในทะเบียน' WHERE id = 4;
        INSERT INTO locations (id, name) VALUES (1, 'ห้อง ก'), (2, 'ห้อง ข');
        INSERT INTO asset_moves (asset_id, moved_at, from_location_id, to_location_id, note, reason)
            VALUES (5, '2026-10-01', 1, 2, 'ตรวจนับปี 2569', 'พบระหว่างตรวจนับ');
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------- ตรวจสอบพัสดุ

def supplies_file(p):
    """ระบบวัสดุ (0.9.0) ใช้คำว่า "ตรวจนับวัสดุ" ตามที่ฝ่ายเสมียนตราใช้ ส่วนครุภัณฑ์ยังต้องเป็น "ตรวจสอบพัสดุ" """
    rel = p.relative_to(APP).as_posix()
    return rel.startswith(("supplies", "routers/supplies", "templates/supplies/", "static/js/supplies"))


def test_word_inventory_check_renamed_everywhere():
    for p in list(APP.rglob("*.py")) + list(APP.rglob("*.html")) + list(APP.rglob("*.js")):
        if supplies_file(p):
            continue
        assert "ตรวจนับ" not in p.read_text(encoding="utf-8"), p.relative_to(ROOT)
    for p in (ROOT / "docs").glob("คู่มือ*.md"):
        assert "ตรวจนับ" not in p.read_text(encoding="utf-8"), p.name


def test_menu_and_review_group_say_tor_sor(editor, db):
    html = editor.get("/assets").text
    assert '>ตรวจสอบพัสดุ</a>' in html
    db.execute("UPDATE assets SET needs_review = 'ไม่พบในการตรวจสอบพัสดุประจำปี 2569' WHERE id = 3")
    db.commit()
    assert "จากการตรวจสอบพัสดุ" in editor.get("/review?group=inventory").text


# ---------------------------------------------------------------- ห้องและผู้รับผิดชอบ

def test_places_menu_and_tabs(editor):
    html = editor.get("/places").text
    assert '<a href="/places" aria-current="page">ห้องและผู้รับผิดชอบ</a>' in html
    for href, label in (("/places", "ห้อง"), ("/places/staff", "ผู้รับผิดชอบ"), ("/places/mapping", "จัดห้องจากข้อมูลเดิม")):
        assert f'href="{href}"' in html and f">{label}</a>" in html
    assert ">ห้อง</a>\n" not in html.split('class="nav"')[1].split("</nav>")[0]  # ไม่มีเมนูห้อง/ผู้รับผิดชอบแยกแล้ว
    assert editor.get("/places/staff").status_code == 200 and editor.get("/places/mapping").status_code == 200


def test_old_place_urls_redirect(editor):
    for old, new in (("/locations", "/places"), ("/staff", "/places/staff"),
                     ("/locations/mapping?todo=1", "/places/mapping?todo=1")):
        r = editor.get(old, follow_redirects=False)
        assert r.status_code == 301 and r.headers["location"] == new, old
    r = editor.post("/locations", data={"name": "ห้องใหม่"}, follow_redirects=False)
    assert r.headers["location"] == "/places"
    r = editor.post("/staff", data={"nickname": "ทดสอบ"}, follow_redirects=False)
    assert r.headers["location"] == "/places/staff"


def test_viewer_has_no_mapping_tab(client):
    login(client, "viewer1")
    assert 'href="/places/mapping"' not in client.get("/places").text


# ---------------------------------------------------------------- เมนู "เพิ่มเติม"

def test_more_menu_holds_rarely_used_items(admin):
    html = admin.get("/assets").text
    nav = html.split('class="nav"')[1].split("</nav>")[0]
    for href in ("/types", "/users", "/settings"):
        assert f'<a href="{href}" class="nav-extra"' in nav, href
    more = nav.split('class="nav-more"')[1]
    assert "เพิ่มเติม" in more and all(f'href="{h}"' in more for h in ("/types", "/users", "/settings"))
    css = admin.get("/static/css/app.css").text
    assert "@media (max-width: 1279.98px)" in css and ".nav > a.nav-extra { display: none; }" in css


# ---------------------------------------------------------------- ค้นหาในหน้ารอตรวจสอบ

def test_review_search_within_group_updates_counts(editor):
    html = editor.get("/review?group=price&q=Lucky").text
    assert "ตู้เหล็ก 4 ลิ้นชัก" in html and 'value="Lucky"' in html
    tabs = html.split('id="review-groups"')[1].split("</nav>")[0]
    assert "ไม่มีราคา <span class=\"count-pill\">1</span>" in " ".join(tabs.split())
    assert "ไม่มีเลขทะเบียน <span class=\"count-pill\">0</span>" in " ".join(tabs.split())
    assert "q=Lucky" in tabs  # แท็บเก็บคำค้นไว้


def test_review_search_thai_digits_and_all_groups(editor):
    assert "ตู้เหล็ก 4 ลิ้นชัก" in editor.get("/review?group=price&q=๑๓๐๖-๐๐๐๔").text
    html = editor.get("/review?group=price&q=TMP").text
    assert "ไม่พบ “TMP” ในกลุ่มนี้" in html
    html = editor.get("/review?group=price&q=TMP&all=1").text
    assert "TMP-0001" in html and "ผลค้นหา “TMP” ทุกกลุ่ม" in html


def test_review_search_htmx_partial_and_url(editor):
    r = editor.get("/review?group=price&q=ตู้", headers={"HX-Request": "true", "HX-Filter-History": "replace"})
    assert r.headers["HX-Replace-Url"] == "/review?group=price&q=%E0%B8%95%E0%B8%B9%E0%B9%89"
    body = r.text
    assert body.lstrip().startswith('<div id="review-rows"')
    assert 'id="review-groups"' in body and 'hx-swap-oob="true"' in body and 'id="review-export"' in body
    assert "<html" not in body and 'id="review-q"' not in body  # ช่องค้นหาไม่ถูกแทนที่


def test_review_export_respects_query(editor):
    html = editor.get("/review?group=price&q=Lucky").text
    assert 'href="/review/export.xlsx?group=price&amp;q=Lucky"' in html
    wb = load_workbook(BytesIO(editor.get("/review/export.xlsx?group=price&q=Lucky").content))
    ws = wb.active
    assert "ค้นหา “Lucky”" in ws["A1"].value and ws["C4"].value == "ตู้เหล็ก 4 ลิ้นชัก" and ws.max_row == 4
    wb = load_workbook(BytesIO(editor.get("/review/export.xlsx?q=ไม่มีคำนี้").content))
    assert "ไม่มีรายการรอตรวจสอบที่ตรงกับ" in wb.active["A1"].value


def test_resolve_keeps_search(editor):
    r = editor.post("/review/resolve", data={"asset_id": "1", "flag": "ไม่มีราคา", "group": "price", "q": "Lucky"},
                    follow_redirects=False)
    assert r.headers["location"] == "/review?group=price&q=Lucky"
