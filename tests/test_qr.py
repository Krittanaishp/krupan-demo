"""หน้าสแกน QR (/q/{code}), ปุ่มลัดบนมือถือ, การสร้าง QR และหน้าตั้งค่าเครือข่าย"""
import json

import pytest

from app import network, qr
from tests.conftest import add_location, add_staff, asset_form, audit_rows, login


# ---------------------------------------------------------------- เปิดหน้าจากรหัสใน QR

def test_open_by_reg_no_without_prefix(client):
    r = client.get("/q/99-9999-1-1306-0004")
    assert r.status_code == 200
    assert "ปค.99-9999-1-1306-0004" in r.text and "ตู้เหล็ก 4 ลิ้นชัก" in r.text
    assert "ห้องปกครอง" in r.text and "ใช้งาน" in r.text


@pytest.mark.parametrize("code", ["ปค.99-9999-1-1306-0004", "%E0%B8%9B%E0%B8%84.99-9999-1-1306-0004",
                                  "๙๙-๙๙๙๙-๑-๑๓๐๖-๐๐๐๔"])
def test_open_by_reg_no_variants(client, code):
    assert client.get(f"/q/{code}").status_code == 200


@pytest.mark.parametrize("code", ["TMP-0001", "tmp-0001"])
def test_open_by_temp_code(client, code):
    r = client.get(f"/q/{code}")
    assert r.status_code == 200 and "โต๊ะทำงานเหล็ก" in r.text and "TMP-0001" in r.text


def test_old_tmp_qr_still_opens_after_reg_no_assigned(editor, db):
    form = asset_form(reg_no="ปค.99-9999-1-1401-0099", name="โต๊ะทำงานเหล็ก", type_code="1401",
                      date_precision="none", fiscal_year="", unit_price="", details="", location_text="ห้องทะเบียน",
                      note="โครงการ")
    assert editor.post("/assets/2/edit", data=form, follow_redirects=False).status_code == 303
    r = editor.get("/q/TMP-0001")
    assert r.status_code == 200
    assert "ปค.99-9999-1-1401-0099" in r.text and "รหัสชั่วคราวเดิม TMP-0001" in r.text
    assert editor.get("/q/99-9999-1-1401-0099").status_code == 200


def test_tmp_found_from_note_if_temp_code_missing(client, db):
    db.execute("UPDATE assets SET temp_code = NULL, reg_no = 'ปค.99-9999-1-1401-0098', "
               "note = 'โครงการ | รหัสชั่วคราวเดิม TMP-0001 (QR ที่ติดไว้ยังใช้รหัสนี้)' WHERE id = 2")
    db.commit()
    assert "โต๊ะทำงานเหล็ก" in client.get("/q/TMP-0001").text


@pytest.mark.parametrize("code", ["99-9999-1-9999-9999", "TMP-9999", "x", "%20"])
def test_unknown_code_shows_polite_page(client, code):
    r = client.get(f"/q/{code}")
    assert r.status_code == 404
    assert "ไม่พบครุภัณฑ์" in r.text and "ขออภัย" in r.text and 'action="/assets"' in r.text


def test_mobile_page_layout(client):
    html = client.get("/q/99-9999-1-1306-0004").text
    assert 'name="viewport"' in html and 'class="q-body"' in html
    assert 'class="nav"' not in html  # ไม่มีเมนูเต็ม
    assert "เข้าสู่ระบบเพื่อเปลี่ยนสถานะ" in html and "/login?next=/q/99-9999-1-1306-0004" in html
    assert "/q/99-9999-1-1306-0004/status" not in html  # ยังไม่ล็อกอิน: ไม่มีปุ่มลัด


def test_editor_sees_quick_actions(editor):
    html = editor.get("/q/99-9999-1-1306-0004").text
    assert "เปลี่ยนสถานะ" in html and "ย้ายห้อง" in html
    assert 'action="/q/99-9999-1-1306-0004/status"' in html and 'name="csrf_token"' in html


def test_viewer_sees_no_quick_actions(client):
    login(client, "viewer1")
    html = client.get("/q/99-9999-1-1306-0004").text
    assert "/status" not in html and "ดูได้อย่างเดียว" in html


# ---------------------------------------------------------------- ปุ่มลัด

@pytest.mark.parametrize("action,data", [("status", {"status": "ชำรุด"}), ("move", {"location_id": "", "holder_id": ""})])
def test_quick_actions_require_login(client, db, action, data):
    r = client.post(f"/q/99-9999-1-1306-0004/{action}", data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    login(client, "viewer1")
    assert client.post(f"/q/99-9999-1-1306-0004/{action}", data=data).status_code == 403
    assert db.execute("SELECT status FROM assets WHERE id = 1").fetchone()[0] == "ใช้งาน"
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_quick_status_change(editor, db):
    r = editor.post("/q/99-9999-1-1306-0004/status", data={"status": "ชำรุด"})
    assert r.status_code == 200 and "เปลี่ยนสถานะเป็น “ชำรุด” แล้ว" in r.text
    assert db.execute("SELECT status FROM assets WHERE id = 1").fetchone()[0] == "ชำรุด"
    ch = json.loads(audit_rows(db)[-1]["changes"])
    assert ch == {"status": {"old": "ใช้งาน", "new": "ชำรุด"}, "via": "qr"}


def test_quick_status_invalid(editor, db):
    r = editor.post("/q/99-9999-1-1306-0004/status", data={"status": "หายไปไหน"})
    assert r.status_code == 400 and "เลือกสถานะ" in r.text
    assert audit_rows(db) == []


def test_quick_move_records_audit_and_move(editor, db):
    old = add_location(db, "ห้องปกครอง")
    new = add_location(db, "ห้องประชุม")
    sid = add_staff(db, nickname="เอ็ม")
    db.execute("UPDATE assets SET location_id = ? WHERE id = 1", (old,))
    db.commit()
    r = editor.post("/q/99-9999-1-1306-0004/move", data={"location_id": str(new), "holder_id": str(sid)})
    assert "บันทึกแล้ว: ห้องประชุม" in r.text
    a = db.execute("SELECT location_id, holder_id FROM assets WHERE id = 1").fetchone()
    assert (a[0], a[1]) == (new, sid)
    m = db.execute("SELECT * FROM asset_moves WHERE asset_id = 1").fetchone()
    assert (m["from_location_id"], m["to_location_id"], m["to_holder_id"], m["reason"]) == (old, new, sid, "ย้ายห้อง")
    ch = json.loads(audit_rows(db)[-1]["changes"])
    assert ch["location_id"]["new_label"] == "ห้องประชุม" and ch["via"] == "qr"


def test_quick_move_by_tmp_code_and_inactive_room_rejected(editor, db):
    closed = add_location(db, "ห้องปิด", active=0)
    r = editor.post("/q/TMP-0001/move", data={"location_id": str(closed), "holder_id": ""})
    assert r.status_code == 400 and "ปิดใช้งาน" in r.text
    assert db.execute("SELECT COUNT(*) FROM asset_moves").fetchone()[0] == 0


# ---------------------------------------------------------------- สร้าง QR

def test_qr_code_and_url():
    assert qr.code_for({"reg_no": "ปค.99-9999-1-1201-0006", "temp_code": None}) == "99-9999-1-1201-0006"
    assert qr.code_for({"reg_no": None, "temp_code": "TMP-0001"}) == "TMP-0001"
    assert qr.url_for("TMP-0001") == "http://192.0.2.10:8000/q/TMP-0001"


def test_qr_svg_uses_error_correction_m(monkeypatch):
    seen = {}
    real = qr.qrcode.QRCode

    def spy(**kw):
        seen.update(kw)
        return real(**kw)
    monkeypatch.setattr(qr.qrcode, "QRCode", spy)
    s = qr.svg("http://192.0.2.10:8000/q/99-9999-1-1201-0006")
    assert seen["error_correction"] == qr.qrcode.constants.ERROR_CORRECT_M
    assert s.startswith('<svg class="qr"') and "<path" in s and 'id="qr-path"' not in s
    assert 'width="' not in s.split(">", 1)[0]  # ขนาดกำหนดด้วย CSS (มม.)


# ---------------------------------------------------------------- ตั้งค่าเครือข่าย

def test_network_page_admin_only(client):
    login(client, "editor1")
    assert client.get("/settings/network").status_code == 403


def test_network_page_warns_when_ip_differs(admin, monkeypatch):
    monkeypatch.setattr(network, "local_ipv4s", lambda: ["192.168.1.77"])
    html = admin.get("/settings/network").text
    assert "http://192.0.2.10:8000" in html and "192.168.1.77" in html
    assert "alert-danger" in html and "QR ที่พิมพ์ไปแล้วอาจใช้ไม่ได้" in html
    assert "BASE_URL=http://192.168.1.77:8000" in html  # แนะนำค่าที่ถูก


def test_network_page_ok_when_ip_matches(admin, monkeypatch):
    monkeypatch.setattr(network, "local_ipv4s", lambda: ["192.0.2.10"])
    html = admin.get("/settings/network").text
    assert "QR ที่พิมพ์ไปแล้วอาจใช้ไม่ได้" not in html and "ตรงกับ IP ของเครื่องนี้" in html


@pytest.mark.parametrize("url,ok", [("", False), ("http://127.0.0.1:8000", False), ("192.168.1.5:8000", False),
                                    ("http://192.168.1.5:8000", True), ("http://192.168.1.6:8000", False)])
def test_network_check(url, ok):
    assert network.check(url, ["192.168.1.5"])[0] is ok
