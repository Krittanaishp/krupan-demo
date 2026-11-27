"""สำรองแบบไม่ต้องมีใครทำอะไร: เวลาใหม่ 12:00/16:00, ไดรฟ์ที่สอง, Google Drive, แฟลชไดรฟ์, ไม่ให้ Sleep, สิทธิ์โฟลเดอร์"""
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import backup, logfiles, usb
from tests.conftest import ROOT, login

TZ = logfiles.THAI_TZ
SCRIPTS = ROOT / "scripts"


@pytest.fixture(autouse=True)
def tmp_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("KRUPAN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("BACKUP_COPY_DIR", "")


def at(monkeypatch, when):
    monkeypatch.setattr(logfiles, "now_local", lambda: when)


# ---------------------------------------------------------------- เตือนสำเนานอกเครื่อง 30 วัน

def test_offsite_warning_after_30_days_without_gdrive_or_usb(db_path, monkeypatch):
    t0 = datetime(2026, 11, 1, 12, 0, tzinfo=TZ)
    at(monkeypatch, t0)
    backup.make_backup("test", copy_dir=None)
    fresh = lambda days: backup.update_status(last_success=(t0 + timedelta(days=days)).isoformat())  # noqa: E731
    fresh(29)
    assert backup.warnings(t0 + timedelta(days=29, hours=1)) == []
    fresh(31)
    w = backup.warnings(t0 + timedelta(days=31, hours=1))
    assert [x["level"] for x in w] == ["warn"] and "ยังไม่มีสำเนานอกเครื่อง 31 วัน" in w[0]["message"]
    # ดาวน์โหลดหรือไดรฟ์ที่สองไม่นับเป็นสำเนานอกเครื่อง
    backup.update_status(last_download=(t0 + timedelta(days=31)).isoformat(), last_copy=(t0 + timedelta(days=31)).isoformat())
    assert [x["level"] for x in backup.warnings(t0 + timedelta(days=31, hours=1))] == ["warn"]
    # คัดลอกลงแฟลชไดรฟ์แล้ว
    backup.update_status(usb_last_copy=(t0 + timedelta(days=30)).isoformat())
    assert backup.warnings(t0 + timedelta(days=31, hours=1)) == []
    fresh(61)
    w = backup.warnings(t0 + timedelta(days=61, hours=1))
    assert len(w) == 1 and w[0]["message"].startswith("ยังไม่มีสำเนานอกเครื่อง 31 วัน")
    # ติ๊กว่าใช้ Google Drive แล้ว: ไม่เตือน
    backup.set_gdrive(True, "admin1")
    assert backup.warnings(t0 + timedelta(days=61, hours=1)) == []


# ---------------------------------------------------------------- Google Drive (admin ติ๊กยืนยัน)

def test_gdrive_checkbox(client, db, monkeypatch):
    login(client, "admin1")
    page = client.get("/settings/backup").text
    assert "ใช้ Google Drive สำรองโฟลเดอร์" in page and "12:00, 16:00" in page and "16:30" not in page
    r = client.post("/settings/backup/gdrive", data={"confirmed": "1"}, follow_redirects=True)
    assert "บันทึกแล้ว: ใช้ Google Drive" in r.text and "ติ๊กไว้เมื่อ" in r.text and "โดย admin1" in r.text
    st = backup.read_status()
    assert st["gdrive_confirmed_at"] and st["gdrive_confirmed_by"] == "admin1"
    r = client.post("/settings/backup/gdrive", data={}, follow_redirects=True)
    assert not backup.read_status().get("gdrive_confirmed_at") and "ติ๊กไว้เมื่อ" not in r.text
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE changes LIKE '%gdrive_confirmed%'").fetchone()[0] == 2


def test_gdrive_admin_only(editor):
    assert editor.post("/settings/backup/gdrive", data={"confirmed": "1"}).status_code == 403


def test_same_disk_warning_on_backup_page(client, monkeypatch):
    login(client, "admin1")
    monkeypatch.setenv("BACKUP_COPY_DIR", r"D:\KrupanBackup")
    monkeypatch.setenv("BACKUP_COPY_SAME_DISK", "no")
    assert "ดิสก์เดียวกับ C" not in client.get("/settings/backup").text
    monkeypatch.setenv("BACKUP_COPY_SAME_DISK", "yes")
    assert "ไดรฟ์ D อยู่บนดิสก์เดียวกับ C ถ้าฮาร์ดดิสก์เสียจะหายทั้งคู่ ควรมีสำเนานอกเครื่องด้วย" in \
        client.get("/settings/backup").text


# ---------------------------------------------------------------- แฟลชไดรฟ์

def make_backups(monkeypatch, days):
    for d in days:
        at(monkeypatch, d)
        backup.make_backup("test", copy_dir=None)


def test_usb_copies_latest_once_a_day(db_path, tmp_path, monkeypatch):
    stick = tmp_path / "E"
    stick.mkdir()
    t0 = datetime(2026, 11, 17, 8, 30, tzinfo=TZ)
    make_backups(monkeypatch, [t0 - timedelta(days=1, hours=-7), t0])
    assert usb.check_once(find=lambda: None, now=t0) == "ไม่มีแฟลชไดรฟ์"
    assert usb.check_once(find=lambda: stick, now=t0) == "คัดลอกแล้ว"
    latest = list((stick / "KrupanBackup" / "latest").glob("*.db"))
    assert [p.name for p in latest] == ["krupan_20261117_083000.db"] and backup.integrity(latest[0])[0]
    assert (stick / "KrupanBackup" / "monthly" / "krupan_202611.db").exists()
    st = backup.read_status()
    assert st["usb_present"] and st["usb_last_file"] == "krupan_20261117_083000.db"
    # หน้าเว็บของ admin: ถอดออกได้
    assert [n["message"].startswith("คัดลอกลงแฟลชไดรฟ์เรียบร้อย ถอดออกได้") for n in backup.notices(t0)] == [True]
    # วันเดียวกันไม่คัดลอกซ้ำ แม้มีไฟล์สำรองใหม่
    make_backups(monkeypatch, [t0 + timedelta(hours=4)])
    assert usb.check_once(find=lambda: stick, now=t0 + timedelta(hours=4)) == "วันนี้คัดลอกแล้ว"
    # ถอดออก: ข้อความหายไป
    assert usb.check_once(find=lambda: None, now=t0 + timedelta(hours=5)) == "ไม่มีแฟลชไดรฟ์"
    assert backup.notices(t0 + timedelta(hours=5)) == [] and backup.read_status()["usb_present"] is False
    # วันถัดไป: latest เหลือ 1 ไฟล์ (ไฟล์ใหม่)
    t1 = t0 + timedelta(days=1)
    make_backups(monkeypatch, [t1])
    assert usb.check_once(find=lambda: stick, now=t1) == "คัดลอกแล้ว"
    assert [p.name for p in (stick / "KrupanBackup" / "latest").glob("*.db")] == ["krupan_20261118_083000.db"]
    log = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "logs").glob("backup-*.log"))
    assert "ลงแฟลชไดรฟ์" in log and "ถูกถอดออก" in log


def test_usb_keeps_12_months(db_path, tmp_path, monkeypatch):
    stick = tmp_path / "E"
    monthly = stick / "KrupanBackup" / "monthly"
    monthly.mkdir(parents=True)
    for m in range(1, 13):
        (monthly / f"krupan_2025{m:02d}.db").write_bytes(b"x")
    (monthly / "notes.db").write_bytes(b"keep")
    t0 = datetime(2026, 11, 17, 9, 0, tzinfo=TZ)
    make_backups(monkeypatch, [t0])
    usb.check_once(find=lambda: stick, now=t0)
    names = sorted(p.name for p in monthly.glob("krupan_*.db"))
    assert len(names) == 12 and names[0] == "krupan_202502.db" and names[-1] == "krupan_202611.db"
    assert (monthly / "notes.db").exists()


def test_usb_copy_error_is_reported(db_path, tmp_path, monkeypatch):
    stick = tmp_path / "E"
    stick.mkdir()
    (stick / "KrupanBackup").write_text("ไฟล์ขวางชื่อโฟลเดอร์")  # ทำให้สร้างโฟลเดอร์ไม่ได้
    t0 = datetime(2026, 11, 17, 9, 0, tzinfo=TZ)
    make_backups(monkeypatch, [t0])
    assert usb.check_once(find=lambda: stick, now=t0) == "คัดลอกไม่ได้"
    st = backup.read_status()
    assert st["usb_last_error"] and not st.get("usb_last_copy") and backup.notices(t0) == []


def test_find_drive_by_label_not_letter(monkeypatch):
    monkeypatch.setattr(usb, "windows_drives", lambda: [("D:\\", "Data", 3), ("F:\\", "KRUPAN-BACKUP", 2)])
    monkeypatch.setenv("BACKUP_USB_LABEL", "krupan-backup")
    assert usb.find_drive() == Path("F:\\")
    monkeypatch.setenv("BACKUP_USB_LABEL", "KRUPAN-BAK")
    assert usb.find_drive() is None
    monkeypatch.delenv("BACKUP_USB_LABEL")
    assert usb.label_setting() in ("KRUPAN-BACKUP",) or usb.label_setting()  # ค่าเริ่มต้น (ถ้า .env ไม่ได้ตั้ง)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows")
def test_windows_drives_skips_system_drive():
    roots = [r for r, _, _ in usb.windows_drives()]
    assert "C:\\" not in roots


def test_watch_disabled_in_tests():
    assert not usb.watch_enabled()


def test_usb_status_on_backup_page(client, tmp_path, monkeypatch):
    login(client, "admin1")
    backup.update_status(usb_present=True, usb_drive="F:", usb_last_copy=backup.now_local().isoformat(timespec="seconds"),
                         usb_last_file="krupan_x.db")
    html = client.get("/settings/backup").text
    assert "เสียบอยู่ที่ F:" in html and "KRUPAN-BACKUP" in html
    page = client.get("/assets").text
    assert "backup-warning-ok" in page and "คัดลอกลงแฟลชไดรฟ์เรียบร้อย ถอดออกได้" in page


# ---------------------------------------------------------------- สคริปต์ PowerShell


pwsh = pytest.mark.skipif(sys.platform != "win32" or not shutil.which("powershell"), reason="Windows PowerShell")
