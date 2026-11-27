"""ข้อมูลรายงานของระบบวัสดุ (ใช้ทั้งหน้าจอและไฟล์ Excel ให้ตัวเลขตรงกันเสมอ) · ไม่มีราคา/มูลค่า

- ใบเบิก / ใบรับ แบบ 1 แถว = 1 บรรทัดวัสดุ ตามเดือน (และฝ่าย) รวมใบที่ยกเลิก (มีสถานะบอก)
- สรุปการเบิกรายเดือน: วัสดุ × ฝ่าย (จำนวนที่จ่ายจริง ไม่นับใบที่ยกเลิก) รวมเฉพาะในแถว (รายการเดียวกัน หน่วยเดียวกัน)
- สรุปรับ–จ่าย–คงเหลือประจำเดือน: ยกมา + รับ − จ่าย ± ปรับยอด = คงเหลือ ของทุกรายการ
  การยกเลิกเอกสาร (reversal) นับหักในประเภทของเอกสารเดิม วันที่เดียวกับเอกสารเดิม
"""
import calendar
from datetime import date

from app import supplies as sp


def month_range(month):
    """'2026-11' -> (date(2026, 11, 1), date(2026, 11, 30))"""
    y, m = map(int, month.split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def issue_status(r):
    if r["cancelled_at"]:
        return "ยกเลิกแล้ว"
    return "จ่ายไม่ครบ" if r["issued_qty"] < r["requested_qty"] else "จ่ายแล้ว"


def issue_lines(db, month=None, department_id=None):
    """บรรทัดวัสดุของใบเบิกทุกใบตามตัวกรอง (ไม่จำกัดจำนวน) เรียงตามวันที่เบิก เลขที่ใบเบิก"""
    where, params = [], []
    if month:
        where.append("substr(s.issue_date, 1, 7) = ?")
        params.append(month)
    if department_id:
        where.append("s.department_id = ?")
        params.append(department_id)
    rows = db.execute(f"""
        SELECT s.id AS issue_id, s.issue_date, s.doc_no, d.name AS department, s.requester_name, s.approver_name,
               s.approved_date, s.cancelled_at, s.cancel_reason, c.name AS category, i.name, i.base_unit,
               l.requested_qty, l.issued_qty
        FROM supply_issue_lines l JOIN supply_issues s ON s.id = l.issue_id JOIN departments d ON d.id = s.department_id
        JOIN supply_items i ON i.id = l.item_id JOIN supply_categories c ON c.id = i.category_id
        {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY s.issue_date, s.id, l.id""", params).fetchall()
    return [{**dict(r), "status": issue_status(r)} for r in rows]


def receipt_lines(db, month=None):
    where, params = ("WHERE substr(r.received_date, 1, 7) = ?", [month]) if month else ("", [])
    rows = db.execute(f"""
        SELECT r.id AS receipt_id, r.received_date, r.doc_no, r.source, r.cancelled_at, r.cancel_reason,
               c.name AS category, i.name, i.base_unit, l.unit_name, l.unit_qty, l.factor, l.qty
        FROM supply_receipt_lines l JOIN supply_receipts r ON r.id = l.receipt_id
        JOIN supply_items i ON i.id = l.item_id JOIN supply_categories c ON c.id = i.category_id
        {where} ORDER BY r.received_date, r.id, l.id""", params).fetchall()
    return [{**dict(r), "status": "ยกเลิกแล้ว" if r["cancelled_at"] else "รับแล้ว"} for r in rows]


def doc_count(lines, key):
    """(จำนวนใบทั้งหมด, จำนวนใบที่ยกเลิก)"""
    docs = {r[key]: bool(r["cancelled_at"]) for r in lines}
    return len(docs), sum(docs.values())


def issue_matrix(db, month):
    """สรุปการเบิกรายเดือน: แถว = วัสดุที่มีการจ่าย, คอลัมน์ = ฝ่ายที่เบิกวัสดุ (ไม่นับใบที่ยกเลิก)"""
    start, end = month_range(month)
    data = db.execute("""
        SELECT l.item_id, s.department_id, SUM(l.issued_qty) AS qty
        FROM supply_issue_lines l JOIN supply_issues s ON s.id = l.issue_id
        WHERE s.cancelled_at IS NULL AND s.issue_date BETWEEN ? AND ?
        GROUP BY l.item_id, s.department_id HAVING SUM(l.issued_qty) > 0""", (str(start), str(end))).fetchall()
    n_docs = dict(db.execute("""SELECT department_id, COUNT(*) FROM supply_issues
                                WHERE cancelled_at IS NULL AND issue_date BETWEEN ? AND ? GROUP BY department_id""",
                             (str(start), str(end))).fetchall())
    used = {r["department_id"] for r in data} | set(n_docs)
    departments = [dict(d) for d in sp.issuing_departments(db, include_ids=used)]
    cells = {}
    for r in data:
        cells.setdefault(r["item_id"], {})[r["department_id"]] = r["qty"]
    items = db.execute(f"""SELECT i.id, c.name AS category, i.name, i.base_unit FROM supply_items i
                           JOIN supply_categories c ON c.id = i.category_id
                           WHERE i.id IN ({','.join('?' * len(cells)) or 'NULL'})
                           ORDER BY c.sort_order, c.name, i.name""", list(cells)).fetchall()
    rows = []
    for it in items:
        by = cells[it["id"]]
        rows.append({**dict(it), "qty": [by.get(d["id"], 0) for d in departments], "total": sum(by.values())})
    # ไม่รวมจำนวนข้ามรายการ (หน่วยต่างกัน: รีม + แท่ง + กล่อง ไม่มีความหมาย) นับเป็นจำนวนใบเบิกและจำนวนรายการแทน
    return {"month": month, "start": start, "end": end, "departments": departments, "rows": rows,
            "n_docs": [n_docs.get(d["id"], 0) for d in departments], "n_docs_total": sum(n_docs.values()),
            "n_items": [sum(1 for r in rows if r["qty"][i]) for i in range(len(departments))], "n_items_total": len(rows)}


def movement(db, month):
    """สรุปรับ–จ่าย–คงเหลือ: ทุกรายการที่ใช้งาน (และรายการที่ปิดแล้วแต่มีความเคลื่อนไหว/ยอดในเดือนนี้)
    ยกมา = ยอดก่อนต้นเดือน + ยอดยกมาที่นำเข้าในเดือนนี้ · จ่าย แสดงเป็นจำนวนบวก"""
    start, end = month_range(month)
    before = dict(db.execute("SELECT item_id, SUM(qty) FROM supply_moves WHERE move_date < ? GROUP BY item_id",
                             (str(start),)).fetchall())
    sums = {}
    for item_id, kind, qty in db.execute("""
            SELECT m.item_id, CASE WHEN m.kind = 'reversal' THEN o.kind ELSE m.kind END, SUM(m.qty)
            FROM supply_moves m LEFT JOIN supply_moves o ON o.id = m.reverses_id
            WHERE m.move_date BETWEEN ? AND ? GROUP BY 1, 2""", (str(start), str(end))):
        sums.setdefault(item_id, {})[kind] = qty
    rows = []
    for it in db.execute("""SELECT i.id, c.name AS category, i.name, i.base_unit, i.reorder_point, i.active
                            FROM supply_items i JOIN supply_categories c ON c.id = i.category_id
                            ORDER BY c.sort_order, c.name, i.name"""):
        s = sums.get(it["id"], {})
        opening = before.get(it["id"], 0) + s.get("opening", 0)
        r = {**dict(it), "opening": opening, "received": s.get("receipt", 0), "issued": -s.get("issue", 0),
             "adjusted": s.get("adjust", 0)}
        r["closing"] = opening + r["received"] - r["issued"] + r["adjusted"]
        if not it["active"] and not s and not r["closing"]:
            continue
        r["level"] = sp.level(r["closing"], it["reorder_point"])
        rows.append(r)
    totals = {k: sum(r[k] for r in rows) for k in ("opening", "received", "issued", "adjusted", "closing")}
    return {"month": month, "start": start, "end": end, "rows": rows, "totals": totals}
