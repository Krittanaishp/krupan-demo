"""นำเข้ารายการวัสดุและยอดยกมาจาก Excel (แบบฟอร์มมาตรฐาน) · ใช้ไฟล์ตัวอย่างข้อมูลสมมติจาก scripts/make_supply_sample.py"""
import json
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from app import supplies_import as si
from app import timeutil
from scripts.make_supply_sample import GOOD, PROBLEMS, build
from tests.conftest import audit_rows, login

TODAY = timeutil.today_th().isoformat()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload(client, data, name="วัสดุ.xlsx", opening_date=TODAY):
    return client.post("/supplies/import/preview", files={"file": (name, data, XLSX)}, data={"opening_date": opening_date})


def hidden(html, name):
    return html.split(f'name="{name}" value="')[1].split('"')[0]


def confirm(client, html, picks=()):
    data = {k: hidden(html, k) for k in ("payload", "sha", "filename", "opening_date")}
    for row, field in picks:
        data[f"pick_{row}_{field}"] = "file"
    return client.post("/supplies/import/confirm", data=data, follow_redirects=False)


def bal(db, name):
    return db.execute("""SELECT COALESCE(SUM(m.qty), 0) FROM supply_items i LEFT JOIN supply_moves m ON m.item_id = i.id
                         WHERE i.name = ?""", (name,)).fetchone()[0]


def test_template_has_exact_header_and_no_price(editor):
    r = editor.get("/supplies/import/template.xlsx")
    assert r.status_code == 200
    wb = load_workbook(BytesIO(r.content))
    assert [c.value for c in wb[si.SHEET][1]] == si.COLUMNS
    assert wb[si.SHEET].max_row == 1  # ไม่มีแถวตัวอย่างในชีตที่นำเข้า
    assert not any("ราคา" in c or "มูลค่า" in c for c in si.COLUMNS)


def test_viewer_cannot_import(client):
    login(client, "viewer1")
    assert client.get("/supplies/import").status_code == 403
    assert upload(client, build(GOOD)).status_code == 403


def test_import_good_file_after_preview(editor, db):
    page = upload(editor, build(GOOD))
    assert page.status_code == 200 and "ยังไม่ได้บันทึกอะไร" in page.text
    assert db.execute("SELECT COUNT(*) FROM supply_items").fetchone()[0] == 0  # แสดงตัวอย่างอย่างเดียว
    assert "1. รายการใหม่ (24)" in page.text
    r = confirm(editor, page.text)
    assert r.status_code == 303
    assert db.execute("SELECT COUNT(*) FROM supply_items").fetchone()[0] == 24
    assert db.execute("SELECT COUNT(*) FROM supply_categories").fetchone()[0] == 5
    assert bal(db, "A4 80 แกรม") == 41 and bal(db, "Canon 337") == 0
    units = db.execute("""SELECT u.name, u.factor FROM supply_units u JOIN supply_items i ON i.id = u.item_id
                          WHERE i.name = 'ถ่าน AA' ORDER BY u.factor""").fetchall()
    assert [tuple(u) for u in units] == [("แพ็ก", 4), ("กล่อง", 48)]
    b = db.execute("SELECT * FROM supply_import_batches").fetchone()
    assert b["opening_date"] == TODAY and json.loads(b["summary"])["new_items"] == 24
    assert db.execute("SELECT COUNT(*) FROM supply_moves WHERE kind = 'opening' AND import_batch_id = ?", (b["id"],)).fetchone()[0] == 23
    assert audit_rows(db, "supply_import_batches", b["id"])
    backups = list((db_path_dir(db) / "backups").glob("*before_supply_import*"))
    assert backups, "ต้องสำรอง db ก่อนนำเข้า"
    hist = editor.get("/supplies/import").text
    assert "ใหม่ 24" in hist and "ยกเลิกทั้งชุด" in hist


def db_path_dir(db):
    from pathlib import Path
    return Path(db.execute("PRAGMA database_list").fetchone()[2]).parent


def test_problem_file_groups(editor, db):
    confirm(editor, upload(editor, build(GOOD)).text)
    page = upload(editor, build(PROBLEMS)).text
    assert "1. รายการใหม่ (1)" in page and "ไม้บรรทัด 30 ซม." in page
    assert "2. มีอยู่แล้วและตรงกัน (1)" in page
    assert "3. มีอยู่แล้วแต่ข้อมูลต่าง (1)" in page
    assert "4. ชื่อซ้ำกันเองในไฟล์ (2)" in page and "ซ้ำกับแถว 6" in page
    assert "5. แถวที่อ่านไม่ได้ (4)" in page
    for msg in ("แถว 7", "ช่อง “หมวด” ว่าง", "ช่อง “ยอดยกมา” ต้องเป็นจำนวนเต็ม (ได้ 2.5)", "อัตรา 1", "ต้องอยู่ระหว่าง 0"):
        assert msg in page, msg
    # มีรายการรับ/จ่ายแล้ว ยอดยกมาใส่ไม่ได้
    assert "ใส่ยอดยกมาไม่ได้" in page


def test_diff_fields_never_overwritten_unless_picked(editor, db):
    confirm(editor, upload(editor, build(GOOD)).text)
    page = upload(editor, build(PROBLEMS)).text
    confirm(editor, page)  # ไม่เลือกอะไรในกลุ่มต่าง
    a4 = db.execute("SELECT * FROM supply_items WHERE name = 'A4 80 แกรม'").fetchone()
    assert a4["reorder_point"] == 10
    assert db.execute("SELECT COUNT(*) FROM supply_units WHERE item_id = ?", (a4["id"],)).fetchone()[0] == 1
    assert bal(db, "ไม้บรรทัด 30 ซม.") == 6  # ของใหม่ยังเข้า
    assert db.execute("SELECT COUNT(*) FROM supply_items WHERE name = 'Brother TN-2380'").fetchone()[0] == 0
    # รอบนี้เลือกใช้ค่าจากไฟล์: จุดสั่งซื้อ + หน่วยใหม่
    page = upload(editor, build(PROBLEMS)).text
    confirm(editor, page, picks=[(3, "reorder_point"), (3, "units"), (3, "opening")])
    a4 = db.execute("SELECT * FROM supply_items WHERE name = 'A4 80 แกรม'").fetchone()
    assert a4["reorder_point"] == 15
    assert db.execute("SELECT name FROM supply_units WHERE item_id = ? AND name = 'ลัง'", (a4["id"],)).fetchone()
    assert bal(db, "A4 80 แกรม") == 41  # มีรายการแล้ว ยอดยกมาไม่ถูกใส่แม้ส่งค่ามา


def test_undo_whole_batch(editor, db):
    confirm(editor, upload(editor, build(GOOD)).text)
    bid = db.execute("SELECT id FROM supply_import_batches").fetchone()[0]
    editor.post(f"/supplies/import/{bid}/undo", data={"reason": ""})
    assert db.execute("SELECT undone_at FROM supply_import_batches").fetchone()[0] is None
    editor.post(f"/supplies/import/{bid}/undo", data={"reason": "นำเข้าไฟล์ผิด"})
    assert db.execute("SELECT undone_at FROM supply_import_batches").fetchone()[0]
    assert db.execute("SELECT COUNT(*) FROM supply_items WHERE active = 1").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM supply_items").fetchone()[0] == 24  # ไม่ลบแถว
    assert db.execute("SELECT COALESCE(SUM(qty), 0) FROM supply_moves").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM supply_categories WHERE active = 1").fetchone()[0] == 0
    # นำเข้าใหม่อีกครั้ง: รายการที่ถูกปิดไว้อยู่กลุ่ม "ต่าง" ให้เลือกเปิดใช้งาน + ใส่ยอดยกมาได้ (ยังไม่มีรับ/จ่ายจริง)
    page = upload(editor, build(GOOD[:1])).text
    assert "เปิดใช้งานอีกครั้ง" in page
    confirm(editor, page, picks=[(2, "active"), (2, "opening"), (2, "units")])
    pen = db.execute("SELECT id FROM supply_items WHERE name = 'ปากกาลูกลื่น สีน้ำเงิน'").fetchone()[0]
    assert db.execute("SELECT active FROM supply_units WHERE item_id = ?", (pen,)).fetchall()[0][0] == 1  # หน่วยกล่องกลับมา
    assert bal(db, "ปากกาลูกลื่น สีน้ำเงิน") == 135
    assert db.execute("SELECT active FROM supply_items WHERE name = 'ปากกาลูกลื่น สีน้ำเงิน'").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM supply_items WHERE active = 1").fetchone()[0] == 1


def test_undo_blocked_after_issue(editor, db):
    db.execute("INSERT INTO departments (id, name, issues_supplies) VALUES (1, 'ปกครอง', 1)")
    db.execute("INSERT INTO staff (id, full_name) VALUES (1, 'นายอนุมัติ')")
    db.commit()
    confirm(editor, upload(editor, build(GOOD)).text)
    editor.post("/supplies/issue", data={"issue_date": TODAY, "department_id": "1", "requester_name": "ก", "approver_staff_id": "1",
                                         "approved_date": TODAY, "item_1": "ยางลบ · เครื่องเขียน", "req_1": "2"})
    bid = db.execute("SELECT id FROM supply_import_batches").fetchone()[0]
    r = editor.post(f"/supplies/import/{bid}/undo", data={"reason": "ลอง"}, follow_redirects=True)
    assert "ยกเลิกทั้งชุดไม่ได้" in r.text and "ยางลบ" in r.text
    assert db.execute("SELECT undone_at FROM supply_import_batches").fetchone()[0] is None


@pytest.mark.parametrize("make,msg", [
    (lambda: b"not an excel file", "ไม่ใช่ไฟล์ .xlsx"),
    (lambda: b"PK\x03\x04broken", "เปิดไฟล์ไม่ได้"),
])
def test_rejects_bad_files(editor, make, msg):
    r = upload(editor, make())
    assert r.status_code == 400 and msg in r.text


def test_rejects_other_extension_wrong_header_and_size(editor):
    assert "รับเฉพาะไฟล์ .xlsx" in upload(editor, build(GOOD), name="วัสดุ.xls").text
    wb = Workbook()
    ws = wb.active
    ws.title = si.SHEET
    ws.append(["หมวด", "ชื่อ", "หน่วย"])
    out = BytesIO()
    wb.save(out)
    r = upload(editor, out.getvalue())
    assert r.status_code == 400 and "หัวตารางไม่ตรงกับแบบฟอร์ม" in r.text and "คอลัมน์ B ต้องเป็น “ชื่อรายการ”" in r.text
    wb2 = Workbook()
    wb2.active.title = "Sheet1"
    out = BytesIO()
    wb2.save(out)
    assert "ไม่พบชีต" in upload(editor, out.getvalue()).text
    big = b"PK" + b"0" * (si.MAX_BYTES + 10)
    assert "ไฟล์ใหญ่เกิน" in upload(editor, big).text
    empty = build([])
    assert "ไม่มีข้อมูล" in upload(editor, empty).text


def test_tampered_payload_rejected(editor, db):
    page = upload(editor, build(GOOD)).text
    data = {k: hidden(page, k) for k in ("payload", "filename", "opening_date")}
    data["sha"] = "0" * 64
    r = editor.post("/supplies/import/confirm", data=data)
    assert r.status_code == 400 and "อัปโหลดไฟล์ใหม่" in r.text
    assert db.execute("SELECT COUNT(*) FROM supply_items").fetchone()[0] == 0


def test_opening_date_cannot_be_future(editor):
    from datetime import timedelta
    r = upload(editor, build(GOOD), opening_date=(timeutil.today_th() + timedelta(days=1)).isoformat())
    assert r.status_code == 400 and "อยู่ในอนาคต" in r.text


# ---------------------------------------------------------------- แก้ยอดยกมา / วันที่ในหน้าตัวอย่าง (วันติดตั้งกรอกยอดที่นับใหม่)

def open_field(html, name):
    import re
    m = re.search(r'name="(open_\d+)"[^>]*\n?[^>]*aria-label="ยอดยกมา ' + re.escape(name) + r' ', html)
    assert m, name
    return m.group(1)


def test_preview_lets_user_edit_each_opening_and_date(editor, db):
    page = upload(editor, build(GOOD)).text
    assert 'type="date" name="opening_date"' in page and "ยอดยกมา (แก้ได้)" in page
    a4, canon = open_field(page, "A4 80 แกรม"), open_field(page, "Canon 337")
    yesterday = (timeutil.today_th() - __import__("datetime").timedelta(days=1)).isoformat()
    data = {k: hidden(page, k) for k in ("payload", "sha", "filename")}
    data.update({"opening_date": yesterday, a4: "7", canon: "๓"})
    r = editor.post("/supplies/import/confirm", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert bal(db, "A4 80 แกรม") == 7 and bal(db, "Canon 337") == 3  # ในไฟล์ 41 และ 0
    dates = {r[0] for r in db.execute("SELECT DISTINCT move_date FROM supply_moves WHERE kind = 'opening'")}
    assert dates == {yesterday}
    assert db.execute("SELECT opening_date FROM supply_import_batches").fetchone()[0] == yesterday
    item = db.execute("SELECT id FROM supply_items WHERE name = 'A4 80 แกรม'").fetchone()[0]
    ch = json.loads(audit_rows(db, "supply_items", item)[0]["changes"])
    assert ch["opening"] == 7 and ch["opening_in_file"] == 41
    # รายการที่ไม่ได้แก้ใช้ค่าในไฟล์ และไม่มี opening_in_file
    pen = db.execute("SELECT id FROM supply_items WHERE name = ?", (GOOD[1][1],)).fetchone()[0]
    assert "opening_in_file" not in json.loads(audit_rows(db, "supply_items", pen)[0]["changes"])


def test_opening_zero_creates_no_move_and_blank_means_zero(editor, db):
    page = upload(editor, build(GOOD)).text
    data = {k: hidden(page, k) for k in ("payload", "sha", "filename", "opening_date")}
    data[open_field(page, "A4 80 แกรม")] = ""
    assert editor.post("/supplies/import/confirm", data=data, follow_redirects=False).status_code == 303
    assert bal(db, "A4 80 แกรม") == 0
    assert db.execute("""SELECT COUNT(*) FROM supply_moves m JOIN supply_items i ON i.id = m.item_id
                         WHERE i.name = 'A4 80 แกรม'""").fetchone()[0] == 0


@pytest.mark.parametrize("bad", ["-1", "2.5", "สิบ"])
def test_invalid_opening_keeps_values_and_saves_nothing(editor, db, bad):
    page = upload(editor, build(GOOD)).text
    a4, canon = open_field(page, "A4 80 แกรม"), open_field(page, "Canon 337")
    data = {k: hidden(page, k) for k in ("payload", "sha", "filename", "opening_date")}
    data.update({a4: bad, canon: "5"})
    r = editor.post("/supplies/import/confirm", data=data, follow_redirects=False)
    assert r.status_code == 400 and "ยังไม่ได้บันทึกอะไร" in r.text and "ยอดยกมาต้องเป็นจำนวนเต็ม" in r.text
    assert db.execute("SELECT COUNT(*) FROM supply_items").fetchone()[0] == 0
    assert f'name="{canon}"' in r.text and 'value="5"' in r.text.split(f'name="{canon}"')[1][:200]  # ค่าที่แก้ไว้ยังอยู่
    assert 'aria-invalid="true"' in r.text.split(f'name="{a4}"')[1][:400]


def test_future_opening_date_in_preview_is_refused(editor, db):
    page = upload(editor, build(GOOD)).text
    data = {k: hidden(page, k) for k in ("payload", "sha", "filename")}
    data["opening_date"] = (timeutil.today_th() + __import__("datetime").timedelta(days=1)).isoformat()
    r = editor.post("/supplies/import/confirm", data=data, follow_redirects=False)
    assert r.status_code == 400 and "อยู่ในอนาคต" in r.text
    assert db.execute("SELECT COUNT(*) FROM supply_items").fetchone()[0] == 0


def test_opening_override_for_existing_item_without_moves(editor, db):
    """รายการมีอยู่แล้วแต่ยังไม่มีรับ/จ่าย: เลือก "ใส่ยอดยกมา" แล้วแก้จำนวนได้"""
    db.execute("INSERT INTO supply_categories (id, name, sort_order) VALUES (1, ?, 1)", (GOOD[0][0],))
    db.execute("INSERT INTO supply_items (category_id, name, base_unit, reorder_point) VALUES (1, ?, ?, ?)",
               (GOOD[0][1], GOOD[0][2], GOOD[0][3]))
    db.commit()
    page = upload(editor, build(GOOD)).text
    field = open_field(page, GOOD[0][1])
    row = field.split("_")[1]
    data = {k: hidden(page, k) for k in ("payload", "sha", "filename", "opening_date")}
    data.update({field: "12", f"pick_{row}_opening": "file"})
    assert editor.post("/supplies/import/confirm", data=data, follow_redirects=False).status_code == 303
    assert bal(db, GOOD[0][1]) == 12
