"""ตรวจสอบพัสดุประจำปี: รอบ snapshot การตรวจด้วย QR / ใบรายการ ปิดรอบ ข้อเสนอเปลี่ยนสถานะ และรายงาน"""
import json
import sqlite3
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app import inventory as inv
from tests.conftest import add_location, audit_rows, login

OK, BROKEN, MISSING = inv.RESULTS


def round_form(**over):
    form = {"fiscal_year": "2569", "day": "1", "month": "10", "year_be": "2569",
            "chair": "นายประธาน ทดสอบ", "members": "นางกรรมการ หนึ่ง\nนายกรรมการ สอง"}
    form.update(over)
    return form


def open_round(client, **over):
    r = client.post("/inventory", data=round_form(**over), follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[1])


def items(db, cid):
    return {r["asset_id"]: r for r in db.execute("SELECT * FROM inventory_check_items WHERE check_id = ?", (cid,))}


def close(client, cid, expected):
    return client.post(f"/inventory/{cid}/close", data={"expected": str(expected), "confirm_year": "2569"},
                       follow_redirects=False)


@pytest.fixture
def rooms(db):
    """ห้องปกครอง = #1 #6 · ห้องทะเบียน = #2 · ที่เหลือยังไม่จัดเข้าห้อง"""
    a = add_location(db, "ห้องปกครอง")
    b = add_location(db, "ห้องทะเบียน")
    db.execute("UPDATE assets SET location_id = ? WHERE id IN (1, 6)", (a,))
    db.execute("UPDATE assets SET location_id = ? WHERE id = 2", (b,))
    db.commit()
    return a, b


# ---------------------------------------------------------------- รอบตรวจสอบพัสดุ

def test_requires_editor(client):
    r = client.get("/inventory", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    login(client, "viewer1")
    assert client.get("/inventory").status_code == 403
    assert client.post("/inventory", data=round_form()).status_code == 403


def test_open_round_snapshots_assets_not_disposed(editor, db, rooms):
    db.execute("UPDATE assets SET status = 'จำหน่ายแล้ว' WHERE id = 5")
    db.execute("UPDATE assets SET status = 'ชำรุด' WHERE id = 4")
    db.commit()
    cid = open_round(editor)
    snap = items(db, cid)
    assert sorted(snap) == [1, 2, 3, 4, 6]
    assert snap[1]["expected_location_id"] == rooms[0] and snap[3]["expected_location_id"] is None
    assert snap[4]["status_at_open"] == "ชำรุด"
    assert all(r["result"] is None and r["unregistered"] == 0 for r in snap.values())
    c = db.execute("SELECT * FROM inventory_checks WHERE id = ?", (cid,)).fetchone()
    assert (c["fiscal_year"], c["started_at"], c["closed_at"]) == (2569, "2026-10-01", None)
    assert json.loads(c["committee"]) == {"chair": "นายประธาน ทดสอบ", "members": ["นางกรรมการ หนึ่ง", "นายกรรมการ สอง"]}
    assert audit_rows(db, "inventory_checks", cid)[0]["action"] == "insert"
    html = editor.get(f"/inventory/{cid}").text
    assert "ตรวจแล้ว 0</strong> / ทั้งหมด 5 ชิ้น" in html
    assert "ห้องปกครอง" in html and "ยังไม่จัดเข้าห้อง" in html


def test_cannot_open_two_rounds(editor, db):
    open_round(editor)
    r = editor.post("/inventory", data=round_form(fiscal_year="2570"))
    assert r.status_code == 400 and "มีรอบตรวจสอบพัสดุที่ยังเปิดอยู่" in r.text
    assert db.execute("SELECT COUNT(*) FROM inventory_checks").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):  # db กันไว้อีกชั้น
        db.execute("INSERT INTO inventory_checks (fiscal_year, started_at) VALUES (2570, '2026-10-01')")


def test_round_form_validation(editor, db):
    r = editor.post("/inventory", data=round_form(chair="", fiscal_year="69", day="31", month="2"))
    assert r.status_code == 400
    assert "กรอกชื่อประธานกรรมการ" in r.text and "ปีงบประมาณต้องเป็น" in r.text and "วันที่เริ่มตรวจไม่ถูกต้อง" in r.text
    assert "ชื่อกรรมการอย่างน้อย 1 คน" in editor.post("/inventory", data=round_form(members=" \n ")).text
    assert db.execute("SELECT COUNT(*) FROM inventory_checks").fetchone()[0] == 0


def test_snapshot_counts_all_even_if_assets_added_during_round(editor, db):
    cid = open_round(editor)
    db.execute("INSERT INTO assets (temp_code, name) VALUES ('TMP-0500', 'ซื้อเพิ่มระหว่างรอบ')")
    db.commit()
    assert inv.counts(db, cid)["total"] == 6
    assert "ไม่อยู่ในรอบตรวจสอบพัสดุนี้" in editor.get("/q/TMP-0500").text
    # ปิดรอบโดยไม่ได้ตรวจเลย: ไม่พบครบ 6 ชิ้นตามตอนเปิดรอบ (ไม่รวมชิ้นที่เพิ่มทีหลัง)
    assert close(editor, cid, 6).status_code == 303
    assert inv.counts(db, cid)["missing"] == 6


def test_edit_open_round(editor, db):
    cid = open_round(editor)
    r = editor.post(f"/inventory/{cid}/edit", data=round_form(members="นางกรรมการ หนึ่ง\nนายกรรมการ สาม\nนายกรรมการ สี่"),
                    follow_redirects=False)
    assert r.status_code == 303
    assert json.loads(db.execute("SELECT committee FROM inventory_checks").fetchone()[0])["members"][2] == "นายกรรมการ สี่"
    assert audit_rows(db, "inventory_checks", cid)[-1]["action"] == "update"


# ---------------------------------------------------------------- รอบที่ปิดแล้วแก้ไม่ได้

def test_closed_round_cannot_be_changed(editor, db, rooms):
    cid = open_round(editor)
    assert close(editor, cid, 6).status_code == 303
    before = [tuple(r) for r in db.execute("SELECT * FROM inventory_check_items ORDER BY asset_id")]
    assert editor.post(f"/inventory/{cid}/rooms/{rooms[0]}", data={"r-1": OK}).status_code == 409
    assert editor.post(f"/inventory/{cid}/edit", data=round_form(chair="คนใหม่")).status_code == 409
    assert editor.post(f"/inventory/{cid}/found-new", data={"name": "ของใหม่"}).status_code == 409
    assert editor.post(f"/inventory/{cid}/close", data={"expected": "0", "confirm_year": "2569"}).status_code == 409
    r = editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": OK}, follow_redirects=False)
    assert r.status_code == 303  # ไม่มีรอบที่เปิดอยู่ = ไม่บันทึก
    assert [tuple(r) for r in db.execute("SELECT * FROM inventory_check_items ORDER BY asset_id")] == before
    assert "ปิดแล้ว" in editor.get(f"/inventory/{cid}/rooms/{rooms[0]}").text
    # trigger ใน db กันการแก้ตรง ๆ ด้วย
    for sql in ("UPDATE inventory_check_items SET result = 'พบ ใช้งานได้' WHERE check_id = 1",
                "DELETE FROM inventory_check_items WHERE check_id = 1",
                "INSERT INTO inventory_check_items (check_id, asset_id) VALUES (1, 999)",
                "UPDATE inventory_checks SET committee = 'x' WHERE id = 1",
                "DELETE FROM inventory_checks WHERE id = 1"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)
    db.rollback()
    # เปิดรอบใหม่ได้หลังปิดรอบเดิม
    assert open_round(editor, fiscal_year="2570") == cid + 1


# ---------------------------------------------------------------- ตรวจด้วยใบรายการ

def test_print_sheet_for_room(editor, db, rooms):
    cid = open_round(editor)
    html = editor.get(f"/inventory/{cid}/rooms/{rooms[0]}/print").text
    assert "ใบรายการตรวจสอบพัสดุ ประจำปีงบประมาณ 2569" in html and "ห้องปกครอง" in html
    assert "ปค.99-9999-1-1306-0004" in html and "ปค.99-9999-1-1306-0005" in html and "TMP-0001" not in html
    assert html.count('<tr class="blank">') == 5 and "ของที่พบแต่ไม่มีในรายการ" in html
    assert html.count('<span class="box"></span>') == 2 * 3 + 5 * 2
    assert "ประธานกรรมการ" in html and "(นายประธาน ทดสอบ)" in html and "(นายกรรมการ สอง)" in html
    assert "/static/css/print-doc.css" in html
    none = editor.get(f"/inventory/{cid}/rooms/none/print").text
    assert "ที่ตั้งตามข้อความเดิม: สนง./เอ็ม" in none
    assert editor.get(f"/inventory/{cid}/rooms/abc").status_code == 404
    assert editor.get(f"/inventory/{cid}/rooms/999").status_code == 404


def test_enter_whole_room_at_once(editor, db, rooms):
    cid = open_round(editor)
    html = editor.get(f"/inventory/{cid}/rooms/none").text
    assert html.count('type="radio"') == 3 * 3 and "ทุกชิ้นที่ยังไม่กรอก = พบ ใช้งานได้" in html
    r = editor.post(f"/inventory/{cid}/rooms/none",
                    data={"r-3": OK, "n-3": "", "r-4": BROKEN, "n-4": "  ขาหัก ", "n-5": "ยังไม่ได้ดู",
                          "r-1": MISSING},  # #1 อยู่ห้องอื่น: ไม่รับ
                    follow_redirects=False)
    assert r.status_code == 303
    snap = items(db, cid)
    assert (snap[3]["result"], snap[3]["method"]) == (OK, "list")
    assert (snap[4]["result"], snap[4]["note"]) == (BROKEN, "ขาหัก")
    assert snap[5]["result"] is None and snap[1]["result"] is None
    assert snap[3]["checked_by"] == db.execute("SELECT id FROM users WHERE username = 'editor1'").fetchone()[0]
    log = audit_rows(db, "assets", 4)[-1]
    assert log["action"] == "inventory" and json.loads(log["changes"])["inventory_result"]["new"] == BROKEN
    page = editor.get(f"/inventory/{cid}/rooms/none").text
    assert "บันทึกผล 2 ชิ้นแล้ว" in page and "ยังไม่ได้ตรวจอีก 1 ชิ้น" in page


def test_room_entry_does_not_overwrite_qr_result_unless_changed(editor, db, rooms):
    cid = open_round(editor)
    editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": BROKEN, "location_id": str(rooms[0]), "note": "สีลอก"})
    editor.post(f"/inventory/{cid}/rooms/{rooms[0]}", data={"r-1": BROKEN, "n-1": "สีลอก", "r-6": OK, "n-6": ""})
    snap = items(db, cid)
    assert (snap[1]["method"], snap[6]["method"]) == ("qr", "list")
    editor.post(f"/inventory/{cid}/rooms/{rooms[0]}", data={"r-1": OK, "n-1": "", "r-6": OK, "n-6": ""})
    assert (items(db, cid)[1]["result"], items(db, cid)[1]["method"]) == (OK, "list")


def test_large_room_form_is_accepted(editor, db):
    """ห้อง "ยังไม่จัดเข้าห้อง" ในข้อมูลจริงมีหลายร้อยชิ้น (เกินค่าเริ่มต้น 1000 ช่องของฟอร์ม)"""
    db.executemany("INSERT INTO assets (temp_code, name) VALUES (?, 'ของทดสอบ')",
                   [(f"TMP-{i:04d}",) for i in range(100, 700)])
    db.commit()
    cid = open_round(editor)
    data = {}
    for (aid,) in db.execute("SELECT asset_id FROM inventory_check_items WHERE check_id = ?", (cid,)):
        data[f"r-{aid}"], data[f"n-{aid}"] = OK, ""
    assert editor.post(f"/inventory/{cid}/rooms/none", data=data, follow_redirects=False).status_code == 303
    assert inv.counts(db, cid)["ok"] == 606


# ---------------------------------------------------------------- ตรวจด้วย QR

def test_qr_box_only_when_round_open_and_logged_in(client, editor, db):
    assert "ตรวจสอบพัสดุประจำปี" not in editor.get("/q/99-9999-1-1306-0004").text
    open_round(editor)
    html = editor.get("/q/99-9999-1-1306-0004").text
    assert "ตรวจสอบพัสดุประจำปี 2569" in html and "ตอนนี้อยู่ที่ห้องไหน" in html
    assert html.index("ตรวจสอบพัสดุประจำปี 2569") < html.index('class="q-card"')  # กล่องบนสุด
    assert 'value="พบ ใช้งานได้"' in html and 'value="พบ ชำรุด"' in html
    editor.post("/logout")
    assert "ตรวจสอบพัสดุประจำปี" not in client.get("/q/99-9999-1-1306-0004").text
    login(client, "viewer1")
    html = client.get("/q/99-9999-1-1306-0004").text
    assert "ตรวจสอบพัสดุประจำปี 2569" in html and "บันทึกผลตรวจสอบพัสดุไม่ได้" in html
    assert client.post("/q/99-9999-1-1306-0004/inventory", data={"result": OK}).status_code == 403


def test_qr_check_same_room_then_shows_previous_result(editor, db, rooms):
    cid = open_round(editor)
    r = editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": OK, "location_id": str(rooms[0]), "note": ""},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/q/99-9999-1-1306-0004"  # ห้องตรง ไม่ต้องถามย้าย
    it = items(db, cid)[1]
    assert (it["result"], it["method"], it["found_location_id"]) == (OK, "qr", None)
    html = editor.get("/q/99-9999-1-1306-0004").text
    assert "ตรวจแล้ว: <strong>พบ ใช้งานได้</strong>" in html and "ผู้ใช้ editor1" in html and "สแกน QR" in html
    assert "แก้ผลการตรวจ" in html
    editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": BROKEN, "location_id": str(rooms[0]), "note": "จอแตก"})
    assert (items(db, cid)[1]["result"], items(db, cid)[1]["note"]) == (BROKEN, "จอแตก")


def test_qr_found_in_other_room_and_move_in_register(editor, db, rooms):
    cid = open_round(editor)
    r = editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": OK, "location_id": str(rooms[1])},
                    follow_redirects=False)
    assert r.headers["location"] == "/q/99-9999-1-1306-0004?ask=move"
    assert items(db, cid)[1]["found_location_id"] == rooms[1]
    assert db.execute("SELECT location_id FROM assets WHERE id = 1").fetchone()[0] == rooms[0]  # ยังไม่ย้ายเอง
    html = editor.get(r.headers["location"]).text
    assert "จะย้ายห้องในทะเบียนเป็น ห้องทะเบียน เลยไหม" in html
    assert inv.counts(db, cid)["outside"] == 1

    editor.post("/q/99-9999-1-1306-0004/inventory/move")
    assert db.execute("SELECT location_id FROM assets WHERE id = 1").fetchone()[0] == rooms[1]
    move = db.execute("SELECT * FROM asset_moves WHERE asset_id = 1").fetchone()
    assert (move["from_location_id"], move["to_location_id"], move["reason"]) == (rooms[0], rooms[1], "พบระหว่างตรวจสอบพัสดุ")
    assert json.loads(audit_rows(db, "assets", 1)[-1]["changes"])["via"] == "inventory"
    # สแกนซ้ำ: ห้องที่เลือกไว้คือห้องที่พบ และยังนับเป็นพบนอกห้องที่บันทึกไว้ (เทียบกับห้อง ณ วันเปิดรอบ)
    editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": OK, "location_id": str(rooms[1])})
    assert items(db, cid)[1]["found_location_id"] == rooms[1] and inv.counts(db, cid)["outside"] == 1


def test_qr_rejects_bad_input(editor, db, rooms):
    open_round(editor)
    assert editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": MISSING}).status_code == 400
    assert editor.post("/q/99-9999-1-1306-0004/inventory", data={"result": OK, "location_id": "999"}).status_code == 400
    assert db.execute("SELECT COUNT(*) FROM inventory_check_items WHERE result IS NOT NULL").fetchone()[0] == 0


def test_qr_page_works_without_migration(client, db_path, monkeypatch, tmp_path):
    """db ที่ยังไม่รัน migrate_005: หน้า /q/ ต้องใช้ได้ หน้า /inventory บอกให้รัน migration"""
    conn = sqlite3.connect(db_path)
    conn.executescript("""DROP TRIGGER inventory_closed_no_item_insert; DROP TRIGGER inventory_closed_no_item_update;
        DROP TRIGGER inventory_closed_no_item_delete; DROP TABLE inventory_check_items;
        CREATE TABLE inventory_check_items (check_id INTEGER, asset_id INTEGER, result TEXT NOT NULL,
            checked_at DATETIME, note TEXT, PRIMARY KEY (check_id, asset_id));""")
    conn.close()
    login(client)
    assert client.get("/q/99-9999-1-1306-0004").status_code == 200
    r = client.get("/inventory")
    assert r.status_code == 503 and "migrate_005.py" in r.text


# ---------------------------------------------------------------- พบของที่ไม่มีในทะเบียน

def test_found_unregistered_item(editor, db, rooms):
    cid = open_round(editor)
    r = editor.post(f"/inventory/{cid}/found-new", data={"name": " พัดลม  ตั้งพื้น ", "location_id": str(rooms[1]),
                                                        "note": "ไม่มีสติกเกอร์", "result": BROKEN},
                    follow_redirects=False)
    assert r.status_code == 303
    a = db.execute("SELECT * FROM assets WHERE name = 'พัดลม ตั้งพื้น'").fetchone()
    assert a["temp_code"] == "TMP-0002" and a["reg_no"] is None
    assert (a["location_id"], a["status"], a["note"]) == (rooms[1], "ชำรุด", "ไม่มีสติกเกอร์")
    assert a["needs_review"] == "พบระหว่างตรวจสอบพัสดุ ไม่มีในทะเบียน"
    it = items(db, cid)[a["id"]]
    assert (it["unregistered"], it["result"], it["found_location_id"]) == (1, BROKEN, rooms[1])
    c = inv.counts(db, cid)
    assert c["total"] == 6 and c["unregistered"] == 1  # ไม่นับรวมกับรายการในทะเบียน
    assert audit_rows(db, "assets", a["id"])[0]["action"] == "insert"
    assert "พบระหว่างตรวจสอบพัสดุ ไม่มีในทะเบียน" in editor.get("/review?group=inventory").text
    assert editor.post(f"/inventory/{cid}/found-new", data={"name": ""}).status_code == 400


# ---------------------------------------------------------------- ปิดรอบ

def test_close_marks_unchecked_as_missing(editor, db, rooms):
    cid = open_round(editor)
    editor.post(f"/inventory/{cid}/rooms/{rooms[0]}", data={"r-1": OK, "r-6": BROKEN})
    page = editor.get(f"/inventory/{cid}/close").text
    assert "ยังมี 4 ชิ้นที่ไม่ได้ตรวจ" in page and "TMP-0001" in page and "ปค.99-9999-1-1306-0004" not in page
    # ไม่ได้ติ๊กยืนยัน = ไม่ปิด
    assert editor.post(f"/inventory/{cid}/close", data={"expected": "4"}, follow_redirects=False).status_code == 303
    assert db.execute("SELECT closed_at FROM inventory_checks").fetchone()[0] is None
    # มีคนตรวจเพิ่มระหว่างที่เปิดหน้ายืนยันค้างไว้: จำนวนไม่ตรง ต้องยืนยันใหม่
    editor.post("/q/TMP-0001/inventory", data={"result": OK, "location_id": str(rooms[1])})
    close(editor, cid, 4)
    assert db.execute("SELECT closed_at FROM inventory_checks").fetchone()[0] is None
    assert "เปลี่ยนไประหว่างนั้น" in editor.get(f"/inventory/{cid}/close").text

    assert close(editor, cid, 3).status_code == 303
    snap = items(db, cid)
    assert [snap[i]["result"] for i in (3, 4, 5)] == [MISSING] * 3
    assert {snap[i]["method"] for i in (3, 4, 5)} == {"close"} and snap[1]["method"] == "list"
    c = db.execute("SELECT * FROM inventory_checks").fetchone()
    assert c["closed_at"] is not None and c["closed_by"] is not None
    assert audit_rows(db, "inventory_checks", cid)[-1]["action"] == "close"
    assert json.loads(audit_rows(db, "assets", 3)[-1]["changes"])["method"] == "close"
    c = inv.counts(db, cid)
    assert (c["ok"], c["broken"], c["missing"], c["unchecked"]) == (2, 1, 3, 0)


def test_proposals_wait_for_confirmation_per_group(editor, db, rooms):
    db.execute("UPDATE assets SET status = 'ชำรุด' WHERE id = 2")
    db.commit()
    cid = open_round(editor)
    editor.post(f"/inventory/{cid}/rooms/{rooms[0]}", data={"r-1": BROKEN, "r-6": OK})   # #1 ใช้งาน -> พบชำรุด
    editor.post(f"/inventory/{cid}/rooms/{rooms[1]}", data={"r-2": OK})                  # #2 ชำรุด -> พบใช้งานได้
    close(editor, cid, 3)                                                                # #3 #4 #5 ไม่พบ
    before = [tuple(r) for r in db.execute("SELECT id, status, needs_review FROM assets ORDER BY id")]
    html = editor.get(f"/inventory/{cid}").text
    assert "ข้อเสนอหลังปิดรอบ" in html and "ไม่พบในการตรวจสอบพัสดุประจำปี 2569" in html
    assert [tuple(r) for r in db.execute("SELECT id, status, needs_review FROM assets ORDER BY id")] == before

    assert editor.post(f"/inventory/{cid}/proposals/damaged", data={"ids": ["1"]}, follow_redirects=False).status_code == 303
    status = dict(db.execute("SELECT id, status FROM assets").fetchall())
    assert status[1] == "ชำรุด" and status[2] == "ชำรุด"  # กลุ่มอื่นยังไม่เปลี่ยน
    log = json.loads(audit_rows(db, "assets", 1)[-1]["changes"])
    assert log["status"] == {"old": "ใช้งาน", "new": "ชำรุด"} and log["check_id"] == cid

    editor.post(f"/inventory/{cid}/proposals/repaired", data={"ids": ["2", "6"]})  # #6 ไม่อยู่ในกลุ่ม: ไม่แตะ
    status = dict(db.execute("SELECT id, status FROM assets").fetchall())
    assert status[2] == "ใช้งาน" and status[6] == "ใช้งาน"

    editor.post(f"/inventory/{cid}/proposals/missing", data={"ids": ["3", "4"]})  # เลือกเฉพาะที่เห็น
    nr = dict(db.execute("SELECT id, needs_review FROM assets").fetchall())
    assert nr[3] == "ไม่พบในการตรวจสอบพัสดุประจำปี 2569" and nr[4] == "ไม่พบในการตรวจสอบพัสดุประจำปี 2569" and nr[5] is None
    assert db.execute("SELECT status FROM assets WHERE id = 3").fetchone()[0] == "ใช้งาน"  # ไม่บันทึกว่าสูญหายเอง
    assert [r["asset_id"] for r in inv.proposal_items(db, inv.get_check(db, cid), "missing")] == [5]
    # ยืนยันซ้ำ ไม่ติดป้ายซ้ำ
    editor.post(f"/inventory/{cid}/proposals/missing", data={"ids": ["3"]})
    assert db.execute("SELECT needs_review FROM assets WHERE id = 3").fetchone()[0] == "ไม่พบในการตรวจสอบพัสดุประจำปี 2569"


def test_proposals_need_closed_round(editor, db, rooms):
    cid = open_round(editor)
    editor.post(f"/inventory/{cid}/rooms/{rooms[0]}", data={"r-1": BROKEN})
    assert editor.post(f"/inventory/{cid}/proposals/damaged", data={"ids": ["1"]}).status_code == 409
    assert db.execute("SELECT status FROM assets WHERE id = 1").fetchone()[0] == "ใช้งาน"
    assert editor.post(f"/inventory/{cid}/proposals/nope", data={"ids": ["1"]}).status_code == 404


# ---------------------------------------------------------------- รายงาน

def finished_round(editor, db, rooms):
    cid = open_round(editor)
    editor.post(f"/inventory/{cid}/rooms/{rooms[0]}", data={"r-1": BROKEN, "n-1": "ล้อหลุด", "r-6": OK})
    editor.post("/q/TMP-0001/inventory", data={"result": BROKEN, "location_id": str(rooms[0])})  # ราคาไม่ทราบ
    editor.post("/q/99-9999-1-1401-0010/inventory", data={"result": OK, "location_id": str(rooms[1])})
    close(editor, cid, 2)
    return cid


def test_report_summary_unknown_values_and_tables(editor, db, rooms):
    cid = finished_round(editor, db, rooms)
    html = editor.get(f"/inventory/{cid}/report").text
    assert "รายงานผลการตรวจสอบพัสดุประจำปีงบประมาณ 2569" in html and "ที่ทำการปกครองอำเภอทดสอบ" in html
    # พบชำรุด 2 รายการ: #1 5,000 บาท + TMP-0001 ไม่ทราบราคา
    assert "<tr><td>พบ ชำรุด</td><td class=\"c\">2</td><td class=\"n\">5,000.00</td>" in html
    assert "ไม่ทราบมูลค่า 1 รายการ" in html
    assert "ตารางแนบ 1 รายการที่พบชำรุด (2 รายการ)" in html and "ล้อหลุด" in html
    assert "ตารางแนบ 2 รายการที่ไม่พบ (2 รายการ)" in html
    assert "ตารางแนบ 3 รายการที่พบนอกห้องที่บันทึกไว้ (1 รายการ)" in html  # TMP-0001 อยู่ห้องทะเบียน พบที่ห้องปกครอง
    assert "(นายประธาน ทดสอบ)" in html and "ลงชื่อ" in html
    assert "/static/css/print-doc.css" in html


def test_report_excel(editor, db, rooms):
    cid = finished_round(editor, db, rooms)
    r = editor.get(f"/inventory/{cid}/report.xlsx")
    assert r.status_code == 200 and "inventory-2569.xlsx" in r.headers["content-disposition"]
    wb = load_workbook(BytesIO(r.content))
    assert wb.sheetnames == ["สรุป", "ชำรุด", "ไม่พบ", "พบนอกห้อง"]
    ws = wb["สรุป"]
    values = [c.value for row in ws.iter_rows() for c in row if c.value is not None]
    assert values[0] == "รายงานผลการตรวจสอบพัสดุประจำปีงบประมาณ 2569"
    assert "ไม่ทราบมูลค่า 1 รายการ" in values
    assert any(str(v).startswith("ลงชื่อ") and str(v).endswith("ประธานกรรมการ") for v in values)
    assert "(นางกรรมการ หนึ่ง)" in values
    assert ws["A1"].font.name == "TH SarabunIT๙" and ws["A1"].font.sz >= 16
    broken = [[c.value for c in row] for row in wb["ชำรุด"].iter_rows(min_row=6, max_row=8)]
    assert broken[1][1] == "ปค.99-9999-1-1306-0004" and broken[1][4] == 5000
    assert broken[2][1] == "TMP-0001" and broken[2][4] == "ไม่ทราบ"


def test_draft_report_while_open(editor, db):
    cid = open_round(editor)
    html = editor.get(f"/inventory/{cid}/report").text
    assert "ฉบับร่าง" in html and "<tr><td>ยังไม่ตรวจ</td>" in html
