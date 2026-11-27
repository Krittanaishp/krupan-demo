"""หน้าแก้ไข: ล้าง needs_review เอง, ห้อง/ผู้รับผิดชอบ + asset_moves และหน้ารายการ (ตัวกรองห้อง)"""
import json

from app import timeutil
from tests.conftest import add_location, add_staff, asset_form, audit_rows


def set_flags(db, asset_id, value):
    db.execute("UPDATE assets SET needs_review = ? WHERE id = ?", (value, asset_id))
    db.commit()


def needs_review(db, asset_id=1):
    return db.execute("SELECT needs_review FROM assets WHERE id = ?", (asset_id,)).fetchone()[0]


# ---------------------------------------------------------------- ล้าง needs_review เอง

def test_price_clears_no_price_flag_only(editor, db):
    set_flags(db, 1, "ไม่มีราคา, เลขทะเบียนซ้ำในชีตเดียวกัน")
    r = editor.post("/assets/1/edit", data=asset_form(unit_price="4500"))
    assert "เอาออกจากรายการรอตรวจสอบ: ไม่มีราคา" in r.text
    assert needs_review(db) == "เลขทะเบียนซ้ำในชีตเดียวกัน"  # เลขซ้ำต้องกดตรวจแล้วเอง
    ch = json.loads(audit_rows(db)[-1]["changes"])
    assert ch["needs_review"] == {"old": "ไม่มีราคา, เลขทะเบียนซ้ำในชีตเดียวกัน", "new": "เลขทะเบียนซ้ำในชีตเดียวกัน"}
    assert ch["auto_resolved"] == ["ไม่มีราคา"]
    assert ch["unit_price"] == {"old": 5000, "new": 4500}


def test_blank_price_keeps_no_price_flag(editor, db):
    set_flags(db, 1, "ไม่มีราคา")
    editor.post("/assets/1/edit", data=asset_form(unit_price=""))
    assert needs_review(db) == "ไม่มีราคา"


def test_reg_no_clears_no_reg_no_flag(editor, db):
    form = asset_form(reg_no="ปค.99-9999-1-1401-0099", name="โต๊ะทำงานเหล็ก", type_code="1401",
                      date_precision="none", fiscal_year="", unit_price="", details="", location_text="ห้องทะเบียน",
                      note="โครงการ")
    editor.post("/assets/2/edit", data=form)
    assert needs_review(db, 2) is None
    ch = json.loads(audit_rows(db, record_id=2)[-1]["changes"])
    assert ch["auto_resolved"] == ["ไม่มีหมายเลขทะเบียน"]


def test_date_change_clears_every_date_flag(editor, db):
    set_flags(db, 1, "Excel อ่านเป็น 1962-12-24 แก้เป็นปี พ.ศ. 2562, ไม่มีราคา, วันที่มีแค่เดือน/ปี, "
                     "Excel เก็บปี พ.ศ. เป็น ค.ศ., ไม่มีวันที่ได้มา, เลขทะเบียนซ้ำในชีตเดียวกัน")
    editor.post("/assets/1/edit", data=asset_form(date_day="24"))
    assert needs_review(db) == "ไม่มีราคา, เลขทะเบียนซ้ำในชีตเดียวกัน"


def test_precision_change_counts_as_date_edit(editor, db):
    set_flags(db, 1, "วันที่มีแค่เดือน/ปี")
    editor.post("/assets/1/edit", data=asset_form(date_precision="month", date_day=""))
    assert needs_review(db) is None


def test_setting_date_unknown_does_not_clear_date_flags(editor, db):
    set_flags(db, 1, "วันที่มีแค่เดือน/ปี")
    editor.post("/assets/1/edit", data=asset_form(date_precision="none"))
    assert needs_review(db) == "วันที่มีแค่เดือน/ปี"


def test_unrelated_edit_does_not_clear_flags(editor, db):
    set_flags(db, 1, "ไม่มีราคา, วันที่มีแค่เดือน/ปี")
    editor.post("/assets/1/edit", data=asset_form(name="ตู้เหล็กสีเทา"))
    assert needs_review(db) == "ไม่มีราคา, วันที่มีแค่เดือน/ปี"
    assert "needs_review" not in json.loads(audit_rows(db)[-1]["changes"])


# ---------------------------------------------------------------- ห้อง / ผู้รับผิดชอบในหน้าแก้ไข

def test_edit_form_has_location_and_holder_dropdowns(editor, db):
    add_location(db, "ห้องปกครอง")
    add_location(db, "ห้องเก่า", active=0)
    add_staff(db, nickname="เอ็ม")
    html = editor.get("/assets/1/edit").text
    assert 'name="location_id"' in html and 'name="holder_id"' in html
    assert "ห้องปกครอง</option>" in html and "เอ็ม</option>" in html
    assert "ห้องเก่า" not in html  # ห้องที่ปิดใช้งานไม่ให้เลือก


def test_change_location_and_holder_records_move_and_audit(editor, db):
    old_room = add_location(db, "ห้องปกครอง")
    new_room = add_location(db, "ห้องประชุม")
    staff = add_staff(db, nickname="เอ็ม", full_name="นางสาวทดสอบ ใจดี")
    db.execute("UPDATE assets SET location_id = ? WHERE id = 1", (old_room,))
    db.commit()

    r = editor.post("/assets/1/edit", data=asset_form(location_id=str(new_room), holder_id=str(staff)),
                    follow_redirects=False)
    assert r.status_code == 303
    row = db.execute("SELECT location_id, holder_id, location_text FROM assets WHERE id = 1").fetchone()
    assert (row[0], row[1], row[2]) == (new_room, staff, "ห้องปกครอง")  # ข้อความเดิมยังอยู่

    moves = db.execute("SELECT * FROM asset_moves WHERE asset_id = 1").fetchall()
    assert len(moves) == 1
    m = moves[0]
    assert (m["from_location_id"], m["to_location_id"], m["from_holder_id"], m["to_holder_id"]) == \
        (old_room, new_room, None, staff)
    assert m["moved_at"] == timeutil.today_th().isoformat()

    ch = json.loads(audit_rows(db)[-1]["changes"])
    assert ch["location_id"] == {"old": old_room, "new": new_room, "old_label": "ห้องปกครอง", "new_label": "ห้องประชุม"}
    assert ch["holder_id"]["new_label"] == "เอ็ม (นางสาวทดสอบ ใจดี)"

    page = editor.get("/assets/1").text
    assert "ประวัติการย้ายห้อง" in page and "ห้องประชุม" in page and "เอ็ม (นางสาวทดสอบ ใจดี)" in page


def test_edit_without_location_change_records_no_move(editor, db):
    editor.post("/assets/1/edit", data=asset_form(name="ตู้เหล็กสีเทา"))
    assert db.execute("SELECT COUNT(*) FROM asset_moves").fetchone()[0] == 0


def test_cannot_pick_inactive_or_unknown_location(editor, db):
    closed = add_location(db, "ห้องปิด", active=0)
    for bad in (str(closed), "9999", "abc"):
        r = editor.post("/assets/1/edit", data=asset_form(location_id=bad))
        assert r.status_code == 400, bad
    assert db.execute("SELECT location_id FROM assets WHERE id = 1").fetchone()[0] is None


def test_keeping_current_inactive_location_is_allowed(editor, db):
    room = add_location(db, "ห้องเดิม")
    db.execute("UPDATE assets SET location_id = ? WHERE id = 1", (room,))
    db.execute("UPDATE locations SET active = 0 WHERE id = ?", (room,))
    db.commit()
    assert "ห้องเดิม (ปิดใช้งาน)" in editor.get("/assets/1/edit").text
    r = editor.post("/assets/1/edit", data=asset_form(location_id=str(room), name="ตู้เหล็กสีเทา"),
                    follow_redirects=False)
    assert r.status_code == 303


# ---------------------------------------------------------------- หน้ารายการ

def test_list_shows_room_name_or_italic_raw_text(client, db):
    room = add_location(db, "ห้อง ปกครองอำเภอ")
    db.execute("UPDATE assets SET location_id = ? WHERE id = 1", (room,))
    db.commit()
    html = client.get("/assets").text
    assert "ห้อง ปกครองอำเภอ" in html
    assert '<span class="loc-raw"' in html and ">ห้องทะเบียน</span>" in html  # ชิ้นที่ยังไม่จัดห้อง


def test_filter_by_room_and_unassigned(client, db):
    room = add_location(db, "ห้อง ปกครองอำเภอ")
    db.execute("UPDATE assets SET location_id = ? WHERE id IN (1, 6)", (room,))
    db.commit()
    html = client.get(f"/assets?loc={room}").text
    assert "พบ <strong>2</strong> รายการ" in html
    html = client.get("/assets?loc=none").text
    assert "พบ <strong>4</strong> รายการ" in html
    assert f'<option value="{room}" selected>' in client.get(f"/assets?loc={room}").text


def test_filter_by_exact_location_text(client):
    html = client.get("/assets", params={"loc_text": "ห้องผู้บริหาร 4 ตัว ห้องประชุม 3 ตัว"}).text
    assert "พบ <strong>2</strong> รายการ" in html and "ข้อความที่ตั้งเดิม" in html
