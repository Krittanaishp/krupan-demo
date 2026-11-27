"""หน้าพิมพ์สติกเกอร์ /labels, preset, การบันทึกว่าพิมพ์แล้ว และการคำนวณตำแหน่ง"""
import json
import re

import pytest

from app import labels
from tests.conftest import add_location, audit_rows, login

DEFAULT = labels.Layout(3, 8, 70, 37, 0.5, 0, 0, 0)


def count_labels(html):
    return len(re.findall(r'<div class="label" id="lb-', html))


def sheets(html):
    return html.count('<section class="sheet')


# ---------------------------------------------------------------- คำนวณตำแหน่ง

def test_cell_positions_default_preset():
    assert DEFAULT.per_page == 24
    assert DEFAULT.cell(0) == (0, 0.5)
    assert DEFAULT.cell(2) == (140, 0.5)
    assert DEFAULT.cell(3) == (0, 37.5)
    assert DEFAULT.cell(23) == (140, 0.5 + 7 * 37)


def test_cell_positions_with_gaps():
    lay = labels.Layout(2, 2, 90, 50, 10, 12, 6, 4)
    assert lay.cell(3) == (12 + 90 + 6, 10 + 50 + 4)


def test_paginate_with_start():
    pages = labels.paginate(range(30), DEFAULT, start=1)
    assert [len(p) for p in pages] == [24, 6]
    pages = labels.paginate(range(3), DEFAULT, start=23)  # ดวง 23, 24 แล้วขึ้นแผ่นใหม่
    assert [len(p) for p in pages] == [2, 1]
    assert pages[0][0][:2] == DEFAULT.cell(22) and pages[1][0][:2] == DEFAULT.cell(0)


@pytest.mark.parametrize("values,field", [
    ({"n_cols": "4"}, "label_w"),            # 4 x 70 = 280 มม. เกิน A4
    ({"n_rows": "9"}, "label_h"),            # 0.5 + 9 x 37 = 333.5 มม.
    ({"label_w": "abc"}, "label_w"),
    ({"gap_x": "-1"}, "gap_x"),
    ({"n_cols": "0"}, "n_cols"),
])
def test_layout_validation(values, field):
    _, errors = labels.parse(values, DEFAULT)
    assert field in errors


def test_reg_number_big_and_fits():
    d = labels.design(DEFAULT)
    reg = "ปค.99-9999-1-1201-0006"
    assert labels.reg_lines(reg) == ["ปค.99-9999-1-", "1201-0006"]
    pt = labels.reg_pt(reg, d)
    assert 11 <= pt <= d["reg_pt"]  # แบ่ง 2 บรรทัดแล้วตัวใหญ่ได้
    for line in labels.reg_lines(reg):
        assert len(line) * 0.56 * pt / labels.PT_PER_MM <= d["text_w"]
    assert labels.reg_lines("TMP-0001") == ["TMP-0001"]
    assert labels.reg_pt("TMP-0001", d) == d["reg_pt"]  # รหัสสั้นใช้ขนาดเต็ม


# ---------------------------------------------------------------- สิทธิ์

def test_labels_require_editor(client):
    for url in ("/labels", "/labels/sheet?mode=room&room=none"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
    login(client, "viewer1")
    assert client.get("/labels").status_code == 403
    assert client.get("/labels/sheet?mode=room&room=none").status_code == 403


# ---------------------------------------------------------------- จำนวนดวงตามที่เลือก

def test_sheet_by_room(editor, db):
    room = add_location(db, "ห้อง ปกครองอำเภอ")
    db.execute("UPDATE assets SET location_id = ? WHERE id IN (1, 6)", (room,))
    db.commit()
    html = editor.get(f"/labels/sheet?mode=room&room={room}").text
    assert count_labels(html) == 2 and sheets(html) == 1
    assert "ปค.99-9999-1-1306-0004" in html and "ปค.99-9999-1-1306-0005" in html
    assert "ห้อง ปกครองอำเภอ" in html and "ที่ว่าการอำเภอทดสอบ" in html
    assert html.count('<svg class="qr"') == 2
    assert "@page { size: A4 portrait; margin: 0; }" in html
    assert re.search(r"width: 70mm; height: 37mm", html)


def test_sheet_unassigned_room(editor):
    html = editor.get("/labels/sheet?mode=room&room=none").text
    assert count_labels(html) == 6


def test_sheet_by_filter_same_as_assets_page(editor):
    html = editor.get("/labels/sheet?mode=filter&q=เก้าอี้").text
    assert count_labels(html) == 3
    assert editor.get("/labels/sheet?mode=filter&status=ชำรุด").status_code == 400  # ไม่มีชิ้นที่ตรง
    assert count_labels(editor.get("/labels/sheet?mode=filter&review=1").text) == 2


def test_sheet_by_pick(editor):
    html = editor.get("/labels/sheet?mode=pick&ids=2&ids=5&ids=999").text
    assert count_labels(html) == 2 and "TMP-0001" in html
    r = editor.get("/labels/sheet?mode=pick")
    assert r.status_code == 400 and "ยังไม่ได้ติ๊กเลือก" in r.text


def test_sheet_start_position_and_pages(editor):
    html = editor.get("/labels/sheet?mode=room&room=none&start=22").text
    assert count_labels(html) == 6 and sheets(html) == 2  # ดวง 22-24 ของแผ่นแรก แล้วต่อแผ่นที่สอง
    # ตำแหน่งดวงอยู่ใน <style nonce> (CSP ไม่อนุญาต style="") ดวงแรกของแผ่นแรก = #lb-1-1
    first = re.search(r'#lb-1-1 \{ left: (\S+)mm; top: (\S+)mm; \}', html)
    assert (float(first.group(1)), float(first.group(2))) == DEFAULT.cell(21)
    r = editor.get("/labels/sheet?mode=room&room=none&start=25")
    assert r.status_code == 400 and "1-24" in r.text


def test_sheet_custom_layout_from_form(editor):
    html = editor.get("/labels/sheet?mode=room&room=none&n_cols=2&n_rows=2&label_w=99&label_h=60").text
    assert count_labels(html) == 6 and sheets(html) == 2
    assert "width: 99mm; height: 60mm" in html
    r = editor.get("/labels/sheet?mode=room&room=none&n_cols=4")
    assert r.status_code == 400 and "เกินกระดาษ A4" in r.text


def test_border_test_prints_every_cell_without_content(editor):
    html = editor.get("/labels/sheet?mode=room&room=none&border=1").text
    assert count_labels(html) == 24 and sheets(html) == 1
    assert "border-test" in html and '<svg class="qr"' not in html
    assert "/labels/mark" not in html


def test_qr_contains_base_url(editor):
    html = editor.get("/labels?mode=pick&ids=2").text
    assert "http://192.0.2.10:8000/q/TMP-0001" in html


def test_setup_page_and_summary(editor):
    html = editor.get("/labels").text
    assert "A4 3x8 (70x37 มม.)" in html and "ตามห้อง" in html and "เลือกเอง" in html
    assert html.count('name="ids"') == 6
    r = editor.get("/labels/summary?mode=room&room=none&start=20")
    assert "6 ดวง · 2 แผ่น" in r.text


def test_assets_page_links_to_labels_with_filter(editor):
    html = editor.get("/assets?q=เก้าอี้").text
    assert "/labels?mode=filter&amp;q=" in html and "(3 ดวง)" in html


# ---------------------------------------------------------------- preset

def save(editor, **form):
    data = {"preset": "1", **{f: "" for f in labels.FIELDS}, **form}
    return editor.post("/labels/presets", data=data, headers={"HX-Request": "true"})


def test_save_preset_and_reuse(editor, db):
    r = save(editor, preset_name="Avery 2x7", n_cols="2", n_rows="7", label_w="99.1", label_h="38.1",
             margin_top="15.1", margin_left="4.7", gap_x="2.5", gap_y="0", mode="room", room="none")
    assert r.status_code == 204 and r.headers["HX-Redirect"].startswith("/labels?mode=room&room=none")
    row = db.execute("SELECT * FROM label_presets WHERE name = 'Avery 2x7'").fetchone()
    assert (row["n_cols"], row["label_w"], row["margin_left"], row["builtin"]) == (2, 99.1, 4.7, 0)
    assert audit_rows(db, "label_presets", row["id"])[0]["action"] == "insert"
    html = editor.get(f"/labels/sheet?mode=room&room=none&preset={row['id']}").text
    assert "width: 99.1mm; height: 38.1mm" in html
    # ชื่อเดิม = แก้ทับ
    save(editor, preset_name="Avery 2x7", n_cols="2", n_rows="7", label_w="99.1", label_h="38.1", margin_top="14")
    assert db.execute("SELECT margin_top FROM label_presets WHERE id = ?", (row["id"],)).fetchone()[0] == 14
    assert db.execute("SELECT COUNT(*) FROM label_presets").fetchone()[0] == 2


def test_preset_validation_and_builtin_protected(editor, db):
    assert "ตั้งชื่อ preset" in save(editor, preset_name="").text
    assert "เกินกระดาษ A4" in save(editor, preset_name="กว้างเกิน", n_cols="5").text
    assert "แก้ไม่ได้" in save(editor, preset_name="A4 3x8 (70x37 มม.)", margin_top="0").text
    assert db.execute("SELECT margin_top FROM label_presets WHERE builtin = 1").fetchone()[0] == 0.5
    editor.post("/labels/presets/1/delete")
    assert db.execute("SELECT COUNT(*) FROM label_presets").fetchone()[0] == 1


def test_delete_saved_preset(editor, db):
    save(editor, preset_name="ชั่วคราว", n_cols="2", n_rows="2", label_w="80", label_h="40")
    pid = db.execute("SELECT id FROM label_presets WHERE name = 'ชั่วคราว'").fetchone()[0]
    editor.post(f"/labels/presets/{pid}/delete")
    assert db.execute("SELECT COUNT(*) FROM label_presets").fetchone()[0] == 1


# ---------------------------------------------------------------- บันทึกว่าพิมพ์แล้ว

def test_mark_printed_and_unprinted_filter(editor, db, client):
    html = editor.get("/assets?unprinted=1").text
    assert "พบ <strong>6</strong> รายการ" in html
    r = editor.post("/labels/mark", data={"ids": ["1", "2"], "back": "/labels?mode=pick&ids=1&ids=2"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/labels?mode=pick&ids=1&ids=2"
    printed = db.execute("SELECT id FROM assets WHERE label_printed_at IS NOT NULL ORDER BY id").fetchall()
    assert [p[0] for p in printed] == [1, 2]
    log = audit_rows(db, record_id=1)
    assert log[-1]["action"] == "label" and json.loads(log[-1]["changes"])["label_printed_at"]["old"] is None
    assert "พบ <strong>4</strong> รายการ" in editor.get("/assets?unprinted=1").text
    assert "พิมพ์สติกเกอร์ QR" in editor.get("/assets/1").text  # ประวัติ
    assert count_labels(editor.get("/labels/sheet?mode=filter&unprinted=1").text) == 4


def test_mark_printed_requires_editor(client, db):
    login(client, "viewer1")
    assert client.post("/labels/mark", data={"ids": ["1"]}).status_code == 403
    assert db.execute("SELECT COUNT(*) FROM assets WHERE label_printed_at IS NOT NULL").fetchone()[0] == 0
