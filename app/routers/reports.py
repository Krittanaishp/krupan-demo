"""เมนูรายงาน /reports (ต้องล็อกอิน): รายละเอียดทรัพย์สิน, ได้มาในปี, สรุป, จำหน่ายในปี, export ทั้งหมด"""
import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from app import reports, reports_xlsx, settings_store, thai, timeutil
from app.auth import require_editor, require_user
from app.db import get_db
from app.templating import templates, thai_date

router = APIRouter()
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx_response(data, ascii_name, thai_name):
    return Response(data, media_type=XLSX, headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(thai_name)}"})


@router.get("/reports")
def index(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    cats = reports.categories(db)
    return templates.TemplateResponse(request, "reports/index.html", {
        "fy": thai.current_fiscal_year(), "categories": cats, "signers": settings_store.signers(db),
        "n_disposed": db.execute("SELECT COUNT(*) FROM assets WHERE status = 'จำหน่ายแล้ว'").fetchone()[0]})


@router.post("/reports/signers")
async def save_signers(request: Request, user=Depends(require_editor), db: sqlite3.Connection = Depends(get_db)):
    """จำชื่อและตำแหน่งในช่องลงชื่อท้ายรายงาน (htmx ส่งมาทุกครั้งที่แก้ช่อง)"""
    form = await request.form()
    value = []
    for i in range(len(settings_store.DEFAULT_SIGNERS)):
        name = " ".join(str(form.get(f"sign_name_{i}", "")).split())[:120]
        role = " ".join(str(form.get(f"sign_role_{i}", "")).split())[:120]
        value.append({"name": name, "role": role})
    with db:
        settings_store.put(db, settings_store.SIGNERS_KEY, value)
    return templates.TemplateResponse(request, "reports/_saved.html", {"at": timeutil.utcnow()})


def register_page(request, db, kind):
    """ข้อมูลรายงาน + การจัดหน้าแบบไฟล์ต้นฉบับ (ขอบ "ตรงต้นฉบับ" หรือ "ปลอดภัยสำหรับเครื่องพิมพ์")"""
    opts = reports.parse_options(request.query_params, reports.categories(db))
    data = reports.register(db, opts, kind)
    data["layout"] = reports.layout(kind, opts["margin"])
    return data


@router.get("/reports/register")
def register(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "reports/register.html", register_page(request, db, "register"))


@router.get("/reports/register.xlsx")
def register_xlsx(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    data = register_page(request, db, "register")
    fy = data["opts"]["fy"]
    return xlsx_response(reports_xlsx.register(data), f"register-{fy}.xlsx", f"รายละเอียดทรัพย์สิน ปีงบประมาณ {fy}.xlsx")


@router.get("/reports/acquired")
def acquired(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    return templates.TemplateResponse(request, "reports/register.html", register_page(request, db, "acquired"))


@router.get("/reports/acquired.xlsx")
def acquired_xlsx(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    data = register_page(request, db, "acquired")
    fy = data["opts"]["fy"]
    return xlsx_response(reports_xlsx.register(data), f"acquired-{fy}.xlsx", f"ครุภัณฑ์ได้มาในปีงบประมาณ {fy}.xlsx")


def area_choice(value):
    return value if value == "none" or value.isdigit() else ""


def summary_query(data):
    """query string ของหน้าสรุปตามตัวเลือกปัจจุบัน (ใช้กับลิงก์ Excel/รวมที่จำหน่ายแล้ว)"""
    from urllib.parse import urlencode
    return urlencode({k: v for k, v in (("disposed", "1" if data["include_disposed"] else ""),
                                        ("building", data["building"]), ("dept", data["dept"])) if v})


@router.get("/reports/summary")
def summary(request: Request, disposed: str = "0", building: str = "", dept: str = "",
            user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    data = reports.summary(db, disposed == "1", area_choice(building), area_choice(dept))
    names = lambda table: db.execute(f"SELECT id, name, active FROM {table} ORDER BY sort_order, name").fetchall()  # noqa: E731
    return templates.TemplateResponse(request, "reports/summary.html", {
        **data, "buildings": names("buildings"), "departments": names("departments"), "query": summary_query(data)})


@router.get("/reports/summary.xlsx")
def summary_xlsx(disposed: str = "0", building: str = "", dept: str = "",
                 user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    data = reports.summary(db, disposed == "1", area_choice(building), area_choice(dept))
    today = timeutil.today_th()
    stamp = f"{today.year + 543}{today:%m%d}"
    suffix = " รวมที่จำหน่ายแล้ว" if data["include_disposed"] else ""
    return xlsx_response(reports_xlsx.summary(data, thai_date(today.isoformat())),
                         f"summary-{stamp}.xlsx", f"สรุปครุภัณฑ์ {stamp}{suffix}.xlsx")


def fy_param(request):
    fy = thai.to_int(request.query_params.get("fy")) or thai.current_fiscal_year()
    return fy if 2400 <= fy <= 2700 else thai.current_fiscal_year()


@router.get("/reports/disposals")
def disposals(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    data = reports.disposals(db, fy_param(request), request.query_params.get("digits", "thai") != "arabic")
    return templates.TemplateResponse(request, "reports/disposals.html", data)


@router.get("/reports/disposals.xlsx")
def disposals_xlsx(request: Request, user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    data = reports.disposals(db, fy_param(request), request.query_params.get("digits", "thai") != "arabic")
    return xlsx_response(reports_xlsx.disposals(data), f"disposals-{data['fy']}.xlsx",
                         f"ครุภัณฑ์ที่จำหน่ายในปีงบประมาณ {data['fy']}.xlsx")


@router.get("/reports/export.xlsx")
def export_all(user=Depends(require_user), db: sqlite3.Connection = Depends(get_db)):
    today = timeutil.today_th()
    stamp = f"{today.year + 543}{today:%m%d}"
    return xlsx_response(reports_xlsx.export_all(db), f"krupan-export-{stamp}.xlsx", f"ทะเบียนครุภัณฑ์ทั้งหมด {stamp}.xlsx")
