"""ส่วนที่ 6: ปิด/เปิดรอบตรวจสอบพัสดุกลับ, รายงานทะเบียน (รวมเลขต่อเนื่อง เลขไทย), ประวัติซ่อม, การจำหน่าย, migration 006"""
import json
import sqlite3
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app import reports, timeutil

from tests.conftest import audit_rows, login
from tests.test_inventory import open_round


def rows_of(**over):
    base = {"id": 1, "reg_no": "ปค.99-9999-1-1422-0001", "temp_code": None, "name": "เก้าอี้พลาสติก", "details": None,
            "acquired_date": "2004-06-10", "acquired_date_precision": "day", "unit_price": 125, "funding_source": "รับบริจาค",
            "location_name": None, "location_text": "ห้องประชุม", "note": None, "status": "ใช้งาน"}
    return {**base, **over}


def run_of(*regs, **over):
    return [rows_of(id=i, reg_no=r, **over) for i, r in enumerate(regs, start=1)]


# ---------------------------------------------------------------- รวมเลขทะเบียนต่อเนื่อง

def test_group_consecutive_reg_numbers():
    rows = run_of(*[f"ปค.99-9999-1-1422-{n:04d}" for n in range(1, 101)])
    runs = reports.group_runs(rows)
    assert [len(r) for r in runs] == [100]
    e = reports.entry(runs[0], 1, thai_digits=False)
    assert e["reg"] == "ปค.99-9999-1-1422-0001-0100" and e["reg_lines"] == ["ปค.99-9999-1-1422-", "0001-0100"]
    assert e["name"] == "เก้าอี้พลาสติก จำนวน 100 รายการ" and e["count"] == 100
    named = reports.entry(reports.group_runs(run_of("ปค.99-9999-1-1422-0001", "ปค.99-9999-1-1422-0002",
                                                    name="เก้าอี้ จำนวน 2 ตัว"))[0], 1, False)
    assert named["name"] == "เก้าอี้ จำนวน 2 ตัว"  # บอกจำนวนอยู่แล้ว ไม่เติมซ้ำ


def test_group_not_consecutive_or_different_type():
    runs = reports.group_runs(run_of("ปค.99-9999-1-1422-0001", "ปค.99-9999-1-1422-0002", "ปค.99-9999-1-1422-0004",
                                     "ปค.99-9999-1-1423-0005"))
    assert [len(r) for r in runs] == [2, 1, 1]


def test_group_requires_same_price_date_funding_name():
    regs = ("ปค.99-9999-1-1421-0011", "ปค.99-9999-1-1421-0012")
    for field, value in (("unit_price", 4000), ("acquired_date", "2004-06-11"), ("funding_source", "งบประมาณ"),
                         ("name", "เก้าอี้อื่น")):
        rows = run_of(*regs)
        rows[1][field] = value
        assert [len(r) for r in reports.group_runs(rows)] == [1, 1], field
    rows = run_of(*regs, unit_price=None)  # ไม่ทราบราคาทั้งคู่ = ราคาเหมือนกัน
    assert [len(r) for r in reports.group_runs(rows)] == [2]
    assert [len(r) for r in reports.group_runs(run_of(*regs), enabled=False)] == [1, 1]


def test_group_mixed_locations_and_unknown_price_blank():
    rows = run_of("ปค.99-9999-1-1421-0001", "ปค.99-9999-1-1421-0002", "ปค.99-9999-1-1421-0003", unit_price=None)
    rows[2]["location_text"] = "ห้องนายอำเภอ"
    e = reports.entry(reports.group_runs(rows)[0], 7, thai_digits=False)
    assert e["place"] == "ห้องประชุม (2), ห้องนายอำเภอ (1)" and e["price"] == ""


def test_thai_digits():
    e = reports.entry(run_of("ปค.99-9999-1-1422-0001", "ปค.99-9999-1-1422-0002"), 184, thai_digits=True)
    assert e["no"] == "๑๘๔" and e["date"] == "๑๐ มิ.ย. ๔๗" and e["price"] == "๑๒๕.๐๐"
    assert e["reg_lines"] == ["ปค.๙๙-๙๙๙๙-๑-๑๔๒๒-", "๐๐๐๑-๐๐๐๒"]


# ---------------------------------------------------------------- หน้ารายงาน

def test_reports_need_login(client):
    for url in ("/reports", "/reports/register", "/reports/export.xlsx", "/reports/summary"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), url
    login(client, "viewer1")
    assert client.get("/reports").status_code == 200
    assert client.post("/reports/signers", data={"sign_name_0": "x"}).status_code == 403


def test_register_report_by_category(editor, db):
    db.execute("UPDATE assets SET details = NULL")  # #1 กับ #6 ต่างกันแค่ details: ทำให้เหมือนกันจะได้รวมแถวได้
    db.commit()
    html = editor.get("/reports/register?fy=2569").text
    assert "รายละเอียดทรัพย์สิน ประจำปีงบประมาณ ๒๕๖๙" in html
    assert "ของที่ทำการปกครองอำเภอทดสอบ (งานการเงินและบัญชี)" in html
    assert html.count('<section class="register"') == 2  # ตู้ / โต๊ะ-เก้าอี้ (หมวดที่มีในข้อมูลทดสอบ)
    assert "ประเภทครุภัณฑ์ ตู้" in html and "ประเภท โต๊ะ-เก้าอี้" in html
    assert "<span>ปค.๙๙-๙๙๙๙-๑-๑๓๐๖-</span><span>๐๐๐๔-๐๐๐๕</span>" in html  # ตู้ 2 ชิ้นต่อเนื่อง รวมเป็นแถวเดียว
    assert "<span>ปค.๙๙-๙๙๙๙-๑-๑๔๐๑-</span><span>๐๐๑๑-๐๐๑๒</span>" in html
    assert "๕,๐๐๐.๐๐" in html
    arabic = editor.get("/reports/register?fy=2569&digits=arabic&group=0&cat=13").text
    assert arabic.count('<section class="register"') == 1 and "ปค.99-9999-1-1306-<wbr>0004" in arabic
    assert "ปค.99-9999-1-1306-<wbr>0005" in arabic and "1401" not in arabic


def test_register_excludes_disposed_and_future_items(editor, db):
    db.execute("UPDATE assets SET status = 'จำหน่ายแล้ว' WHERE id = 3")
    db.execute("UPDATE assets SET acquired_date = '2027-01-05', fiscal_year = 2570 WHERE id = 4")
    db.commit()
    html = editor.get("/reports/register?fy=2569&digits=arabic&group=0").text
    assert "1401-<wbr>0010" not in html and "1401-<wbr>0011" not in html and "1401-<wbr>0012" in html
    assert "1401-<wbr>0010" in editor.get("/reports/register?fy=2569&digits=arabic&group=0&disposed=1").text
    acquired = editor.get("/reports/acquired?fy=2570&digits=arabic").text
    assert "รายการครุภัณฑ์ได้มาในปีงบประมาณ 2570" in acquired and "(เพิ่มเติม)" in acquired
    assert "1401-<wbr>0011" in acquired and "1306-<wbr>0004" not in acquired


def test_register_excel_looks_like_old_sheet(editor, db):
    r = editor.get("/reports/register.xlsx?fy=2569")
    wb = load_workbook(BytesIO(r.content))
    assert wb.sheetnames == ["13. ตู้", "14. โต๊ะ-เก้าอี้"]
    ws = wb["14. โต๊ะ-เก้าอี้"]
    assert ws["A1"].value == "รายละเอียดทรัพย์สิน ประจำปีงบประมาณ ๒๕๖๙" and ws["A1"].font.name == "TH SarabunIT๙"
    assert [c.value for c in ws[3]] == ["ลำดับ", "วัน,เดือน,ปี", "ประเภทครุภัณฑ์ โต๊ะ-เก้าอี้", "หมายเลขทะเบียน",
                                        "ราคาต่อหน่วย", "วิธีการได้มา", "ใช้ประจำที่", "หมายเหตุ"]
    assert ws["C4"].value == "ประเภท โต๊ะ-เก้าอี้" and ws["C4"].font.b and ws.print_title_rows == "$1:$4"
    values = [ws.cell(r, 4).value for r in range(5, ws.max_row + 1)]
    assert "ปค.๙๙-๙๙๙๙-๑-๑๔๐๑-\n๐๐๑๑-๐๐๑๒" in values
    prices = [ws.cell(r, 5).value for r in range(5, 9)]
    assert None in prices  # TMP-0001 ไม่ทราบราคา = เว้นว่าง ไม่ใช่ 0


def test_signers_are_remembered(editor, db):
    r = editor.post("/reports/signers", data={"sign_role_0": "ประธานกรรมการ", "sign_name_0": "นายทดสอบ หนึ่ง",
                                              "sign_role_1": "กรรมการ", "sign_name_1": "นางทดสอบ สอง",
                                              "sign_role_2": "หัวหน้าเจ้าหน้าที่", "sign_name_2": ""},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200 and "บันทึกช่องลงชื่อแล้ว" in r.text
    assert 'value="นายทดสอบ หนึ่ง"' in editor.get("/reports").text
    html = editor.get("/reports/register?fy=2569").text
    assert "(นายทดสอบ หนึ่ง)" in html and "ลงชื่อ ................... หัวหน้าเจ้าหน้าที่" in html


def test_summary_counts_unknown_value_separately(editor, db):
    html = editor.get("/reports/summary").text
    assert "ทั้งหมด 6 ชิ้น" in html and "มูลค่า 13,000.00 บาท" in html and "ไม่ทราบมูลค่า 1 รายการ" in html
    assert "แยกตามหมวด" in html and "แยกตามห้อง" in html and "ยังไม่จัดเข้าห้อง" in html


def test_export_all_has_every_column(editor, db):
    db.execute("INSERT INTO locations (id, name) VALUES (1, 'ห้องปกครอง')")
    db.execute("INSERT INTO staff (id, nickname) VALUES (1, 'เอ็ม')")
    db.execute("UPDATE assets SET location_id = 1, holder_id = 1 WHERE id = 1")
    db.commit()
    wb = load_workbook(BytesIO(editor.get("/reports/export.xlsx").content))
    assert wb.sheetnames[:4] == ["ครุภัณฑ์", "ห้อง", "ผู้รับผิดชอบ", "ประวัติการย้าย"]
    ws = wb["ครุภัณฑ์"]
    head = [c.value for c in ws[1]]
    n_cols = len(db.execute("PRAGMA table_info(assets)").fetchall())
    assert len(head) == n_cols + 6 and "ห้อง" in head and "ซ้ำกับรายการ" in head and "ผู้รับผิดชอบ" in head and "ต้องตรวจสอบ" in head
    assert ws.max_row == 7
    first = dict(zip(head, [c.value for c in ws[2]]))
    assert first["เลขทะเบียน"] == "ปค.99-9999-1-1306-0004" and first["ห้อง"] == "ห้องปกครอง" and first["ผู้รับผิดชอบ"] == "เอ็ม"


# ---------------------------------------------------------------- ปิดรอบ: พิมพ์ปียืนยัน + สำรอง db

def test_close_requires_typing_fiscal_year_and_backs_up(editor, db, tmp_path):
    cid = open_round(editor)
    for year in ("", "2570", "abc"):
        editor.post(f"/inventory/{cid}/close", data={"expected": "6", "confirm_year": year})
        assert db.execute("SELECT closed_at FROM inventory_checks").fetchone()[0] is None
    assert not (tmp_path / "backups").exists()
    r = editor.post(f"/inventory/{cid}/close", data={"expected": "6", "confirm_year": "๒๕๖๙"}, follow_redirects=False)
    assert r.status_code == 303
    assert db.execute("SELECT closed_at FROM inventory_checks").fetchone()[0] is not None
    saved = list((tmp_path / "backups").glob("*before_close_inventory_1_*.db"))
    assert len(saved) == 1
    copy = sqlite3.connect(saved[0])  # ไฟล์สำรองคือสภาพก่อนปิดรอบ
    assert copy.execute("SELECT closed_at FROM inventory_checks").fetchone()[0] is None
    copy.close()


# ---------------------------------------------------------------- เปิดรอบกลับ (admin ภายใน 7 วัน)

def close_round(client, cid, n):
    return client.post(f"/inventory/{cid}/close", data={"expected": str(n), "confirm_year": "2569"}, follow_redirects=False)


def test_admin_reopens_within_7_days(client, db):
    login(client, "admin1")
    cid = open_round(client)
    client.post(f"/inventory/{cid}/rooms/none", data={"r-2": "พบ ใช้งานได้", "r-3": "พบ ชำรุด"})
    close_round(client, cid, 4)
    client.post(f"/inventory/{cid}/proposals/damaged", data={"ids": ["3"]})
    assert db.execute("SELECT status FROM assets WHERE id = 3").fetchone()[0] == "ชำรุด"
    page = client.get(f"/inventory/{cid}").text
    assert "เปิดรอบกลับ…" in page

    assert client.post(f"/inventory/{cid}/reopen", data={"reason": "  "}).status_code == 409
    r = client.post(f"/inventory/{cid}/reopen", data={"reason": "ปิดก่อนกรอกผลห้องอื่นครบ"}, follow_redirects=False)
    assert r.status_code == 303
    c = db.execute("SELECT closed_at, closed_by FROM inventory_checks").fetchone()
    assert tuple(c) == (None, None)
    items = dict(db.execute("SELECT asset_id, result FROM inventory_check_items").fetchall())
    assert items[2] == "พบ ใช้งานได้" and items[3] == "พบ ชำรุด" and items[1] is None  # ไม่พบตอนปิด -> ยังไม่ตรวจ
    log = db.execute("SELECT * FROM inventory_reopens").fetchone()
    assert log["reason"] == "ปิดก่อนกรอกผลห้องอื่นครบ" and log["used"] == 1
    assert audit_rows(db, "inventory_checks", cid)[-1]["action"] == "reopen"
    # ข้อเสนอที่ยืนยันไปแล้วไม่ย้อนกลับ แต่แสดงให้ admin เห็น
    assert db.execute("SELECT status FROM assets WHERE id = 3").fetchone()[0] == "ชำรุด"
    page = client.get(f"/inventory/{cid}").text
    assert "ข้อเสนอที่กดยืนยันไปแล้ว 1 รายการ" in page and "สถานะ ใช้งาน → ชำรุด" in page
    # ปิดได้อีกครั้งตามปกติ
    assert close_round(client, cid, 4).status_code == 303
    with pytest.raises(sqlite3.IntegrityError):  # บันทึกการเปิดกลับที่ใช้แล้วแก้ไม่ได้
        db.execute("UPDATE inventory_reopens SET reason = 'x'")
    db.rollback()


def test_only_admin_can_reopen(editor, db):
    cid = open_round(editor)
    close_round(editor, cid, 6)
    assert editor.get(f"/inventory/{cid}/reopen").status_code == 403
    assert editor.post(f"/inventory/{cid}/reopen", data={"reason": "x"}).status_code == 403
    assert "เปิดรอบกลับ…" not in editor.get(f"/inventory/{cid}").text
    assert db.execute("SELECT closed_at FROM inventory_checks").fetchone()[0] is not None


def test_cannot_reopen_after_7_days(client, db, monkeypatch):
    login(client, "admin1")
    cid = open_round(client)
    real = timeutil.utcnow()
    monkeypatch.setattr(timeutil, "utcnow", lambda: real - timedelta(days=8))
    close_round(client, cid, 6)
    monkeypatch.setattr(timeutil, "utcnow", lambda: real)
    closed = db.execute("SELECT closed_at FROM inventory_checks").fetchone()[0]
    assert closed == (real + timedelta(hours=7) - timedelta(days=8)).date().isoformat()
    r = client.post(f"/inventory/{cid}/reopen", data={"reason": "อยากแก้"})
    assert r.status_code == 409 and "ภายใน 7 วัน" in r.text
    assert "เลย 7 วันหลังปิดรอบแล้ว" in client.get(f"/inventory/{cid}/reopen").text
    # trigger ใน db กันไว้ด้วย แม้จะมีบันทึกเหตุผลแล้ว
    db.execute("INSERT INTO inventory_reopens (check_id, reason, closed_at_before) VALUES (?, 'ลอง', ?)", (cid, closed))
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE inventory_checks SET closed_at = NULL, closed_by = NULL WHERE id = ?", (cid,))
    db.rollback()


def test_trigger_blocks_reopen_without_log(admin, db):
    cid = open_round(admin)
    close_round(admin, cid, 6)
    with pytest.raises(sqlite3.IntegrityError):  # ไม่มีบันทึกเหตุผลใน inventory_reopens
        db.execute("UPDATE inventory_checks SET closed_at = NULL, closed_by = NULL WHERE id = ?", (cid,))
    db.rollback()
    db.execute("INSERT INTO inventory_reopens (check_id, reason, closed_at_before) "
               "SELECT id, 'ทดสอบ', closed_at FROM inventory_checks")
    with pytest.raises(sqlite3.IntegrityError):  # มีบันทึกแล้วก็ยังแก้ช่องอื่นพร้อมกันไม่ได้
        db.execute("UPDATE inventory_checks SET closed_at = NULL, closed_by = NULL, fiscal_year = 2500 WHERE id = ?", (cid,))
    db.rollback()


# ---------------------------------------------------------------- ประวัติซ่อม

def repair_form(**over):
    form = {"day": "6", "month": "8", "year_be": "2558", "description": "เปลี่ยนหลังคา", "cost": "670,000", "note": "",
            "back": "/assets/1"}
    form.update(over)
    return form


def test_repair_add_edit_delete(editor, db):
    r = editor.post("/assets/1/repairs", data=repair_form(), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/assets/1#repairs"
    row = db.execute("SELECT * FROM repairs").fetchone()
    assert (row["repaired_at"], row["description"], row["cost"]) == ("2015-08-06", "เปลี่ยนหลังคา", 670000)
    assert audit_rows(db)[-1]["action"] == "repair_insert"
    html = editor.get("/assets/1").text
    assert "ประวัติการซ่อม" in html and "เปลี่ยนหลังคา" in html and "670,000 บาท" in html

    editor.post(f"/assets/1/repairs/{row['id']}", data=repair_form(cost="", note="ไม่มีใบเสร็จ"))
    row = db.execute("SELECT * FROM repairs").fetchone()
    assert row["cost"] is None and row["note"] == "ไม่มีใบเสร็จ"
    ch = json.loads(audit_rows(db)[-1]["changes"])
    assert ch["repair_cost"] == {"old": 670000, "new": None}
    assert "ไม่ทราบค่าใช้จ่าย" in editor.get("/assets/1").text

    editor.post(f"/assets/1/repairs/{row['id']}/delete", data={"back": "/q/99-9999-1-1306-0004"})
    assert db.execute("SELECT COUNT(*) FROM repairs").fetchone()[0] == 0
    assert audit_rows(db)[-1]["action"] == "repair_delete"
    assert "ลบประวัติซ่อม" in editor.get("/assets/1").text


def test_repair_validation_and_permissions(client, db):
    login(client, "viewer1")
    assert client.post("/assets/1/repairs", data=repair_form()).status_code == 403
    client.post("/logout")
    login(client)
    client.post("/assets/1/repairs", data=repair_form(description=""))
    client.post("/assets/1/repairs", data=repair_form(year_be="2700"))
    client.post("/assets/1/repairs", data=repair_form(cost="abc"))
    assert db.execute("SELECT COUNT(*) FROM repairs").fetchone()[0] == 0
    client.post("/assets/1/repairs", data=repair_form(back="https://evil.example/"))
    assert client.post("/assets/2/repairs/1", data=repair_form()).status_code == 404  # ประวัติของชิ้นอื่น


def test_repairs_on_qr_page(editor, db):
    editor.post("/assets/1/repairs", data=repair_form(back="/q/99-9999-1-1306-0004"))
    html = editor.get("/q/99-9999-1-1306-0004").text
    assert "ประวัติการซ่อม" in html and "เปลี่ยนหลังคา" in html and "เพิ่มประวัติการซ่อม" in html


# ---------------------------------------------------------------- การจำหน่าย

def dispose_form(ids, **over):
    form = {"ids": [str(i) for i in ids], "day": "1", "month": "10", "year_be": "2569", "method": "ขาย",
            "doc_ref": "ที่ สก 0118/1234", "amount": "1,500", "note": ""}
    form.update(over)
    return form


def test_dispose_requires_doc_ref(editor, db):
    r = editor.post("/disposals", data=dispose_form([1], doc_ref="  "))
    assert r.status_code == 400 and "เลขที่หนังสืออนุมัติ" in r.text
    assert editor.post("/disposals", data=dispose_form([1], method="ทิ้ง")).status_code == 400
    assert db.execute("SELECT COUNT(*) FROM disposals").fetchone()[0] == 0
    assert db.execute("SELECT status FROM assets WHERE id = 1").fetchone()[0] == "ใช้งาน"


def test_dispose_single_from_detail_page(editor, db):
    assert "บันทึกการจำหน่าย" in editor.get("/assets/1").text
    assert "TMP-0001" not in editor.get("/disposals/new?ids=1").text
    r = editor.post("/disposals", data=dispose_form([1]), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/assets/1"
    d = db.execute("SELECT * FROM disposals").fetchone()
    assert (d["disposed_at"], d["method"], d["doc_ref"], d["amount"], d["status_before"]) == \
        ("2026-10-01", "ขาย", "ที่ สก 0118/1234", 1500, "ใช้งาน")
    assert db.execute("SELECT status FROM assets WHERE id = 1").fetchone()[0] == "จำหน่ายแล้ว"
    assert audit_rows(db)[-1]["action"] == "dispose"
    assert editor.post("/disposals", data=dispose_form([1])).status_code == 400  # จำหน่ายซ้ำไม่ได้


def test_dispose_batch_under_one_approval(editor, db):
    html = editor.get("/assets").text
    assert 'name="ids" value="3"' in html and "จำหน่ายรายการที่ติ๊กเลือก" in html
    r = editor.post("/disposals", data=dispose_form([3, 4, 5], amount="2,700", method="แปรสภาพหรือทำลาย"),
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/reports/disposals?fy=2570"
    rows = db.execute("SELECT * FROM disposals ORDER BY id").fetchall()
    assert len({r["lot_id"] for r in rows}) == 1 and [r["amount"] for r in rows] == [2700, None, None]
    assert {r["doc_ref"] for r in rows} == {"ที่ สก 0118/1234"}
    report = editor.get("/reports/disposals?fy=2570&digits=arabic").text
    assert "รายการครุภัณฑ์ที่จำหน่ายในปีงบประมาณ 2570" in report
    assert "2,700.00 (รวม 3 รายการ)" in report and "1401-0011" in report
    assert "ไม่มีรายการจำหน่าย" in editor.get("/reports/disposals?fy=2569").text
    wb = load_workbook(BytesIO(editor.get("/reports/disposals.xlsx?fy=2570").content))
    assert wb.active.max_row >= 6


def test_cancel_disposal_admin_only_with_reason(client, db):
    login(client)
    client.post("/disposals", data=dispose_form([4, 5], amount="900"))
    first = db.execute("SELECT id FROM disposals ORDER BY id").fetchone()[0]
    assert client.get(f"/disposals/{first}/cancel").status_code == 403
    assert client.post(f"/disposals/{first}/cancel", data={"reason": "ผิด"}).status_code == 403
    client.post("/logout")
    login(client, "admin1")
    assert client.post(f"/disposals/{first}/cancel", data={"reason": " "}).status_code == 400
    r = client.post(f"/disposals/{first}/cancel", data={"reason": "บันทึกผิดชิ้น"}, follow_redirects=False)
    assert r.status_code == 303
    d = db.execute("SELECT * FROM disposals WHERE id = ?", (first,)).fetchone()
    assert d["cancel_reason"] == "บันทึกผิดชิ้น" and d["cancelled_at"] is not None and d["amount"] is None
    assert db.execute("SELECT amount FROM disposals WHERE id = ?", (first + 1,)).fetchone()[0] == 900  # ยอดย้ายไปชิ้นที่เหลือ
    assert db.execute("SELECT status FROM assets WHERE id = 4").fetchone()[0] == "ใช้งาน"
    log = audit_rows(db, "assets", 4)[-1]
    assert log["action"] == "dispose_cancel" and json.loads(log["changes"])["cancel_reason"]["new"] == "บันทึกผิดชิ้น"
    assert "ยกเลิกแล้ว" in client.get("/assets/4").text
    # จำหน่ายใหม่ได้หลังยกเลิก
    client.post("/disposals", data=dispose_form([4]))
    assert db.execute("SELECT status FROM assets WHERE id = 4").fetchone()[0] == "จำหน่ายแล้ว"


# ---------------------------------------------------------------- migration 006: ประวัติซ่อมจากไฟล์เดิม


def test_part6_tests_use_temp_backup_dir(tmp_path, db_path):
    import os
    assert Path(os.environ["KRUPAN_BACKUP_DIR"]).resolve().is_relative_to(tmp_path.resolve())
