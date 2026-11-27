"""ตั้งค่า test: ใช้ db ชั่วคราวที่สร้างจาก docs/schema.sql + ข้อมูลสมมติ ห้ามแตะ data/krupan.db"""
import base64
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "data" / "krupan.db"
SCHEMA = ROOT / "docs" / "schema.sql"

# ต้องตั้งก่อน import app (SessionMiddleware อ่าน secret key ตอนสร้าง app)
os.environ["KRUPAN_SECRET_KEY"] = "test-secret-key-" + "x" * 32
# กันไว้ก่อน: ถ้ามีโค้ดเรียก connect() ก่อน fixture ตั้ง db จะชี้ไปไฟล์ที่ไม่มีอยู่ แทนที่จะเปิด db จริง
os.environ["KRUPAN_DB"] = str(ROOT / "tests" / "__no_db_before_fixture__.db")
# ที่อยู่ใน QR ของ test (IP สำหรับเอกสาร RFC 5737) ไม่อ่าน BASE_URL จากไฟล์ .env จริง
os.environ["BASE_URL"] = "http://192.0.2.10:8000"
# ไม่ตรวจแฟลชไดรฟ์จริงของเครื่องระหว่าง test
os.environ["KRUPAN_USB_WATCH"] = "off"
# แถบเตือนสำรองข้อมูลเปิดเสมอใน test ไม่ขึ้นกับ .env ของเครื่อง (เครื่องพัฒนาตั้ง BACKUP_WARNING=off ไว้)
os.environ["BACKUP_WARNING"] = "on"
# test เดิมของ QR (สติกเกอร์ หน้าสแกน คำเตือน BASE_URL) ต้องเปิด QR ไว้ · test ของการปิด QR ตั้ง off เองด้วย monkeypatch
os.environ["QR_ENABLED"] = "on"
# ไฟล์สำรอง db ที่โปรแกรมทำเอง (เช่น ก่อนปิดรอบตรวจสอบพัสดุ) ต้องไม่ไปลงโฟลเดอร์ backups/ จริง: fixture ชี้ไปโฟลเดอร์ชั่วคราว
os.environ["KRUPAN_BACKUP_DIR"] = str(ROOT / "tests" / "__no_backup_before_fixture__")
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from scripts.create_user import save_user  # noqa: E402

PASSWORD = "correct-horse-1"
REG_1 = "ปค.99-9999-1-1306-0004"
MULTI_ROOM = "ห้องผู้บริหาร 4 ตัว ห้องประชุม 3 ตัว"


def _seed(conn):
    conn.executescript(f"""
        INSERT INTO categories (code, name) VALUES ('13', 'ตู้'), ('14', 'โต๊ะ-เก้าอี้');
        INSERT INTO asset_types (code, category_code, name, name_is_draft) VALUES
            ('1306', '13', 'ตู้เหล็ก 4 ลิ้นชัก', 1), ('1401', '14', 'โต๊ะทำงาน', 1);
        INSERT INTO assets (id, reg_no, temp_code, type_code, name, details, acquired_date, acquired_date_precision,
                            fiscal_year, unit_price, location_text, status, note, needs_review, source_sheet, source_row)
        VALUES
            (1, 'ปค.99-9999-1-1306-0004', NULL, '1306', 'ตู้เหล็ก 4 ลิ้นชัก', 'Lucky', '2019-12-25', 'day',
             2563, 5000, 'ห้องปกครอง', 'ใช้งาน', NULL, 'ไม่มีราคา, วันที่มีแค่เดือน/ปี', '13. ', 7),
            (2, NULL, 'TMP-0001', '1401', 'โต๊ะทำงานเหล็ก', NULL, NULL, NULL,
             NULL, NULL, 'ห้องทะเบียน', 'ใช้งาน', 'โครงการ', 'ไม่มีหมายเลขทะเบียน', '14. ', 3),
            (3, 'ปค.99-9999-1-1401-0010', NULL, '1401', 'เก้าอี้สำนักงาน', NULL, '2020-01-10', 'day',
             2563, 1200, 'สนง./เอ็ม', 'ใช้งาน', NULL, NULL, '14. ', 9),
            (4, 'ปค.99-9999-1-1401-0011', NULL, '1401', 'เก้าอี้บุนวม', NULL, '2020-01-10', 'day',
             2563, 900, '{MULTI_ROOM}', 'ใช้งาน', NULL, NULL, '14. ', 10),
            (5, 'ปค.99-9999-1-1401-0012', NULL, '1401', 'เก้าอี้บุนวม', NULL, '2020-01-10', 'day',
             2563, 900, '{MULTI_ROOM}', 'ใช้งาน', NULL, NULL, '14. ', 11),
            (6, 'ปค.99-9999-1-1306-0005', NULL, '1306', 'ตู้เหล็ก 4 ลิ้นชัก', NULL, '2019-12-25', 'day',
             2563, 5000, 'ห้องปกครอง', 'ใช้งาน', NULL, NULL, '13. ', 8);
    """)


@pytest.fixture(scope="session", autouse=True)
def real_db_untouched():
    """ตรวจว่าทั้งชุด test ไม่ได้แก้ไฟล์ db จริง"""
    before = REAL_DB.stat() if REAL_DB.exists() else None
    yield
    if before:
        after = REAL_DB.stat()
        assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns, before.st_size), "test แตะ data/krupan.db!"


def make_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    _seed(conn)
    # ชื่อหน่วยงาน (ค่าตั้งค่าใน db เหมือนเครื่องจริงหลัง migration 008)
    conn.executemany("INSERT INTO app_settings (key, value) VALUES (?, ?)",
                     [("ORG_DISPLAY_NAME", '"ที่ว่าการอำเภอทดสอบ"'), ("ORG_REPORT_NAME", '"ที่ทำการปกครองอำเภอทดสอบ"')])
    conn.commit()
    for username, role in (("editor1", "editor"), ("admin1", "admin"), ("viewer1", "viewer")):
        save_user(conn, username, PASSWORD, role, display_name=f"ผู้ใช้ {username}")
    conn.close()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    make_db(path)
    monkeypatch.setenv("KRUPAN_DB", str(path))
    monkeypatch.setenv("KRUPAN_BACKUP_DIR", str(tmp_path / "backups"))
    assert path.resolve() != REAL_DB.resolve()
    return path


@pytest.fixture
def db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


class Client(TestClient):
    """TestClient ที่แนบ CSRF token ของ session ให้ทุก POST อัตโนมัติ (ส่ง csrf=False เพื่อทดสอบกรณีไม่มี token)"""

    def session_data(self):
        raw = self.cookies.get("krupan_session")
        if not raw:
            return {}
        # cookie ของ Starlette = base64(json).timestamp.signature
        return json.loads(base64.b64decode(raw.strip('"').split(".")[0]))

    def csrf_token(self):
        token = self.session_data().get("csrf")
        if not token:
            self.get("/login", follow_redirects=False)  # หน้าแรกที่แสดงผลจะสร้าง token ให้ session
            token = self.session_data().get("csrf")
        return token

    def post(self, url, *args, csrf=True, **kwargs):
        if csrf:
            data = dict(kwargs.get("data") or {})
            data.setdefault("csrf_token", self.csrf_token())
            kwargs["data"] = data
        return super().post(url, *args, **kwargs)


@pytest.fixture
def client(db_path):
    with Client(app) as c:
        yield c


def login(client, username="editor1", password=PASSWORD):
    return client.post("/login", data={"username": username, "password": password, "next": "/assets"},
                       follow_redirects=False)


@pytest.fixture
def editor(client):
    assert login(client).status_code == 303
    return client


@pytest.fixture
def admin(client):
    assert login(client, "admin1").status_code == 303
    return client


def asset_form(**overrides):
    """ฟอร์มแก้ไขของครุภัณฑ์ #1 ตามค่าปัจจุบัน แก้เฉพาะช่องที่ส่งมา"""
    form = {
        "reg_no": REG_1, "type_code": "1306", "name": "ตู้เหล็ก 4 ลิ้นชัก", "details": "Lucky",
        "date_precision": "day", "date_day": "25", "date_month": "12", "date_year": "2562",
        "fiscal_year": "2563", "unit_price": "5000", "funding_source": "", "gfmis_code": "",
        "location_text": "ห้องปกครอง", "location_id": "", "holder_id": "", "status": "ใช้งาน", "note": "",
    }
    form.update(overrides)
    return form


def audit_rows(db, table="assets", record_id=1):
    return db.execute("SELECT * FROM audit_log WHERE table_name = ? AND record_id = ? ORDER BY id",
                      (table, record_id)).fetchall()


def add_location(db, name, active=1):
    lid = db.execute("INSERT INTO locations (name, active) VALUES (?, ?)", (name, active)).lastrowid
    db.commit()
    return lid


def add_staff(db, nickname=None, full_name=None, active=1):
    sid = db.execute("INSERT INTO staff (nickname, full_name, active) VALUES (?, ?, ?)",
                     (nickname, full_name, active)).lastrowid
    db.commit()
    return sid
