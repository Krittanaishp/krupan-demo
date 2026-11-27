"""URL ของไฟล์ static พร้อมเลขรุ่นตามเนื้อไฟล์ (/static/css/app.css?v=<hash>)

หลังอัปเดตระบบ เบราว์เซอร์ของเจ้าหน้าที่อาจยังใช้ CSS/JS เก่าที่เก็บไว้ (ต้องกด Ctrl+F5 ถึงจะเห็นหน้าตาใหม่)
เทมเพลตจึงอ้างไฟล์ด้วย {{ static_url('css/app.css') }} ซึ่งต่อท้าย ?v= ด้วย hash ของเนื้อไฟล์
เนื้อไฟล์เปลี่ยน = URL เปลี่ยน = เบราว์เซอร์โหลดใหม่เอง · ไฟล์เดิม = URL เดิม = ใช้ที่เก็บไว้ได้
hash คำนวณครั้งเดียวต่อ (เวลาแก้ไข, ขนาด) ของไฟล์ ไม่อ่านไฟล์ทุก request
"""
import hashlib
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent / "static"
_hashes = {}


def file_hash(path):
    st = path.stat()
    key = (str(path), st.st_mtime_ns, st.st_size)
    h = _hashes.get(key)
    if h is None:
        h = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        _hashes[key] = h
    return h


def static_url(name, root=None):
    """'css/app.css' -> '/static/css/app.css?v=<hash 12 ตัว>' · ไม่มีไฟล์ = FileNotFoundError (เทมเพลตอ้างผิด)"""
    name = name.lstrip("/")
    return f"/static/{name}?v={file_hash((root or STATIC_DIR) / name)}"
