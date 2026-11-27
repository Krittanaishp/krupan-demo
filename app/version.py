"""เวอร์ชันของโปรแกรม: ชุดติดตั้งมีไฟล์ VERSION.txt (make_release.ps1 เขียนให้) เครื่องพัฒนาอ่านจาก pyproject.toml"""
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def info():
    """{"version": "0.7.0", "commit": "abc1234", "date": "20261117"} (commit/date มีเฉพาะชุดติดตั้ง)"""
    out = {}
    try:
        for line in (ROOT / "VERSION.txt").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    if not out.get("version"):
        try:
            m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
            out["version"] = (m.group(1) if m else "0") + "-dev"
        except OSError:
            out["version"] = "unknown"
    return out


def version():
    return info()["version"]


SERVER_INFO = "server-info.txt"


def write_server_info():
    """ตอน server เปิด: เขียน logs/server-info.txt (version, pid, เวลาเริ่ม)
    install.ps1 / update.ps1 อ่านไฟล์นี้เพื่อยืนยันว่า server ที่ตอบ /health เป็นเวอร์ชันใหม่
    (/health ตอบแค่ "ok" ให้คนที่ไม่ได้ล็อกอิน) · เขียนไม่ได้ก็ไม่เป็นไร server ยังทำงานต่อ"""
    import os
    from datetime import datetime

    from app import logfiles
    try:
        d = logfiles.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        text = f"version={version()}\npid={os.getpid()}\nstarted={datetime.now():%Y-%m-%d %H:%M:%S}\n"
        tmp = d / (SERVER_INFO + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, d / SERVER_INFO)
    except OSError:
        pass
