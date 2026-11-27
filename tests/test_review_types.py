import io
import json
from datetime import timedelta

from openpyxl import load_workbook

from app import timeutil
from tests.conftest import audit_rows, login


def test_review_groups_and_counts(client):
    r = client.get("/review")
    assert r.status_code == 200
    assert "ไม่มีราคา" in r.text and "ไม่มีเลขทะเบียน" in r.text and "วันที่ไม่ครบ" in r.text
    r = client.get("/review?group=regno")
    assert "TMP-0001" in r.text and "ตรวจแล้ว</button>" not in r.text  # ยังไม่ล็อกอิน: ไม่มีปุ่ม


def test_resolve_removes_only_that_flag_and_audits(editor, db):
    r = editor.post("/review/resolve", data={"asset_id": 1, "flag": "ไม่มีราคา", "group": "price"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert db.execute("SELECT needs_review FROM assets WHERE id = 1").fetchone()[0] == "วันที่มีแค่เดือน/ปี"
    rows = audit_rows(db)
    assert len(rows) == 1 and rows[0]["action"] == "review"
    ch = json.loads(rows[0]["changes"])
    assert ch["resolved"] == "ไม่มีราคา"
    assert ch["needs_review"] == {"old": "ไม่มีราคา, วันที่มีแค่เดือน/ปี", "new": "วันที่มีแค่เดือน/ปี"}

    editor.post("/review/resolve", data={"asset_id": 1, "flag": "วันที่มีแค่เดือน/ปี", "group": "partial"})
    assert db.execute("SELECT needs_review FROM assets WHERE id = 1").fetchone()[0] is None
    assert len(audit_rows(db)) == 2
    assert "ตรวจแล้ว" in editor.get("/assets/1").text  # แสดงในประวัติการแก้ไข


def resolve_htmx(client, asset_id=2, flag="ไม่มีหมายเลขทะเบียน", group="regno"):
    return client.post("/review/resolve", data={"asset_id": asset_id, "flag": flag, "group": group},
                       headers={"HX-Request": "true"})


def last_review_audit_id(db):
    return db.execute("SELECT MAX(id) FROM audit_log WHERE action = 'review'").fetchone()[0]


def test_resolve_htmx_shows_undo_bar_and_oob_counts(editor, db):
    r = resolve_htmx(editor)
    assert r.status_code == 200
    assert 'hx-swap-oob="true"' in r.text
    # แถวถูกแทนด้วยแถบ "ตรวจแล้ว · ยกเลิก" ที่หายไปเองใน 5 วินาที
    assert 'class="undo-row" data-expires-in="5000"' in r.text
    assert "ยกเลิก</button>" in r.text and f'name="audit_id" value="{last_review_audit_id(db)}"' in r.text
    assert 'name="csrf_token"' in r.text


def test_undo_restores_flag_in_original_order_and_audits(editor, db):
    editor.post("/review/resolve", data={"asset_id": 1, "flag": "ไม่มีราคา", "group": "price"})
    audit_id = last_review_audit_id(db)
    assert db.execute("SELECT needs_review FROM assets WHERE id = 1").fetchone()[0] == "วันที่มีแค่เดือน/ปี"

    r = editor.post("/review/undo", data={"audit_id": audit_id, "group": "price"}, headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "ตรวจแล้ว</button>" in r.text  # ได้แถวเดิมกลับมา
    assert db.execute("SELECT needs_review FROM assets WHERE id = 1").fetchone()[0] == "ไม่มีราคา, วันที่มีแค่เดือน/ปี"

    rows = audit_rows(db)
    assert [r["action"] for r in rows] == ["review", "review_undo"]
    ch = json.loads(rows[1]["changes"])
    assert ch["restored"] == "ไม่มีราคา" and ch["undo_of"] == audit_id
    assert ch["needs_review"] == {"old": "วันที่มีแค่เดือน/ปี", "new": "ไม่มีราคา, วันที่มีแค่เดือน/ปี"}
    assert "ยกเลิกการตรวจ" in editor.get("/assets/1").text


def test_undo_twice_does_nothing(editor, db):
    resolve_htmx(editor)
    audit_id = last_review_audit_id(db)
    editor.post("/review/undo", data={"audit_id": audit_id})
    r = editor.post("/review/undo", data={"audit_id": audit_id}, headers={"HX-Request": "true"})
    assert "ยกเลิกรายการนี้ไปแล้ว" in r.text
    assert len(audit_rows(db, record_id=2)) == 2


def test_undo_after_window_rejected(editor, db, monkeypatch):
    resolve_htmx(editor)
    audit_id = last_review_audit_id(db)
    later = timeutil.utcnow() + timedelta(minutes=5)
    monkeypatch.setattr(timeutil, "utcnow", lambda: later)
    r = editor.post("/review/undo", data={"audit_id": audit_id}, headers={"HX-Request": "true"})
    assert "เลยเวลายกเลิกแล้ว" in r.text
    assert db.execute("SELECT needs_review FROM assets WHERE id = 2").fetchone()[0] is None


def test_undo_by_other_user_rejected(client, db):
    login(client, "editor1")
    resolve_htmx(client)
    audit_id = last_review_audit_id(db)
    client.post("/logout")
    login(client, "admin1")
    r = client.post("/review/undo", data={"audit_id": audit_id}, headers={"HX-Request": "true"})
    assert "ไม่พบรายการที่คุณกดตรวจแล้ว" in r.text
    assert db.execute("SELECT needs_review FROM assets WHERE id = 2").fetchone()[0] is None


def test_resolve_without_htmx_shows_undo_bar_on_page(editor, db):
    r = editor.post("/review/resolve", data={"asset_id": 2, "flag": "ไม่มีหมายเลขทะเบียน", "group": "regno"})
    assert r.status_code == 200  # ตาม redirect มาที่ /review
    assert "undo-bar-page" in r.text and f'value="{last_review_audit_id(db)}"' in r.text
    assert "undo-bar-page" not in editor.get("/review").text  # แสดงครั้งเดียว


def test_undo_requires_login(client, db):
    r = client.post("/review/undo", data={"audit_id": 1}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_resolve_unknown_flag_changes_nothing(editor, db):
    editor.post("/review/resolve", data={"asset_id": 1, "flag": "ไม่มีจริง", "group": "price"})
    assert audit_rows(db) == []


def test_export_excel(client):
    r = client.get("/review/export.xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    assert {"ไม่มีราคา", "ไม่มีเลขทะเบียน", "วันที่ไม่ครบ"} <= set(wb.sheetnames)
    assert wb["ไม่มีเลขทะเบียน"].cell(row=4, column=2).value == "TMP-0001 (ชั่วคราว)"

    one = load_workbook(io.BytesIO(client.get("/review/export.xlsx?group=price").content))
    assert one.sheetnames == ["ไม่มีราคา"]


def test_rename_type_clears_draft_and_audits(editor, db):
    r = editor.post("/types/1306", data={"name": "ตู้เหล็กเก็บเอกสาร 4 ลิ้นชัก"}, follow_redirects=False)
    assert r.status_code == 303
    row = db.execute("SELECT name, name_is_draft FROM asset_types WHERE code = '1306'").fetchone()
    assert (row[0], row[1]) == ("ตู้เหล็กเก็บเอกสาร 4 ลิ้นชัก", 0)
    log = audit_rows(db, "asset_types", 1306)
    assert len(log) == 1
    assert json.loads(log[0]["changes"]) == {
        "code": "1306",
        "name": {"old": "ตู้เหล็ก 4 ลิ้นชัก", "new": "ตู้เหล็กเก็บเอกสาร 4 ลิ้นชัก"},
        "name_is_draft": {"old": 1, "new": 0}}


def test_confirm_draft_name_without_renaming(editor, db):
    r = editor.post("/types/1401", data={"name": "โต๊ะทำงาน"}, headers={"HX-Request": "true"})
    assert "บันทึกแล้ว" in r.text
    assert db.execute("SELECT name_is_draft FROM asset_types WHERE code = '1401'").fetchone()[0] == 0
    assert len(audit_rows(db, "asset_types", 1401)) == 1


def test_empty_type_name_rejected(editor, db):
    r = editor.post("/types/1306", data={"name": "  "}, headers={"HX-Request": "true"})
    assert "ต้องมีชื่อประเภท" in r.text
    assert db.execute("SELECT name_is_draft FROM asset_types WHERE code = '1306'").fetchone()[0] == 1
    assert audit_rows(db, "asset_types", 1306) == []
