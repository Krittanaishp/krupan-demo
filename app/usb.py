"""สำรองลงแฟลชไดรฟ์อัตโนมัติ (เลือกใช้ได้ ไม่บังคับ)

- หาไดรฟ์จากชื่อ Volume Label (BACKUP_USB_LABEL ใน .env ค่าเริ่มต้น KRUPAN-BACKUP) ไม่ใช้อักษรไดรฟ์ตายตัว
  ตั้งชื่อแฟลชไดรฟ์ด้วย "เตรียมแฟลชไดรฟ์.bat" (ไม่ format ไม่ลบข้อมูล)
- server ตรวจทุก 1 นาที (watch_forever ใน thread แยก) ถ้าเสียบอยู่และวันนี้ยังไม่ได้คัดลอก
  คัดลอกไฟล์สำรองล่าสุดลงไปทันที:  <ไดรฟ์>\\KrupanBackup\\latest\\  เก็บ 1 ไฟล์ (ไฟล์ล่าสุด)
                                    <ไดรฟ์>\\KrupanBackup\\monthly\\ krupan_YYYYMM.db เก็บ 12 เดือน
- สถานะอยู่ใน backups/status.json (usb_present, usb_drive, usb_last_copy, usb_last_error)
  หน้าเว็บของ admin ขึ้น "คัดลอกลงแฟลชไดรฟ์เรียบร้อย ถอดออกได้" (backup.notices)
test ตั้ง KRUPAN_USB_WATCH=off (ไม่ตรวจไดรฟ์จริงของเครื่อง) และส่ง find_drive ปลอมเข้ามาเอง
"""
import os
import shutil
import sys
import threading
from pathlib import Path

from app import backup
from app.config import get_setting

DEFAULT_LABEL = "KRUPAN-BACKUP"
FOLDER = "KrupanBackup"
MONTHLY_KEEP = 12
CHECK_SECONDS = 60


def label_setting():
    return (get_setting("BACKUP_USB_LABEL") or DEFAULT_LABEL).strip()


def windows_drives():
    """[(root เช่น 'E:\\\\', label, ชนิดไดรฟ์)] ไดรฟ์ที่มีอักษรทุกตัว ยกเว้นไดรฟ์ของ Windows (ใช้ Win32 API ไม่ต้องเปิด PowerShell)"""
    if sys.platform != "win32":
        return []
    import ctypes
    k32 = ctypes.windll.kernel32
    system = os.environ.get("SystemDrive", "C:").upper()
    mask = k32.GetLogicalDrives()
    out = []
    old_mode = k32.SetErrorMode(1)  # SEM_FAILCRITICALERRORS: ไดรฟ์ว่าง (เช่นเครื่องอ่านการ์ด) ไม่ขึ้นหน้าต่างถาม
    try:
        for i in range(26):
            if not mask & (1 << i):
                continue
            root = f"{chr(65 + i)}:\\"
            if root[:2].upper() == system:
                continue
            kind = k32.GetDriveTypeW(root)
            if kind not in (2, 3):  # 2 = removable, 3 = fixed (ฮาร์ดดิสก์ USB บางรุ่นรายงานเป็น fixed)
                continue
            buf = ctypes.create_unicode_buffer(261)
            if k32.GetVolumeInformationW(root, buf, 261, None, None, None, None, 0):
                out.append((root, buf.value, kind))
    finally:
        k32.SetErrorMode(old_mode)
    return out


def find_drive(label=None):
    """root ของไดรฟ์ที่มีชื่อ label (ไม่สนตัวพิมพ์เล็กใหญ่ เพราะ FAT32 เก็บเป็นตัวใหญ่) หรือ None"""
    label = (label or label_setting()).upper()
    for root, name, _ in windows_drives():
        if name.upper() == label:
            return Path(root)
    return None


def _copy_checked(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part")
    shutil.copyfile(src, tmp)
    ok, msg = backup.integrity(tmp)
    if not ok:
        tmp.unlink(missing_ok=True)
        raise OSError(f"ไฟล์ที่คัดลอกลงแฟลชไดรฟ์ไม่ผ่าน integrity_check: {msg}")
    os.replace(tmp, dst)


def copy_to(root, src, now):
    """คัดลอก src ลง <root>/KrupanBackup: latest/ เหลือ 1 ไฟล์, monthly/ ไฟล์ของเดือนนี้ (ถ้ายังไม่มี) เก็บ 12 เดือน"""
    base = Path(root) / FOLDER
    latest = base / "latest"
    _copy_checked(src, latest / src.name)
    for old in latest.glob("krupan_*.db"):
        if old.name != src.name:
            old.unlink(missing_ok=True)
    monthly = base / "monthly" / f"krupan_{now:%Y%m}.db"
    if not monthly.exists():
        _copy_checked(src, monthly)
    months = sorted((p for p in (base / "monthly").glob("krupan_*.db") if backup.MONTHLY_RE.fullmatch(p.name)),
                    key=lambda p: p.name)
    for old in months[:-MONTHLY_KEEP]:
        old.unlink(missing_ok=True)
    return latest / src.name


def check_once(find=find_drive, now=None):
    """ตรวจ 1 รอบ คืนข้อความสั้น ๆ ว่าทำอะไร (บันทึกสถานะ/ log ไว้แล้ว)"""
    now = now or backup.now_local()
    st = backup.read_status()
    root = find()
    if root is None:
        if st.get("usb_present"):
            backup.update_status(usb_present=False)
            backup.log("แฟลชไดรฟ์ถูกถอดออกแล้ว")
        return "ไม่มีแฟลชไดรฟ์"
    drive = str(root)[:2]
    if not st.get("usb_present") or st.get("usb_drive") != drive:
        backup.update_status(usb_present=True, usb_drive=drive)
        backup.log(f"พบแฟลชไดรฟ์ {label_setting()} ที่ {drive}")
    last = backup.parse_time(st.get("usb_last_copy"))
    if last and last.astimezone(now.tzinfo).date() == now.date():
        return "วันนี้คัดลอกแล้ว"
    src = backup.latest_daily()
    if src is None:
        return "ยังไม่มีไฟล์สำรอง"
    try:
        copied = copy_to(root, src, now)
    except OSError as e:
        backup.update_status(usb_last_error=str(e), usb_last_error_at=now.isoformat(timespec="seconds"))
        backup.log(f"คัดลอกลงแฟลชไดรฟ์ {drive} ไม่ได้: {e}")
        return "คัดลอกไม่ได้"
    backup.update_status(usb_last_copy=now.isoformat(timespec="seconds"), usb_last_file=src.name,
                         usb_last_error=None)
    backup.log(f"คัดลอก {src.name} ลงแฟลชไดรฟ์ {drive} แล้ว ({copied})")
    return "คัดลอกแล้ว"


def watch_enabled():
    return (os.environ.get("KRUPAN_USB_WATCH") or "on").lower() not in ("off", "0", "no", "false")


def watch_forever(stop: threading.Event, interval=CHECK_SECONDS):
    while not stop.is_set():
        try:
            check_once()
        except Exception as e:  # noqa: BLE001 - ตัวตรวจต้องไม่ทำให้ server ล้ม
            try:
                backup.log(f"ตรวจแฟลชไดรฟ์ผิดพลาด: {e}")
            except Exception:  # noqa: BLE001
                pass
        stop.wait(interval)


_stop = threading.Event()
_thread = None


def start():
    """เรียกตอน server เริ่ม (app.main) ทำงานใน thread แยก หยุดเมื่อ server ปิด"""
    global _thread
    if not watch_enabled() or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=watch_forever, args=(_stop,), name="usb-backup", daemon=True)
    _thread.start()


def stop():
    _stop.set()

