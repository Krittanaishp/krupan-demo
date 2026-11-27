"""
สร้างฐานข้อมูล demo พร้อมข้อมูลสมมติทั้งหมด (ไม่มีข้อมูลจริงของหน่วยงานใด) ด้วยคำสั่งเดียว

    uv run python scripts/seed_demo.py           สร้าง data/krupan.db (ถ้ามีอยู่แล้วจะหยุด)
    uv run python scripts/seed_demo.py --reset   ลบ db demo เดิมแล้วสร้างใหม่
    uv run python scripts/seed_demo.py --production
                                                  เหมือนเดิม แต่ทุกบัญชีต้องเปลี่ยนรหัสผ่านตอนล็อกอินครั้งแรก
                                                  (ใช้เมื่อจะเปิดให้คนอื่นใช้จริง รหัสผ่าน demo อยู่ใน README สาธารณะ)
    (ตั้ง KRUPAN_DB เพื่อสร้างที่ไฟล์อื่น)

สิ่งที่สร้าง:
- ไฟล์ .env (ถ้ายังไม่มี) พร้อม KRUPAN_SECRET_KEY ที่สุ่มใหม่ทุกเครื่อง
- ชื่อหน่วยงาน "ที่ว่าการอำเภอตัวอย่าง" (ตั้งผ่านค่าตั้งค่า ไม่ได้ฝังในโค้ด)
- ผู้ใช้ demo 3 บัญชี (admin / editor / viewer) รหัสผ่านดูใน README.md
- ครุภัณฑ์ 72 ชิ้น 6 หมวด ห้อง อาคาร ฝ่าย และเจ้าหน้าที่สมมติ
- วัสดุ 20 รายการ 6 หมวด ยอดยกมา ใบรับ/ใบเบิกย้อนหลังประมาณ 3 เดือน (มีใบที่ยกเลิก)
ข้อมูลสุ่มแบบกำหนด seed: รันกี่ครั้งก็ได้ชุดเดิม (วันที่เลื่อนตามวันที่รัน)
"""
import argparse
import os
import random
import secrets
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

ORG_DISPLAY = "ที่ว่าการอำเภอตัวอย่าง"
ORG_REPORT = "ที่ทำการปกครองอำเภอตัวอย่าง"
USERS = [  # (username, รหัสผ่าน demo, role, ชื่อที่แสดง)
    ("admin", "demo-admin-2569", "admin", "ผู้ดูแลระบบ (ตัวอย่าง)"),
    ("editor", "demo-editor-2569", "editor", "เจ้าหน้าที่พัสดุ (ตัวอย่าง)"),
    ("viewer", "demo-viewer-2569", "viewer", "ผู้บริหาร (ตัวอย่าง)"),
]
STAFF = [  # ชื่อสมมติทั้งหมด
    ("นายสมมติ ใจดี", "สมมติ", "ปลัดอำเภอ (หัวหน้าฝ่ายบริหารงานปกครอง)"),
    ("นางสาวตัวอย่าง รักงาน", "ตัวอย่าง", "เจ้าพนักงานธุรการ"),
    ("นายทดสอบ มั่นคง", "ทดสอบ", "เจ้าพนักงานปกครอง"),
    ("นางสมหญิง ตัวอย่าง", "หญิง", "นักวิชาการเงินและบัญชี"),
    ("นายใจเย็น มีสุข", "เย็น", "เจ้าพนักงานพัสดุ"),
]
BUILDINGS = ["อาคารสำนักงาน ชั้น 1", "อาคารสำนักงาน ชั้น 2", "อาคารอเนกประสงค์"]
DEPARTMENTS = ["งานบริหารทั่วไป", "งานทะเบียนและบัตร", "งานปกครอง", "งานการเงินและพัสดุ", "งานป้องกันภัย", "ศูนย์บริการประชาชน"]
ROOMS = [  # (ชื่อห้อง, อาคาร, ฝ่าย)
    ("ห้องธุรการ", 0, 0), ("ห้องทะเบียนราษฎร", 0, 1), ("ห้องบัตรประชาชน", 0, 1), ("ห้องศูนย์บริการ", 0, 5),
    ("ห้องงานปกครอง", 1, 2), ("ห้องการเงิน", 1, 3), ("ห้องผู้บริหาร", 1, 0), ("ห้องประชุมเล็ก", 1, 0),
    ("ห้องประชุมใหญ่", 2, 0), ("ห้องเก็บพัสดุ", 2, 3), ("ห้องปฏิบัติการป้องกันภัย", 2, 4),
]
CATEGORIES = [("12", "ครุภัณฑ์คอมพิวเตอร์"), ("13", "ตู้"), ("14", "โต๊ะ-เก้าอี้"), ("15", "ครุภัณฑ์ไฟฟ้าและวิทยุ"),
              ("16", "ครุภัณฑ์โฆษณาและเผยแพร่"), ("17", "ครุภัณฑ์งานบ้านงานครัว")]
TYPES = [  # (รหัสประเภท, หมวด, ชื่อ, ราคาโดยประมาณ, จำนวนชิ้น, รายละเอียดตัวอย่าง)
    ("1201", "12", "เครื่องคอมพิวเตอร์ตั้งโต๊ะ", 22000, 9, "ยี่ห้อตัวอย่าง รุ่น PC-100"),
    ("1202", "12", "เครื่องคอมพิวเตอร์โน้ตบุ๊ก", 24000, 4, "ยี่ห้อตัวอย่าง รุ่น NB-14"),
    ("1203", "12", "เครื่องพิมพ์เลเซอร์", 7900, 6, "ยี่ห้อตัวอย่าง รุ่น LP-20"),
    ("1204", "12", "เครื่องสำรองไฟฟ้า", 2500, 6, "800VA"),
    ("1301", "13", "ตู้เหล็ก 2 บาน", 5500, 6, "บานเปิด สีเทา"),
    ("1302", "13", "ตู้เหล็ก 4 ลิ้นชัก", 5000, 5, None),
    ("1401", "14", "โต๊ะทำงานเหล็ก", 4500, 8, "ขนาด 5 ฟุต"),
    ("1402", "14", "เก้าอี้สำนักงาน", 1500, 12, "มีล้อ ปรับระดับได้"),
    ("1501", "15", "เครื่องปรับอากาศ", 28000, 5, "ขนาด 24,000 BTU"),
    ("1502", "15", "พัดลมตั้งพื้น", 1200, 4, "ขนาด 18 นิ้ว"),
    ("1601", "16", "เครื่องฉายภาพโปรเจกเตอร์", 18000, 2, None),
    ("1602", "16", "กล้องถ่ายภาพดิจิทัล", 15000, 1, None),
    ("1701", "17", "ตู้เย็น", 9000, 2, "ขนาด 7 คิว"),
    ("1702", "17", "เครื่องทำน้ำร้อน-น้ำเย็น", 6500, 2, None),
]
SUPPLIES = [  # แบบฟอร์มนำเข้า: หมวด, ชื่อ, หน่วยหลัก, เตือนเมื่อต่ำกว่า, หน่วยใหญ่ 1, ตัวคูณ, หน่วยใหญ่ 2, ตัวคูณ, ยอดยกมา, หมายเหตุ
    ["กระดาษ", "กระดาษ A4 80 แกรม", "รีม", 10, "กล่อง", 5, None, None, 60, None],
    ["กระดาษ", "กระดาษ A4 70 แกรม", "รีม", 10, "กล่อง", 5, None, None, 35, None],
    ["กระดาษ", "กระดาษโน้ตกาวในตัว", "เล่ม", 10, "แพ็ก", 12, None, None, 30, None],
    ["เครื่องเขียน", "ปากกาลูกลื่น สีน้ำเงิน", "ด้าม", 20, "กล่อง", 50, None, None, 150, None],
    ["เครื่องเขียน", "ปากกาลูกลื่น สีแดง", "ด้าม", 10, "กล่อง", 50, None, None, 40, None],
    ["เครื่องเขียน", "ดินสอดำ 2B", "แท่ง", 12, "กล่อง", 12, None, None, 48, None],
    ["เครื่องเขียน", "ปากกาเน้นข้อความ", "ด้าม", 6, "แพ็ก", 10, None, None, 20, None],
    ["เครื่องเขียน", "น้ำยาลบคำผิดแบบเทป", "อัน", 5, None, None, None, None, 7, None],
    ["หมึกพิมพ์", "ตลับหมึกเลเซอร์ LP-20 (สีดำ)", "ตลับ", 2, None, None, None, None, 6, "ใช้กับเครื่องพิมพ์รุ่นตัวอย่าง LP-20"],
    ["หมึกพิมพ์", "หมึกเติมอิงค์เจ็ท (สีดำ)", "ขวด", 2, "แพ็ก", 4, None, None, 5, None],
    ["หมึกพิมพ์", "หมึกเติมอิงค์เจ็ท (สีฟ้า)", "ขวด", 1, None, None, None, None, 0, None],
    ["แฟ้มและซอง", "แฟ้มสันกว้าง 3 นิ้ว", "แฟ้ม", 10, "โหล", 12, None, None, 30, None],
    ["แฟ้มและซอง", "ซองน้ำตาล A4", "ซอง", 50, "แพ็ก", 50, None, None, 200, None],
    ["แฟ้มและซอง", "ซองขาวยาว", "ซอง", 100, "กล่อง", 500, None, None, 400, None],
    ["อุปกรณ์สำนักงาน", "ลวดเย็บกระดาษ เบอร์ 35", "กล่อง", 10, "แพ็ก", 24, None, None, 40, None],
    ["อุปกรณ์สำนักงาน", "คลิปดำ ขนาดกลาง", "กล่อง", 4, None, None, None, None, 6, None],
    ["อุปกรณ์สำนักงาน", "เทปใส 1 นิ้ว", "ม้วน", 6, "แถว", 6, None, None, 18, None],
    ["อุปกรณ์สำนักงาน", "ถ่าน AA", "ก้อน", 8, "แพ็ก", 4, None, None, 12, None],
    ["ของใช้ทำความสะอาด", "กระดาษชำระ", "ม้วน", 24, "แพ็ก", 6, "ลัง", 48, 96, None],
    ["ของใช้ทำความสะอาด", "น้ำยาทำความสะอาดพื้น", "ขวด", 3, None, None, None, None, 4, None],
]
REQUESTERS = ["นางสาวทดลอง ใช้ระบบ", "นายตัวอย่าง เบิกของ", "นางสาวสมมุติ งานดี", "นายทดสอบ ระบบดี", "นางตัวอย่าง ขยัน"]


def write_env():
    env = ROOT / ".env"
    if env.exists():
        return False
    env.write_text("# สร้างโดย scripts/seed_demo.py (ค่าสำหรับ demo เท่านั้น ไฟล์นี้ไม่ขึ้น git)\n"
                   f"KRUPAN_SECRET_KEY={secrets.token_urlsafe(48)}\n"
                   "BASE_URL=http://127.0.0.1:8000\n"
                   "QR_ENABLED=off\n"
                   "# demo ไม่มีงานสำรองข้อมูลตามเวลา จึงปิดแถบเตือนสำรองข้อมูล\n"
                   "BACKUP_WARNING=off\n", encoding="utf-8")
    return True


def thai_fiscal_year(d):
    return d.year + 543 + (1 if d.month >= 10 else 0)


def seed_assets(conn, rng, staff_ids, room_ids):
    conn.executemany("INSERT INTO categories (code, name) VALUES (?, ?)", CATEGORIES)
    conn.executemany("INSERT INTO asset_types (code, category_code, name, name_is_draft) VALUES (?, ?, ?, 0)",
                     [(t[0], t[1], t[2]) for t in TYPES])
    rooms = list(room_ids.items())
    n, temp = 0, 0
    for code, _, name, price, count, details in TYPES:
        for seq in range(1, count + 1):
            n += 1
            acquired = date(rng.randint(2015, 2025), rng.randint(1, 12), rng.randint(1, 28))
            precision = "month" if n % 11 == 0 else "day"
            unit_price = None if n % 13 == 0 else round(price * rng.uniform(0.85, 1.15), -1)
            room_name, room_id = rooms[(n * 7) % len(rooms)]
            if name.startswith("เครื่องคอมพิวเตอร์โน้ตบุ๊ก"):
                room_name, room_id = "ประจำตัวผู้ใช้", None
            status = "ชำรุด" if n % 17 == 0 else ("รอจำหน่าย" if n % 29 == 0 else "ใช้งาน")
            reg_no, temp_code = f"ปค.99-9999-1-{code}-{seq:04d}", None
            if n % 23 == 0:  # ยังไม่มีเลขทะเบียน ใช้รหัสชั่วคราว
                temp += 1
                reg_no, temp_code = None, f"TMP-{temp:04d}"
            review = ", ".join(x for x in ("ไม่มีราคา" if unit_price is None else "",
                                            "ไม่มีหมายเลขทะเบียน" if temp_code else "",
                                            "วันที่มีแค่เดือน/ปี" if precision == "month" else "") if x) or None
            holder = staff_ids[n % len(staff_ids)] if room_id is None or n % 5 == 0 else None
            conn.execute("""INSERT INTO assets (reg_no, temp_code, type_code, name, details, acquired_date, acquired_date_precision,
                                fiscal_year, unit_price, funding_source, location_id, location_text, holder_id, status, needs_review,
                                source_sheet, source_row)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ข้อมูลสมมติ', ?)""",
                         (reg_no, temp_code, code, name, details, acquired.isoformat(), precision, thai_fiscal_year(acquired),
                          unit_price, "เงินงบประมาณ" if n % 3 else "เงินบริจาค (ตัวอย่าง)", room_id, room_name, holder,
                          status, review, n + 5))
    return n


def seed_supplies(conn, rng, uid, dep_ids, staff_ids):
    from app import supplies as sp
    from app import supplies_import as si
    from app import timeutil
    from openpyxl import load_workbook
    from io import BytesIO
    today = timeutil.today_th()
    wb = load_workbook(BytesIO(si.template()))
    ws = wb[si.SHEET]
    for r in SUPPLIES:
        ws.append(r)
    out = BytesIO()
    wb.save(out)
    start = today - timedelta(days=95)
    si.apply(conn, uid, out.getvalue(), "ยอดยกมา_ข้อมูลสมมติ.xlsx", start, set())
    label = lambda name: sp.resolve_item(conn, name)["label"]  # noqa: E731
    big = lambda name: str(sp.resolve_item(conn, name)["units"][0]["id"])  # noqa: E731
    events = []
    for k, days in enumerate((80, 50, 20)):  # รับเข้าเดือนละครั้ง
        events.append((today - timedelta(days=days), "receipt", k))
    for days in range(88, 0, -6):  # เบิกประมาณสัปดาห์ละครั้ง
        events.append((today - timedelta(days=days), "issue", days))
    receipts, issues = [], []
    names = [r[1] for r in SUPPLIES]
    for day, kind, k in sorted(events):
        if kind == "receipt":
            form = {"received_date": day.isoformat(), "source": "จัดซื้อ ร้านเครื่องเขียนตัวอย่าง (สมมติ)",
                    "doc_no": f"บส {k + 1}/2569",
                    "item_1": label("กระดาษ A4 80 แกรม"), "unit_1": big("กระดาษ A4 80 แกรม"), "qty_1": "4",
                    "item_2": label("ปากกาลูกลื่น สีน้ำเงิน"), "unit_2": "base", "qty_2": "50",
                    "item_3": label(names[8 + k]), "unit_3": "base", "qty_3": "3"}
            h, lines, errors, _ = sp.parse_receipt(conn, form)
            assert not errors, errors
            receipts.append(sp.record_receipt(conn, uid, h, lines))
            continue
        picks = rng.sample(names, rng.randint(2, 4))
        form = {"issue_date": day.isoformat(), "department_id": str(dep_ids[len(issues) % len(dep_ids)]),
                "requester_name": REQUESTERS[len(issues) % len(REQUESTERS)], "approver_staff_id": str(staff_ids[0]),
                "approved_date": day.isoformat(), "doc_no": f"บก {len(issues) + 1}/2569"}
        n = 0
        for name in picks:
            avail = sp.available_on(conn, sp.resolve_item(conn, name)["id"], day)
            if avail < 2:
                continue
            n += 1
            form.update({f"item_{n}": label(name), f"req_{n}": str(rng.randint(1, max(1, min(6, avail // 3))))})
        if not n:
            continue
        h, lines, errors, _ = sp.parse_issue(conn, form)
        assert not errors, errors
        issues.append(sp.record_issue(conn, uid, h, lines))
    # เบิกหมึกสีฟ้าที่เหลือทั้งหมด ให้มีตัวอย่างรายการ "หมดแล้ว" ในหน้าต่ำกว่าจุดสั่งซื้อ
    ink = "หมึกเติมอิงค์เจ็ท (สีฟ้า)"
    day = today - timedelta(days=2)
    left = sp.available_on(conn, sp.resolve_item(conn, ink)["id"], day)
    if left:
        form = {"issue_date": day.isoformat(), "department_id": str(dep_ids[1]), "requester_name": REQUESTERS[0],
                "approver_staff_id": str(staff_ids[0]), "approved_date": day.isoformat(), "doc_no": f"บก {len(issues) + 1}/2569",
                "item_1": label(ink), "req_1": str(left)}
        h, lines, errors, _ = sp.parse_issue(conn, form)
        assert not errors, errors
        issues.append(sp.record_issue(conn, uid, h, lines))
    sp.cancel_issue(conn, uid, issues[3], "บันทึกซ้ำ (ตัวอย่างใบเบิกที่ยกเลิก)")
    # ใบรับเพิ่มอีกใบแล้วยกเลิก (ยังไม่มีการจ่ายจากใบนี้ ยกเลิกได้เสมอ)
    form = {"received_date": (today - timedelta(days=10)).isoformat(), "source": "รับบริจาค (สมมติ)", "doc_no": "บจ 1/2569",
            "item_1": label("แฟ้มสันกว้าง 3 นิ้ว"), "unit_1": "base", "qty_1": "12"}
    h, lines, errors, _ = sp.parse_receipt(conn, form)
    assert not errors, errors
    rid = sp.record_receipt(conn, uid, h, lines)
    sp.cancel_receipt(conn, uid, rid, "บันทึกผิดใบ (ตัวอย่างใบรับที่ยกเลิก)")
    return len(receipts) + 1, len(issues)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="สร้างฐานข้อมูล demo พร้อมข้อมูลสมมติ")
    ap.add_argument("--reset", action="store_true", help="ลบ db demo เดิมแล้วสร้างใหม่")
    ap.add_argument("--no-env", action="store_true", help="ไม่สร้างไฟล์ .env (ใช้ใน test)")
    ap.add_argument("--production", action="store_true",
                    help="ทุกบัญชีต้องเปลี่ยนรหัสผ่านตอนล็อกอินครั้งแรก (รหัสผ่าน demo เปิดเผยอยู่ใน README)")
    args = ap.parse_args()
    if not args.no_env and write_env():
        print("สร้างไฟล์ .env พร้อม KRUPAN_SECRET_KEY ใหม่แล้ว")
    from app.db import db_path
    path = Path(db_path())
    if path.exists():
        if not args.reset:
            print(f"มี {path} อยู่แล้ว ใช้ --reset ถ้าต้องการสร้างใหม่")
            return 1
        for suffix in ("", "-wal", "-shm", "-journal"):
            side = path.with_name(path.name + suffix)
            if side.exists():
                side.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("KRUPAN_BACKUP_DIR", str(ROOT / "backups"))
    rng = random.Random(2569)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((ROOT / "docs" / "schema.sql").read_text(encoding="utf-8"))
    import json
    from scripts.create_user import save_user
    conn.executemany("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                     [("ORG_DISPLAY_NAME", json.dumps(ORG_DISPLAY, ensure_ascii=False)),
                      ("ORG_REPORT_NAME", json.dumps(ORG_REPORT, ensure_ascii=False))])
    for i, b in enumerate(BUILDINGS, 1):
        conn.execute("INSERT INTO buildings (name, sort_order) VALUES (?, ?)", (b, i))
    for i, d in enumerate(DEPARTMENTS, 1):
        conn.execute("INSERT INTO departments (name, sort_order, issues_supplies) VALUES (?, ?, 1)", (d, i))
    b_ids = [r[0] for r in conn.execute("SELECT id FROM buildings ORDER BY sort_order")]
    d_ids = [r[0] for r in conn.execute("SELECT id FROM departments ORDER BY sort_order")]
    room_ids = {}
    for name, b, d in ROOMS:
        room_ids[name] = conn.execute("INSERT INTO locations (name, building_id, department_id) VALUES (?, ?, ?)",
                                      (name, b_ids[b], d_ids[d])).lastrowid
    staff_ids = [conn.execute("INSERT INTO staff (full_name, nickname, position) VALUES (?, ?, ?)", s).lastrowid for s in STAFF]
    conn.commit()
    for username, password, role, display in USERS:
        save_user(conn, username, password, role, display_name=display)
    if args.production:
        conn.execute("UPDATE users SET must_change_password = 1")
        conn.commit()
    n_assets = seed_assets(conn, rng, staff_ids, room_ids)
    conn.commit()
    uid = conn.execute("SELECT id FROM users WHERE username = 'editor'").fetchone()[0]
    n_rec, n_iss = seed_supplies(conn, rng, uid, d_ids, staff_ids)
    conn.close()
    print(f"สร้าง {path} แล้ว: ครุภัณฑ์ {n_assets} ชิ้น · วัสดุ {len(SUPPLIES)} รายการ · ใบรับ {n_rec} ใบ · ใบเบิก {n_iss} ใบ")
    print("บัญชี demo:", ", ".join(f"{u} / {p}" for u, p, _, _ in USERS))
    if args.production:
        print("--production: ทุกบัญชีต้องเปลี่ยนรหัสผ่านตอนล็อกอินครั้งแรก")
    else:
        print("รหัสผ่านเหล่านี้เปิดเผยใน README ใช้กับข้อมูลสมมติเท่านั้น ถ้าจะใช้จริงให้รันด้วย --production หรือเปลี่ยนรหัสผ่าน")
    return 0


if __name__ == "__main__":
    sys.exit(main())
