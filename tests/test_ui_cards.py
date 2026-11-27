"""0.8.4 ตารางจัดการเป็นการ์ดบนจอแคบกว่า 1024px (.stack-table) ปุ่มเห็นครบโดยไม่เลื่อน · การตรวจด้วยเบราว์เซอร์จริงอยู่ใน tests/ui_audit.py"""
import re

from tests.conftest import ROOT, add_location, add_staff

CSS = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")


def stack_tables(html):
    return re.findall(r'<table class="[^"]*\bstack-table\b[^"]*">', html)


def test_stack_table_css():
    block = CSS[CSS.index("0.8.4 ตารางจัดการบนจอแคบ"):]
    assert "@media (max-width: 1023.98px)" in block
    assert ".stack-table thead { display: none; }" in block
    assert "content: attr(data-label)" in block
    assert re.search(r"\.stack-table td\.actions \{[^}]*flex-wrap: wrap", block)
    # checkbox ในการ์ดหน้าทะเบียนบนมือถือ 20px เท่าที่อื่น
    assert ".asset-list .pick-hit input { width: 20px; height: 20px; }" in CSS
    assert "22px; height: 22px" not in CSS


def test_management_pages_use_stack_table(admin, db):
    add_location(db, "ห้องทะเบียน")
    add_staff(db, "เอ็ม", "นางสาวทดลอง ใจดี")
    for url in ("/places", "/places/staff", "/settings/rooms", "/users", "/types", "/reports/summary", "/places/mapping"):
        html = admin.get(url).text
        assert stack_tables(html), url
    rooms = admin.get("/places").text
    assert 'data-label="ครุภัณฑ์"' in rooms and 'class="pick stack-inline" data-label="เลือกเพื่อรวม"' in rooms
    staff = admin.get("/places/staff").text
    assert 'data-label="ชื่อ-นามสกุล"' in staff and '<td class="actions">' in staff
    users = admin.get("/users").text
    assert 'data-label="รีเซ็ตรหัสผ่าน"' in users and 'data-label="ชื่อที่แสดง / สิทธิ์"' in users


def test_settings_rooms_rule_cells_labelled(admin, db):
    db.execute("INSERT INTO location_text_rules (match_text, exact, room_name) VALUES ('สนง.', 1, 'สำนักงาน')")
    db.commit()
    html = admin.get("/settings/rooms").text
    assert 'value="สนง."' in html
    for label in ("ข้อความเดิม", "ห้องที่ถูกต้อง", "ผู้รับผิดชอบที่เสนอ", "หมายเหตุ"):
        assert f'<td data-label="{label}">' in html
    for label in ("ตรงทุกตัวอักษร", "ใช้งาน"):
        assert f'<td class="stack-inline" data-label="{label}">' in html


def test_summary_hides_empty_unknown_on_cards(editor):
    # ข้อมูลสมมติ: หมวดตู้ (13) ทราบราคาทุกชิ้น · หมวดโต๊ะ-เก้าอี้ (14) มีชิ้นไม่ทราบราคา 1 ชิ้น
    html = editor.get("/reports/summary").text
    assert '<td class="stack-inline stack-hide">' in html
    assert '<td class="stack-inline">ไม่ทราบมูลค่า 1 รายการ' in html


def test_inventory_radio_has_visible_text_for_cards():
    html = (ROOT / "app" / "templates" / "inventory" / "room.html").read_text(encoding="utf-8")
    assert '<span class="inv-res-text" aria-hidden="true">{{ res }}</span></label>' in html
    assert ".inv-res-text { display: none; }" in CSS and ".stack-table .inv-res-text { display: inline; }" in CSS


def test_assets_date_column_hidden_mid_width():
    m = re.search(r"@media \(min-width: 1024px\) and \(max-width: 1279\.98px\) \{([^}]*\}[^}]*\})", CSS)
    assert m and ".asset-list th.date, .asset-list td.date { display: none; }" in m.group(1)
