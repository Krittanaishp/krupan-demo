"""หน้าจัดข้อความที่ตั้งเข้าห้อง (/locations/mapping), หน้าห้อง (/locations) และผู้รับผิดชอบ (/staff)"""
import json
import re

import pytest

from app.places import is_multi_room, person_in, suggest_room
from tests.conftest import MULTI_ROOM, add_location, add_staff, audit_rows, login


def mapping(client, text, location="", **extra):
    return client.post("/locations/mapping", data={"text": text, "location": location, **extra},
                       headers={"HX-Request": "true"})


# ---------------------------------------------------------------- อ่านข้อความเดิม

@pytest.mark.parametrize("text,multi", [
    (MULTI_ROOM, True), ("ห้องทะเบียน (3 ชุด) และห้องประชุม", True),
    ("ห้องการเงิน ห้องการเงิน ห้องการเงิน", False), ("ห้องพัสดุ อยู่ที่ห้องพัสดุ", False),
    ("กองร้อย อส.  กองร้อย อส.", False), ("บ้านพัก 99/9", False), ("ห้องประชุมชั้น 3", False),
])
def test_multi_room_detection(text, multi):
    assert is_multi_room(text) is multi


@pytest.mark.parametrize("text,person", [
    ("สนง./เอ็ม", ("เอ็ม", "")), ("ปค./พี่เอ", ("พี่เอ", "")),
    ("ห้อง ปกครองอำเภอ /นายทดลอง", ("", "นายทดลอง")),
    ("ใช้ประจำที่ ที่ทำการกำนัน ตำบลตัวอย่าง นายตัวอย่าง สมมุติ", ("", "นายตัวอย่าง สมมุติ")),
    ("ห้องนายอำเภอ", None), ("หน้าห้อง นาย.", None), ("ประจำตำแหน่ง ปลัดอำเภอ", None), ("ป.มั่นคง", None),
])
def test_person_detection(text, person):
    assert person_in(text) == person


def test_room_suggestion():
    assert suggest_room("กองร้อย อส.  กองร้อย อส.  กองร้อย อส.") == "กองร้อย อส."
    assert suggest_room("สนง./เอ็ม") == "สนง."
    assert suggest_room("ใช้ประจำที่ ที่ทำการกำนัน ตำบลตัวอย่าง นายตัวอย่าง สมมุติ") == "ที่ทำการกำนัน ตำบลตัวอย่าง"


# ---------------------------------------------------------------- สิทธิ์

def test_mapping_requires_editor(client):
    r = client.get("/places/mapping", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    login(client, "viewer1")
    assert client.get("/locations/mapping").status_code == 403
    assert client.post("/locations/mapping", data={"text": "ห้องปกครอง", "location": "1"}).status_code == 403


# ---------------------------------------------------------------- หน้าจัดห้อง

def test_mapping_lists_texts_by_count(editor):
    html = editor.get("/locations/mapping").text
    rows = re.findall(r'<span class="map-quote">([^<]+)</span>.*?<td class="num[^"]*"[^>]*>(\d+)</td>', html, re.S)
    assert [int(n) for _, n in rows] == [2, 2, 1, 1]  # เรียงจากจำนวนชิ้นมากไปน้อย
    assert sorted(t for t, _ in rows) == sorted(["ห้องปกครอง", MULTI_ROOM, "สนง./เอ็ม", "ห้องทะเบียน"])  # ไม่ซ้ำ
    assert "ต้องแยกรายชิ้น" in html and "มีชื่อคน" in html


def test_multi_room_row_links_to_filtered_list_and_cannot_be_mapped(editor, db):
    room = add_location(db, "ห้องนายอำเภอ")
    html = editor.get("/locations/mapping").text
    assert "/assets?loc_text=%E0%B8%AB" in html  # ลิงก์ไปหน้ารายการที่กรองเฉพาะข้อความนี้
    r = mapping(editor, MULTI_ROOM, str(room))
    assert "ต้องแยกจัดทีละชิ้น" in r.text
    assert db.execute("SELECT COUNT(*) FROM assets WHERE location_id IS NOT NULL").fetchone()[0] == 0


def test_map_text_to_existing_room(editor, db):
    room = add_location(db, "ห้อง ปกครองอำเภอ")
    r = mapping(editor, "ห้องปกครอง", str(room))
    assert r.status_code == 200 and "บันทึกแล้ว 2 ชิ้น" in r.text
    rows = db.execute("SELECT id, location_id, location_text FROM assets WHERE id IN (1, 6)").fetchall()
    assert all(x["location_id"] == room and x["location_text"] == "ห้องปกครอง" for x in rows)  # ข้อความเดิมยังอยู่
    for aid in (1, 6):
        log = audit_rows(db, record_id=aid)
        assert len(log) == 1
        ch = json.loads(log[0]["changes"])
        assert ch["location_id"]["new_label"] == "ห้อง ปกครองอำเภอ" and ch["from_location_text"] == "ห้องปกครอง"
    assert db.execute("SELECT COUNT(*) FROM asset_moves WHERE to_location_id = ?", (room,)).fetchone()[0] == 2
    # ชิ้นอื่นไม่โดน
    assert db.execute("SELECT location_id FROM assets WHERE id = 2").fetchone()[0] is None


def test_map_with_new_room_created_inline(editor, db):
    r = mapping(editor, "ห้องทะเบียน", "__new__", new_location="  ห้องทะเบียน   อ. ")
    room = db.execute("SELECT id, name FROM locations").fetchone()
    assert room["name"] == "ห้องทะเบียน อ."
    assert db.execute("SELECT location_id FROM assets WHERE id = 2").fetchone()[0] == room["id"]
    assert len(audit_rows(db, "locations", room["id"])) == 1
    # แถวอื่นใน dropdown จะได้ห้องใหม่ผ่านเหตุการณ์ htmx
    trigger = json.loads(r.headers["HX-Trigger"])
    assert trigger["krupan:location-added"] == {"id": room["id"], "name": "ห้องทะเบียน อ."}
    assert r.headers["HX-Trigger"].isascii()


def test_map_with_new_staff_nickname_only(editor, db):
    room = add_location(db, "สำนักงานอำเภอ")
    mapping(editor, "สนง./เอ็ม", str(room), holder="__new__", new_nickname="เอ็ม", new_full_name="")
    s = db.execute("SELECT * FROM staff").fetchone()
    assert (s["nickname"], s["full_name"]) == ("เอ็ม", None)
    a = db.execute("SELECT location_id, holder_id FROM assets WHERE id = 3").fetchone()
    assert (a[0], a[1]) == (room, s["id"])
    assert len(audit_rows(db, "staff", s["id"])) == 1
    move = db.execute("SELECT * FROM asset_moves WHERE asset_id = 3").fetchone()
    assert move["to_holder_id"] == s["id"] and "สนง./เอ็ม" in move["note"]


def test_map_with_existing_staff(editor, db):
    room = add_location(db, "สำนักงานอำเภอ")
    sid = add_staff(db, nickname="เอ็ม")
    mapping(editor, "สนง./เอ็ม", str(room), holder=str(sid))
    assert db.execute("SELECT holder_id FROM assets WHERE id = 3").fetchone()[0] == sid


def test_mapping_validation(editor, db):
    assert "เลือกห้อง" in mapping(editor, "ห้องปกครอง", "").text
    assert "พิมพ์ชื่อห้องใหม่" in mapping(editor, "ห้องปกครอง", "__new__", new_location=" ").text
    closed = add_location(db, "ห้องปิด", active=0)
    assert "เลือกห้อง" in mapping(editor, "ห้องปกครอง", str(closed)).text
    room = add_location(db, "ห้องเปิด")
    assert "ใส่ชื่อเล่นหรือชื่อจริง" in mapping(editor, "ห้องปกครอง", str(room), holder="__new__").text
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0


def test_remap_skips_asset_moved_individually(editor, db):
    first = add_location(db, "ห้อง ก")
    other = add_location(db, "ห้อง ข")
    second = add_location(db, "ห้อง ค")
    mapping(editor, "ห้องปกครอง", str(first))
    db.execute("UPDATE assets SET location_id = ? WHERE id = 6", (other,))  # ย้ายแยกไปแล้ว
    db.commit()
    r = mapping(editor, "ห้องปกครอง", str(second))
    assert "บันทึกแล้ว 1 ชิ้น" in r.text and "ข้าม 1 ชิ้น" in r.text
    assert db.execute("SELECT location_id FROM assets WHERE id = 1").fetchone()[0] == second
    assert db.execute("SELECT location_id FROM assets WHERE id = 6").fetchone()[0] == other


def test_mapping_without_htmx_redirects_with_flash(editor, db):
    room = add_location(db, "ห้อง ปกครองอำเภอ")
    r = editor.post("/locations/mapping", data={"text": "ห้องปกครอง", "location": str(room)})
    assert r.status_code == 200 and "บันทึกแล้ว 2 ชิ้น" in r.text


# ---------------------------------------------------------------- หน้าห้อง

def test_locations_page_public_but_edit_needs_editor(client, db):
    add_location(db, "ห้องปกครอง")
    assert client.get("/locations").status_code == 200
    r = client.post("/locations", data={"name": "ห้องใหม่"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_add_rename_deactivate_location(editor, db):
    editor.post("/locations", data={"name": "ห้องประชุม"})
    lid = db.execute("SELECT id FROM locations WHERE name = 'ห้องประชุม'").fetchone()[0]
    assert "มีห้อง “ห้องประชุม” อยู่แล้ว" in editor.post("/locations", data={"name": "ห้องประชุม "}).text
    editor.post(f"/locations/{lid}", data={"name": "ห้องประชุมใหญ่"})
    editor.post(f"/locations/{lid}/active", data={"active": "0"})
    row = db.execute("SELECT name, active FROM locations WHERE id = ?", (lid,)).fetchone()
    assert (row[0], row[1]) == ("ห้องประชุมใหญ่", 0)
    actions = [r["action"] for r in audit_rows(db, "locations", lid)]
    assert actions == ["insert", "update", "update"]
    # ห้องที่ปิดแล้วไม่อยู่ใน dropdown ของหน้าจัดห้อง
    assert "ห้องประชุมใหญ่</option>" not in editor.get("/locations/mapping").text


def test_cannot_delete_location_with_assets(editor, db):
    room = add_location(db, "ห้องปกครอง")
    db.execute("UPDATE assets SET location_id = ? WHERE id = 1", (room,))
    db.commit()
    r = editor.post(f"/locations/{room}/delete")
    assert "ลบห้อง “ห้องปกครอง” ไม่ได้" in r.text
    assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 1


def test_cannot_delete_location_in_move_history(editor, db):
    room = add_location(db, "ห้องเก่า")
    db.execute("INSERT INTO asset_moves (asset_id, moved_at, from_location_id) VALUES (1, '2026-01-01', ?)", (room,))
    db.commit()
    editor.post(f"/locations/{room}/delete")
    assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 1


def test_delete_unused_location(editor, db):
    room = add_location(db, "ห้องพิมพ์ผิด")
    editor.post(f"/locations/{room}/delete")
    assert db.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 0
    assert audit_rows(db, "locations", room)[-1]["action"] == "delete"


# ---------------------------------------------------------------- ผู้รับผิดชอบ

def test_add_staff_nickname_only_then_fill_full_name(editor, db):
    editor.post("/staff", data={"nickname": "เจ", "full_name": "", "position": ""})
    sid = db.execute("SELECT id FROM staff WHERE nickname = 'เจ'").fetchone()[0]
    editor.post(f"/staff/{sid}", data={"nickname": "เจ", "full_name": "นายทดสอบ ระบบ", "position": "ปลัดอำเภอ"})
    row = db.execute("SELECT full_name, position FROM staff WHERE id = ?", (sid,)).fetchone()
    assert (row[0], row[1]) == ("นายทดสอบ ระบบ", "ปลัดอำเภอ")
    log = audit_rows(db, "staff", sid)
    assert [r["action"] for r in log] == ["insert", "update"]
    assert json.loads(log[1]["changes"])["full_name"] == {"old": None, "new": "นายทดสอบ ระบบ"}


def test_staff_needs_some_name(editor, db):
    r = editor.post("/staff", data={"nickname": " ", "full_name": "", "position": "พนักงาน"})
    assert "ใส่ชื่อเล่นหรือชื่อ-นามสกุล" in r.text
    assert db.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0


def test_staff_with_assets_can_be_deactivated_not_deleted(editor, db):
    sid = add_staff(db, nickname="บอล")
    db.execute("UPDATE assets SET holder_id = ? WHERE id = 1", (sid,))
    db.commit()
    assert "ไม่ได้" in editor.post(f"/staff/{sid}/delete").text
    editor.post(f"/staff/{sid}/active", data={"active": "0"})
    assert db.execute("SELECT active FROM staff WHERE id = ?", (sid,)).fetchone()[0] == 0
    assert db.execute("SELECT holder_id FROM assets WHERE id = 1").fetchone()[0] == sid  # ข้อมูลเดิมไม่หาย


def test_viewer_cannot_change_staff(client, db):
    sid = add_staff(db, nickname="บอล")
    login(client, "viewer1")
    assert client.post(f"/staff/{sid}/delete").status_code == 403
    assert db.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 1
