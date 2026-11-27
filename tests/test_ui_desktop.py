"""0.8.3 หน้าจอคอม: ตารางหน้ารอตรวจสอบ (ความกว้างคอลัมน์คงที่ ป้ายกับค่าบรรทัดเดียวกัน รายละเอียด 2 บรรทัด ปุ่มเรียงแนวตั้ง)
และตัวเลือก “วันที่ได้มา” แบบ segmented control · การตรวจด้วยเบราว์เซอร์จริงอยู่ใน tests/ui_audit.py"""
import re

from tests.conftest import ROOT, login

CSS = (ROOT / "app" / "static" / "css" / "app.css").read_text(encoding="utf-8")
LONG_DETAILS = "MONITER SVOA 17 นิ้ว N476612487361196 จอ MASTER CASE BETTA ปค.134.70/50 ลว.8 ต.ค.50 จอ E 54 IBM SN 23 - LA 718"
LONG_FLAG = "Excel อ่านเป็น 1962-12-25 แก้เป็นปี พ.ศ. 2505 แล้ว"


def media_block(query):
    start = CSS.index(f"@media ({query})")
    depth, i = 0, CSS.index("{", start)
    for j in range(i, len(CSS)):
        depth += {"{": 1, "}": -1}.get(CSS[j], 0)
        if depth == 0:
            return CSS[i:j]
    raise AssertionError(query)


def test_review_columns_fixed_and_sum_to_100():
    block = media_block("min-width: 1024px")
    assert "table-layout: fixed" in block
    widths = dict(re.findall(r"col\.c-(\w+) \{ width: (\d+)%; \}", block))
    assert set(widths) == {"reg", "name", "flags", "facts", "source", "actions"}
    assert sum(int(v) for v in widths.values()) == 100
    # จอแคบกว่า 1024px เป็นการ์ด (คอลัมน์ปัญหาแคบเกินไปสำหรับป้ายเต็มคำ)
    assert ".review-table thead { display: none; }" in media_block("max-width: 1023.98px")


def test_review_row_markup(editor, db):
    db.execute("UPDATE assets SET details = ?, needs_review = ? WHERE id = 1", (LONG_DETAILS, "ไม่มีราคา, " + LONG_FLAG))
    db.commit()
    html = editor.get("/review?group=price").text
    assert '<col class="c-reg"><col class="c-name"><col class="c-flags"><col class="c-facts"><col class="c-source">' in html
    assert '<col class="c-actions">' in html
    # ป้ายกับค่าอยู่ในกล่องเดียวกัน (ไม่แยกบรรทัด)
    assert '<div class="fact"><span class="k">ราคา:</span> <span class="v">5,000</span></div>' in html
    # เลขทะเบียนตัดบรรทัดได้เฉพาะหลังขีด
    assert "ปค.99-<wbr>9999-<wbr>1-<wbr>1306-<wbr>0004" in html
    # รายละเอียดยาว: 2 บรรทัด + “ดูเพิ่ม” และชี้เมาส์เห็นข้อความเต็ม
    assert '<details class="more-details">' in html and '<span class="more-link">ดูเพิ่ม</span>' in html
    assert f'title="{LONG_DETAILS}"' in html
    # ปัญหาสั้นเป็นเม็ด ข้อความยาวทั้งประโยคเป็นกล่องที่ตัดบรรทัดระหว่างคำได้
    assert '<span class="flag flag-main" title="ไม่มีราคา">' in html
    assert f'<span class="tag flag-long" title="ปัญหาอื่นของรายการนี้: {LONG_FLAG}">' in html


def test_review_short_details_no_more_link(editor):
    html = editor.get("/review?group=price").text
    assert '<div class="clamp-2 muted small" title="Lucky">Lucky</div>' in html
    assert "more-details" not in html


def test_review_viewer_has_no_actions_column(client):
    login(client, "viewer1")
    html = client.get("/review?group=price").text
    assert '<col class="c-source">' in html and '<col class="c-actions">' not in html


def test_edit_date_precision_segmented(editor):
    html = editor.get("/assets/1/edit").text
    assert '<div class="seg" role="radiogroup" aria-label="รู้วันที่ได้มาแค่ไหน">' in html
    opts = re.findall(r'<label class="seg-opt"><input type="radio" name="date_precision" value="(\w+)"', html)
    assert opts == ["day", "month", "year", "none"]
    assert html.count('type="radio" name="date_precision"') == 4 and "checked><span>รู้วันที่</span>" in html
    assert 'class="check"' not in html.split('class="seg"')[1].split("</div>")[0]  # แบบเดิม (radio ยืดตามช่องกรอก) ไม่มีแล้ว


def test_css_option_rules():
    # radio / checkbox ขนาดเดียวกันทั้งระบบ
    assert re.search(r'input\[type="checkbox"\], input\[type="radio"\] \{[^}]*width: 20px; height: 20px;', CSS)
    seg = re.search(r"\.seg-opt \{([^}]*)\}", CSS).group(1)
    assert "min-height: 44px" in seg and "white-space: nowrap" in seg
    assert re.search(r"\.seg \{[^}]*flex-wrap: wrap", CSS)
    assert ".review-table .fact .k { white-space: nowrap; }" in CSS
