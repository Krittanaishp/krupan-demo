"""ข้อความปัญหาใน assets.needs_review (คั่นด้วย ', ' ตามที่สคริปต์ import เขียนไว้)"""

SEP = ", "

NO_PRICE = "ไม่มีราคา"
NO_REG_NO = "ไม่มีหมายเลขทะเบียน"
REG_NO_INCOMPLETE = "หมายเลขทะเบียนไม่ครบ"
DUP_IN_SHEET = "เลขทะเบียนซ้ำในชีตเดียวกัน"
NO_DATE = "ไม่มีวันที่ได้มา"
DATE_MONTH_ONLY = "วันที่มีแค่เดือน/ปี"
DATE_YEAR_ONLY = "มีแค่ปี"
EXCEL_YEAR_PREFIX = "Excel อ่านเป็น"           # ปี พ.ศ. 2 หลักที่ Excel อ่านเป็น ค.ศ. 19xx
BE_STORED_AS_CE = "Excel เก็บปี พ.ศ. เป็น ค.ศ."  # ปี พ.ศ. 4 หลักที่ Excel เก็บเป็น ค.ศ. 25xx
DATE_INVALID = ("วันที่ไม่ถูกต้อง", "แปลงวันที่ไม่ได้")
INVENTORY_UNREGISTERED = "พบระหว่างตรวจสอบพัสดุ ไม่มีในทะเบียน"
INVENTORY_MISSING_PREFIX = "ไม่พบในการตรวจสอบพัสดุประจำปี "
NAME_SUSPECT = "ชื่อรายการอาจเพี้ยนจากไฟล์เดิม"  # migration 009: บรรทัดต่อเนื่องในไฟล์เดิมถูกอ่านเป็นรายการใหม่
DUP_REG_PREFIX = "ลงทะเบียนซ้ำกับ "            # "ลงทะเบียนซ้ำกับ <รหัส> รอเลือกเลขที่ถูกต้อง" (ข้อมูลจากเจ้าหน้าที่)
NOT_FOUND_ROOM_PREFIX = "ไม่พบในห้อง"            # "ไม่พบในห้องประชุม รอตรวจว่ามีเอกสารจำหน่ายหรือไม่"


def dup_reg(other_code):
    return f"{DUP_REG_PREFIX}{other_code} รอเลือกเลขที่ถูกต้อง"


def is_staff_verify_flag(flag):
    """ปัญหาที่ต้องรอเจ้าหน้าที่ยืนยัน (ไม่หายเองเมื่อแก้ข้อมูล)"""
    return flag.startswith(DUP_REG_PREFIX) or flag.startswith(NOT_FOUND_ROOM_PREFIX)


def split(value):
    return [f for f in (value or "").split(SEP) if f]


def join(flags):
    return SEP.join(flags) or None


def is_excel_year(flag):
    return flag.startswith(EXCEL_YEAR_PREFIX) or flag == BE_STORED_AS_CE


def is_date_flag(flag):
    return (flag in (NO_DATE, DATE_MONTH_ONLY, DATE_YEAR_ONLY, *DATE_INVALID)
            or is_excel_year(flag))


def inventory_missing(fiscal_year):
    return f"{INVENTORY_MISSING_PREFIX}{fiscal_year}"


def is_inventory_flag(flag):
    return flag == INVENTORY_UNREGISTERED or flag.startswith(INVENTORY_MISSING_PREFIX)


def is_reg_no_flag(flag):
    return flag in (NO_REG_NO, REG_NO_INCOMPLETE)


def auto_resolve(current, changed, new):
    """ปัญหาที่หายไปเองเมื่อแก้ข้อมูลในหน้าแก้ไข คืน (needs_review ใหม่, [ปัญหาที่ล้าง])

    - ใส่ราคา -> ล้าง "ไม่มีราคา"
    - ใส่เลขทะเบียน (ผ่านการตรวจรูปแบบแล้ว) -> ล้าง "ไม่มีหมายเลขทะเบียน" / "หมายเลขทะเบียนไม่ครบ"
    - แก้วันที่หรือความละเอียดของวันที่ และยังมีวันที่ -> ล้างปัญหาเรื่องวันที่ทุกแบบ
    - แก้ชื่อรายการ -> ล้าง "ชื่อรายการอาจเพี้ยนจากไฟล์เดิม"
    "เลขทะเบียนซ้ำในชีตเดียวกัน" ไม่ล้างเอง ต้องกด "ตรวจแล้ว" ที่หน้ารอตรวจสอบ
    """
    def done(flag):
        if "unit_price" in changed and new["unit_price"] is not None and flag == NO_PRICE:
            return True
        if "reg_no" in changed and new["reg_no"] and is_reg_no_flag(flag):
            return True
        if "name" in changed and (new.get("name") or "").strip() and flag == NAME_SUSPECT:
            return True
        date_changed = "acquired_date" in changed or "acquired_date_precision" in changed
        return date_changed and new["acquired_date"] is not None and is_date_flag(flag)

    flags = split(current)
    resolved = [f for f in flags if done(f)]
    return join([f for f in flags if f not in resolved]), resolved
