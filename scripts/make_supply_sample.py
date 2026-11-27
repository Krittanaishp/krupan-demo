"""
สร้างไฟล์ตัวอย่างสำหรับทดลองนำเข้าวัสดุ (ข้อมูลสมมติทั้งหมด ไม่ใช่ของจริง) ตามแบบฟอร์มมาตรฐานของระบบ

- ตัวอย่างนำเข้าวัสดุ_ข้อมูลสมมติ.xlsx        ไฟล์ที่ถูกต้อง 24 รายการ 5 หมวด (มียอดยกมา)
- ตัวอย่างนำเข้าวัสดุ_มีปัญหา_ข้อมูลสมมติ.xlsx  มีทุกกรณีที่หน้าตรวจต้องจับได้ (ชื่อซ้ำในไฟล์ แถวอ่านไม่ได้ ฯลฯ)
  นำเข้าไฟล์แรกก่อน แล้วลองไฟล์นี้ จะเห็นกลุ่ม "มีอยู่แล้วและตรงกัน" และ "มีอยู่แล้วแต่ต่าง" ด้วย

วิธีใช้ (ไฟล์อยู่ใน exports\\ ซึ่งไม่ขึ้น git):
    uv run python scripts/make_supply_sample.py [โฟลเดอร์ปลายทาง]
ลองนำเข้ากับสำเนา db เท่านั้น อย่านำเข้าข้อมูลสมมติใน db จริง
"""
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook  # noqa: E402

from app import supplies_import as si  # noqa: E402

GOOD = [
    ["เครื่องเขียน", "ปากกาลูกลื่น สีน้ำเงิน", "ด้าม", 20, "กล่อง", 50, None, None, 135, None],
    ["เครื่องเขียน", "ปากกาลูกลื่น สีแดง", "ด้าม", 10, "กล่อง", 50, None, None, 42, None],
    ["เครื่องเขียน", "ดินสอดำ 2B", "แท่ง", 12, "กล่อง", 12, None, None, 30, None],
    ["เครื่องเขียน", "ยางลบ", "ก้อน", 5, None, None, None, None, 18, None],
    ["เครื่องเขียน", "ปากกาเน้นข้อความ สีเหลือง", "ด้าม", 5, "แพ็ก", 10, None, None, 7, None],
    ["เครื่องเขียน", "ลวดเย็บกระดาษ เบอร์ 10", "กล่อง", 10, "แพ็ก", 24, None, None, 50, None],
    ["เครื่องเขียน", "คลิปหนีบกระดาษ", "กล่อง", 5, None, None, None, None, 9, None],
    ["เครื่องเขียน", "ซองน้ำตาล A4", "ซอง", 50, "แพ็ก", 50, None, None, 230, None],
    ["กระดาษ", "A4 70 แกรม", "รีม", 10, "กล่อง", 5, None, None, 23, None],
    ["กระดาษ", "A4 80 แกรม", "รีม", 10, "กล่อง", 5, None, None, 41, None],
    ["กระดาษ", "F4 70 แกรม", "รีม", 3, "กล่อง", 5, None, None, 4, None],
    ["กระดาษ", "กระดาษการ์ดสี A4", "รีม", 1, None, None, None, None, 2, None],
    ["หมึกพิมพ์", "Printo PX-410", "ตลับ", 2, None, None, None, None, 3, "เครื่อง Printo ห้องเสมียนตรา (ข้อมูลสมมติ)"],
    ["หมึกพิมพ์", "HP 85A", "ตลับ", 1, None, None, None, None, 1, None],
    ["หมึกพิมพ์", "Canon 337", "ตลับ", 1, None, None, None, None, 0, None],
    ["หมึกพิมพ์", "หมึกเติม Inko สีดำ", "ขวด", 2, "แพ็ก", 4, None, None, 6, None],
    ["อุปกรณ์สำนักงาน", "เทปใส 1 นิ้ว", "ม้วน", 6, "แถว", 6, None, None, 14, None],
    ["อุปกรณ์สำนักงาน", "เทปกาวสองหน้า", "ม้วน", 3, None, None, None, None, 5, None],
    ["อุปกรณ์สำนักงาน", "แฟ้มสันกว้าง 3 นิ้ว", "แฟ้ม", 10, "โหล", 12, None, None, 26, None],
    ["อุปกรณ์สำนักงาน", "แฟ้มเจาะ A4", "แฟ้ม", 20, "โหล", 12, None, None, 60, None],
    ["อุปกรณ์สำนักงาน", "ถ่าน AA", "ก้อน", 8, "แพ็ก", 4, "กล่อง", 48, 20, None],
    ["อุปกรณ์สำนักงาน", "กาวแท่ง", "แท่ง", 5, None, None, None, None, 11, None],
    ["ของใช้ทำความสะอาด", "กระดาษชำระ", "ม้วน", 24, "แพ็ก", 6, "ลัง", 48, 96, None],
    ["ของใช้ทำความสะอาด", "น้ำยาล้างจาน", "ขวด", 2, None, None, None, None, 4, None],
]

PROBLEMS = [
    # มีอยู่แล้วและตรงกัน (ถ้านำเข้าไฟล์แรกแล้ว)
    ["เครื่องเขียน", "ยางลบ", "ก้อน", 5, None, None, None, None, None, None],
    # มีอยู่แล้วแต่ต่าง: จุดสั่งซื้อ + หน่วยใหม่ + ยอดยกมา
    ["กระดาษ", "A4 80 แกรม", "รีม", 15, "กล่อง", 5, "ลัง", 10, 40, None],
    # ใหม่
    ["เครื่องเขียน", "ไม้บรรทัด 30 ซม.", "อัน", 2, None, None, None, None, 6, None],
    # ชื่อซ้ำกันเองในไฟล์
    ["หมึกพิมพ์", "Brother TN-2380", "ตลับ", 1, None, None, None, None, 2, None],
    ["หมึกพิมพ์", "Brother TN-2380", "ตลับ", 1, None, None, None, None, 3, None],
    # อ่านไม่ได้
    ["", "ไม่มีหมวด", "อัน", None, None, None, None, None, 1, None],
    ["เครื่องเขียน", "จำนวนไม่เต็ม", "ด้าม", None, None, None, None, None, 2.5, None],
    ["เครื่องเขียน", "อัตราผิด", "ด้าม", None, "กล่อง", 1, None, None, 3, None],
    ["เครื่องเขียน", "ยอดติดลบ", "ด้าม", None, None, None, None, None, -4, None],
]


def build(rows):
    wb = load_workbook(BytesIO(si.template()))
    ws = wb[si.SHEET]
    for r in rows:
        ws.append(r)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "exports"
    dest.mkdir(parents=True, exist_ok=True)
    for name, rows in (("ตัวอย่างนำเข้าวัสดุ_ข้อมูลสมมติ.xlsx", GOOD), ("ตัวอย่างนำเข้าวัสดุ_มีปัญหา_ข้อมูลสมมติ.xlsx", PROBLEMS)):
        path = dest / name
        path.write_bytes(build(rows))
        print(f"สร้าง {path} ({len(rows)} แถว)")


if __name__ == "__main__":
    main()
