"""id ใน URL ที่เป็นไปไม่ได้ (ติดลบ, 0, ใหญ่เกิน INTEGER ของ SQLite) ตอบ 404 ทุก route ไม่ใช่ 500

- check_path_ids: dependency ระดับ app ตรวจทุก path parameter ที่ประกาศเป็น int ก่อนถึง route
- not_found_on_overflow: กันอีกชั้นสำหรับเลขที่ route แปลงเองจากข้อความ (เช่น ห้องในหน้าตรวจสอบพัสดุ)
  sqlite3 แปลงเลขที่เกิน 64 bit ไม่ได้ (OverflowError) ถือว่าไม่พบ
"""
import inspect

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

SQLITE_MAX = 2 ** 63 - 1
_int_params = {}


def int_params(route):
    """ชื่อ path parameter ที่ route ประกาศเป็น int (จำไว้ต่อ route)"""
    key = id(route)
    if key not in _int_params:
        names = set()
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            sig = inspect.signature(endpoint)
            names = {n for n, p in sig.parameters.items() if p.annotation is int and "{" + n in getattr(route, "path", "")}
        _int_params[key] = names
    return _int_params[key]


def check_path_ids(request: Request):
    route = request.scope.get("route")
    if route is None:
        return
    for name in int_params(route):
        raw = str(request.path_params.get(name, "")).strip()
        try:
            value = int(raw)
        except ValueError:
            continue  # ไม่ใช่ตัวเลข: FastAPI ตอบ 422 เอง
        if value < 1 or value > SQLITE_MAX:
            raise HTTPException(status_code=404)


def not_found_on_overflow(request: Request, exc: OverflowError):
    return JSONResponse({"detail": "Not Found"}, status_code=404)
