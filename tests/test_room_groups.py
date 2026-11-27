"""0.8.0 ส่วนแรก: ค้นหา/ตัวกรองหน้าจัดห้อง, กลุ่มที่ระบบเสนอ, ตาราง "ข้อความเดิม → ห้อง", รวมห้อง และหน้าตั้งค่าจัดห้อง"""
import io
import json
from urllib.parse import parse_qs, urlparse

from openpyxl import load_workbook

from app import flags, places, room_groups
from app.routers.locations import mapping_rows
from tests.conftest import add_location, asset_form, audit_rows, login

ABBR = room_groups.DEFAULT_ABBREVIATIONS
ROLES = room_groups.DEFAULT_ROLE_WORDS
PLACE = room_groups.DEFAULT_PLACE_WORDS


def key(text):
    return room_groups.group_key(text, ABBR)


def add_asset(db, aid, text, name="ของทดสอบ", type_code="1401", location_id=None, reg_no=None):
    db.execute("""INSERT INTO assets (id, reg_no, temp_code, type_code, name, location_text, location_id, status)
                  VALUES (?, ?, ?, ?, ?, ?, ?, 'ใช้งาน')""",
               (aid, reg_no, None if reg_no else f"TMP-{aid:04d}", type_code, name, text, location_id))
    db.commit()


def add_rule(db, match_text, room, exact=0, holder=None, note=None):
    db.execute("INSERT INTO location_text_rules (match_text, exact, room_name, holder_hint, note) VALUES (?, ?, ?, ?, ?)",
               (match_text, exact, room, holder, note))
    db.commit()


def groups_of(db):
    return room_groups.build_groups(db, mapping_rows(db))


def group_with(groups, text):
    return next(g for g in groups if any(t["row"]["text"] == text for t in g["texts"]))


def text_in(group, text):
    return next(t for t in group["texts"] if t["row"]["text"] == text)


# ---------------------------------------------------------------- normalize

def test_brackets_are_kept_so_back_room_does_not_collide():
    """“ห้องมั่นคง (ชั้นบน)” คือห้องเก็บเอกสาร ต้องไม่ถูก normalize จนชนกับ “ห้องมั่นคง”"""
    assert key("ห้องมั่นคง (ชั้นบน)") == "มั่นคง(ชั้นบน)"
    assert key("ห้องมั่นคง (ชั้นบน)") != key("ห้องมั่นคง") == key("มค.") == "มั่นคง"


def test_normalize_spaces_dots_slash_names_duplicates_and_room_words():
    assert key("ห้องปกครอง") == key("ห้องปกครอง อ.") == key("ปค.") == key("ปค./พี่เอ") == \
        key("ห้อง ปกครองอำเภอ /นายทดลอง") == "ปกครอง"
    assert key("กองร้อย อส.  กองร้อย อส.  กองร้อย อส.") == key("กองร้อย อส.") == "กองร้อยอาสารักษาดินแดน"
    assert key("ห้องสำนักงานอำเภอ ป. สมมติ ตัวอย่าง") == key("ห้อง สนง.") == "สำนักงาน"
    assert key("หัองทะเบียน") == "ทะเบียน"                        # หัอง = ห้อง (คำสะกดผิด)
    assert key("ห้องปลัด  อาวุธโส") == key("ห้องปลัดอาวุโส")       # อาวุธโส = อาวุโส


def test_word_end_abbreviation_only_at_end_of_word():
    assert key("ห้องเสมียนตรา อ.") == key("ห้องเสมียนตรา") == "เสมียนตรา"
    assert key("ห้องประชุมอ. ชั้น1") == key("ห้องประชุม ชั้น 1") == "ประชุมชั้น1"
    assert key("ป.อาวุโส") == "ปอาวุโส"                        # “อ” ใน “ป.อาวุโส” ไม่ใช่ อ. ท้ายคำ


def test_ambiguous_abbreviation_follows_other_texts():
    """“ทะเบียนอ.” อ่านได้ทั้ง ทะเบียน+อ. และ ทะเบีย+นอ. ให้เลือกแบบที่ตรงกับข้อความอื่น"""
    keys = room_groups.resolve_keys(["ห้องการเงินอ.", "ห้องการเงิน", "บ้านพัก นอ.", "บ้านพัก นายอำเภอ"], ABBR)
    assert keys["ห้องการเงินอ."] == keys["ห้องการเงิน"] == "การเงิน"
    assert keys["บ้านพัก นอ."] == keys["บ้านพัก นายอำเภอ"] == "บ้านพักนาย"


def test_role_like_texts():
    role = lambda t: room_groups.looks_like_role(t, ABBR, ROLES, PLACE)  # noqa: E731
    assert role("นายอำเภอ") and role("นอ.") and role("ป.อาวุโส") and role("ป.ทะเบียน")
    assert role("ประจำตำแหน่ง ปลัดอำเภอ")
    assert not role("ห้องนายอำเภอ") and not role("ห้อง ป.อาวุโส") and not role("บ้านพัก นอ.")
    assert not role("ปค.") and not role("ทะเบียน") and not role("หอประชุม")


def test_abbreviation_text_round_trip_and_errors():
    text = room_groups.abbr_to_text(ABBR)
    assert "อ. = อำเภอ (ท้ายคำ)" in text
    items, errors = room_groups.parse_abbr_text(text)
    assert items == ABBR and errors == []
    items, errors = room_groups.parse_abbr_text("ปค. = ปกครอง\nไม่มีเครื่องหมาย\nปค. = ซ้ำ\nก = ก")
    assert len(items) == 1 and len(errors) == 3


# ---------------------------------------------------------------- ตาราง "ข้อความเดิม → ห้อง"

def test_rules_exact_beats_normalized(db):
    add_rule(db, "ป.อาวุโส", "ประจำตำแหน่ง/ติดตัวบุคคล", exact=1, holder="ปลัดอาวุโส")
    add_rule(db, "ห้อง ป.อาวุโส", "ห้องปกครอง", holder="ปลัดอาวุโส")
    add_rule(db, "ห้องประชุม อ.", "หอประชุม", exact=1)
    add_rule(db, "ห้องประชุม", "ห้องประชุม ชั้น 1")
    rules = room_groups.rules(db)
    rule = lambda t: room_groups.match_rule(t, key(t), rules, ABBR)["room_name"]  # noqa: E731
    assert rule("ป.อาวุโส") == "ประจำตำแหน่ง/ติดตัวบุคคล"
    assert rule("ห้อง ป.อาวุโส") == "ห้องปกครอง"
    # “ห้องประชุม อ.” หลัง normalize ชนกับ “ห้องประชุม” แต่กฎตรงทุกตัวอักษรต้องชนะ
    assert key("ห้องประชุม อ.") == key("ห้องประชุม")
    assert rule("ห้องประชุม อ.") == "หอประชุม"
    assert rule("ห้องประชุม") == rule("ห้อง ประชุมอำเภอ") == "ห้องประชุม ชั้น 1"
    assert room_groups.match_rule("ห้องประชุม  อำเภอ ", key("ห้องประชุม  อำเภอ"), rules, ABBR)["room_name"] == \
        "ห้องประชุม ชั้น 1"  # ไม่ตรงทุกตัวอักษร (เว้นวรรค 2 ช่อง) จึงใช้กฎแบบ normalize


def test_inactive_rule_is_ignored(db):
    add_rule(db, "ห้องประชุม", "ห้องประชุม ชั้น 1")
    db.execute("UPDATE location_text_rules SET active = 0")
    db.commit()
    assert room_groups.match_rule("ห้องประชุม", key("ห้องประชุม"), room_groups.rules(db), ABBR) is None


# ---------------------------------------------------------------- กลุ่มที่เสนอ

def seed_texts(db):
    pk = add_location(db, "ห้องปกครอง")
    other = add_location(db, "ห้องอื่น")
    db.execute("UPDATE assets SET location_id = ? WHERE id IN (1, 6)", (pk,))  # “ห้องปกครอง” 2 ชิ้นจัดแล้ว
    db.commit()
    add_asset(db, 10, "ปค.", name="เครื่องพิมพ์เลเซอร์ ขาวดำ")
    add_asset(db, 11, "ห้อง ปกครองอำเภอ", name="โต๊ะทำงานเหล็กขนาดยาวมากกว่าสี่สิบตัวอักษรแน่นอนเลย")
    add_asset(db, 12, "ห้องปกครอง อ.", location_id=other)
    add_asset(db, 13, "นายอำเภอ", name="โทรศัพท์")
    add_asset(db, 14, "นอ.", name="แท็บเล็ต")
    add_asset(db, 15, "ห้องนายอำเภอ", name="โซฟา")
    add_asset(db, 16, "ห้องมั่นคง (ชั้นบน)", name="แอร์")
    add_asset(db, 17, "ห้องมั่นคง", name="สว่าน")
    add_asset(db, 18, "มค.", name="สว่าน 2")
    return pk, other


def test_groups_default_ticks_notes_and_samples(db):
    pk, other = seed_texts(db)
    groups = groups_of(db)
    g = group_with(groups, "ปค.")
    assert g["kind"] == "guess" and g["target_id"] == pk and g["target_name"] == "ห้องปกครอง"
    assert {t["row"]["text"] for t in g["texts"]} == {"ห้องปกครอง", "ปค.", "ห้อง ปกครองอำเภอ", "ห้องปกครอง อ."}
    done = text_in(g, "ห้องปกครอง")
    assert done["locked"] and not done["checked"] and ("ok", "อยู่ห้องนี้แล้ว") in done["notes"]
    assert text_in(g, "ปค.")["checked"]
    moved = text_in(g, "ห้องปกครอง อ.")  # จัดไว้ห้องอื่นแล้ว: ไม่ติ๊กให้ พร้อมบอกห้อง
    assert not moved["checked"] and ("warn", "จัดไว้ที่ ห้องอื่น (1) แล้ว") in moved["notes"]
    sample = text_in(g, "ห้อง ปกครองอำเภอ")["row"]["samples"][0]
    assert sample.endswith("…") and len(sample) == 40  # ตัดชื่อให้สั้น
    assert g["n"] == 5 and g["n_todo"] == 3 and g["show"]

    g = group_with(groups, "นอ.")  # ห้องนายอำเภอ + นอ. + นายอำเภอ (ไม่มีกฎ)
    role = text_in(g, "นอ.")
    assert not role["checked"] and ("role", "อาจเป็นผู้รับผิดชอบ ไม่ใช่ห้อง") in role["notes"]
    assert text_in(g, "ห้องนายอำเภอ")["checked"]
    # “ห้องมั่นคง (ชั้นบน)” ไม่อยู่กลุ่มเดียวกับห้องมั่นคง
    assert "ห้องมั่นคง (ชั้นบน)" not in {t["row"]["text"] for t in group_with(groups, "มค.")["texts"]}


def test_rule_groups(db):
    seed_texts(db)
    add_rule(db, "นายอำเภอ", "ประจำตำแหน่ง/ติดตัวบุคคล", exact=1, holder="นายอำเภอ", note="ของประจำตำแหน่ง")
    add_rule(db, "ห้องมั่นคง (ชั้นบน)", "ห้องเก็บเอกสาร")
    groups = groups_of(db)
    g = group_with(groups, "นายอำเภอ")
    assert g["kind"] == "rule" and g["target_name"] == "ประจำตำแหน่ง/ติดตัวบุคคล" and g["target_id"] is None
    assert g["holder_hint"] == "นายอำเภอ" and g["holder_id"] is None
    assert [t["row"]["text"] for t in g["texts"]] == ["นายอำเภอ"]  # กลุ่มจากกฎแสดงแม้มีข้อความเดียว
    assert text_in(g, "นายอำเภอ")["checked"] and ("rule", "ตามข้อมูลที่ยืนยันแล้ว (ตรงทุกตัวอักษร)") in \
        text_in(g, "นายอำเภอ")["notes"]
    assert "นายอำเภอ" not in {t["row"]["text"] for t in group_with(groups, "ห้องนายอำเภอ")["texts"]}
    g = group_with(groups, "ห้องมั่นคง (ชั้นบน)")
    assert g["target_name"] == "ห้องเก็บเอกสาร" and g["kind"] == "rule"
    # ผู้รับผิดชอบที่มีอยู่แล้วตามตำแหน่ง
    sid = db.execute("INSERT INTO staff (nickname, position) VALUES ('ท่านนายอำเภอ', 'นายอำเภอ')").lastrowid
    db.commit()
    assert group_with(groups_of(db), "นายอำเภอ")["holder_id"] == sid


def test_rule_target_room_follows_merge(db):
    old = add_location(db, "ห้องประชุมเก่า")
    new = add_location(db, "ห้องประชุม ชั้น 1")
    db.execute("UPDATE locations SET active = 0, merged_into_id = ? WHERE id = ?", (new, old))
    db.commit()
    assert room_groups.room_by_name(db, "ห้องประชุมเก่า") == new


# ---------------------------------------------------------------- หน้าจัดห้อง: ค้นหาและตัวกรอง

def table(html):
    """เฉพาะตาราง “ทีละข้อความ” (ส่วนกลุ่มด้านบนแสดงทั้งกลุ่มเมื่อมีข้อความใดในกลุ่มตรงกับตัวกรอง)"""
    return html.split('<h3 class="section-title">ทีละข้อความ</h3>', 1)[1] if "ทีละข้อความ</h3>" in html else ""


def quoted(text):
    return f'<span class="map-quote">{text}</span>'


def test_mapping_search_status_and_category_filters(editor, db):
    seed_texts(db)
    html = editor.get("/places/mapping?q=" + "ปค").text
    assert quoted("ปค.") in table(html) and quoted("ห้องนายอำเภอ") not in html
    html = table(editor.get("/places/mapping?status=done").text)  # จัดครบ: ห้องปกครอง + ห้องปกครอง อ. (ห้องอื่น)
    assert quoted("ห้องปกครอง") in html and quoted("ห้องปกครอง อ.") in html and quoted("ปค.") not in html
    html = table(editor.get("/places/mapping?status=todo").text)
    assert quoted("ปค.") in html and quoted("ห้องปกครอง") not in html
    html = table(editor.get("/places/mapping?status=multi").text)
    assert "ต้องแยกรายชิ้น" in html and quoted("ปค.") not in html
    html = table(editor.get("/places/mapping?status=person").text)
    assert quoted("สนง./เอ็ม") in html and quoted("ปค.") not in html
    page = editor.get("/places/mapping?cat=13").text  # หมวดตู้: มีแค่ “ห้องปกครอง” (2 ชิ้น หมวดนี้ 2)
    html = table(page)
    assert quoted("ห้องปกครอง") in html and "หมวดนี้ 2" in html and quoted("ปค.") not in html
    assert "ตรงกับตัวกรอง 1 ข้อความ" in page and "ล้างตัวกรอง" in page


def test_mapping_search_accepts_thai_digits_and_old_todo_url(editor, db):
    add_asset(db, 20, "ห้องประชุม ชั้น 1")
    html = editor.get("/places/mapping?q=ชั้น ๑").text
    assert "ห้องประชุม ชั้น 1" in html
    html = editor.get("/places/mapping?todo=1").text
    assert 'value="todo" selected' in html


def test_mapping_htmx_returns_results_and_clean_url(editor, db):
    r = editor.get("/places/mapping?q=ปกครอง&status=&cat=", headers={"HX-Request": "true", "HX-Filter-History": "replace"})
    assert r.status_code == 200 and 'id="mapping-results"' in r.text
    assert "<html" not in r.text and 'id="map-filters"' not in r.text
    assert parse_qs(urlparse(r.headers["HX-Replace-Url"]).query) == {"q": ["ปกครอง"]}


def test_row_save_keeps_filters(editor, db):
    lid = add_location(db, "ห้องทะเบียนจริง")
    r = editor.post("/locations/mapping", data={"text": "ห้องทะเบียน", "location": str(lid), "back": "q=ทะเบียน&status=todo"},
                    follow_redirects=False)
    assert r.status_code == 303
    url = urlparse(r.headers["location"])
    assert url.path == "/places/mapping" and parse_qs(url.query) == {"q": ["ทะเบียน"], "status": ["todo"]}
    assert url.fragment == places.text_key("ห้องทะเบียน")
    # htmx: ตอบแถวเดียว ไม่ redirect และแถวยังมีค่าตัวกรองไว้ส่งต่อ
    r = editor.post("/locations/mapping", data={"text": "ห้องทะเบียน", "location": str(lid), "back": "cat=14"},
                    headers={"HX-Request": "true"})
    assert r.status_code == 200 and 'name="back" value="cat=14"' in r.text and "หมวดนี้ 1" in r.text


def test_mapping_page_needs_editor(client):
    assert client.get("/places/mapping", follow_redirects=False).status_code in (303, 401, 403)


# ---------------------------------------------------------------- จัดทั้งกลุ่ม

def test_save_group_applies_only_ticked_texts(editor, db):
    pk, other = seed_texts(db)
    r = editor.post("/places/mapping/group", data={
        "texts": ["ปค.", "ห้อง ปกครองอำเภอ"], "location": str(pk), "group": "g-abc", "back": "status=todo"},
        follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/places/mapping?status=todo#g-abc"
    loc = lambda aid: db.execute("SELECT location_id FROM assets WHERE id = ?", (aid,)).fetchone()[0]  # noqa: E731
    assert loc(10) == loc(11) == pk and loc(12) == other  # ข้อความที่ไม่ได้ติ๊กไม่เปลี่ยน
    moves = db.execute("SELECT reason, note FROM asset_moves WHERE asset_id = 10").fetchall()
    assert [m["reason"] for m in moves] == [places.REASON_FIRST] and "“ปค.”" in moves[0]["note"]
    assert json.loads(audit_rows(db, record_id=10)[-1]["changes"])["from_location_text"] == "ปค."


def test_save_group_creates_room_and_holder_with_position(editor, db):
    seed_texts(db)
    r = editor.post("/places/mapping/group", data={
        "texts": ["นายอำเภอ"], "location": "__new__", "new_location": "ประจำตำแหน่ง/ติดตัวบุคคล",
        "holder": "__new__", "new_nickname": "นายอำเภอ", "new_position": "นายอำเภอ"}, follow_redirects=False)
    assert r.status_code == 303
    a = db.execute("""SELECT l.name, s.nickname, s.position FROM assets a JOIN locations l ON l.id = a.location_id
                      JOIN staff s ON s.id = a.holder_id WHERE a.id = 13""").fetchone()
    assert tuple(a) == ("ประจำตำแหน่ง/ติดตัวบุคคล", "นายอำเภอ", "นายอำเภอ")


def test_save_group_rejects_multi_room_and_empty(editor, db):
    from tests.conftest import MULTI_ROOM
    lid = add_location(db, "ห้องใดก็ได้")
    editor.post("/places/mapping/group", data={"texts": [MULTI_ROOM], "location": str(lid)})
    assert db.execute("SELECT COUNT(*) FROM assets WHERE location_id = ?", (lid,)).fetchone()[0] == 0
    assert "ติ๊กเลือกข้อความอย่างน้อย 1 ข้อความ" in editor.post("/places/mapping/group", data={"location": str(lid)}).text


def test_groups_page_and_excel_export(editor, db):
    seed_texts(db)
    html = editor.get("/places/mapping").text
    assert "กลุ่มที่น่าจะเป็นห้องเดียวกัน" in html and "อาจเป็นผู้รับผิดชอบ ไม่ใช่ห้อง" in html
    assert "จัดทั้งกลุ่มเข้าห้องนี้" in html and "เครื่องพิมพ์เลเซอร์ ขาวดำ" in html
    r = editor.get("/places/mapping/groups.xlsx")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(r.content)).active
    cells = [c.value for row in ws.iter_rows() for c in row if c.value is not None]
    assert "ตัวอย่างรายการ (3 รายการแรก)" in cells and "ปค." in cells
    assert any(isinstance(c, str) and "เครื่องพิมพ์เลเซอร์ ขาวดำ" in c for c in cells)
    assert any(isinstance(c, str) and "อาจเป็นผู้รับผิดชอบ" in c for c in cells)


# ---------------------------------------------------------------- รวมห้อง

def rooms_with_assets(db):
    a = add_location(db, "ห้องประชุมใหญ่")
    b = add_location(db, "ห้องประชุมชั้นสอง")
    db.execute("UPDATE assets SET location_id = ? WHERE id IN (1, 2)", (a,))
    db.execute("UPDATE assets SET location_id = ? WHERE id = 3", (b,))
    db.commit()
    return a, b


def test_merge_moves_assets_and_keeps_history(editor, db):
    a, b = rooms_with_assets(db)
    page = editor.get(f"/places/merge?ids={a}&ids={b}")
    assert page.status_code == 200 and "ห้องที่จะเก็บไว้" in page.text
    r = editor.post("/places/merge", data={"ids": [str(a), str(b)], "keep": str(a)}, follow_redirects=False)
    assert r.status_code == 303
    assert db.execute("SELECT location_id FROM assets WHERE id = 3").fetchone()[0] == a
    room_b = db.execute("SELECT active, merged_into_id, name FROM locations WHERE id = ?", (b,)).fetchone()
    assert tuple(room_b) == (0, a, "ห้องประชุมชั้นสอง")  # ไม่ลบ ไม่เปลี่ยนชื่อ
    move = db.execute("SELECT * FROM asset_moves WHERE asset_id = 3").fetchone()
    assert move["reason"] == places.REASON_MERGE and move["from_location_id"] == b
    assert places.REASON_MERGE in places.NOT_REAL_MOVE_REASONS and places.REASON_MERGE not in places.REAL_MOVE_REASONS
    assert places.REASON_FIRST in places.NOT_REAL_MOVE_REASONS
    assert json.loads(audit_rows(db, "locations", b)[-1]["changes"])["merged_into_id"] == {"old": None, "new": a}
    # หน้ารายการ: แสดงว่าถูกรวม มีปุ่มยกเลิก ห้องที่ถูกรวมไม่มีให้เลือกในหน้าจัดห้อง
    html = editor.get("/places").text
    assert "ถูกรวมแล้ว" in html and "ยกเลิกการรวม" in html
    assert b not in [r["id"] for r in places.location_options(db)]
    # เปิดใช้งานเองไม่ได้
    editor.post(f"/locations/{b}/active", data={"active": "1"})
    assert db.execute("SELECT active FROM locations WHERE id = ?", (b,)).fetchone()[0] == 0
    # ประวัติของชิ้นที่ 3 ยังอ่านชื่อห้องเดิมได้
    assert "ห้องประชุมชั้นสอง" in editor.get("/assets/3").text


def test_merge_undo_restores_rooms_and_assets(editor, db):
    a, b = rooms_with_assets(db)
    db.execute("UPDATE assets SET location_id = ? WHERE id = 4", (b,))  # ห้อง b มี 2 ชิ้น (#3, #4)
    db.commit()
    editor.post("/places/merge", data={"ids": [str(a), str(b)], "keep": str(a)})
    mid = db.execute("SELECT id FROM location_merges").fetchone()[0]
    db.execute("UPDATE assets SET location_id = NULL WHERE id = 4")  # ถูกย้ายออกหลังรวม: ยกเลิกแล้วต้องไม่ถูกแตะ
    db.commit()
    r = editor.post(f"/places/merges/{mid}/undo")
    assert "คืนครุภัณฑ์ 1 ชิ้นกลับห้องเดิม (ข้าม 1 ชิ้นที่ถูกย้ายไปห้องอื่นหลังรวม)" in r.text
    assert db.execute("SELECT location_id FROM assets WHERE id = 3").fetchone()[0] == b
    assert db.execute("SELECT location_id FROM assets WHERE id = 1").fetchone()[0] == a  # ของห้องที่เก็บไว้เดิมไม่ย้าย
    assert db.execute("SELECT location_id FROM assets WHERE id = 4").fetchone()[0] is None
    assert tuple(db.execute("SELECT active, merged_into_id FROM locations WHERE id = ?", (b,)).fetchone()) == (1, None)
    assert db.execute("SELECT reason FROM asset_moves WHERE asset_id = 3 ORDER BY id DESC").fetchone()[0] == \
        places.REASON_MERGE_UNDO
    assert db.execute("SELECT undone_at IS NOT NULL FROM location_merges WHERE id = ?", (mid,)).fetchone()[0] == 1
    assert "ยกเลิกการรวมนี้ไปแล้ว" in editor.post(f"/places/merges/{mid}/undo").text  # ซ้ำไม่ได้


def test_merge_undo_only_within_one_day(editor, db):
    a, b = rooms_with_assets(db)
    editor.post("/places/merge", data={"ids": [str(a), str(b)], "keep": str(a)})
    db.execute("UPDATE location_merges SET merged_at = datetime('now', '-25 hours')")
    db.commit()
    assert "ยกเลิกการรวม" not in editor.get("/places").text.split("ห้อง / หน่วยงาน")[1].split("เพิ่มห้องใหม่")[0]
    assert "เกิน 1 วันแล้ว" in editor.post("/places/merges/1/undo").text
    assert db.execute("SELECT location_id FROM assets WHERE id = 3").fetchone()[0] == a


def test_merge_blocked_while_inventory_round_open(editor, db):
    a, b = rooms_with_assets(db)
    db.execute("INSERT INTO inventory_checks (fiscal_year, started_at) VALUES (2570, '2026-11-01')")
    db.commit()
    assert "รวมห้องหรือยกเลิกการรวมไม่ได้" in editor.get(f"/places/merge?ids={a}&ids={b}").text
    assert "ปีงบประมาณ 2570 เปิดอยู่" in editor.post("/places/merge", data={"ids": [str(a), str(b)], "keep": str(a)}).text
    assert db.execute("SELECT location_id FROM assets WHERE id = 3").fetchone()[0] == b
    assert db.execute("SELECT COUNT(*) FROM location_merges").fetchone()[0] == 0


def test_merge_needs_two_rooms_and_active_keep(editor, db):
    a, b = rooms_with_assets(db)
    r = editor.get(f"/places/merge?ids={a}")
    assert r.url.path == "/places" and "อย่างน้อย 2 ห้อง" in r.text
    db.execute("UPDATE locations SET active = 0 WHERE id = ?", (b,))
    db.commit()
    assert "ต้องเป็นห้องที่ใช้งานอยู่" in editor.post("/places/merge", data={"ids": [str(a), str(b)], "keep": str(b)}).text


def test_viewer_cannot_merge(client, db):
    a, b = rooms_with_assets(db)
    login(client, "viewer1")
    assert client.post("/places/merge", data={"ids": [str(a), str(b)], "keep": str(a)}).status_code == 403


# ---------------------------------------------------------------- แท็บห้อง: ค้นหาและตัวกรอง

def test_rooms_tab_filters(editor, db):
    a, b = rooms_with_assets(db)
    add_location(db, "ห้องเก็บของ", active=0)
    html = editor.get("/places?q=ประชุม").text
    assert "ห้องประชุมใหญ่" in html and "ห้องเก็บของ" not in html
    html = editor.get("/places?status=inactive").text
    assert "ห้องเก็บของ" in html and "ห้องประชุมใหญ่" not in html
    html = editor.get("/places?cat=13").text  # ตู้: ห้องประชุมใหญ่มี 1 ชิ้น (#1)
    assert "ห้องประชุมใหญ่" in html and "หมวดนี้ 1" in html and "ห้องประชุมชั้นสอง" not in html
    editor.post("/places/merge", data={"ids": [str(a), str(b)], "keep": str(a)})
    html = editor.get("/places?status=merged").text
    assert "ห้องประชุมชั้นสอง" in html and "เข้ากับ “ห้องประชุมใหญ่”" in html
    r = editor.get("/places?q=ใหญ่", headers={"HX-Request": "true", "HX-Filter-History": "replace"})
    assert 'id="rooms-results"' in r.text and "<html" not in r.text and r.headers["HX-Replace-Url"].startswith("/places?q=")


# ---------------------------------------------------------------- หน้าตั้งค่าจัดห้อง (admin)

def test_room_settings_admin_only(editor, client):
    assert editor.get("/settings/rooms", follow_redirects=False).status_code == 403


def test_room_settings_save_lists_with_audit(admin, db):
    html = admin.get("/settings/rooms").text
    assert "อ. = อำเภอ (ท้ายคำ)" in html and "ข้อความเดิม → ห้องที่ถูกต้อง" in html
    r = admin.post("/settings/rooms/lists", data={"abbr": "ปค. = ปกครอง\nอ. = อำเภอ (ท้ายคำ)",
                                                  "roles": "นายอำเภอ\n\nนายอำเภอ\nป.", "places_text": "ห้อง"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert room_groups.abbreviations(db) == [{"short": "ปค.", "full": "ปกครอง", "word_end": False},
                                             {"short": "อ.", "full": "อำเภอ", "word_end": True}]
    assert room_groups.role_words(db) == ["นายอำเภอ", "ป."] and room_groups.place_words(db) == ["ห้อง"]
    changes = json.loads(audit_rows(db, "app_settings", 0)[-1]["changes"])
    assert set(changes) == {room_groups.ABBR_KEY, room_groups.ROLE_KEY, room_groups.PLACE_KEY}
    r = admin.post("/settings/rooms/lists", data={"abbr": "ผิดรูปแบบ", "roles": "", "places_text": ""})
    assert r.status_code == 400 and "ตัวย่อ = คำเต็ม" in r.text
    assert len(room_groups.abbreviations(db)) == 2  # ไม่บันทึก


def test_room_settings_rules_add_edit_disable(admin, db):
    admin.post("/settings/rooms/rules", data={"match_text": "ห้องมั่นคง (ชั้นบน)", "room_name": "ห้องเก็บเอกสาร",
                                               "note": "ทางเข้าเดียวกัน"})
    rule = db.execute("SELECT * FROM location_text_rules").fetchone()
    assert (rule["match_text"], rule["exact"], rule["room_name"], rule["active"]) == ("ห้องมั่นคง (ชั้นบน)", 0, "ห้องเก็บเอกสาร", 1)
    assert "มีข้อความ" in admin.post("/settings/rooms/rules", data={"match_text": "ห้องมั่นคง (ชั้นบน)", "room_name": "ซ้ำ"}).text
    admin.post(f"/settings/rooms/rules/{rule['id']}", data={"match_text": "ห้องมั่นคง (ชั้นบน)", "exact": "1",
                                                           "room_name": "ห้องเก็บเอกสาร", "note": "ทางเข้าเดียวกัน"})
    rule = db.execute("SELECT * FROM location_text_rules").fetchone()
    assert rule["exact"] == 1 and rule["active"] == 0  # ไม่ได้ติ๊ก “ใช้งาน” = ปิด
    actions = [r["action"] for r in audit_rows(db, "location_text_rules", rule["id"])]
    assert actions == ["insert", "update"]


# ---------------------------------------------------------------- ชื่อรายการอาจเพี้ยน

def test_name_flag_clears_when_name_edited(editor, db):
    db.execute("UPDATE assets SET name = 'จำนวน 4 ตัว', needs_review = ? WHERE id = 1",
               (f"{flags.NO_PRICE}, {flags.NAME_SUSPECT}",))
    db.commit()
    assert "ชื่อรายการอาจเพี้ยน" in editor.get("/review?group=name").text
    editor.post("/assets/1/edit", data=asset_form(name="ไมค์ผู้ร่วมประชุม"))
    assert db.execute("SELECT needs_review FROM assets WHERE id = 1").fetchone()[0] == flags.NO_PRICE
