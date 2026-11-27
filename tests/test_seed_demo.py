"""scripts/seed_demo.py สร้างข้อมูลสมมติครบด้วยคำสั่งเดียว (เลขทะเบียนและชื่อหน่วยงานเป็นค่าตัวอย่างเท่านั้น)"""
import json
import os
import re
import sqlite3
import subprocess
import sys

from app.security import verify_password
from tests.conftest import ROOT

DEMO_REG = re.compile(r"ปค\.99-9999-1-\d{4}-\d{4}")


def run_seed(tmp_path):
    db = tmp_path / "demo.db"
    env = dict(os.environ, KRUPAN_DB=str(db), KRUPAN_BACKUP_DIR=str(tmp_path / "b"), PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "scripts/seed_demo.py", "--no-env"], cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return db, out.stdout


def test_seed_creates_fictional_demo_data(tmp_path):
    db, out = run_seed(tmp_path)
    assert "ครุภัณฑ์ 72 ชิ้น" in out and "วัสดุ 20 รายการ" in out
    c = sqlite3.connect(db)
    one = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
    assert 50 <= one("SELECT COUNT(*) FROM assets") <= 80 and one("SELECT COUNT(*) FROM categories") >= 5
    assert one("SELECT COUNT(*) FROM assets WHERE unit_price IS NULL") > 0  # ไม่ทราบราคา = NULL ไม่ใช่ 0
    assert one("SELECT COUNT(*) FROM assets WHERE unit_price = 0") == 0
    assert one("SELECT COUNT(*) FROM supply_items") == 20 and one("SELECT COUNT(*) FROM supply_categories") == 6
    assert one("SELECT julianday(MAX(issue_date)) - julianday(MIN(issue_date)) FROM supply_issues") >= 60  # ย้อนหลังหลายเดือน
    assert one("SELECT COUNT(*) FROM supply_issues WHERE cancelled_at IS NOT NULL") == 1
    assert one("SELECT COUNT(*) FROM supply_receipts WHERE cancelled_at IS NOT NULL") == 1
    assert json.loads(one("SELECT value FROM app_settings WHERE key = 'ORG_DISPLAY_NAME'")) == "ที่ว่าการอำเภอตัวอย่าง"
    users = {r[0]: r for r in c.execute("SELECT username, role, password_hash, must_change_password FROM users")}
    for name, role, pw in (("admin", "admin", "demo-admin-2569"), ("editor", "editor", "demo-editor-2569"),
                           ("viewer", "viewer", "demo-viewer-2569")):
        assert users[name][1] == role and verify_password(users[name][2], pw) and users[name][3] == 0
    # เลขทะเบียนทุกชิ้นเป็นรหัสหน่วยงานตัวอย่าง 99-9999 · ชื่อหน่วยงานทุกช่องเป็น "อำเภอตัวอย่าง"
    regs = [r[0] for r in c.execute("SELECT reg_no FROM assets WHERE reg_no IS NOT NULL")]
    assert regs and all(DEMO_REG.fullmatch(r) for r in regs)
    for (v,) in c.execute("SELECT value FROM app_settings WHERE key LIKE 'ORG_%'"):
        assert json.loads(v).endswith("อำเภอตัวอย่าง")
    c.close()


def test_seed_refuses_existing_db_without_reset(tmp_path):
    db, _ = run_seed(tmp_path)
    env = dict(os.environ, KRUPAN_DB=str(db), KRUPAN_BACKUP_DIR=str(tmp_path / "b"), PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "scripts/seed_demo.py", "--no-env"], cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding="utf-8")
    assert out.returncode == 1 and "--reset" in out.stdout


def test_seed_production_forces_password_change(tmp_path):
    db = tmp_path / "prod.db"
    env = dict(os.environ, KRUPAN_DB=str(db), KRUPAN_BACKUP_DIR=str(tmp_path / "b"), PYTHONIOENCODING="utf-8")
    out = subprocess.run([sys.executable, "scripts/seed_demo.py", "--no-env", "--production"], cwd=ROOT, env=env,
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert "--production" in out.stdout
    c = sqlite3.connect(db)
    assert {r[0] for r in c.execute("SELECT must_change_password FROM users")} == {1}
    c.close()


def test_readme_warns_demo_passwords_are_public():
    for doc, word in (("README.md", "--production"), ("README.en.md", "--production")):
        text = (ROOT / doc).read_text(encoding="utf-8")
        assert word in text and ("คำเตือน" in text or "Warning" in text), doc
