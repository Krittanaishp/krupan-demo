"""ไฟล์ log รายวันในโฟลเดอร์ logs/ (ไม่ขึ้น git) เก็บ 30 วัน

ใช้ไฟล์แยกตามวันที่ เช่น logs/server-20261117.log แทนการเปลี่ยนชื่อไฟล์ตอนเที่ยงคืน
เพราะบน Windows ถ้ามีโปรแกรมอื่นเปิดไฟล์ค้างอยู่ การเปลี่ยนชื่อจะล้มเหลว
และหลายโปรเซส (server, งานสำรองข้อมูล) เขียนไฟล์เดียวกันได้โดยไม่ชนกัน
test ตั้ง KRUPAN_LOG_DIR ไปที่โฟลเดอร์ชั่วคราว
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP_DAYS = 30
THAI_TZ = timezone(timedelta(hours=7))


def log_dir():
    return Path(os.environ.get("KRUPAN_LOG_DIR") or ROOT / "logs")


def now_local():
    return datetime.now(THAI_TZ)


def path_for(name, day=None):
    return log_dir() / f"{name}-{(day or now_local()):%Y%m%d}.log"


def prune(name, today=None, keep_days=KEEP_DAYS):
    """ลบไฟล์ log ของ name ที่เก่ากว่า keep_days วัน (นับวันนี้เป็นวันที่ 1)"""
    today = today or now_local().date()
    oldest = today - timedelta(days=keep_days - 1)
    removed = []
    for p in log_dir().glob(f"{name}-*.log"):
        m = re.fullmatch(rf"{re.escape(name)}-(\d{{8}})\.log", p.name)
        if not m:
            continue
        try:
            day = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if day < oldest:
            try:
                p.unlink()
                removed.append(p)
            except OSError:
                pass
    return removed


def write(name, message):
    """ต่อท้ายข้อความ 1 บรรทัด (เปิด-ปิดไฟล์ทุกครั้ง หลายโปรเซสเขียนพร้อมกันได้)"""
    d = log_dir()
    d.mkdir(parents=True, exist_ok=True)
    now = now_local()
    with open(path_for(name, now), "a", encoding="utf-8") as f:
        f.write(f"{now:%Y-%m-%d %H:%M:%S} {message}\n")


class DailyFileHandler(logging.Handler):
    """logging handler ที่เขียนลง logs/<name>-YYYYMMDD.log เปลี่ยนไฟล์เองเมื่อขึ้นวันใหม่ และลบไฟล์เก่ากว่า 30 วัน"""

    def __init__(self, name="server"):
        super().__init__()
        self.base = name
        self.day = None
        self.stream = None

    def _open(self, now):
        if self.stream:
            self.stream.close()
        log_dir().mkdir(parents=True, exist_ok=True)
        self.stream = open(path_for(self.base, now), "a", encoding="utf-8")
        self.day = now.date()
        prune(self.base, now.date())

    def emit(self, record):
        try:
            now = now_local()
            if self.stream is None or now.date() != self.day:
                self._open(now)
            self.stream.write(self.format(record) + "\n")
            self.stream.flush()
        except Exception:  # noqa: BLE001 - log ต้องไม่ทำให้ server ล้ม
            self.handleError(record)

    def close(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        super().close()
