"""ระบบทะเบียนครุภัณฑ์ (ชื่อหน่วยงานตั้งที่หน้า ตั้งค่า > ทั่วไป)

รัน:  uv run uvicorn app.main:app --reload --reload-dir app --no-server-header
"""
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import backup, security_headers, usb, version
from app.path_ids import check_path_ids, not_found_on_overflow
from app.auth import Forbidden, LoginRequired, PasswordChangeRequired, SchemaOutdated, get_user, login_url, password_gate
from app.config import secret_key, session_cookie_name
from app.csrf import CsrfFailed, csrf_protect
from app.routers import (account, assets, auth, inventory, labels, locations, mobile, records, reports, review, settings, staff,
                         supplies, types, users)
from app.templating import templates

SESSION_MAX_AGE = 8 * 60 * 60  # ล็อกอินค้างได้ 8 ชั่วโมง (1 วันทำงาน)

# csrf_protect ตรวจทุก request ที่ไม่ใช่ GET (ทุกฟอร์มที่ POST ต้องมี csrf_token)
@asynccontextmanager
async def lifespan(app):
    """ตรวจแฟลชไดรฟ์สำรองข้อมูลทุก 1 นาทีระหว่างที่ server ทำงาน (ปิดได้ด้วย KRUPAN_USB_WATCH=off ซึ่ง test ตั้งไว้)"""
    version.write_server_info()
    usb.start()
    yield
    usb.stop()


app = FastAPI(title="ระบบทะเบียนครุภัณฑ์", docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(check_path_ids), Depends(csrf_protect), Depends(password_gate)], lifespan=lifespan)
# cookie แบบ signed + httponly (Starlette ตั้ง HttpOnly ให้เสมอ), SameSite=Lax เป็นด่านที่สองต่อจาก CSRF token
app.add_middleware(SessionMiddleware, secret_key=secret_key(), session_cookie=session_cookie_name(),
                   max_age=SESSION_MAX_AGE, same_site="lax", https_only=False)
# header ความปลอดภัย (CSP, X-Frame-Options ฯลฯ) ทุก response · ดู app/security_headers.py
app.middleware("http")(security_headers.middleware)
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")
for r in (auth, account, supplies, assets, review, types, locations, staff, users, mobile, labels, inventory, records, reports,
          settings):
    app.include_router(r.router)


# id ติดลบ / 0 / ใหญ่เกิน INTEGER ของ SQLite ใน URL ตอบ 404 (ดู app/path_ids.py)
app.add_exception_handler(OverflowError, not_found_on_overflow)


@app.exception_handler(LoginRequired)
def _login_required(request: Request, exc: LoginRequired):
    url = login_url(exc.next_url)
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@app.exception_handler(PasswordChangeRequired)
def _password_change_required(request: Request, exc: PasswordChangeRequired):
    url = "/account/password"
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


@app.exception_handler(Forbidden)
def _forbidden(request: Request, exc: Forbidden):
    return templates.TemplateResponse(request, "forbidden.html", {}, status_code=403)


@app.exception_handler(inventory.NotMigrated)
def _not_migrated(request: Request, exc: inventory.NotMigrated):
    return templates.TemplateResponse(request, "inventory/not_migrated.html", {}, status_code=503)


def pending_migrations():
    """ชื่อ migration ที่ค้าง (แสดงในหน้าแจ้งเตือน) · อ่านไม่ได้ก็ไม่เป็นไร"""
    try:
        from app.db import db_path
        from scripts import migrate
        return migrate.pending(db_path())
    except Exception:  # noqa: BLE001 - หน้าแจ้งเตือนต้องแสดงได้เสมอ
        return []


def not_migrated_page(request: Request):
    """หน้า "ต้องปรับฐานข้อมูลก่อน" (503) ห้ามพังซ้ำเป็น 500: ถ้าแสดงแบบเต็มหน้าไม่ได้ ใช้หน้าข้อความธรรมดา"""
    if request.headers.get("HX-Request"):
        return Response(status_code=503, headers={"HX-Refresh": "true"})  # โหลดทั้งหน้าใหม่ให้เห็นหน้าแจ้งเตือน
    if not hasattr(request.state, "user"):
        request.state.user = None  # เมนูของหน้าแจ้งเตือนไม่ต้องอ่านผู้ใช้จาก db ที่ยังไม่ครบ
    ctx = {"script": "migrate.py", "pending": pending_migrations()}
    try:
        return templates.TemplateResponse(request, "inventory/not_migrated.html", ctx, status_code=503)
    except Exception:  # noqa: BLE001
        return HTMLResponse("<!doctype html><meta charset='utf-8'><title>ต้องปรับฐานข้อมูลก่อน</title>"
                            "<h1>ต้องปรับโครงสร้างฐานข้อมูลก่อน</h1><p>ให้ผู้ดูแลระบบปิดโปรแกรมแล้วรัน "
                            r"<code>scripts\migrate.py</code> (หรือดับเบิลคลิก อัปเดต.bat) 1 ครั้ง</p>", status_code=503)


@app.exception_handler(SchemaOutdated)
def _schema_outdated_user(request: Request, exc: SchemaOutdated):
    return not_migrated_page(request)


@app.exception_handler(sqlite3.OperationalError)
def _schema_outdated(request: Request, exc: sqlite3.OperationalError):
    """db ยังไม่ได้รัน migration ล่าสุด (ไม่มีตาราง/คอลัมน์ใหม่): บอกวิธีแก้แทนหน้า error 500"""
    if "no such" not in str(exc):
        raise exc
    return not_migrated_page(request)


@app.exception_handler(CsrfFailed)
def _csrf_failed(request: Request, exc: CsrfFailed):
    if request.headers.get("HX-Request"):
        # htmx ไม่แสดงผลตอบ 403: ให้โหลดหน้าใหม่ (ได้ token ใหม่) พร้อมข้อความบอก
        request.session["flash"] = "หน้านี้เปิดค้างไว้นานเกินไป ระบบโหลดหน้าใหม่ให้แล้ว กรุณาทำรายการอีกครั้ง"
        return Response(status_code=403, headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "csrf_failed.html", {}, status_code=403)


@app.get("/health", include_in_schema=False)
def health(request: Request):
    """ตรวจว่า server ตอบได้ ไม่ต้องล็อกอิน
    ไม่ได้ล็อกอิน: ตอบแค่ "ok" · ล็อกอินแล้ว: ตามด้วย version และเวลาที่สำรองข้อมูลสำเร็จล่าสุด (เวลาไทย) หรือ none"""
    if get_user(request) is None:
        return PlainTextResponse("ok\n")
    st = backup.read_status()
    lines = ["ok", f"version={version.version()}", f"last_backup_ok={st.get('last_success') or 'none'}"]
    return PlainTextResponse("\n".join(lines) + "\n")


@app.get("/", include_in_schema=False)
def home(request: Request):
    """เข้าสู่ระบบแล้วไปหน้าวัสดุ (งานหลัก) · ยังไม่เข้าสู่ระบบไปหน้าทะเบียนครุภัณฑ์เหมือนเดิม
    (หน้าแรกแบบภาพรวมจะมาแทนใน 0.9.1)"""
    return RedirectResponse("/supplies" if get_user(request) else "/assets")
