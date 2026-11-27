"""URL ของหน้าที่กรองด้วย htmx (static/js/autofilter.js)

ตัวกรองอัตโนมัติบอกมาใน header HX-Filter-History ว่าควรเพิ่มประวัติใหม่ (push: เปลี่ยนตัวกรอง กดย้อนกลับได้)
หรือแทนที่ประวัติเดิม (replace: พิมพ์ค้นหาต่อเนื่อง) · request อื่นที่ไม่บอก (เช่น ลิงก์เลือกหน้า) ใช้แทนที่เหมือนเดิม
"""

FILTER_HISTORY_HEADER = "HX-Filter-History"


def is_filter_request(request) -> bool:
    """request จากตัวกรองอัตโนมัติ (ไม่ใช่ตอนกู้ประวัติหน้าเว็บ ซึ่งต้องการทั้งหน้า)"""
    return bool(request.headers.get("HX-Request") and request.headers.get(FILTER_HISTORY_HEADER)
                and not request.headers.get("HX-History-Restore-Request"))


def set_filter_url(request, response, url):
    """ให้แถบที่อยู่แสดง URL สะอาดของตัวกรอง (ไม่มี category=&status=...) แชร์ลิงก์ต่อได้"""
    push = request.headers.get(FILTER_HISTORY_HEADER) == "push"
    response.headers["HX-Push-Url" if push else "HX-Replace-Url"] = url
    return response
