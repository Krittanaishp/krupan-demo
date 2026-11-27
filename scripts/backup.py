"""
สำรองฐานข้อมูล 1 ครั้ง (Task Scheduler "KrupanBackup" เรียกตอน 12:00, 16:00 และตอนเปิดเครื่อง)

- SQLite online backup API -> backups/daily/krupan_YYYYMMDD_HHMMSS.db แล้ว integrity_check
- เก็บรายวัน 30 วัน / รายเดือน 12 เดือน (backups/monthly/)
- ถ้าตั้ง BACKUP_COPY_DIR ใน .env คัดลอกไปที่นั่นด้วย (คัดลอกไม่ได้ = บันทึก log แต่ยังถือว่าสำเร็จ)
- log: logs/backup-YYYYMMDD.log  สถานะ: backups/status.json

วิธีใช้:
    .venv\\Scripts\\python.exe scripts\\backup.py                    (เครื่องที่ติดตั้งด้วยชุดติดตั้ง)
    uv run python scripts/backup.py --reason before_update
exit code: 0 สำเร็จ, 1 ล้มเหลว
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import backup  # noqa: E402


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="สำรองฐานข้อมูล 1 ครั้ง")
    ap.add_argument("--reason", default="scheduled", help="เหตุผล (บันทึกใน log) เช่น scheduled, before_update")
    ap.add_argument("--db", help="ไฟล์ db ต้นทาง (ค่าเริ่มต้น data/krupan.db)")
    args = ap.parse_args(argv)
    try:
        r = backup.make_backup(args.reason, src=args.db)
    except backup.BackupError as e:
        print(f"ล้มเหลว: {e}")
        return 1
    print(f"สำเร็จ: {r['file']} · {r['check']}")
    if r["copied"]:
        print("คัดลอกไปที่ BACKUP_COPY_DIR แล้ว")
    elif r["copy_error"]:
        print(f"คำเตือน: คัดลอกไปที่ BACKUP_COPY_DIR ไม่ได้: {r['copy_error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
