"""
รัน server แบบใช้งานจริง (ไม่มี --reload) เขียน log ลง logs/server-YYYYMMDD.log เก็บ 30 วัน

ปกติ scripts/run_server.ps1 เรียกไฟล์นี้ (Task Scheduler "KrupanServer" เรียก run_server.ps1 อีกที)
    .venv\\Scripts\\python.exe scripts\\serve.py --port 8000
"""
import argparse
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import logfiles  # noqa: E402

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def log_config(console=True):
    handlers = {"file": {"()": "app.logfiles.DailyFileHandler", "name": "server", "formatter": "default"}}
    if console:
        handlers["console"] = {"class": "logging.StreamHandler", "formatter": "default", "stream": "ext://sys.stderr"}
    names = list(handlers)
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"default": {"format": FORMAT}},
        "handlers": handlers,
        "loggers": {
            "uvicorn": {"handlers": names, "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            "uvicorn.access": {"handlers": names, "level": "INFO", "propagate": False},
        },
        "root": {"handlers": names, "level": "WARNING"},
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="รัน server แบบใช้งานจริง")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args(argv)
    console = bool(sys.stderr and sys.stderr.isatty())  # รันจาก Task Scheduler ไม่มีหน้าต่าง: เขียนลงไฟล์อย่างเดียว
    try:
        import uvicorn
        uvicorn.run("app.main:app", host=args.host, port=args.port, log_config=log_config(console),
                    workers=1, proxy_headers=False, server_header=False)
    except Exception:  # noqa: BLE001 - เปิดไม่ขึ้น (เช่น ไม่มี .env / port ถูกใช้อยู่) ต้องเหลือร่องรอยใน log
        logfiles.write("server", "เปิด server ไม่ได้:\n" + traceback.format_exc())
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
