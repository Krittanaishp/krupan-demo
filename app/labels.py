"""คำนวณตำแหน่งสติกเกอร์บนกระดาษ A4 (หน่วย มม. ทั้งหมด) และขนาดตัวอักษรในดวง"""
import math
import re
from dataclasses import asdict, dataclass

A4_W, A4_H = 210.0, 297.0
TOLERANCE = 0.05  # มม. กันเศษทศนิยม
PT_PER_MM = 72 / 25.4
INT_FIELDS = ("n_cols", "n_rows")
MM_FIELDS = ("label_w", "label_h", "margin_top", "margin_left", "gap_x", "gap_y")
FIELDS = INT_FIELDS + MM_FIELDS
FIELD_LABELS = {
    "n_cols": "จำนวนคอลัมน์", "n_rows": "จำนวนแถว", "label_w": "กว้าง (มม.)", "label_h": "สูง (มม.)",
    "margin_top": "ขอบบน (มม.)", "margin_left": "ขอบซ้าย (มม.)", "gap_x": "ระยะห่างแนวนอน (มม.)",
    "gap_y": "ระยะห่างแนวตั้ง (มม.)",
}


@dataclass
class Layout:
    n_cols: int
    n_rows: int
    label_w: float
    label_h: float
    margin_top: float = 0
    margin_left: float = 0
    gap_x: float = 0
    gap_y: float = 0

    @property
    def per_page(self):
        return self.n_cols * self.n_rows

    def cell(self, index):
        """ตำแหน่ง (left, top) ของดวงที่ index (เริ่ม 0) นับจากซ้ายไปขวา บนลงล่าง"""
        row, col = divmod(index, self.n_cols)
        return (round(self.margin_left + col * (self.label_w + self.gap_x), 2),
                round(self.margin_top + row * (self.label_h + self.gap_y), 2))

    def as_dict(self):
        return asdict(self)


def from_row(row):
    return Layout(**{f: (int(row[f]) if f in INT_FIELDS else float(row[f])) for f in FIELDS})


def parse(values, base):
    """ค่าจากฟอร์ม (string) ทับค่าของ preset; คืน (Layout, errors {ช่อง: ข้อความ})"""
    data, errors = base.as_dict(), {}
    for f in FIELDS:
        raw = str(values.get(f, "") or "").strip().replace(",", ".")
        if raw == "":
            continue
        try:
            data[f] = int(raw) if f in INT_FIELDS else round(float(raw), 2)
        except ValueError:
            errors[f] = f"{FIELD_LABELS[f]} ต้องเป็นตัวเลข"
    layout = Layout(**data)
    errors.update(validate(layout))
    return layout, errors


def validate(layout):
    errors = {}
    if not 1 <= layout.n_cols <= 10:
        errors["n_cols"] = "จำนวนคอลัมน์ต้องอยู่ระหว่าง 1-10"
    if not 1 <= layout.n_rows <= 40:
        errors["n_rows"] = "จำนวนแถวต้องอยู่ระหว่าง 1-40"
    if not 10 <= layout.label_w <= A4_W:
        errors["label_w"] = "ความกว้างของดวงต้องอยู่ระหว่าง 10-210 มม."
    if not 10 <= layout.label_h <= A4_H:
        errors["label_h"] = "ความสูงของดวงต้องอยู่ระหว่าง 10-297 มม."
    for f in ("margin_top", "margin_left", "gap_x", "gap_y"):
        if getattr(layout, f) < 0:
            errors[f] = f"{FIELD_LABELS[f]} ติดลบไม่ได้"
    if errors:
        return errors
    width = layout.margin_left + layout.n_cols * layout.label_w + (layout.n_cols - 1) * layout.gap_x
    height = layout.margin_top + layout.n_rows * layout.label_h + (layout.n_rows - 1) * layout.gap_y
    if width > A4_W + TOLERANCE:
        errors["label_w"] = f"ดวงทั้งแถวกว้าง {width:g} มม. เกินกระดาษ A4 (210 มม.)"
    if height > A4_H + TOLERANCE:
        errors["label_h"] = f"ดวงทั้งคอลัมน์สูง {height:g} มม. เกินกระดาษ A4 (297 มม.)"
    return errors


def paginate(items, layout, start=1):
    """แบ่งเป็นแผ่น: ข้าม start-1 ดวงแรกของแผ่นแรก (ใช้แผ่นที่เหลือจากครั้งก่อน) คืน [[(left, top, item|None)]]"""
    slots = [None] * (start - 1) + list(items)
    pages = []
    for p in range(0, max(len(slots), 1), layout.per_page):
        chunk = slots[p:p + layout.per_page]
        pages.append([(*layout.cell(i), item) for i, item in enumerate(chunk) if item is not None])
    return pages


def border_page(layout):
    """แผ่นทดสอบ: กรอบของทุกดวง ไม่มีเนื้อหา"""
    return [[(*layout.cell(i), None) for i in range(layout.per_page)]]


def design(layout):
    """ขนาดส่วนต่าง ๆ ในดวง (มม./pt) ดวงกว้าง: QR ซ้าย ข้อความขวา / ดวงแคบ: QR บน ข้อความล่าง"""
    w, h = layout.label_w, layout.label_h
    pad = round(min(2.0, h * 0.06, w * 0.04), 2)
    k = max(0.6, min(1.6, h / 37, w / 70))
    horizontal = w >= h * 1.3
    if horizontal:
        qr = min(h - 2 * pad, w * 0.45)
        text_w = w - qr - 3 * pad
    else:
        qr = min(w - 2 * pad, h * 0.55)
        text_w = w - 2 * pad
    return {"pad": pad, "qr": round(qr, 2), "text_w": round(text_w, 2), "horizontal": horizontal,
            "reg_pt": round(13 * k, 1), "name_pt": round(8 * k, 1), "room_pt": round(7.5 * k, 1),
            "office_pt": round(6 * k, 1)}


REG_SPLIT_RE = re.compile(r"^(ปค\.[0-9]{2}-[0-9]{4}-[0-9]-)(.+)$")


def reg_lines(text):
    """เลขทะเบียนยาวแบ่ง 2 บรรทัด ('ปค.99-9999-1-' / '1201-0006') ตัวอักษรจึงใหญ่ได้เกือบเท่าตัว"""
    m = REG_SPLIT_RE.match(text or "")
    return [m.group(1), m.group(2)] if m else [text or ""]


def reg_pt(text, d):
    """ขนาดตัวอักษรของเลขทะเบียน: ใหญ่ที่สุดที่ทุกบรรทัดยังไม่ล้นความกว้าง (ตัวเลข Sarabun กว้างราว 0.56 em)
    ปัดลงเสมอ กันล้นจากการปัดเศษ"""
    longest = max(len(line) for line in reg_lines(text)) or 1
    fit = d["text_w"] / (longest * 0.56) * PT_PER_MM
    return math.floor(min(d["reg_pt"], fit) * 10) / 10
