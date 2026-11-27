import json

import pytest

from tests.conftest import PASSWORD, REG_1, asset_form, audit_rows, login


def test_view_pages_without_login(client):
    for url in ("/assets", "/assets/1", "/assets/2", "/review", "/types"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert 'href="/assets/1/edit">แก้ไข' not in r.text


@pytest.mark.parametrize("method,url", [
    ("get", "/assets/1/edit"), ("post", "/assets/1/edit"),
    ("post", "/review/resolve"), ("post", "/types/1306"),
])
def test_edit_pages_require_login(client, db, method, url):
    kwargs = {"data": {"asset_id": 1, "flag": "ไม่มีราคา", "name": "x"}} if method == "post" else {}
    r = getattr(client, method)(url, follow_redirects=False, **kwargs)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
    assert db.execute("SELECT name FROM asset_types WHERE code = '1306'").fetchone()[0] == "ตู้เหล็ก 4 ลิ้นชัก"


def test_login_redirects_back_to_edit_page(client):
    r = client.get("/assets/1/edit", follow_redirects=False)
    nxt = r.headers["location"].split("next=", 1)[1]
    r = client.post("/login", data={"username": "editor1", "password": PASSWORD, "next": nxt},
                    follow_redirects=False)
    assert r.headers["location"] == "/assets/1/edit"


def test_viewer_cannot_edit(client, db):
    login(client, "viewer1")
    assert client.get("/assets/1/edit").status_code == 403
    assert client.post("/assets/1/edit", data=asset_form(name="แก้โดย viewer")).status_code == 403
    assert db.execute("SELECT name FROM assets WHERE id = 1").fetchone()[0] == "ตู้เหล็ก 4 ลิ้นชัก"


def test_admin_can_edit(client):
    login(client, "admin1")
    assert client.get("/assets/1/edit").status_code == 200


def test_edit_form_shows_buddhist_year(editor):
    r = editor.get("/assets/1/edit")
    assert 'value="2562"' in r.text and 'value="5000"' in r.text


def test_blank_price_saved_as_null(editor, db):
    r = editor.post("/assets/1/edit", data=asset_form(unit_price="  "), follow_redirects=False)
    assert r.status_code == 303
    price = db.execute("SELECT unit_price, typeof(unit_price) FROM assets WHERE id = 1").fetchone()
    assert price[0] is None and price[1] == "null"


def test_price_with_comma_and_thai_digits(editor, db):
    editor.post("/assets/1/edit", data=asset_form(unit_price="๑,๒๓๔.๕๐"))
    assert db.execute("SELECT unit_price FROM assets WHERE id = 1").fetchone()[0] == 1234.5


@pytest.mark.parametrize("bad", ["-5", "abc", "12.345"])
def test_invalid_price_rejected(editor, db, bad):
    r = editor.post("/assets/1/edit", data=asset_form(unit_price=bad))
    assert r.status_code == 400
    assert db.execute("SELECT unit_price FROM assets WHERE id = 1").fetchone()[0] == 5000


def test_duplicate_reg_no_rejected(editor, db):
    r = editor.post("/assets/2/edit", data=asset_form(reg_no=REG_1, name="โต๊ะทำงานเหล็ก", type_code="1401"))
    assert r.status_code == 400
    assert "ใช้กับรายการอื่นแล้ว" in r.text
    assert db.execute("SELECT reg_no FROM assets WHERE id = 2").fetchone()[0] is None
    assert audit_rows(db, record_id=2) == []


@pytest.mark.parametrize("bad", ["ปค.9-9999-1-1306-0004", "99-9999-1-1306-0004",
                                 "ปค.99-9999-1-1306-004", "ปค.99/9999/1/1306/0004"])
def test_bad_reg_no_format_rejected(editor, db, bad):
    r = editor.post("/assets/1/edit", data=asset_form(reg_no=bad))
    assert r.status_code == 400
    assert "ปค.xx-xxxx-x-xxxx-xxxx" in r.text
    assert db.execute("SELECT reg_no FROM assets WHERE id = 1").fetchone()[0] == REG_1


def test_reg_no_cannot_be_removed_without_temp_code(editor, db):
    r = editor.post("/assets/1/edit", data=asset_form(reg_no=""))
    assert r.status_code == 400
    assert db.execute("SELECT reg_no FROM assets WHERE id = 1").fetchone()[0] == REG_1


def test_new_reg_no_keeps_temp_code_in_note(editor, db):
    form = asset_form(reg_no="ปค.99-9999-1-1401-0009", name="โต๊ะทำงานเหล็ก", type_code="1401", note="โครงการ",
                      date_precision="none", date_day="", date_month="", date_year="", fiscal_year="",
                      unit_price="", details="", location_text="ห้องทะเบียน")
    r = editor.post("/assets/2/edit", data=form, follow_redirects=False)
    assert r.status_code == 303
    row = db.execute("SELECT reg_no, temp_code, note FROM assets WHERE id = 2").fetchone()
    assert row["reg_no"] == "ปค.99-9999-1-1401-0009"
    assert row["temp_code"] == "TMP-0001"  # QR ที่ติดไว้แล้วยังค้นเจอ
    assert row["note"].startswith("โครงการ") and "TMP-0001" in row["note"]


def test_buddhist_year_and_month_precision(editor, db):
    editor.post("/assets/1/edit", data=asset_form(date_precision="month", date_day="", date_month="11",
                                                  date_year="2567"))
    row = db.execute("SELECT acquired_date, acquired_date_precision FROM assets WHERE id = 1").fetchone()
    assert (row[0], row[1]) == ("2024-11-01", "month")


def test_year_precision_and_unknown_date(editor, db):
    editor.post("/assets/1/edit", data=asset_form(date_precision="year", date_day="9", date_month="9",
                                                  date_year="2558"))
    q = "SELECT acquired_date, acquired_date_precision FROM assets WHERE id = 1"
    assert tuple(db.execute(q).fetchone()) == ("2015-01-01", "year")
    editor.post("/assets/1/edit", data=asset_form(date_precision="none", date_year="2558"))
    assert tuple(db.execute(q).fetchone()) == (None, None)


@pytest.mark.parametrize("day,month,year", [("31", "2", "2567"), ("1", "1", "1999"), ("1", "1", "2650")])
def test_invalid_dates_rejected(editor, db, day, month, year):
    r = editor.post("/assets/1/edit", data=asset_form(date_day=day, date_month=month, date_year=year))
    assert r.status_code == 400
    assert db.execute("SELECT acquired_date FROM assets WHERE id = 1").fetchone()[0] == "2019-12-25"


def test_every_edit_writes_audit_log(editor, db):
    editor.post("/assets/1/edit", data=asset_form(name="ตู้เหล็ก 4 ลิ้นชัก สีเทา", unit_price=""))
    editor.post("/assets/1/edit", data=asset_form(name="ตู้เหล็ก 4 ลิ้นชัก สีเทา", unit_price="", status="ชำรุด"))
    rows = audit_rows(db)
    assert len(rows) == 2
    assert json.loads(rows[0]["changes"]) == {
        "name": {"old": "ตู้เหล็ก 4 ลิ้นชัก", "new": "ตู้เหล็ก 4 ลิ้นชัก สีเทา"},
        "unit_price": {"old": 5000, "new": None}}
    assert json.loads(rows[1]["changes"]) == {"status": {"old": "ใช้งาน", "new": "ชำรุด"}}
    editor_id = db.execute("SELECT id FROM users WHERE username = 'editor1'").fetchone()[0]
    assert all(r["user_id"] == editor_id and r["action"] == "update" for r in rows)

    page = editor.get("/assets/1").text
    assert "ประวัติการแก้ไข" in page and "ตู้เหล็ก 4 ลิ้นชัก สีเทา" in page and "ผู้ใช้ editor1" in page


def test_save_without_changes_writes_no_audit(editor, db):
    r = editor.post("/assets/1/edit", data=asset_form(), follow_redirects=False)
    assert r.status_code == 303
    assert audit_rows(db) == []


def test_failed_edit_writes_no_audit(editor, db):
    editor.post("/assets/1/edit", data=asset_form(name="", unit_price="abc"))
    assert audit_rows(db) == []
