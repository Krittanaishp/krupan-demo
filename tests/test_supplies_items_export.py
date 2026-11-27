"""ส่งออกรายการวัสดุในรูปแบบแบบฟอร์มนำเข้า แล้วนำเข้ากลับใน db ใหม่ได้ยอดตรงกัน (db ชั่วคราวเท่านั้น)"""
import json
import re
import sqlite3
from io import BytesIO
from urllib.parse import unquote

from openpyxl import load_workbook

from app import supplies as sp
from app import supplies_import as si
from tests.conftest import login, make_db
from tests.test_supplies import TODAY
from tests.test_supplies_export import data, setup  # noqa: F401  (ข้อมูลรับ/จ่าย/ยกเลิก ชุดเดียวกับ test ส่งออก)


def download(client, url="/supplies/items.xlsx"):
    r = client.get(url)
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"], r.text[:200]
    name = unquote(re.search(r"filename\*=UTF-8''(.+)$", r.headers["content-disposition"]).group(1))
    return r.content, name


def items_of(db):
    rows = {}
    for r in sp.balances(db, include_inactive=True):
        rows[(r["category"], r["name"])] = (r["base_unit"], r["reorder_point"], r["note"] or None, r["balance"],
                                            sorted((u["name"], u["factor"]) for u in r["units"]))
    return rows


def test_export_uses_import_template_layout(editor, data):  # noqa: F811
    content, name = download(editor)
    wb = load_workbook(BytesIO(content))
    assert wb.sheetnames[0] == si.SHEET and "ข้อมูลการส่งออก" in wb.sheetnames and "คำอธิบาย" in wb.sheetnames
    ws = wb[si.SHEET]
    assert [c.value for c in ws[1]][:len(si.COLUMNS)] == si.COLUMNS  # หัวตารางเหมือนแบบฟอร์มทุกตัวอักษร
    rows = {r[1]: r for r in ws.iter_rows(min_row=2, values_only=True)}
    assert rows["A4 80 แกรม"][:6] == ("กระดาษ", "A4 80 แกรม", "รีม", 5, "กล่อง", 5)
    assert rows["A4 80 แกรม"][8] == 12 and rows["ปากกาลูกลื่น สีน้ำเงิน"][8] == 40  # ยอดยกมา = ยอดคงเหลือวันนี้
    assert rows["เทปใส"][3] is None and rows["เทปใส"][8] == 0
    info = [c.value for c in wb["ข้อมูลการส่งออก"]["A"] if c.value]
    assert info[1] == "ที่ทำการปกครองอำเภอทดสอบ" and "ผู้ใช้ editor1" in info[3]
    assert any("ยอดยกมา = ยอดคงเหลือ ณ วันที่" in t for t in info)
    assert name.startswith("รายการวัสดุ_ณ") and re.search(r"25\d\d\.xlsx$", name)


def test_export_then_import_into_new_db_gives_same_items_and_balances(editor, data, tmp_path):  # noqa: F811
    content, name = download(editor)
    src = items_of(data)
    path = tmp_path / "other.db"
    make_db(path)
    other = sqlite3.connect(path)
    other.row_factory = sqlite3.Row
    other.execute("PRAGMA foreign_keys = ON")
    uid = other.execute("SELECT id FROM users WHERE username = 'editor1'").fetchone()[0]
    parsed = si.read_file(content, name)
    assert not [p for p in parsed if "errors" in p]  # ทุกแถวอ่านผ่าน
    si.apply(other, uid, content, name, TODAY, set())
    assert items_of(other) == src
    other.close()


def test_export_follows_filters_and_page_button(editor, data):  # noqa: F811
    content, _ = download(editor, "/supplies/items.xlsx?category=2")
    names = [r[1] for r in load_workbook(BytesIO(content))[si.SHEET].iter_rows(min_row=2, values_only=True)]
    assert sorted(names) == ["ปากกาลูกลื่น สีน้ำเงิน", "เทปใส"]
    data.execute("UPDATE supply_items SET active = 0 WHERE name = 'เทปใส'")
    data.commit()
    content, _ = download(editor, "/supplies/items.xlsx?status=inactive")
    wb = load_workbook(BytesIO(content))
    assert [r[1] for r in wb[si.SHEET].iter_rows(min_row=2, values_only=True)] == ["เทปใส"]
    assert any("ปิดใช้งานแล้ว 1 รายการ" in (c.value or "") for c in wb["ข้อมูลการส่งออก"]["A"])
    html = editor.get("/supplies/items?q=ปากกา").text
    assert 'href="/supplies/items.xlsx?q=%E0%B8%9B' in html and ">ดาวน์โหลด Excel</a>" in html
    assert "ไม่มีรายการวัสดุตามตัวกรองนี้" in editor.get("/supplies/items?q=zzzz").text


def test_more_than_two_big_units_are_reported(editor, data):  # noqa: F811
    data.execute("INSERT INTO supply_units (item_id, name, factor) VALUES (1, 'ลัง', 50), (1, 'พาเลท', 500)")
    data.commit()
    wb = load_workbook(BytesIO(download(editor)[0]))
    row = next(r for r in wb[si.SHEET].iter_rows(min_row=2, values_only=True) if r[1] == "A4 80 แกรม")
    assert row[4:8] == ("พาเลท", 500, "ลัง", 50)  # 2 หน่วยที่อัตรามากที่สุด
    info = " ".join(c.value or "" for c in wb["ข้อมูลการส่งออก"]["A"])
    assert "หน่วยใหญ่เกิน 2 หน่วย" in info and "กล่อง = 5 รีม" in info


def test_items_export_is_audited_and_viewer_can_export(client, data):  # noqa: F811
    login(client, "viewer1")
    download(client)
    row = data.execute("SELECT * FROM audit_log WHERE action = 'export' ORDER BY id DESC").fetchone()
    ch = json.loads(row["changes"])
    assert row["table_name"] == "exports" and ch["report"] == "รายการวัสดุ (แบบฟอร์มนำเข้า)" and ch["rows"] == 3
