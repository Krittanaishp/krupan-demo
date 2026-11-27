"""ระบบวัสดุสิ้นเปลือง 0.9.0: รายการ/หน่วย รับเข้า เบิกจ่าย ยกเลิก บัญชีวัสดุ ยอดคงเหลือ ตั้งค่า
ข้อมูลทดสอบอยู่ใน db ชั่วคราวเท่านั้น"""
import json
import sqlite3
from datetime import date, timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app import supplies as sp
from app import timeutil
from tests.conftest import audit_rows, login

TODAY = timeutil.today_th()
D = lambda n=0: (TODAY - timedelta(days=n)).isoformat()  # noqa: E731


@pytest.fixture
def setup(db):
    """ฝ่าย 3 ฝ่าย (เบิกได้ 2), เจ้าหน้าที่ 2 คน, หมวด 2 หมวด, วัสดุ 3 รายการ (กระดาษมีหน่วยกล่อง = 5 รีม)"""
    db.executescript("""
        INSERT INTO departments (id, name, sort_order, issues_supplies) VALUES
            (1, 'ปกครอง', 1, 1), (2, 'การเงิน (เสมียนตรา)', 2, 1), (3, 'กิ่งกาชาด', 3, 0);
        INSERT INTO staff (id, full_name, position) VALUES (1, 'นายอนุมัติ ใจดี', 'ปลัดอำเภอ'), (2, 'นางสอง ลองดู', NULL);
        INSERT INTO supply_categories (id, name, sort_order) VALUES (1, 'กระดาษ', 1), (2, 'เครื่องเขียน', 2);
        INSERT INTO supply_items (id, category_id, name, base_unit, reorder_point) VALUES
            (1, 1, 'A4 80 แกรม', 'รีม', 5), (2, 2, 'ปากกาลูกลื่น สีน้ำเงิน', 'ด้าม', 20), (3, 2, 'เทปใส', 'ม้วน', NULL);
        INSERT INTO supply_units (item_id, name, factor) VALUES (1, 'กล่อง', 5), (2, 'กล่อง', 50);
    """)
    db.commit()
    return db


def uid(db, name="editor1"):
    return db.execute("SELECT id FROM users WHERE username = ?", (name,)).fetchone()[0]


def bal(db, item_id):
    return db.execute("SELECT COALESCE(SUM(qty), 0) FROM supply_moves WHERE item_id = ?", (item_id,)).fetchone()[0]


def unit_id(db, item_id, name="กล่อง"):
    return db.execute("SELECT id FROM supply_units WHERE item_id = ? AND name = ?", (item_id, name)).fetchone()[0]


def receive(client, lines, day=None, source="จัดซื้อ ร้านทดสอบ", **extra):
    data = {"received_date": day or D(), "source": source, **extra}
    for i, (label, qty, unit) in enumerate(lines, 1):
        data.update({f"item_{i}": label, f"qty_{i}": str(qty), f"unit_{i}": str(unit)})
    return client.post("/supplies/receive", data=data, follow_redirects=False)


def issue(client, lines, day=None, dep="1", requester="นายเบิก ของ", approver="1", approved=None, **extra):
    data = {"issue_date": day or D(), "department_id": dep, "requester_name": requester, "approver_staff_id": approver,
            "approved_date": approved or day or D(), **extra}
    for i, line in enumerate(lines, 1):
        label, req = line[0], line[1]
        data.update({f"item_{i}": label, f"req_{i}": str(req)})
        if len(line) > 2:
            data[f"out_{i}"] = str(line[2])
    return client.post("/supplies/issue", data=data, follow_redirects=False)


PAPER, PEN, TAPE = "A4 80 แกรม · กระดาษ", "ปากกาลูกลื่น สีน้ำเงิน · เครื่องเขียน", "เทปใส · เครื่องเขียน"


# ---------------------------------------------------------------- สิทธิ์

def test_requires_login_and_viewer_is_read_only(client, setup):
    r = client.get("/supplies", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    login(client, "viewer1")
    assert client.get("/supplies").status_code == 200
    assert client.get("/supplies/items/1").status_code == 200
    assert client.get("/supplies/issues").status_code == 200 and client.get("/supplies/receipts").status_code == 200
    for url in ("/supplies/issue", "/supplies/receive", "/supplies/items/new"):
        assert client.get(url).status_code == 403, url
    assert receive(client, [(PAPER, 1, "base")]).status_code == 403
    assert issue(client, [(PAPER, 1)]).status_code == 403
    page = client.get("/supplies").text
    assert 'href="/supplies/issue"' not in page.split("<main")[1]  # ไม่มีปุ่มบันทึก


def test_menu_supplies_before_assets(editor):
    nav = editor.get("/assets").text.split('class="nav"')[1].split("</nav>")[0]
    assert nav.index('href="/supplies"') < nav.index('href="/assets"')
    assert 'href="/supplies/issue"' in nav
    r = editor.get("/", follow_redirects=False)
    assert r.headers["location"] == "/supplies"


def test_home_for_anonymous_is_still_assets(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/assets"


# ---------------------------------------------------------------- หมวด / รายการ / หน่วย

def test_create_category_and_item_with_units(editor, db):
    editor.post("/supplies/categories", data={"name": "หมึกพิมพ์"})
    cat = db.execute("SELECT id FROM supply_categories WHERE name = 'หมึกพิมพ์'").fetchone()[0]
    r = editor.post("/supplies/items/new", data={"category_id": str(cat), "name": " Printo  PX-410 ", "base_unit": "ตลับ",
                                                  "reorder_point": "2", "unit_name_1": "แพ็ก", "unit_factor_1": "2"},
                    follow_redirects=False)
    assert r.status_code == 303
    item = db.execute("SELECT * FROM supply_items WHERE name = 'Printo PX-410'").fetchone()
    assert item["base_unit"] == "ตลับ" and item["reorder_point"] == 2
    assert [tuple(r) for r in db.execute("SELECT name, factor FROM supply_units WHERE item_id = ?", (item["id"],))] == [("แพ็ก", 2)]
    ch = json.loads(audit_rows(db, "supply_items", item["id"])[0]["changes"])
    assert ch["name"]["new"] == "Printo PX-410" and ch["units_added"] == ["แพ็ก = 2 ตลับ"]
    assert audit_rows(db, "supply_categories", cat)
    # ชื่อซ้ำในหมวดเดียวกันไม่ได้
    r = editor.post("/supplies/items/new", data={"category_id": str(cat), "name": "Printo PX-410", "base_unit": "ตลับ"})
    assert r.status_code == 400 and "หมวดนี้มีรายการชื่อนี้อยู่แล้ว" in r.text


@pytest.mark.parametrize("over,msg", [
    ({"name": ""}, "กรอกชื่อรายการ"), ({"base_unit": ""}, "กรอกหน่วยหลัก"),
    ({"reorder_point": "-1"}, "จุดสั่งซื้อต้องเป็นจำนวนเต็ม"), ({"reorder_point": "2.5"}, "จุดสั่งซื้อต้องเป็นจำนวนเต็ม"),
    ({"unit_name_1": "กล่อง", "unit_factor_1": "1"}, "จำนวนเต็มมากกว่า 1"),
    ({"unit_name_1": "รีม", "unit_factor_1": "5"}, "ไม่ซ้ำกับหน่วยหลัก"),
    ({"category_id": ""}, "เลือกหมวด"),
])
def test_item_validation(editor, setup, over, msg):
    data = {"category_id": "1", "name": "A4 70 แกรม", "base_unit": "รีม", **over}
    r = editor.post("/supplies/items/new", data=data)
    assert r.status_code == 400 and msg in r.text


def test_unit_factor_cannot_change_and_base_unit_locked_after_moves(editor, setup):
    r = editor.post("/supplies/items/1/edit", data={"category_id": "1", "name": "A4 80 แกรม", "base_unit": "รีม",
                                                    "unit_name_1": "กล่อง", "unit_factor_1": "10"})
    assert r.status_code == 400 and "อัตราแปลงแก้ไม่ได้" in r.text
    receive(editor, [(PAPER, 1, "base")])
    r = editor.post("/supplies/items/1/edit", data={"category_id": "1", "name": "A4 80 แกรม", "base_unit": "แผ่น"})
    assert r.status_code == 400 and "เปลี่ยนหน่วยหลักไม่ได้" in r.text
    # ปิดใช้หน่วยแล้วเพิ่มหน่วยชื่อใหม่ได้
    uid_ = unit_id(setup, 1)
    editor.post(f"/supplies/units/{uid_}/active", data={"active": "0"})
    assert setup.execute("SELECT active FROM supply_units WHERE id = ?", (uid_,)).fetchone()[0] == 0
    r = editor.post("/supplies/items/1/edit", data={"category_id": "1", "name": "A4 80 แกรม", "base_unit": "รีม",
                                                    "unit_name_1": "ลัง", "unit_factor_1": "10"}, follow_redirects=False)
    assert r.status_code == 303


def test_item_deactivate_only_when_empty(editor, setup):
    receive(editor, [(TAPE, 3, "base")])
    editor.post("/supplies/items/3/active", data={"active": "0"})
    assert setup.execute("SELECT active FROM supply_items WHERE id = 3").fetchone()[0] == 1
    issue(editor, [(TAPE, 3)])
    editor.post("/supplies/items/3/active", data={"active": "0"})
    assert setup.execute("SELECT active FROM supply_items WHERE id = 3").fetchone()[0] == 0
    assert TAPE not in editor.get("/supplies/issue").text  # ไม่โผล่ในหน้าเบิก
    assert editor.get("/supplies/items/3").status_code == 200  # ประวัติยังดูได้


def test_no_price_or_value_columns_in_supply_tables(db):
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'supply%'"):
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({t})")]
        assert not any(w in c for c in cols for w in ("price", "value", "cost", "amount")), (t, cols)


# ---------------------------------------------------------------- รับเข้า

def test_receive_big_unit_converts_to_base_and_breakdown(editor, setup):
    r = receive(editor, [(PAPER, 4, unit_id(setup, 1)), (PEN, 30, "base")], doc_no="บส 12/2569")
    assert r.status_code == 303
    rid = int(r.headers["location"].split("/")[-1].split("?")[0])
    assert bal(setup, 1) == 20 and bal(setup, 2) == 30
    line = setup.execute("SELECT unit_name, unit_qty, factor, qty FROM supply_receipt_lines WHERE item_id = 1").fetchone()
    assert tuple(line) == ("กล่อง", 4, 5, 20)
    ch = json.loads(audit_rows(setup, "supply_receipts", rid)[0]["changes"])
    assert ch["source"]["new"] == "จัดซื้อ ร้านทดสอบ" and "A4 80 แกรม 4 กล่อง (= 20 รีม)" in ch["lines"]
    issue(editor, [(PAPER, 2)])
    assert sp.breakdown(18, "รีม", [{"name": "กล่อง", "factor": 5}]) == "3 กล่อง 3 รีม"
    page = editor.get("/supplies").text
    assert "3 กล่อง 3 รีม" in page
    doc = editor.get(f"/supplies/receipts/{rid}").text
    assert "4 กล่อง" in doc and "20 รีม" in doc and "บส 12/2569" in doc


@pytest.mark.parametrize("lines,msg", [
    ([("ไม่มีของนี้", 1, "base")], "ไม่พบวัสดุ"),
    ([(PAPER, 0, "base")], "จำนวนต้องเป็นจำนวนเต็มมากกว่า 0"),
    ([(PAPER, "1.5", "base")], "จำนวนต้องเป็นจำนวนเต็มมากกว่า 0"),
    ([(PAPER, 1, "base"), (PAPER, 2, "base")], "ซ้ำกับบรรทัดก่อนหน้า"),
    ([(PAPER, 1, "999")], "เลือกหน่วย"),
    ([], "ยังไม่มีรายการวัสดุ"),
])
def test_receive_validation(editor, setup, lines, msg):
    r = receive(editor, lines)
    assert r.status_code == 400 and msg in r.text
    assert setup.execute("SELECT COUNT(*) FROM supply_moves").fetchone()[0] == 0


def test_receive_header_validation(editor, setup):
    assert "กรอกแหล่งที่ได้" in receive(editor, [(PAPER, 1, "base")], source="").text
    assert "อยู่ในอนาคต" in receive(editor, [(PAPER, 1, "base")], day=(TODAY + timedelta(days=1)).isoformat()).text
    assert "ปี พ.ศ." in receive(editor, [(PAPER, 1, "base")], day="2569-11-01").text


def test_receive_accepts_unique_name_and_thai_digits(editor, setup):
    r = receive(editor, [("เทปใส", "๑๒", "base")])
    assert r.status_code == 303 and bal(setup, 3) == 12


# ---------------------------------------------------------------- เบิกจ่าย

def test_issue_pays_what_is_available_and_records_requested(editor, setup):
    receive(editor, [(PEN, 10, "base"), (PAPER, 3, "base")])
    r = issue(editor, [(PEN, 25), (PAPER, 2)], doc_no="บก 1/69")
    assert r.status_code == 303
    iid = int(r.headers["location"].split("/")[-1].split("?")[0])
    lines = setup.execute("SELECT item_id, requested_qty, issued_qty FROM supply_issue_lines WHERE issue_id = ? ORDER BY item_id",
                          (iid,)).fetchall()
    assert [tuple(x) for x in lines] == [(1, 2, 2), (2, 25, 10)]
    assert bal(setup, 2) == 0 and bal(setup, 1) == 1
    h = setup.execute("SELECT * FROM supply_issues WHERE id = ?", (iid,)).fetchone()
    assert h["approver_name"] == "นายอนุมัติ ใจดี (ปลัดอำเภอ)" and h["department_id"] == 1 and h["doc_no"] == "บก 1/69"
    ch = json.loads(audit_rows(setup, "supply_issues", iid)[0]["changes"])
    assert ch["department"]["new"] == "ปกครอง" and "ปากกาลูกลื่น สีน้ำเงิน ขอ 25 จ่าย 10 ด้าม" in ch["lines"]
    doc = editor.get(r.headers["location"]).text
    assert "จ่ายไม่ครบ" in doc and "บันทึกใบเบิกถัดไป" in doc
    assert "บันทึกใบเบิกถัดไป" not in editor.get(f"/supplies/issues/{iid}").text
    assert "จ่ายไม่ครบ 1" in editor.get("/supplies/issues").text


def test_issue_with_nothing_in_stock_records_zero(editor, setup):
    r = issue(editor, [(TAPE, 4)])
    assert r.status_code == 303
    assert setup.execute("SELECT issued_qty FROM supply_issue_lines").fetchone()[0] == 0
    assert setup.execute("SELECT COUNT(*) FROM supply_moves").fetchone()[0] == 0  # ไม่มีรายการเคลื่อนไหว 0


def test_issue_more_than_available_is_refused_with_proposal(editor, setup):
    receive(editor, [(PEN, 10, "base")])
    r = issue(editor, [(PEN, 15, 12)])
    assert r.status_code == 409 and "ระบบเสนอจำนวนจ่ายใหม่" in r.text and 'value="10"' in r.text
    assert bal(setup, 2) == 10 and setup.execute("SELECT COUNT(*) FROM supply_issues").fetchone()[0] == 0
    assert issue(editor, [(PEN, 15, 10)]).status_code == 303 and bal(setup, 2) == 0


def test_stock_checked_again_at_save_time(editor, setup):
    """สองคนกรอกพร้อมกัน: อีกคนจ่ายไปก่อน ตอนบันทึกต้องตรวจยอดใหม่ ไม่ให้ติดลบ"""
    receive(editor, [(PEN, 10, "base")])
    h, lines, errors, _ = sp.parse_issue(setup, {"issue_date": D(), "department_id": "1", "requester_name": "ก",
                                                 "approver_staff_id": "1", "approved_date": D(),
                                                 "item_1": PEN, "req_1": "8", "out_1": "8"})
    assert not errors
    issue(editor, [(PEN, 5)])  # อีกคนจ่ายไป 5 ระหว่างนั้น
    with pytest.raises(sp.StockChanged) as e:
        sp.record_issue(setup, uid(setup), h, lines)
    assert e.value.short[0][1] == 5 and bal(setup, 2) == 5
    # trigger เป็นด่านสุดท้าย: insert ตรง ๆ ที่ทำให้ติดลบไม่ได้
    with pytest.raises(sqlite3.IntegrityError, match="below zero"):
        setup.execute("INSERT INTO supply_moves (item_id, move_date, kind, qty, created_by) VALUES (2, ?, 'issue', -6, ?)",
                      (D(), uid(setup)))
    setup.rollback()


def test_backdated_issue_before_receipt_is_limited(editor, setup):
    receive(editor, [(PAPER, 5, "base")], day=D(1))
    r = issue(editor, [(PAPER, 3, 3)], day=D(3), approved=D(3))
    assert r.status_code == 409  # 3 วันก่อนยังไม่มีของ
    assert issue(editor, [(PAPER, 3)], day=D(3), approved=D(3)).status_code == 303
    assert setup.execute("SELECT issued_qty FROM supply_issue_lines").fetchone()[0] == 0
    assert issue(editor, [(PAPER, 3)], day=D(0)).status_code == 303 and bal(setup, 1) == 2


@pytest.mark.parametrize("over,msg", [
    ({"dep": "3"}, "เลือกฝ่ายที่เบิก"), ({"dep": ""}, "เลือกฝ่ายที่เบิก"), ({"requester": ""}, "กรอกชื่อผู้เบิก"),
    ({"approver": ""}, "เลือกผู้อนุมัติ"), ({"approved": "x"}, "กรอกวันที่อนุมัติ"),
])
def test_issue_header_validation(editor, setup, over, msg):
    receive(editor, [(PEN, 5, "base")])
    r = issue(editor, [(PEN, 1)], **over)
    assert r.status_code == 400 and msg in r.text and bal(setup, 2) == 5


def test_issue_line_validation(editor, setup):
    assert "กรอกจำนวนที่ขอ" in issue(editor, [(PEN, 0)]).text
    assert "จำนวนที่จ่ายต้องอยู่ระหว่าง 0 ถึงจำนวนที่ขอ" in issue(editor, [(PEN, 2, 3)]).text
    assert "ซ้ำกับบรรทัดก่อนหน้า" in issue(editor, [(PEN, 1), (PEN, 2)]).text


def test_issue_form_shows_departments_defaults_and_requesters(editor, admin_settings_approver):
    page = editor.get("/supplies/issue").text
    assert "ปกครอง" in page and "การเงิน (เสมียนตรา)" in page and "กิ่งกาชาด" not in page
    assert '<option value="2" selected>' in page  # ผู้อนุมัติเริ่มต้นจากหน้าตั้งค่า (คนที่ 2)
    receive(editor, [(PEN, 10, "base")])
    issue(editor, [(PEN, 1)], requester="นางเคยเบิก", approver="2")
    page = editor.get("/supplies/issue").text
    assert "นางเคยเบิก" in page.split('id="requester-data"')[1].split("</script>")[0]


@pytest.fixture
def admin_settings_approver(setup, client):
    login(client, "admin1")
    client.post("/settings/supplies", data={"approver": "2", "issues": ["1", "2"]})
    client.post("/logout")
    login(client, "editor1")
    return setup


def test_next_issue_remembers_dates_and_approver(editor, setup):
    receive(editor, [(PEN, 10, "base")], day=D(2))
    issue(editor, [(PEN, 1)], day=D(2), approved=D(2), approver="2")
    page = editor.get("/supplies/issue").text
    assert f'value="{D(2)}"' in page and '<option value="2" selected>' in page
    assert 'value="' + D() + '"' not in page.split('id="issue_date"')[1].split(">")[0]


# ---------------------------------------------------------------- ยกเลิก / คัดลอก

def test_cancel_issue_returns_stock_and_keeps_history(editor, setup):
    receive(editor, [(PEN, 10, "base")])
    iid = int(issue(editor, [(PEN, 4)]).headers["location"].split("/")[-1].split("?")[0])
    editor.post(f"/supplies/issues/{iid}/cancel", data={"reason": ""})
    assert setup.execute("SELECT cancelled_at FROM supply_issues").fetchone()[0] is None  # ต้องมีเหตุผล
    editor.post(f"/supplies/issues/{iid}/cancel", data={"reason": "บันทึกผิดฝ่าย"})
    h = setup.execute("SELECT * FROM supply_issues WHERE id = ?", (iid,)).fetchone()
    assert h["cancelled_at"] and h["cancel_reason"] == "บันทึกผิดฝ่าย" and bal(setup, 2) == 10
    kinds = [r[0] for r in setup.execute("SELECT kind FROM supply_moves WHERE item_id = 2 ORDER BY id")]
    assert kinds == ["receipt", "issue", "reversal"]  # ไม่ลบรายการเดิม
    assert setup.execute("SELECT COUNT(*) FROM supply_issue_lines").fetchone()[0] == 1
    assert json.loads(audit_rows(setup, "supply_issues", iid)[-1]["changes"])["cancel_reason"]["new"] == "บันทึกผิดฝ่าย"
    r = editor.post(f"/supplies/issues/{iid}/cancel", data={"reason": "อีกครั้ง"}, follow_redirects=True)
    assert "ยกเลิกไปแล้ว" in r.text and bal(setup, 2) == 10
    # คัดลอกเป็นใบใหม่: เติมข้อมูลเดิมให้ (จำนวนที่จ่ายให้ระบบเสนอใหม่)
    page = editor.get(f"/supplies/issue?copy={iid}").text
    assert PEN in page and 'value="4"' in page and "คัดลอกจากใบเบิก #" in page and 'name="copied_from"' in page
    r = issue(editor, [(PEN, 4)], copied_from=str(iid))
    new = int(r.headers["location"].split("/")[-1].split("?")[0])
    assert setup.execute("SELECT copied_from_id FROM supply_issues WHERE id = ?", (new,)).fetchone()[0] == iid
    assert f'href="/supplies/issues/{new}"' in editor.get(f"/supplies/issues/{iid}").text


def test_cancel_receipt_blocked_when_stock_already_issued(editor, setup):
    rid = int(receive(editor, [(PEN, 10, "base")]).headers["location"].split("/")[-1].split("?")[0])
    issue(editor, [(PEN, 4)])
    r = editor.post(f"/supplies/receipts/{rid}/cancel", data={"reason": "ผิด"}, follow_redirects=True)
    assert "ยกเลิกไม่ได้" in r.text and bal(setup, 2) == 6
    assert setup.execute("SELECT cancelled_at FROM supply_receipts").fetchone()[0] is None
    rid2 = int(receive(editor, [(PAPER, 2, unit_id(setup, 1))]).headers["location"].split("/")[-1].split("?")[0])
    editor.post(f"/supplies/receipts/{rid2}/cancel", data={"reason": "รับผิดรายการ"})
    assert bal(setup, 1) == 0 and setup.execute("SELECT cancel_reason FROM supply_receipts WHERE id = ?", (rid2,)).fetchone()[0]
    page = editor.get(f"/supplies/receive?copy={rid2}").text
    assert PAPER in page and 'data-selected="' + str(unit_id(setup, 1)) + '"' in page and 'value="2"' in page


def test_documents_cannot_be_edited(editor, setup):
    receive(editor, [(PEN, 10, "base")])
    with pytest.raises(sqlite3.IntegrityError, match="read-only"):
        setup.execute("UPDATE supply_moves SET qty = 1")
    assert editor.post("/supplies/receipts/1/edit", data={}).status_code in (404, 405)


# ---------------------------------------------------------------- บัญชีวัสดุ / ยอดคงเหลือ / Excel

def test_stock_card_running_balance(editor, setup):
    receive(editor, [(PAPER, 2, unit_id(setup, 1))], day=D(5), doc_no="ร1")
    iid = int(issue(editor, [(PAPER, 3)], day=D(3), approved=D(3), doc_no="บ1").headers["location"].split("/")[-1].split("?")[0])
    editor.post(f"/supplies/issues/{iid}/cancel", data={"reason": "ผิด"})
    issue(editor, [(PAPER, 4)], day=D(1), approved=D(1), dep="2", requester="นางสอง")
    opening, rows = sp.stock_card(setup, 1)
    assert opening == 0
    assert [(r["in"], r["out"], r["balance"]) for r in rows] == [(10, None, 10), (None, 3, 7), (3, None, 10), (None, 4, 6)]
    assert rows[0]["party"] == "รับจาก จัดซื้อ ร้านทดสอบ" and rows[0]["unit_text"] == "2 กล่อง"
    assert rows[2]["party"].startswith("ยกเลิก: จ่ายให้ ปกครอง")
    opening, rows = sp.stock_card(setup, 1, date.fromisoformat(D(2)))
    assert opening == 10 and len(rows) == 1
    page = editor.get("/supplies/items/1").text
    assert "จ่ายให้ การเงิน (เสมียนตรา) (นางสอง)" in page and "ยกเลิก:" in page
    x = editor.get("/supplies/items/1/card.xlsx")
    ws = load_workbook(BytesIO(x.content)).active
    values = [c for row in ws.iter_rows(values_only=True) for c in row if c is not None]
    assert "บัญชีวัสดุ: A4 80 แกรม" in values and 6 in values


def test_balances_filters_low_banner_and_low_page(editor, setup):
    receive(editor, [(PAPER, 6, "base"), (PEN, 50, "base")])
    issue(editor, [(PAPER, 2)])  # เหลือ 4 < จุดสั่งซื้อ 5
    page = editor.get("/supplies").text
    assert "หมดแล้ว 1 รายการ" in page and "ต่ำกว่าจุดสั่งซื้อ 1 รายการ" in page  # เทปใสหมด, กระดาษต่ำกว่าจุดสั่งซื้อ
    low = editor.get("/supplies/low").text
    assert "A4 80 แกรม" in low and "เทปใส" in low and "ปากกาลูกลื่น" not in low
    only = editor.get("/supplies?low=1").text
    assert "ปากกาลูกลื่น" not in only.split('id="balances"')[1]
    cat = editor.get("/supplies?category=2").text.split('id="balances"')[1]
    assert "A4 80 แกรม" not in cat and "เทปใส" in cat
    assert "A4 80 แกรม" in editor.get("/supplies?q=A4").text.split('id="balances"')[1]


@pytest.mark.parametrize("balance, reorder, want", [
    (5, 5, "ok"),      # เท่ากับจุดสั่งซื้อพอดี: ยังไม่เตือน (ฝ่ายเขียนว่า "เหลือต่ำกว่านี้ให้เตือน")
    (4, 5, "low"),     # ต่ำกว่า 1: เตือน
    (6, 5, "ok"),
    (1, 1, "ok"),      # หมึก 1 กล่อง จุดสั่งซื้อ 1
    (0, 1, "out"),
    (0, None, "out"),
    (3, None, "ok"),   # ไม่ได้ตั้งจุดสั่งซื้อ: เตือนเฉพาะตอนหมด
    (1, 0, "ok"),
])
def test_level_warns_only_below_reorder_point(balance, reorder, want):
    assert sp.level(balance, reorder) == want


def test_equal_to_reorder_point_is_not_low_but_one_less_is(editor, setup):
    receive(editor, [(PAPER, 6, "base"), (PEN, 50, "base"), (TAPE, 3, "base")])
    issue(editor, [(PAPER, 1)])  # เหลือ 5 = จุดสั่งซื้อ 5
    assert sp.low_count(setup) == (0, 0)
    assert "A4 80 แกรม" not in editor.get("/supplies/low").text.split("<main")[1]
    issue(editor, [(PAPER, 1)])  # เหลือ 4 < 5
    assert sp.low_count(setup) == (0, 1)
    low = editor.get("/supplies/low").text
    assert "A4 80 แกรม" in low and "เตือนเมื่อเหลือต่ำกว่า" in low


def test_reorder_label_says_warn_when_below(editor, setup):
    assert "เตือนเมื่อเหลือต่ำกว่า" in editor.get("/supplies/items/1/edit").text
    assert "เตือนเมื่อเหลือต่ำกว่า" in editor.get("/supplies/items/1").text
    assert "เตือนเมื่อเหลือต่ำกว่า" in editor.get("/supplies").text


def test_balances_excel_all_items_no_money(editor, setup):
    receive(editor, [(PAPER, 2, unit_id(setup, 1)), (PEN, 7, "base")])
    r = editor.get("/supplies/balances.xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    ws = load_workbook(BytesIO(r.content)).active
    rows = [row for row in ws.iter_rows(values_only=True)]
    flat = [c for row in rows for c in row if c is not None]
    assert "A4 80 แกรม" in flat and "เทปใส" in flat and 10 in flat and "2 กล่อง" in flat
    assert not any(isinstance(c, str) and ("ราคา" in c or "มูลค่า" in c or "บาท" in c) for c in flat)
    assert "ที่ทำการปกครองอำเภอทดสอบ" in flat
    low = load_workbook(BytesIO(editor.get("/supplies/balances.xlsx?low=1").content)).active
    flat = [c for row in low.iter_rows(values_only=True) for c in row if c is not None]
    assert "เทปใส" in flat and "ปากกาลูกลื่น สีน้ำเงิน" in flat and "A4 80 แกรม" not in flat  # ปากกา 7 <= 20


def test_available_on_uses_min_of_later_days(setup):
    u = uid(setup)
    setup.execute("INSERT INTO supply_moves (item_id, move_date, kind, qty, created_by) VALUES (1, ?, 'receipt', 10, ?)", (D(10), u))
    setup.execute("INSERT INTO supply_moves (item_id, move_date, kind, qty, created_by) VALUES (1, ?, 'issue', -8, ?)", (D(2), u))
    setup.commit()
    assert sp.available_on(setup, 1, D(0)) == 2
    assert sp.available_on(setup, 1, D(5)) == 2   # ถ้าจ่ายวันที่ 5 วันก่อน ยอดวันที่ 2 วันก่อนจะติดลบ
    assert sp.available_on(setup, 1, D(11)) == 0


# ---------------------------------------------------------------- ตั้งค่า > วัสดุ

def test_supply_settings_admin_only_and_audited(client, setup):
    login(client, "editor1")
    assert client.get("/settings/supplies").status_code == 403
    client.post("/logout")
    login(client, "admin1")
    page = client.get("/settings/supplies").text
    assert "กิ่งกาชาด" in page and 'href="/settings/supplies"' in page
    client.post("/settings/supplies", data={"approver": "1", "issues": ["1", "3"]})
    flags = dict(setup.execute("SELECT name, issues_supplies FROM departments").fetchall())
    assert flags == {"ปกครอง": 1, "การเงิน (เสมียนตรา)": 0, "กิ่งกาชาด": 1}
    assert sp.default_approver(setup) == 1
    assert audit_rows(setup, "departments", 3) and audit_rows(setup, "app_settings", 0)
    r = client.post("/settings/supplies", data={"approver": "99"})
    assert r.status_code == 400


def test_department_rename_shows_in_asset_reports(admin, db):
    """ฝ่ายที่เปลี่ยนชื่อ (เสมียนตรา -> การเงิน (เสมียนตรา)) ต้องแสดงชื่อใหม่ในรายงานครุภัณฑ์แยกตามฝ่าย"""
    db.execute("INSERT INTO departments (id, name) VALUES (9, 'การเงิน (เสมียนตรา)')")
    lid = db.execute("INSERT INTO locations (name, department_id) VALUES ('ห้องการเงิน', 9)").lastrowid
    db.execute("UPDATE assets SET location_id = ? WHERE id = 1", (lid,))
    db.commit()
    page = admin.get("/reports/summary").text
    assert "การเงิน (เสมียนตรา)" in page and "เสมียนตรา</td>" not in page.replace("(เสมียนตรา)</td>", "")


def test_full_export_includes_supply_tables(editor, setup):
    receive(editor, [(PAPER, 2, unit_id(setup, 1))])
    wb = load_workbook(BytesIO(editor.get("/reports/export.xlsx").content))
    assert wb.sheetnames[-1] == "รายการซ้ำ"
    for name in ("วัสดุ-ยอดคงเหลือ", "วัสดุ-รายการเคลื่อนไหว", "วัสดุ-ใบรับ", "วัสดุ-ใบเบิก"):
        assert name in wb.sheetnames, name
    rows = list(wb["วัสดุ-ยอดคงเหลือ"].iter_rows(values_only=True))
    assert ("A4 80 แกรม" in rows[1]) and rows[1][-1] == 10


# ---------------------------------------------------------------- ช่องเลือกวัสดุ (combobox.js): ข้อมูลรายการที่ส่งให้หน้าเว็บ

def picker_options(html):
    return json.loads(html.split('<script type="application/json" id="supply-options">')[1].split("</script>")[0])


@pytest.mark.parametrize("url", ["/supplies/issue", "/supplies/receive"])
def test_item_picker_is_combobox_not_datalist(editor, setup, url):
    html = editor.get(url).text
    assert "<datalist id=\"supply-items\"" not in html and 'list="supply-items"' not in html
    assert 'role="combobox"' in html and 'data-combobox="supply-options"' in html and "data-combobox-unique" in html
    assert 'aria-controls="cb-listbox"' in html and 'aria-autocomplete="list"' in html
    assert 'data-combobox-error="เลือกวัสดุจากรายการ"' in html and 'data-combobox-empty="ไม่พบวัสดุ"' in html
    assert "/static/js/combobox.js" in html


def test_item_picker_options_balance_unit_and_tags(editor, setup):
    receive(editor, [(PAPER, 4, "base"), (PEN, 150, "base")])  # กระดาษ 4 < 5 ใกล้หมด · ปากกา 150 · เทปใสหมด
    opts = {o["name"]: o for o in picker_options(editor.get("/supplies/issue").text)}
    assert opts["A4 80 แกรม"] == {"value": PAPER, "name": "A4 80 แกรม", "sub": "กระดาษ", "right": "4 รีม",
                                  "tag": "ใกล้หมด", "tagKind": "warn", "dim": False, "group": "กระดาษ", "keys": ""}
    assert opts["ปากกาลูกลื่น สีน้ำเงิน"]["right"] == "150 ด้าม" and opts["ปากกาลูกลื่น สีน้ำเงิน"]["tag"] is None
    assert opts["เทปใส"]["tag"] == "หมด" and opts["เทปใส"]["tagKind"] == "danger" and opts["เทปใส"]["dim"] is True
    # ยอด = จุดสั่งซื้อพอดี: ไม่มีป้ายใกล้หมด
    receive(editor, [(PAPER, 1, "base")])
    assert picker_options(editor.get("/supplies/issue").text)[0]["tag"] is None


def test_item_picker_json_is_escaped(editor, setup):
    setup.execute("UPDATE supply_items SET name = 'ปากกา </script><b>x' WHERE id = 2")
    setup.commit()
    html = editor.get("/supplies/issue").text
    assert "</script><b>x" not in html
    assert any(o["name"] == "ปากกา </script><b>x" for o in picker_options(html))


def test_item_picker_options_have_group_and_search_keys(editor, setup):
    setup.execute("UPDATE supply_items SET note = 'ชื่อเดิมในไฟล์: ปากกา bic' WHERE id = 2")
    setup.commit()
    html = editor.get("/supplies/receive").text
    opts = {o["name"]: o for o in picker_options(html)}
    assert opts["ปากกาลูกลื่น สีน้ำเงิน"]["group"] == "เครื่องเขียน" and opts["ปากกาลูกลื่น สีน้ำเงิน"]["keys"] == "ชื่อเดิมในไฟล์: ปากกา bic"
    assert opts["A4 80 แกรม"]["keys"] == ""
    assert [o["group"] for o in picker_options(html)] == sorted((o["group"] for o in picker_options(html)),
                                                                key=["กระดาษ", "เครื่องเขียน"].index)  # ตามลำดับหมวด
    assert 'data-combobox-toggle' in html and 'title="ดูวัสดุทั้งหมด"' in html and 'data-combobox-all="ดูวัสดุทั้งหมด"' in html
