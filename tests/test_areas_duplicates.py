"""0.8.0 ส่วนที่ 2: อาคาร/ฝ่ายของห้อง, ตัวกรองและสรุปตามอาคาร/ฝ่าย, ใบรายการทีละอาคาร และรายการซ้ำ"""
import io
import json

from openpyxl import load_workbook

from app import flags, inventory
from tests.conftest import REG_1, add_location, add_staff, audit_rows, login

REG_6 = "ปค.99-9999-1-1306-0005"  # ครุภัณฑ์ #6 ใน conftest


def area_id(db, table, name):
    return db.execute(f"SELECT id FROM {table} WHERE name = ?", (name,)).fetchone()[0]


def add_area(db, table, name, holder_focus=0):
    order = db.execute(f"SELECT COUNT(*) + 1 FROM {table}").fetchone()[0]  # ลำดับตามที่เพิ่ม
    cols = "name, sort_order, holder_focus" if table == "buildings" else "name, sort_order"
    vals = (name, order, holder_focus) if table == "buildings" else (name, order)
    aid = db.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' * len(vals))})", vals).lastrowid
    db.commit()
    return aid


def setup_rooms(db):
    """ห้องปกครอง (อาคารชั้น 1, ฝ่ายปกครอง) มี #1 #6 · ห้องทะเบียน (ไม่ระบุ) มี #2 · ห้องติดตัว (อาคารเน้นผู้รับผิดชอบ) มี #3"""
    b1 = add_area(db, "buildings", "อาคารทดสอบ ชั้น 1")
    bp = add_area(db, "buildings", "ติดตัวบุคคล", holder_focus=1)
    d1 = add_area(db, "departments", "ปกครอง")
    pk = add_location(db, "ห้องปกครอง")
    tb = add_location(db, "ห้องทะเบียน")
    personal = add_location(db, "ประจำตำแหน่ง")
    db.execute("UPDATE locations SET building_id = ?, department_id = ? WHERE id = ?", (b1, d1, pk))
    db.execute("UPDATE locations SET building_id = ? WHERE id = ?", (bp, personal))
    db.execute("UPDATE assets SET location_id = ? WHERE id IN (1, 6)", (pk,))
    db.execute("UPDATE assets SET location_id = ? WHERE id = 2", (tb,))
    sid = add_staff(db, nickname="ท่านนายอำเภอ")
    db.execute("UPDATE staff SET position = 'นายอำเภอ' WHERE id = ?", (sid,))
    db.execute("UPDATE assets SET location_id = ?, holder_id = ? WHERE id = 3", (personal, sid))
    db.commit()
    return {"b1": b1, "bp": bp, "d1": d1, "pk": pk, "tb": tb, "personal": personal}


# ---------------------------------------------------------------- แท็บห้อง: อาคาร/ฝ่าย

def test_room_row_sets_building_and_department_with_audit(editor, db):
    ids = setup_rooms(db)
    r = editor.post(f"/locations/{ids['tb']}", data={"name": "ห้องทะเบียน", "building": str(ids["b1"]),
                                                    "department": str(ids["d1"]), "back": "q=ทะเบียน"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/places?q=")
    row = db.execute("SELECT building_id, department_id FROM locations WHERE id = ?", (ids["tb"],)).fetchone()
    assert tuple(row) == (ids["b1"], ids["d1"])
    ch = json.loads(audit_rows(db, "locations", ids["tb"])[-1]["changes"])
    assert ch["building_id"]["new_label"] == "อาคารทดสอบ ชั้น 1" and ch["department_id"]["new_label"] == "ปกครอง"
    # ส่งแค่ชื่อ (แบบฟอร์มเดิม) = อาคาร/ฝ่ายคงเดิม
    editor.post(f"/locations/{ids['tb']}", data={"name": "ห้องทะเบียนใหม่"})
    row = db.execute("SELECT name, building_id FROM locations WHERE id = ?", (ids["tb"],)).fetchone()
    assert tuple(row) == ("ห้องทะเบียนใหม่", ids["b1"])
    # เลือก "ไม่ระบุ" = ล้าง
    editor.post(f"/locations/{ids['tb']}", data={"name": "ห้องทะเบียนใหม่", "building": "none", "department": "none"})
    assert db.execute("SELECT building_id FROM locations WHERE id = ?", (ids["tb"],)).fetchone()[0] is None


def test_bulk_set_areas(editor, db):
    ids = setup_rooms(db)
    editor.post("/places/bulk", data={"ids": [str(ids["tb"]), str(ids["personal"])], "building": "",
                                      "department": str(ids["d1"])})
    rows = db.execute("SELECT id, building_id, department_id FROM locations WHERE id IN (?, ?) ORDER BY id",
                      (ids["tb"], ids["personal"])).fetchall()
    assert [tuple(r) for r in rows] == [(ids["tb"], None, ids["d1"]), (ids["personal"], ids["bp"], ids["d1"])]  # อาคารไม่เปลี่ยน
    editor.post("/places/bulk", data={"ids": [str(ids["personal"])], "building": "none", "department": ""})
    assert db.execute("SELECT building_id FROM locations WHERE id = ?", (ids["personal"],)).fetchone()[0] is None
    assert json.loads(audit_rows(db, "locations", ids["personal"])[-1]["changes"])["bulk"] == 1
    assert "เลือกอาคารหรือฝ่าย" in editor.post("/places/bulk", data={"ids": [str(ids["tb"])]}).text
    assert "ติ๊กเลือกห้องก่อน" in editor.post("/places/bulk", data={"building": "none"}).text


def test_merge_button_redirects_without_token_in_url(editor, db):
    ids = setup_rooms(db)
    r = editor.post("/places/merge/start", data={"ids": [str(ids["pk"]), str(ids["tb"])]}, follow_redirects=False)
    assert r.headers["location"] == f"/places/merge?ids={ids['pk']}&ids={ids['tb']}"


def test_rooms_tab_filters_by_building_and_department(editor, db):
    ids = setup_rooms(db)
    html = editor.get(f"/places?building={ids['b1']}").text
    assert 'value="ห้องปกครอง"' in html and 'value="ห้องทะเบียน"' not in html
    html = editor.get("/places?building=none").text
    assert 'value="ห้องทะเบียน"' in html and 'value="ห้องปกครอง"' not in html
    html = editor.get(f"/places?dept={ids['d1']}").text
    assert 'value="ห้องปกครอง"' in html and 'value="ประจำตำแหน่ง"' not in html


def test_area_lists_admin_only_with_audit(admin, db):
    admin.post("/places/areas/buildings", data={"name": "ภายนอกอาคาร"})
    bid = area_id(db, "buildings", "ภายนอกอาคาร")
    admin.post(f"/places/areas/buildings/{bid}", data={"name": "ลานจอดรถ", "active": "1", "holder_focus": "1",
                                                      "sort_order": "9"})
    row = db.execute("SELECT name, active, holder_focus, sort_order FROM buildings WHERE id = ?", (bid,)).fetchone()
    assert tuple(row) == ("ลานจอดรถ", 1, 1, 9)
    admin.post(f"/places/areas/buildings/{bid}", data={"name": "ลานจอดรถ", "sort_order": "9"})  # ไม่ติ๊ก = ปิด
    assert db.execute("SELECT active, holder_focus FROM buildings WHERE id = ?", (bid,)).fetchone()[:] == (0, 0)
    assert [r["action"] for r in audit_rows(db, "buildings", bid)] == ["insert", "update", "update"]
    admin.post("/places/areas/departments", data={"name": "กิ่งกาชาด"})
    assert area_id(db, "departments", "กิ่งกาชาด")
    assert "มี “กิ่งกาชาด” อยู่แล้ว" in admin.post("/places/areas/departments", data={"name": "กิ่งกาชาด"}).text
    assert admin.post("/places/areas/rooms", data={"name": "x"}).status_code == 404


def test_area_lists_not_for_editor(editor, db):
    assert editor.post("/places/areas/buildings", data={"name": "ห้ามเพิ่ม"}).status_code == 403
    html = editor.get("/places").text
    assert "แก้รายการนี้ได้เฉพาะ admin" in html


# ---------------------------------------------------------------- /assets และรายงานสรุป

def test_assets_filter_by_building_and_department(client, db):
    ids = setup_rooms(db)
    html = client.get(f"/assets?building={ids['b1']}").text
    assert REG_1 in html and "TMP-0001" not in html
    html = client.get("/assets?building=none").text  # ห้องที่ยังไม่ระบุอาคาร (#2 ห้องทะเบียน)
    assert "TMP-0001" in html and REG_1 not in html
    html = client.get(f"/assets?dept={ids['d1']}").text
    assert REG_1 in html and REG_6 in html and "TMP-0001" not in html
    r = client.get(f"/assets?building={ids['b1']}", headers={"HX-Request": "true", "HX-Filter-History": "replace"})
    assert r.headers["HX-Replace-Url"] == f"/assets?building={ids['b1']}"


def test_summary_tables_and_filters_by_area(editor, db):
    ids = setup_rooms(db)
    html = editor.get("/reports/summary").text
    assert "แยกตามอาคาร" in html and "แยกตามฝ่าย" in html and "อาคารทดสอบ ชั้น 1" in html
    assert "ห้องที่ยังไม่ระบุอาคาร" in html and "ยังไม่จัดเข้าห้อง" in html
    html = editor.get(f"/reports/summary?building={ids['b1']}").text
    assert "ทั้งหมด 2 ชิ้น" in html
    r = editor.get(f"/reports/summary.xlsx?building={ids['b1']}")
    cells = [c.value for row in load_workbook(io.BytesIO(r.content)).active.iter_rows() for c in row if c.value]
    assert "แยกตามอาคาร" in cells and "อาคาร" in cells and any("ทั้งหมด 2 ชิ้น" in str(c) for c in cells)


# ---------------------------------------------------------------- ตรวจสอบพัสดุ: ใบรายการทีละอาคาร

def open_round(db):
    cid, n = inventory.create_check(db, None, 2570, "2026-11-01", "ประธานทดสอบ", [])
    return cid, n


def test_building_print_groups_rooms_and_holder_focus(editor, db):
    ids = setup_rooms(db)
    second = add_location(db, "ห้องกลาง")
    db.execute("UPDATE locations SET building_id = ? WHERE id = ?", (ids["b1"], second))
    db.execute("UPDATE assets SET location_id = ? WHERE id = 4", (second,))
    db.commit()
    cid, _ = open_round(db)
    groups = inventory.building_rooms(db, cid)
    assert [g["name"] for g in groups] == ["อาคารทดสอบ ชั้น 1", "ติดตัวบุคคล", "ห้องที่ยังไม่ระบุอาคาร"]
    assert [r["name"] for r in groups[0]["rooms"]] == ["ห้องกลาง", "ห้องปกครอง"]  # เรียงตามชื่อห้อง
    assert groups[1]["holder_focus"] and not groups[0]["holder_focus"]
    assert [r["name"] for r in groups[2]["rooms"]] == ["ห้องทะเบียน", "ยังไม่จัดเข้าห้อง"]
    html = editor.get(f"/inventory/{cid}/buildings/{ids['b1']}/print").text
    assert html.count('class="doc-page') == 2 and "new-page" in html and "holder-col" not in html
    assert html.index("ห้องกลาง</strong>") < html.index("ห้องปกครอง</strong>")
    html = editor.get(f"/inventory/{cid}/buildings/{ids['bp']}/print").text
    assert 'class="holder-col"' in html and "ท่านนายอำเภอ" in html and "ขอดูจากผู้รับผิดชอบ" in html
    html = editor.get(f"/inventory/{cid}/rooms/{ids['personal']}/print").text  # พิมพ์ทีละห้องก็เน้นเหมือนกัน
    assert 'class="holder-col"' in html
    assert "holder-col" not in editor.get(f"/inventory/{cid}/rooms/{ids['pk']}/print").text
    assert editor.get(f"/inventory/{cid}/buildings/none/print").status_code == 200
    assert editor.get(f"/inventory/{cid}/buildings/999/print").status_code == 404
    detail = editor.get(f"/inventory/{cid}").text
    assert "พิมพ์ใบรายการทีละอาคาร" in detail and f"/inventory/{cid}/buildings/{ids['bp']}/print" in detail


# ---------------------------------------------------------------- รายการซ้ำ

def test_mark_duplicate_admin_only_and_links_both_ways(admin, db):
    db.execute("UPDATE assets SET needs_review = ? WHERE id = 6", (flags.dup_reg(REG_1),))
    db.execute("UPDATE assets SET needs_review = ? WHERE id = 1", (f"{flags.NO_PRICE}, {flags.dup_reg(REG_6)}",))
    db.commit()
    html = admin.get("/assets/6").text
    assert f'value="{REG_1}"' in html  # เติมเลขจากป้าย “ลงทะเบียนซ้ำกับ ...” ให้
    r = admin.post("/assets/6/duplicate", data={"target": REG_1, "reason": "พัสดุยืนยันว่าเลขที่ถูกคือ 0004"})
    assert "ตั้งเป็นรายการซ้ำของ" in r.text
    a6 = db.execute("SELECT status, duplicate_of_id, duplicate_reason, needs_review FROM assets WHERE id = 6").fetchone()
    assert tuple(a6) == ("ใช้งาน", 1, "พัสดุยืนยันว่าเลขที่ถูกคือ 0004", None)  # สถานะเดิมเก็บไว้ ป้ายหาย
    assert db.execute("SELECT needs_review FROM assets WHERE id = 1").fetchone()[0] == flags.NO_PRICE
    ch = json.loads(audit_rows(db, "assets", 6)[-1]["changes"])
    assert ch["duplicate_of_id"]["new_label"] == REG_1 and ch["duplicate_reason"]["new"]
    assert 'href="/assets/1"' in admin.get("/assets/6").text and "รายการซ้ำ" in admin.get("/assets/6").text
    assert 'href="/assets/6"' in admin.get("/assets/1").text  # รายการหลักลิงก์กลับ
    html = admin.get("/assets?status=รายการซ้ำ").text
    assert REG_6 in html and REG_1 not in html
    assert REG_6 not in admin.get("/assets?status=ใช้งาน").text


def test_mark_duplicate_validation(admin, db):
    err = lambda data: admin.post("/assets/6/duplicate", data=data).text  # noqa: E731
    assert "ใส่เหตุผล" in err({"target": REG_1, "reason": " "})
    assert "ไม่พบรายการ" in err({"target": "ไม่มีเลขนี้", "reason": "x"})
    assert "ซ้ำกับตัวเองไม่ได้" in err({"target": REG_6, "reason": "x"})
    admin.post("/assets/6/duplicate", data={"target": REG_1, "reason": "x"})
    assert "เป็นรายการซ้ำอยู่แล้ว" in admin.post("/assets/2/duplicate", data={"target": REG_6, "reason": "x"}).text
    assert "มีรายการอื่นตั้งว่าซ้ำกับรายการนี้อยู่" in admin.post("/assets/1/duplicate",
                                                                   data={"target": "TMP-0001", "reason": "x"}).text


def test_duplicate_needs_admin(editor, db):
    assert editor.post("/assets/6/duplicate", data={"target": REG_1, "reason": "x"}).status_code == 403
    assert "ตั้งเป็นรายการซ้ำ…" not in editor.get("/assets/6").text


def test_cancel_duplicate_restores_with_reason(admin, db):
    db.execute("UPDATE assets SET status = 'ชำรุด' WHERE id = 6")
    db.commit()
    admin.post("/assets/6/duplicate", data={"target": REG_1, "reason": "ซ้ำ"})
    assert "ใส่เหตุผลที่ยกเลิก" in admin.post("/assets/6/duplicate/cancel", data={"reason": ""}).text
    r = admin.post("/assets/6/duplicate/cancel", data={"reason": "เลือกผิดเลข"})
    assert "สถานะกลับเป็น “ชำรุด”" in r.text
    assert tuple(db.execute("SELECT status, duplicate_of_id FROM assets WHERE id = 6").fetchone()) == ("ชำรุด", None)
    assert json.loads(audit_rows(db, "assets", 6)[-1]["changes"])["cancel_reason"] == "เลือกผิดเลข"


def test_duplicates_left_out_of_reports_export_and_inventory(admin, db):
    admin.post("/assets/6/duplicate", data={"target": REG_1, "reason": "ซ้ำ"})
    html = admin.get("/reports/register?digits=arabic&group=0").text.replace("<wbr>", "")  # เลขทะเบียนตัดบรรทัดได้
    assert REG_1 in html and REG_6 not in html
    html = admin.get("/reports/summary").text
    assert "ทั้งหมด 5 ชิ้น" in html and "ไม่นับรายการซ้ำ 1 รายการ" in html
    wb = load_workbook(io.BytesIO(admin.get("/reports/export.xlsx").content))
    codes = lambda ws: [r[1].value for r in ws.iter_rows(min_row=2)]  # noqa: E731
    assert REG_6 not in codes(wb["ครุภัณฑ์"]) and codes(wb["รายการซ้ำ"]) == [REG_6]
    assert wb.sheetnames[-1] == "รายการซ้ำ" and wb.sheetnames[:4] == ["ครุภัณฑ์", "ห้อง", "ผู้รับผิดชอบ", "ประวัติการย้าย"]
    cid, n = open_round(db)
    assert n == 5 and db.execute("SELECT COUNT(*) FROM inventory_check_items WHERE check_id = ? AND asset_id = 6",
                                 (cid,)).fetchone()[0] == 0


def test_staff_verify_flags_have_review_group(client, db):
    db.execute("UPDATE assets SET needs_review = ? WHERE id = 2",
               ("ไม่พบในห้องประชุม รอตรวจว่ามีเอกสารจำหน่ายหรือไม่",))
    db.commit()
    login(client, "viewer1")
    html = client.get("/review?group=verify").text
    assert "รอเจ้าหน้าที่ยืนยัน" in html and "ไม่พบในห้องประชุม" in html
    assert flags.is_staff_verify_flag(flags.dup_reg("TMP-0074"))


def test_duplicate_between_temp_codes(admin, db):
    """คู่ส่วนใหญ่จากชีต “(ลงแล้ว)” เป็น TMP↔TMP และ 2 รายการซ้ำกับรายการเดียวได้ (เช่น TMP-0063, 0064 ↔ 0055)"""
    for aid, code in ((20, "TMP-0055"), (21, "TMP-0063"), (22, "TMP-0064")):
        db.execute("INSERT INTO assets (id, temp_code, name, status) VALUES (?, ?, 'เก้าอี้อเนกประสงค์', 'ใช้งาน')", (aid, code))
    db.commit()
    for aid in (21, 22):
        r = admin.post(f"/assets/{aid}/duplicate", data={"target": " TMP-0055 ", "reason": "ลงซ้ำจากชีต (ลงแล้ว)"})
        assert "ตั้งเป็นรายการซ้ำของ TMP-0055 แล้ว" in r.text
    assert [r[0] for r in db.execute("SELECT duplicate_of_id FROM assets WHERE id IN (21, 22)")] == [20, 20]
    html = admin.get("/assets/20").text
    assert 'href="/assets/21"' in html and 'href="/assets/22"' in html
    assert "เป็นรายการซ้ำอยู่แล้ว" in admin.post("/assets/2/duplicate", data={"target": "TMP-0063", "reason": "x"}).text
