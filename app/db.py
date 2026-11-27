"""การเชื่อมต่อฐานข้อมูล SQLite"""
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "krupan.db"


def db_path():
    """ใช้ KRUPAN_DB ถ้ากำหนดไว้ (test ใช้ชี้ไปที่ db ชั่วคราว) ไม่อย่างนั้นใช้ data/krupan.db"""
    return Path(os.environ.get("KRUPAN_DB") or DEFAULT_DB_PATH)


def connect():
    path = db_path()
    if not path.exists():
        raise RuntimeError(f"ไม่พบฐานข้อมูล {path} ให้รัน scripts/init_db.py และ scripts/import_clean.py ก่อน")
    # mode=rw: ไม่สร้างไฟล์ db เปล่าขึ้นมาเองถ้าหาไม่เจอ
    # check_same_thread=False: FastAPI อาจสร้าง connection ใน threadpool แต่ใช้ใน route แบบ async
    # ปลอดภัยเพราะ 1 request ใช้ 1 connection ของตัวเองตามลำดับ ไม่มีการใช้พร้อมกันหลาย thread
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=rw", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """FastAPI dependency: เปิด connection ต่อ 1 request แล้วปิดเมื่อเสร็จ"""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
