"""สำรองฐานข้อมูลไว้ใน backups/

1. สำรองก่อนเปลี่ยนแปลงสำคัญ (เช่น ก่อนปิดรอบตรวจสอบพัสดุ): backup(db, label) -> backups/krupan_<label>_<เวลา>.db
2. สำรองตามรอบ (Task Scheduler "KrupanBackup" 12:00, 16:00, ตอนเปิดเครื่อง / ปุ่ม "สำรองตอนนี้" / ก่อนอัปเดต): make_backup()
   - backups/daily/krupan_YYYYMMDD_HHMMSS.db   วันนี้เก็บทุกไฟล์ วันก่อนหน้าเก็บไฟล์สุดท้ายของวัน ย้อนหลัง 30 วัน
   - backups/monthly/krupan_YYYYMM.db          ไฟล์แรกของแต่ละเดือน เก็บ 12 เดือนล่าสุด
   - ใช้ SQLite online backup API (ปลอดภัยแม้มีคนใช้งานอยู่) แล้ว PRAGMA integrity_check กับไฟล์ที่ได้
     ไม่ผ่าน = ลบไฟล์นั้นทิ้งและถือว่าล้มเหลว
   - ถ้าตั้ง BACKUP_COPY_DIR ใน .env (ปกติไดรฟ์ที่สองในเครื่อง เช่น D:/KrupanBackup) คัดลอกไปที่นั่นด้วย
     (โครงเดียวกัน daily/ monthly/ และกฎเก็บเดียวกัน) คัดลอกไม่ได้ = บันทึก log ไว้ แต่ไม่ถือว่าการสำรองล้มเหลว
3. สำเนานอกเครื่อง (เลือกได้): Google Drive สำรองโฟลเดอร์ BACKUP_COPY_DIR (admin ติ๊กยืนยันเอง)
   และ/หรือแฟลชไดรฟ์ที่ตั้งชื่อ BACKUP_USB_LABEL (app/usb.py คัดลอกให้เองเมื่อเสียบ)
   - สถานะล่าสุดอยู่ใน backups/status.json (หน้า /settings/backup, แถบเตือนของ admin และ /health อ่านจากไฟล์นี้)
   - log อยู่ใน logs/backup-YYYYMMDD.log

test ตั้ง KRUPAN_BACKUP_DIR ไปที่โฟลเดอร์ชั่วคราว
"""
import json
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from app import logfiles
from app.config import get_setting
from app.db import ROOT, db_path

DAILY_KEEP_DAYS = 30
MONTHLY_KEEP = 12
FAIL_WARN_HOURS = 48       # สำรองไม่สำเร็จนานเกินนี้ -> แถบแดง
OFFSITE_WARN_DAYS = 30     # ไม่ได้ใช้ Google Drive และไม่ได้คัดลอกลงแฟลชไดรฟ์นานเกินนี้ -> แถบส้ม
LOCK_STALE_SECONDS = 15 * 60
DAILY_RE = re.compile(r"krupan_(\d{8})_(\d{6})\.db")
MONTHLY_RE = re.compile(r"krupan_(\d{6})\.db")


class BackupError(Exception):
    """ข้อความแสดงให้ผู้ใช้เห็นได้"""


def backup_dir():
    return Path(os.environ.get("KRUPAN_BACKUP_DIR") or ROOT / "backups")


def now_local():
    """เวลาไทยปัจจุบัน (มี tzinfo) เรียกผ่านโมดูลเพื่อให้ test เลื่อนเวลาได้"""
    return logfiles.now_local()


def log(message):
    logfiles.write("backup", message)


# ---------------------------------------------------------------- สำรองก่อนเปลี่ยนแปลงสำคัญ

def backup(db, label):
    """คืน path ของไฟล์สำรอง เช่น backups/krupan_before_close_inventory_3_20261116_101500_123456.db"""
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"{db_path().stem}_{label}_{datetime.now():%Y%m%d_%H%M%S_%f}.db"
    out = sqlite3.connect(dst)
    try:
        db.backup(out)
    finally:
        out.close()
    return dst


# ---------------------------------------------------------------- สถานะ

def status_path():
    return backup_dir() / "status.json"


def read_status():
    try:
        return json.loads(status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def update_status(**values):
    d = backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    st = read_status()
    st.update(values)
    tmp = d / f"status.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, status_path())
    return st


def parse_time(text):
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


# ---------------------------------------------------------------- ตรวจไฟล์

def integrity(path):
    """คืน (ok, ข้อความ) จาก PRAGMA integrity_check"""
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
        try:
            rows = [r[0] for r in conn.execute("PRAGMA integrity_check")]
            n_assets = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return False, f"เปิดไฟล์ไม่ได้: {e}"
    if rows != ["ok"]:
        return False, "integrity_check ไม่ผ่าน: " + "; ".join(rows[:5])
    return True, f"integrity_check ผ่าน (ครุภัณฑ์ {n_assets:,} รายการ)"


# ---------------------------------------------------------------- กฎการเก็บ

def prune(root, today, keep_days=DAILY_KEEP_DAYS, keep_months=MONTHLY_KEEP):
    """daily/: วันนี้เก็บทุกไฟล์ วันก่อนหน้าเก็บไฟล์สุดท้ายของวัน ไม่เกิน keep_days วัน (นับวันนี้)
    monthly/: เก็บ keep_months เดือนล่าสุด  ไม่แตะไฟล์ที่ชื่อไม่ตรงรูปแบบ  คืนรายการไฟล์ที่ลบ"""
    root = Path(root)
    removed = []
    oldest = today - timedelta(days=keep_days - 1)
    by_day = {}
    for p in (root / "daily").glob("krupan_*.db"):
        m = DAILY_RE.fullmatch(p.name)
        if not m:
            continue
        try:
            day = datetime.strptime(m.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        by_day.setdefault(day, []).append(p)
    for day, files in by_day.items():
        files.sort(key=lambda p: p.name)
        if day < oldest:
            drop = files
        elif day < today:
            drop = files[:-1]
        else:
            drop = []
        removed += drop
    months = sorted((p for p in (root / "monthly").glob("krupan_*.db") if MONTHLY_RE.fullmatch(p.name)),
                    key=lambda p: p.name)
    removed += months[:-keep_months] if len(months) > keep_months else []
    for p in removed:
        p.unlink(missing_ok=True)
    return removed


# ---------------------------------------------------------------- สำรองตามรอบ

class _Lock:
    """กันการสำรองซ้อนกัน (ตามเวลา + กดปุ่มพร้อมกัน) ล็อกที่ค้างเกิน 15 นาทีถือว่าตายแล้ว"""

    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except OSError:
                    continue
                if age < LOCK_STALE_SECONDS:
                    raise BackupError("มีการสำรองข้อมูลกำลังทำอยู่ ลองใหม่อีกครั้งในอีกสักครู่") from None
                self.path.unlink(missing_ok=True)
        raise BackupError("สร้างไฟล์ล็อกไม่ได้")

    def __exit__(self, *exc):
        os.close(self.fd)
        self.path.unlink(missing_ok=True)


def copy_dir_setting():
    v = (get_setting("BACKUP_COPY_DIR") or "").strip().strip('"')
    return Path(v) if v else None


def _copy_offsite(dst, monthly_file, copy_dir, today):
    copy_dir = Path(copy_dir)
    if not copy_dir.is_dir():
        raise OSError(f"ไม่พบโฟลเดอร์ {copy_dir} (ไดรฟ์ถูกถอดหรือเครือข่ายใช้ไม่ได้?)")
    pairs = [(dst, copy_dir / "daily" / dst.name)]
    m_target = copy_dir / "monthly" / monthly_file.name
    if not m_target.exists():
        pairs.append((monthly_file, m_target))
    for src, target in pairs:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part")
        shutil.copyfile(src, tmp)
        if tmp.stat().st_size != src.stat().st_size:
            raise OSError(f"คัดลอก {src.name} ไม่ครบ")
        os.replace(tmp, target)
    prune(copy_dir, today)


def make_backup(reason="manual", src=None, copy_dir="from-settings"):
    """สำรอง 1 ครั้ง คืน dict {file, monthly, copied, copy_error, check}
    ล้มเหลว -> raise BackupError (บันทึกสถานะและ log ไว้แล้ว)"""
    src = Path(src or db_path())
    root = backup_dir()
    now = now_local()
    if copy_dir == "from-settings":
        copy_dir = copy_dir_setting()
    try:
        with _Lock(root / ".backup.lock"):
            if not src.exists():
                raise BackupError(f"ไม่พบฐานข้อมูล {src}")
            daily = root / "daily"
            daily.mkdir(parents=True, exist_ok=True)
            dst = daily / f"krupan_{now:%Y%m%d_%H%M%S}.db"
            tmp = dst.with_suffix(".part")
            try:
                s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
                o = sqlite3.connect(tmp)
                try:
                    s.backup(o)
                finally:
                    o.close()
                    s.close()
                ok, check = integrity(tmp)
                if not ok:
                    raise BackupError(check)
                os.replace(tmp, dst)
            except sqlite3.Error as e:
                raise BackupError(f"สำรองไม่สำเร็จ: {e}") from e
            finally:
                tmp.unlink(missing_ok=True)

            monthly = root / "monthly" / f"krupan_{now:%Y%m}.db"
            new_monthly = None
            if not monthly.exists():
                monthly.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(dst, monthly)
                new_monthly = monthly
            removed = prune(root, now.date())

            copied, copy_error = False, None
            if copy_dir:
                try:
                    _copy_offsite(dst, monthly, copy_dir, now.date())
                    copied = True
                except OSError as e:
                    copy_error = str(e)
    except BackupError as e:
        update_status(last_failure=now.isoformat(timespec="seconds"), last_error=str(e), last_reason=reason)
        log(f"ล้มเหลว ({reason}): {e}")
        raise
    except OSError as e:
        update_status(last_failure=now.isoformat(timespec="seconds"), last_error=str(e), last_reason=reason)
        log(f"ล้มเหลว ({reason}): {e}")
        raise BackupError(f"สำรองไม่สำเร็จ: {e}") from e

    st = read_status()
    values = {"last_success": now.isoformat(timespec="seconds"), "last_file": dst.name, "last_size": dst.stat().st_size,
              "last_reason": reason, "last_check": check, "first_success": st.get("first_success") or now.isoformat(timespec="seconds")}
    if copied:
        values.update(last_copy=now.isoformat(timespec="seconds"), last_copy_dir=str(copy_dir), last_copy_error=None)
    elif copy_error:
        values.update(last_copy_error=copy_error, last_copy_error_at=now.isoformat(timespec="seconds"))
    update_status(**values)
    log(f"สำเร็จ ({reason}): {dst.name} {dst.stat().st_size:,} ไบต์ · {check}"
        + (f" · เก็บรายเดือน {new_monthly.name}" if new_monthly else "")
        + (f" · ลบไฟล์เก่า {len(removed)} ไฟล์" if removed else ""))
    if copied:
        log(f"คัดลอกไป {copy_dir} แล้ว")
    elif copy_error:
        log(f"คัดลอกไป {copy_dir} ไม่ได้ (การสำรองในเครื่องยังสำเร็จ): {copy_error}")
    return {"file": dst, "monthly": new_monthly, "copied": copied, "copy_error": copy_error, "check": check}


# ---------------------------------------------------------------- รายการ / ดาวน์โหลด / เตือน

def list_backups():
    """{"daily": [...], "monthly": [...], "other": [...]} ไฟล์ใหม่สุดก่อน แต่ละรายการ {name, path, size, mtime}"""
    root = backup_dir()

    def files(d, pattern=None):
        out = []
        for p in d.glob("*.db") if d.is_dir() else []:
            if pattern and not pattern.fullmatch(p.name):
                continue
            st = p.stat()
            out.append({"name": p.name, "path": p, "size": st.st_size,
                        "mtime": datetime.fromtimestamp(st.st_mtime, logfiles.THAI_TZ)})
        return sorted(out, key=lambda f: f["name"], reverse=True)

    other = sorted(files(root), key=lambda f: f["mtime"], reverse=True)
    return {"daily": files(root / "daily", DAILY_RE), "monthly": files(root / "monthly", MONTHLY_RE), "other": other}


def latest_daily():
    daily = list_backups()["daily"]
    return daily[0]["path"] if daily else None


def same_disk_warning():
    """install.ps1 ตรวจตอนติดตั้งว่า BACKUP_COPY_DIR อยู่บนดิสก์จริงลูกเดียวกับ C: หรือไม่ (BACKUP_COPY_SAME_DISK ใน .env)"""
    if (get_setting("BACKUP_COPY_SAME_DISK") or "").strip().lower() not in ("1", "yes", "true"):
        return None
    drive = (copy_dir_setting() or Path("D:")).drive.rstrip(":") or "D"
    return f"ไดรฟ์ {drive} อยู่บนดิสก์เดียวกับ C ถ้าฮาร์ดดิสก์เสียจะหายทั้งคู่ ควรมีสำเนานอกเครื่องด้วย"


def set_gdrive(confirmed, username):
    """admin ติ๊ก/เอาติ๊กออก "ใช้ Google Drive สำรองโฟลเดอร์นี้แล้ว" (ระบบตรวจเองไม่ได้ เก็บวันที่ที่ติ๊กไว้)"""
    now = now_local().isoformat(timespec="seconds")
    if confirmed:
        update_status(gdrive_confirmed_at=now, gdrive_confirmed_by=username)
    else:
        update_status(gdrive_confirmed_at=None, gdrive_confirmed_by=None)
    log(f"Google Drive: {'ยืนยันว่าใช้' if confirmed else 'ยกเลิกการยืนยัน'} โดย {username}")


def mark_downloaded(name):
    now = now_local()
    update_status(last_download=now.isoformat(timespec="seconds"), last_download_file=name)
    log(f"ดาวน์โหลด {name} (ถือเป็นสำเนานอกเครื่อง)")


def warnings_enabled():
    return (get_setting("BACKUP_WARNING") or "on").strip().lower() not in ("off", "0", "no", "false")


def warnings(now=None):
    """แถบเตือนของ admin: [{"level": "danger"|"warn", "message"}]"""
    if not warnings_enabled():
        return []
    now = now or now_local()
    st = read_status()
    out = []
    last_ok = parse_time(st.get("last_success"))
    if last_ok is None or now - last_ok > timedelta(hours=FAIL_WARN_HOURS):
        when = f"สำเร็จครั้งล่าสุด {fmt(last_ok)}" if last_ok else "ยังไม่เคยสำรองสำเร็จ"
        err = st.get("last_error")
        out.append({"level": "danger", "message": f"สำรองข้อมูลไม่สำเร็จเกิน 2 วัน ({when})"
                    + (f" · ข้อผิดพลาดล่าสุด: {err}" if err else "")})
    # สำเนานอกเครื่อง = ติ๊กว่าใช้ Google Drive แล้ว หรือคัดลอกลงแฟลชไดรฟ์ภายใน 30 วัน
    if not st.get("gdrive_confirmed_at"):
        usb = parse_time(st.get("usb_last_copy"))
        since = usb or parse_time(st.get("first_success"))
        if since is not None and now - since > timedelta(days=OFFSITE_WARN_DAYS):
            days = (now - since).days
            out.append({"level": "warn", "message": f"ยังไม่มีสำเนานอกเครื่อง {days} วัน"
                        + (f" (คัดลอกลงแฟลชไดรฟ์ครั้งล่าสุด {fmt(usb)})" if usb else "")
                        + " · ตั้ง Google Drive หรือเสียบแฟลชไดรฟ์สำรองข้อมูล"})
    return out


def notices(now=None):
    """ข้อความแจ้ง (ไม่ใช่คำเตือน) ของ admin: แฟลชไดรฟ์ที่เสียบอยู่คัดลอกของวันนี้เสร็จแล้ว ถอดได้"""
    now = now or now_local()
    st = read_status()
    usb = parse_time(st.get("usb_last_copy"))
    if st.get("usb_present") and usb and usb.astimezone(logfiles.THAI_TZ).date() == now.date():
        return [{"level": "ok", "message": f"คัดลอกลงแฟลชไดรฟ์เรียบร้อย ถอดออกได้ ({st.get('usb_drive', '')} "
                                            f"{fmt(usb)})"}]
    return []


THAI_MONTHS_ABBR = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def fmt(t):
    """datetime -> '17 พ.ย. 2569 12:00 น.'"""
    if not t:
        return "-"
    t = t.astimezone(logfiles.THAI_TZ)
    return f"{t.day} {THAI_MONTHS_ABBR[t.month - 1]} {t.year + 543} {t:%H:%M} น."
