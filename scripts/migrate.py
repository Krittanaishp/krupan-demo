"""
รัน migration ที่ยังค้างทั้งหมดตามลำดับ (ใช้ตอนติดตั้งและอัปเดต)

- db ที่ยังไม่มีตาราง schema_migrations ต้องผ่าน migration 006 แล้ว (รุ่นที่ใช้งานอยู่ตอนทำส่วนที่ 7)
  จะเริ่มจาก migrate_007.py ซึ่งสร้างตารางและบันทึก 001-006 ย้อนหลังให้
- หลังจากนั้นดูจากตาราง schema_migrations: รันเฉพาะ scripts/migrate_NNN.py ที่ยังไม่มีชื่อในตาราง
  ตัวที่รันแล้วจะไม่รันซ้ำ แต่ละตัวสำรอง db ก่อนและบันทึกชื่อตัวเองในตารางภายใน transaction เดียวกัน

วิธีใช้:
    .venv\\Scripts\\python scripts\\migrate.py            รันตัวที่ค้าง
    .venv\\Scripts\\python scripts\\migrate.py --check    แค่บอกว่าค้างตัวไหน (exit 1 ถ้ามีตัวค้าง)
"""
import argparse
import importlib
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.db import db_path  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
FIRST_TRACKED = 7  # migration ตั้งแต่ตัวนี้บันทึกตัวเองใน schema_migrations


class MigrationError(Exception):
    pass


def available(scripts_dir=SCRIPTS):
    """ชื่อ migration ที่มีสคริปต์ (ตั้งแต่ 007) เรียงตามลำดับ เช่น ['007', '008']"""
    names = []
    for p in Path(scripts_dir).glob("migrate_*.py"):
        m = re.fullmatch(r"migrate_(\d{3})\.py", p.name)
        if m and int(m.group(1)) >= FIRST_TRACKED:
            names.append(m.group(1))
    return sorted(names)


def applied(path):
    """ชื่อ migration ที่รันกับ db นี้แล้ว"""
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "schema_migrations" not in tables:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(disposals)")}
            if "lot_id" not in cols:
                raise MigrationError("db นี้ยังไม่ผ่าน migration 006 ให้รัน scripts/migrate_00x.py ที่ค้างทีละตัวก่อน "
                                     "(ดูคู่มือผู้ดูแลระบบ)")
            return {f"{i:03d}" for i in range(1, FIRST_TRACKED)}
        return {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    finally:
        conn.close()


def pending(path, scripts_dir=SCRIPTS):
    done = applied(path)
    return [n for n in available(scripts_dir) if n not in done]


def run(path, backup_dir=None, out=print, scripts_dir=SCRIPTS):
    """รันตัวที่ค้างตามลำดับ คืนรายชื่อที่รันไป หยุดทันทีถ้าตัวไหนไม่สำเร็จ"""
    path = Path(path)
    if not path.exists():
        raise MigrationError(f"ไม่พบ {path}")
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    done = []
    for name in pending(path, scripts_dir):
        mod = importlib.import_module(f"migrate_{name}")
        out(f"== migration {name} ==")
        kwargs = {"out": out}
        if backup_dir is not None:
            kwargs["backup_dir"] = Path(backup_dir)
        mod.run(path, **kwargs)
        if name not in applied(path):
            raise MigrationError(f"migration {name} ไม่สำเร็จ (ไม่มีชื่อใน schema_migrations) หยุดไว้แค่นี้")
        done.append(name)
    if not done:
        out("ไม่มี migration ค้าง")
    return done


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="รัน migration ที่ยังค้าง")
    ap.add_argument("--check", action="store_true", help="แค่แสดงตัวที่ค้าง (exit 1 ถ้ามี)")
    ap.add_argument("--db", help="ไฟล์ db (ค่าเริ่มต้น data/krupan.db หรือ KRUPAN_DB)")
    ap.add_argument("--backup-dir", help="โฟลเดอร์เก็บไฟล์สำรองก่อน migrate (ค่าเริ่มต้น backups/ ของโปรแกรม)")
    args = ap.parse_args()
    path = Path(args.db) if args.db else db_path()
    try:
        if args.check:
            todo = pending(path)
            print("ค้าง: " + ", ".join(todo) if todo else "ไม่มี migration ค้าง")
            return 1 if todo else 0
        run(path, backup_dir=args.backup_dir)
        return 0
    except MigrationError as e:
        print(f"หยุด: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
