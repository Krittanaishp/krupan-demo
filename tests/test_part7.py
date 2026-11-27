"""ส่วนที่ 7: จำหน่ายได้ทางเดียว, migration 007 + schema_migrations, export สรุป, สำรองข้อมูล, /health, ไฟล์ติดตั้ง"""
import json
import sqlite3
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app import backup, logfiles
from scripts import migrate
from scripts import backup as backup_cli
from tests.conftest import ROOT, SCHEMA, asset_form, audit_rows, login, make_db

TZ = logfiles.THAI_TZ


@pytest.fixture(autouse=True)
def tmp_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("KRUPAN_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("BACKUP_COPY_DIR", raising=False)
    monkeypatch.setenv("BACKUP_COPY_DIR", "")


# ---------------------------------------------------------------- ก.1 จำหน่ายได้ทางเดียว

def test_edit_form_has_no_disposed_option(editor):
    html = editor.get("/assets/1/edit").text
    assert '<option value="จำหน่ายแล้ว"' not in html and '<option value="รอจำหน่าย"' in html
    assert "บันทึกการจำหน่าย" in html


def test_edit_cannot_set_disposed(editor, db):
    r = editor.post("/assets/1/edit", data=asset_form(status="จำหน่ายแล้ว"))
    assert r.status_code == 400 and "ได้ทางปุ่ม “บันทึกการจำหน่าย”" in r.text
    assert db.execute("SELECT status FROM assets WHERE id = 1").fetchone()[0] == "ใช้งาน"
    assert audit_rows(db) == []


def test_disposed_asset_status_is_read_only(editor, db):
    db.execute("""INSERT INTO disposals (asset_id, disposed_at, method, doc_ref, lot_id, status_before)
                  VALUES (1, '2026-10-05', 'ขาย', 'มท 0309/99', 1, 'ใช้งาน')""")
    db.execute("UPDATE assets SET status = 'จำหน่ายแล้ว' WHERE id = 1")
    db.commit()
    html = editor.get("/assets/1/edit").text
    assert 'id="status" value="จำหน่ายแล้ว" readonly' in html and 'select id="status"' not in html
    assert 'href="/assets/1#disposal"' in html and "มท 0309/99" in html
    # ส่งสถานะอื่นมาเอง (แก้ HTML) ก็ไม่เปลี่ยน แต่ช่องอื่นยังแก้ได้
    r = editor.post("/assets/1/edit", data=asset_form(status="ใช้งาน", note="ขายทอดตลาด"), follow_redirects=False)
    assert r.status_code == 303
    row = db.execute("SELECT status, note FROM assets WHERE id = 1").fetchone()
    assert tuple(row) == ("จำหน่ายแล้ว", "ขายทอดตลาด")
    assert "status" not in json.loads(audit_rows(db)[-1]["changes"])


def test_qr_status_cannot_dispose(editor, db):
    page = editor.get("/q/99-9999-1-1306-0004").text
    assert 'value="จำหน่ายแล้ว"' not in page
    r = editor.post("/q/99-9999-1-1306-0004/status", data={"status": "จำหน่ายแล้ว"})
    assert r.status_code == 400 and db.execute("SELECT status FROM assets WHERE id = 1").fetchone()[0] == "ใช้งาน"
    db.execute("UPDATE assets SET status = 'จำหน่ายแล้ว' WHERE id = 6")
    db.commit()
    assert "ดูบันทึกการจำหน่าย" in editor.get("/q/99-9999-1-1306-0005").text
    r = editor.post("/q/99-9999-1-1306-0005/status", data={"status": "ใช้งาน"})
    assert r.status_code == 400 and db.execute("SELECT status FROM assets WHERE id = 6").fetchone()[0] == "จำหน่ายแล้ว"


# ---------------------------------------------------------------- ก.2 migration 007 / schema_migrations

def db_at_006(path):
    """db โครงสร้างแบบหลัง migration 006 (ยังไม่มี schema_migrations)"""
    make_db(path)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE schema_migrations")
    conn.executescript("""
        UPDATE assets SET status = 'จำหน่ายแล้ว', needs_review = NULL WHERE id = 3;
        UPDATE assets SET status = 'จำหน่ายแล้ว', needs_review = 'ไม่มีราคา' WHERE id = 4;
        UPDATE assets SET status = 'จำหน่ายแล้ว' WHERE id = 5;
        INSERT INTO disposals (asset_id, disposed_at, method, lot_id) VALUES (5, '2026-10-01', 'ขาย', 1);
        INSERT INTO disposals (asset_id, disposed_at, method, lot_id, cancelled_at, cancel_reason)
            VALUES (4, '2026-10-01', 'ขาย', 2, '2026-10-02', 'บันทึกผิด');
    """)
    conn.commit()
    conn.close()


def write_migration(scripts_dir, name, body):
    (scripts_dir / f"migrate_{name}.py").write_text(f'''
import sqlite3
from pathlib import Path
COUNT = Path(__file__).with_suffix(".count")

def run(path, backup_dir=None, out=print):
    COUNT.write_text(str(int(COUNT.read_text()) + 1 if COUNT.exists() else 1))
    conn = sqlite3.connect(path)
    with conn:
        {body}
        conn.execute("INSERT INTO schema_migrations (name, note) VALUES ('{name}', 'test')")
    conn.close()
    out("ran {name}")
    return path
''', encoding="utf-8")


def test_migration_runner_stops_if_script_does_not_record(tmp_path, monkeypatch):
    scripts_dir = tmp_path / "s"
    scripts_dir.mkdir()
    (scripts_dir / "migrate_909.py").write_text("def run(path, backup_dir=None, out=print):\n    return None\n",
                                                encoding="utf-8")
    monkeypatch.syspath_prepend(str(scripts_dir))
    path = tmp_path / "f.db"
    make_db(path)
    with pytest.raises(migrate.MigrationError, match="909"):
        migrate.run(path, out=lambda *_: None, scripts_dir=scripts_dir)


# ---------------------------------------------------------------- ก.3 export สรุป

def test_summary_xlsx(editor):
    r = editor.get("/reports/summary.xlsx")
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    ws = load_workbook(BytesIO(r.content)).active
    values = [c for row in ws.iter_rows(values_only=True) for c in row if c is not None]
    assert "สรุปครุภัณฑ์" in values and "แยกตามหมวด" in values and "แยกตามห้อง" in values and "แยกตามสถานะ" in values
    assert any(isinstance(v, str) and v.startswith("ทั้งหมด 6 ชิ้น") for v in values)
    # แถวหมวดตู้: 2 ชิ้น 10,000 บาท เป็นตัวเลขจริง
    rows = [row for row in ws.iter_rows(values_only=True)]
    assert ("ตู้", 2, 10000.0, 0) in [tuple(r[:4]) for r in rows]
    assert 'href="/reports/summary.xlsx"' in editor.get("/reports/summary").text
    assert "?disposed=1" in editor.get("/reports/summary?disposed=1").text


# ---------------------------------------------------------------- ค. สำรองข้อมูล

def fake_now(monkeypatch, when):
    monkeypatch.setattr(logfiles, "now_local", lambda: when)


def test_backup_makes_checked_copy_and_status(db_path, tmp_path, monkeypatch):
    fake_now(monkeypatch, datetime(2026, 11, 17, 12, 0, 5, tzinfo=TZ))
    r = backup.make_backup("test")
    assert r["file"].name == "krupan_20261117_120005.db" and r["file"].parent.name == "daily"
    assert r["monthly"].name == "krupan_202611.db"
    ok, msg = backup.integrity(r["file"])
    assert ok and "6 รายการ" in msg
    st = backup.read_status()
    assert st["last_success"] == "2026-11-17T12:00:05+07:00" and st["last_file"] == r["file"].name
    log = (tmp_path / "logs" / "backup-20261117.log").read_text(encoding="utf-8")
    assert "สำเร็จ (test)" in log


def test_backup_rejects_corrupt_database(tmp_path, monkeypatch):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200 + b"garbage" * 1000)
    monkeypatch.setenv("KRUPAN_BACKUP_DIR", str(tmp_path / "backups"))
    with pytest.raises(backup.BackupError):
        backup.make_backup("test", src=bad)
    assert list((tmp_path / "backups" / "daily").glob("*")) == []  # ไม่เหลือไฟล์ที่เสีย
    st = backup.read_status()
    assert st["last_error"] and "last_success" not in st


def test_integrity_check_detects_damage(db_path, tmp_path):
    copy = tmp_path / "copy.db"
    copy.write_bytes(db_path.read_bytes())
    data = bytearray(copy.read_bytes())
    page = 4096
    for i in range(page * 2, min(len(data), page * 6)):  # ทำลายหน้าข้อมูลกลางไฟล์
        data[i] = 0xFF
    copy.write_bytes(bytes(data))
    ok, msg = backup.integrity(copy)
    assert not ok


def touch(root, sub, name):
    p = root / sub / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_retention_30_days_and_12_months(tmp_path):
    from datetime import date
    root = tmp_path / "b"
    today = date(2026, 11, 17)
    for d in range(0, 40):
        day = today - timedelta(days=d)
        for hhmm in ("1200", "1630"):
            touch(root, "daily", f"krupan_{day:%Y%m%d}_{hhmm}00.db")
    touch(root, "daily", "krupan_20261117_080000.db")
    keep_other = touch(root, "daily", "notes.db")  # ชื่อไม่ตรงรูปแบบ ไม่แตะ
    for m in range(18):
        y, mo = divmod(2026 * 12 + 10 - m, 12)
        touch(root, "monthly", f"krupan_{y}{mo + 1:02d}.db")
    backup.prune(root, today)
    daily = sorted(p.name for p in (root / "daily").glob("krupan_*.db"))
    # วันนี้เก็บทั้ง 3 ไฟล์, ย้อนหลัง 29 วันเก็บไฟล์สุดท้ายของวัน (16:30), เก่ากว่านั้นลบ
    assert [n for n in daily if n.startswith("krupan_20261117")] == \
        ["krupan_20261117_080000.db", "krupan_20261117_120000.db", "krupan_20261117_163000.db"]
    older = [n for n in daily if not n.startswith("krupan_20261117")]
    assert len(older) == 29 and all(n.endswith("_163000.db") for n in older)
    assert min(daily) == "krupan_20261019_163000.db"  # 17 พ.ย. ย้อนไป 29 วัน
    assert keep_other.exists()
    monthly = sorted(p.name for p in (root / "monthly").glob("*.db"))
    assert len(monthly) == 12 and monthly[0] == "krupan_202512.db" and monthly[-1] == "krupan_202611.db"


def test_copy_dir_failure_is_logged_not_fatal(db_path, tmp_path, monkeypatch):
    fake_now(monkeypatch, datetime(2026, 11, 17, 16, 30, tzinfo=TZ))
    r = backup.make_backup("test", copy_dir=tmp_path / "no-such-drive")
    assert r["file"].exists() and not r["copied"] and "ไม่พบโฟลเดอร์" in r["copy_error"]
    st = backup.read_status()
    assert st["last_success"] and "ไม่พบโฟลเดอร์" in st["last_copy_error"] and "last_copy" not in st
    assert "คัดลอกไป" in (tmp_path / "logs" / "backup-20261117.log").read_text(encoding="utf-8")

    target = tmp_path / "usb"
    target.mkdir()
    r = backup.make_backup("test", copy_dir=target)
    assert r["copied"] and (target / "daily" / r["file"].name).exists() and (target / "monthly" / "krupan_202611.db").exists()
    assert backup.read_status()["last_copy"]


def test_backup_cli(db_path, capsys):
    assert backup_cli.main(["--reason", "cli"]) == 0
    assert "สำเร็จ" in capsys.readouterr().out
    assert backup_cli.main(["--db", str(db_path.parent / "missing.db")]) == 1


def test_lock_blocks_concurrent_backup(db_path, tmp_path):
    lock = Path(backup.backup_dir()) / ".backup.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("123")
    with pytest.raises(backup.BackupError, match="กำลังทำอยู่"):
        backup.make_backup("test")


def test_warnings_red_after_two_days(db_path, monkeypatch):
    t0 = datetime(2026, 11, 1, 12, 0, tzinfo=TZ)
    assert [w["level"] for w in backup.warnings(t0)] == ["danger"]  # ยังไม่เคยสำรอง
    fake_now(monkeypatch, t0)
    backup.make_backup("test", copy_dir=None)
    assert backup.warnings(t0 + timedelta(hours=47)) == []
    assert [w["level"] for w in backup.warnings(t0 + timedelta(hours=49))] == ["danger"]
    monkeypatch.setenv("BACKUP_WARNING", "off")
    assert backup.warnings(t0 + timedelta(days=90)) == []


def test_admin_sees_backup_warning_on_every_page(client):
    login(client, "admin1")
    for url in ("/assets", "/reports", "/q/99-9999-1-1306-0004"):
        html = client.get(url).text
        assert 'class="backup-warning backup-warning-danger"' in html and "ยังไม่เคยสำรองสำเร็จ" in html, url
    client.post("/logout")
    login(client)
    assert "backup-warning" not in client.get("/assets").text


def test_settings_backup_page_run_and_download(client, tmp_path):
    login(client, "admin1")
    page = client.get("/settings/backup")
    assert page.status_code == 200 and "สำรองตอนนี้" in page.text and "ยังไม่เคยสำรองสำเร็จ" in page.text
    r = client.post("/settings/backup/run", follow_redirects=True)
    assert "สำรองข้อมูลแล้ว: krupan_" in r.text and "backup-warning-danger" not in r.text
    assert "ดาวน์โหลดไฟล์สำรองล่าสุด" in r.text
    d = client.get("/settings/backup/latest")
    assert d.status_code == 200 and d.content.startswith(b"SQLite format 3")
    assert "attachment" in d.headers["content-disposition"]
    assert backup.read_status()["last_download"]


def test_settings_backup_admin_only(editor):
    assert editor.get("/settings/backup").status_code == 403
    assert editor.post("/settings/backup/run").status_code == 403
    assert editor.get("/settings/backup/latest").status_code == 403


def test_health_reports_last_backup(editor, monkeypatch):
    """เวลาสำรองข้อมูลล่าสุดแสดงเฉพาะเมื่อล็อกอินแล้ว (ไม่ล็อกอินได้แค่ "ok")"""
    fake_now(monkeypatch, datetime(2026, 11, 17, 12, 0, 0, tzinfo=TZ))
    backup.make_backup("test", copy_dir=None)
    lines = editor.get("/health").text.splitlines()
    assert lines[0] == "ok" and "last_backup_ok=2026-11-17T12:00:00+07:00" in lines


def test_daily_log_files_pruned(tmp_path):
    from datetime import date
    d = logfiles.log_dir()
    d.mkdir(parents=True)
    for i in range(35):
        (d / f"server-{date(2026, 11, 17) - timedelta(days=i):%Y%m%d}.log").write_text("x")
    (d / "backup-20200101.log").write_text("x")
    logfiles.prune("server", date(2026, 11, 17))
    assert len(list(d.glob("server-*.log"))) == 30 and (d / "backup-20200101.log").exists()


# ---------------------------------------------------------------- ไฟล์ของชุดติดตั้ง


def test_gitignore_keeps_runtime_data_out():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("logs/", "dist/", ".env", "*.db", "backups/", "exports/", ".vscode/", "data/", "*.xlsx", "*.xls", "*.pdf"):
        assert pattern in ignore, pattern
