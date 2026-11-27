"""
สร้างฐานข้อมูล data/krupan.db จาก docs/schema.sql

วิธีใช้:
    uv run python scripts/init_db.py           สร้างใหม่ (ถ้ามีไฟล์อยู่แล้วจะหยุด)
    uv run python scripts/init_db.py --reset   สำรองไฟล์เดิมไป backups/ แล้วสร้างใหม่
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "krupan.db"
SCHEMA_PATH = ROOT / "docs" / "schema.sql"
BACKUP_DIR = ROOT / "backups"


def backup(db_path):
    """ย้ายไฟล์ db เดิม (และไฟล์ -wal/-shm/-journal ถ้ามี) ไปไว้ใน backups/"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    BACKUP_DIR.mkdir(exist_ok=True)
    dst = BACKUP_DIR / f"{db_path.stem}_{stamp}{db_path.suffix}"
    shutil.copy2(db_path, dst)
    for suffix in ("-wal", "-shm", "-journal"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            shutil.copy2(side, dst.with_name(dst.name + suffix))
            side.unlink()
    db_path.unlink()
    return dst


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="สร้างฐานข้อมูลจาก docs/schema.sql")
    ap.add_argument("--reset", action="store_true", help="สำรองไฟล์ db เดิมไป backups/ แล้วสร้างใหม่")
    args = ap.parse_args()

    if DB_PATH.exists():
        if not args.reset:
            print(f"หยุด: มีฐานข้อมูลอยู่แล้วที่ {DB_PATH}")
            print("ถ้าต้องการสร้างใหม่ ให้สั่ง --reset (ไฟล์เดิมจะถูกสำรองไว้ใน backups/ ก่อน)")
            sys.exit(1)
        dst = backup(DB_PATH)
        print(f"สำรองฐานข้อมูลเดิมไว้ที่ {dst}")

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(schema)
    except Exception:
        conn.close()
        DB_PATH.unlink(missing_ok=True)  # ไม่ทิ้งไฟล์ db ที่สร้างไม่สำเร็จไว้
        raise
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    conn.close()
    print(f"สร้าง {DB_PATH} แล้ว ({len(tables)} ตาราง: {', '.join(tables)})")


if __name__ == "__main__":
    main()
