"""เวลา: db เก็บเป็น UTC (แบบ CURRENT_TIMESTAMP ของ SQLite) แสดงผลเป็นเวลาไทย

เรียกผ่านโมดูล (timeutil.utcnow()) เสมอ เพื่อให้ test เลื่อนเวลาได้ด้วย monkeypatch
"""
from datetime import datetime, timedelta, timezone

THAI_TZ = timezone(timedelta(hours=7))


def utcnow():
    """เวลาปัจจุบัน UTC ไม่มี tzinfo (เทียบกับค่าใน db ได้ตรง ๆ)"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today_th():
    """วันที่วันนี้ตามเวลาไทย (ใช้เป็นวันที่ย้ายครุภัณฑ์)"""
    return (utcnow() + timedelta(hours=7)).date()


def parse_db_time(text):
    return datetime.fromisoformat(str(text))


YEAR_RANGE = 100  # ปีที่ห่างจากปีปัจจุบันเกินนี้ถือว่ากรอกผิด (เช่น พิมพ์ปี พ.ศ. ลงช่อง ค.ศ.)


def reasonable_year(d):
    """วันที่ (date) ที่ปีห่างจากปีปัจจุบันไม่เกิน 100 ปี · ใช้ตรวจทุกวันที่ที่รับจากฟอร์มและ URL"""
    return abs(d.year - today_th().year) <= YEAR_RANGE
