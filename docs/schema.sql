-- ระบบทะเบียนครุภัณฑ์ ที่ทำการปกครองอำเภอตัวอย่าง
-- ฐานข้อมูล SQLite (ร่างแรก)
PRAGMA foreign_keys = ON;

-- หมวดครุภัณฑ์ (2 หลักแรกของรหัสประเภท เช่น 13 = ตู้, 14 = โต๊ะ-เก้าอี้)
CREATE TABLE categories (
    code        TEXT PRIMARY KEY,          -- '13'
    name        TEXT NOT NULL              -- 'ตู้'
);

-- ประเภทครุภัณฑ์ (รหัส 4 หลักในเลขทะเบียน เช่น 1306 = ตู้เหล็ก 4 ลิ้นชัก)
CREATE TABLE asset_types (
    code              TEXT PRIMARY KEY,    -- '1306'
    category_code     TEXT NOT NULL REFERENCES categories(code),
    name              TEXT,
    name_is_draft     INTEGER DEFAULT 1,   -- 1 = ชื่อเดาจากชื่อรายการที่พบบ่อยที่สุด ยังไม่ได้ยืนยัน
    useful_life_years INTEGER              -- อายุการใช้งาน สำหรับคิดค่าเสื่อม
);

-- อาคาร/พื้นที่ และฝ่ายที่ดูแล ของห้อง (migration 010 · admin เพิ่ม/แก้/ปิดใช้งานได้ในแท็บห้อง)
CREATE TABLE buildings (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,     -- 'อาคารสำนักงาน ชั้น 1', 'ติดตัวบุคคล/ประจำตำแหน่ง'
    active       INTEGER NOT NULL DEFAULT 1,
    holder_focus INTEGER NOT NULL DEFAULT 0, -- 1 = ใบรายการตรวจสอบพัสดุเน้นคอลัมน์ผู้รับผิดชอบ (ของติดตัวบุคคล)
    sort_order   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE departments (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,       -- 'ปกครอง', 'ความมั่นคง'
    active     INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    issues_supplies INTEGER NOT NULL DEFAULT 0  -- 1 = เบิกวัสดุได้ (โผล่ในหน้าเบิก) migration 011 · admin เปิด/ปิดที่ ตั้งค่า > วัสดุ
);

-- ห้อง / หน่วยงานที่ใช้ประจำ
CREATE TABLE locations (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,           -- 'ห้องปกครอง', 'กองร้อย อส.'
    active INTEGER NOT NULL DEFAULT 1,     -- 0 = ปิดใช้งาน (ไม่แสดงให้เลือก แต่ประวัติยังอ้างถึงได้)
    merged_into_id INTEGER REFERENCES locations(id), -- ถูกรวมเข้าห้องนี้แล้ว (ปิดใช้งานด้วย ห้ามลบ) migration 009
    building_id    INTEGER REFERENCES buildings(id),     -- อาคาร/พื้นที่ (ไม่บังคับ) migration 010
    department_id  INTEGER REFERENCES departments(id)    -- ฝ่ายที่ดูแล (ไม่บังคับ) migration 010
);

-- เจ้าหน้าที่ผู้รับผิดชอบครุภัณฑ์
CREATE TABLE staff (
    id        INTEGER PRIMARY KEY,
    full_name TEXT,                        -- เว้นว่างได้ เติมทีหลัง
    nickname  TEXT,                        -- ในไฟล์เดิมมักเขียนแค่ชื่อเล่น
    position  TEXT,
    active    INTEGER NOT NULL DEFAULT 1,
    CHECK (full_name IS NOT NULL OR nickname IS NOT NULL)
);

-- ครุภัณฑ์ 1 แถว = 1 ชิ้น
CREATE TABLE assets (
    id              INTEGER PRIMARY KEY,
    reg_no          TEXT UNIQUE,           -- 'ปค.99-9999-1-1306-0004' (NULL = ยังไม่มีเลข)
    temp_code       TEXT UNIQUE,           -- รหัสชั่วคราวใช้ทำ QR เมื่อยังไม่มี reg_no เช่น 'TMP-0001'
    type_code       TEXT REFERENCES asset_types(code),
    name            TEXT NOT NULL,
    details         TEXT,                  -- ยี่ห้อ รุ่น หมายเลขเครื่อง
    acquired_date   DATE,
    acquired_date_precision TEXT DEFAULT 'day' CHECK (acquired_date_precision IN ('day','month','year')),
    acquired_date_raw TEXT,                -- วันที่ตามที่เขียนในไฟล์ Excel เดิม เช่น '19 เม.ย. 65'
    fiscal_year     INTEGER,               -- ปีงบประมาณ พ.ศ.
    unit_price      NUMERIC,               -- NULL = ไม่ทราบราคา (ไม่ใช่ 0)
    funding_source  TEXT,                  -- วิธีการได้มา / แหล่งเงิน
    gfmis_code      TEXT,
    location_id     INTEGER REFERENCES locations(id),   -- เว้นว่างไว้ก่อน จัดเข้าห้องจริงผ่านหน้าจอระบบ
    location_text   TEXT,                  -- ข้อความ "ใช้ประจำที่" ตามไฟล์ Excel เดิม (เก็บไว้ตามจริง)
    holder_id       INTEGER REFERENCES staff(id),
    status          TEXT NOT NULL DEFAULT 'ใช้งาน'
                    CHECK (status IN ('ใช้งาน','ชำรุด','รอจำหน่าย','จำหน่ายแล้ว','สูญหาย')),
    note            TEXT,
    needs_review    TEXT,                  -- สิ่งที่ยังต้องตรวจ (จากสคริปต์ import)
    source_sheet    TEXT,                  -- ชีตต้นทางในไฟล์ Excel เดิม
    source_row      INTEGER,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    label_printed_at DATETIME,             -- พิมพ์สติกเกอร์ QR ครั้งล่าสุดเมื่อไร (UTC) NULL = ยังไม่เคยพิมพ์
    duplicate_of_id INTEGER REFERENCES assets(id), -- "รายการซ้ำ" ของรายการนี้ (migration 010) ไม่นับในรายงาน/รอบตรวจสอบพัสดุ
    duplicate_reason TEXT,                 -- เหตุผลที่ตั้งเป็นรายการซ้ำ (admin) · status เดิมเก็บไว้ ยกเลิกแล้วกลับเป็นสถานะนั้น
    CHECK (reg_no IS NOT NULL OR temp_code IS NOT NULL)
);
CREATE INDEX idx_assets_type     ON assets(type_code);
CREATE INDEX idx_assets_location ON assets(location_id);
CREATE INDEX idx_assets_status   ON assets(status);
CREATE INDEX idx_assets_duplicate ON assets(duplicate_of_id);
CREATE INDEX idx_assets_holder   ON assets(holder_id);

-- ประวัติการย้ายห้อง / เปลี่ยนผู้รับผิดชอบ
CREATE TABLE asset_moves (
    id              INTEGER PRIMARY KEY,
    asset_id        INTEGER NOT NULL REFERENCES assets(id),
    moved_at        DATE NOT NULL,
    from_location_id INTEGER REFERENCES locations(id),
    to_location_id   INTEGER REFERENCES locations(id),
    from_holder_id   INTEGER REFERENCES staff(id),
    to_holder_id     INTEGER REFERENCES staff(id),
    note            TEXT,
    reason          TEXT                   -- จัดห้องครั้งแรก / แก้การจัดห้อง / ย้ายห้อง / เปลี่ยนผู้รับผิดชอบ / พบระหว่างตรวจสอบพัสดุ
);
CREATE INDEX idx_asset_moves_asset ON asset_moves(asset_id);

-- ประวัติการซ่อมบำรุง
CREATE TABLE repairs (
    id          INTEGER PRIMARY KEY,
    asset_id    INTEGER NOT NULL REFERENCES assets(id),
    repaired_at DATE NOT NULL,
    description TEXT NOT NULL,
    cost        NUMERIC,                   -- NULL = ไม่ทราบค่าใช้จ่าย
    note        TEXT
);
CREATE INDEX idx_repairs_asset ON repairs(asset_id);

-- การตรวจสอบพัสดุประจำปี
-- เปิดได้ทีละ 1 รอบ (idx_inventory_checks_one_open) และรอบที่ปิดแล้วแก้ไม่ได้ (trigger ด้านล่าง)
CREATE TABLE inventory_checks (
    id          INTEGER PRIMARY KEY,
    fiscal_year INTEGER NOT NULL,              -- ปีงบประมาณ พ.ศ.
    started_at  DATE NOT NULL,                 -- วันที่เริ่มตรวจ (กรอกเอง)
    closed_at   DATE,                          -- NULL = ยังเปิดอยู่
    committee   TEXT,                          -- JSON {"chair": "...", "members": ["...", ...]} ใช้พิมพ์ช่องลงชื่อ
    created_by  INTEGER REFERENCES users(id),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,  -- เวลาเปิดรอบ (UTC) = เวลาที่เก็บรายการครุภัณฑ์ของรอบ
    closed_by   INTEGER REFERENCES users(id)
);
CREATE UNIQUE INDEX idx_inventory_checks_one_open ON inventory_checks((closed_at IS NULL)) WHERE closed_at IS NULL;

-- 1 แถว = ครุภัณฑ์ 1 ชิ้นในรอบ สร้างครบทุกชิ้นตอนเปิดรอบ (snapshot) แล้วค่อยกรอกผล
CREATE TABLE inventory_check_items (
    check_id    INTEGER NOT NULL REFERENCES inventory_checks(id),
    asset_id    INTEGER NOT NULL REFERENCES assets(id),
    expected_location_id INTEGER REFERENCES locations(id),  -- ห้องตามทะเบียน ณ วันเปิดรอบ (ใช้แบ่งใบรายการตามห้อง)
    status_at_open TEXT,                       -- สถานะในทะเบียน ณ วันเปิดรอบ
    unregistered INTEGER NOT NULL DEFAULT 0,   -- 1 = พบระหว่างตรวจสอบพัสดุ ไม่มีในทะเบียน (ไม่อยู่ใน snapshot)
    result      TEXT CHECK (result IN ('พบ ใช้งานได้','พบ ชำรุด','ไม่พบ')),  -- NULL = ยังไม่ตรวจ
    checked_at  DATETIME,                      -- UTC
    checked_by  INTEGER REFERENCES users(id),
    found_location_id INTEGER REFERENCES locations(id),     -- ห้องที่พบจริง เมื่อไม่ตรงกับห้องในทะเบียน
    method      TEXT CHECK (method IN ('qr','list','close')),  -- close = ไม่ได้ตรวจ บันทึกเป็นไม่พบตอนปิดรอบ
    note        TEXT,
    PRIMARY KEY (check_id, asset_id)
);
CREATE INDEX idx_inventory_items_asset ON inventory_check_items(asset_id);

CREATE TRIGGER inventory_closed_no_item_insert BEFORE INSERT ON inventory_check_items
WHEN (SELECT closed_at FROM inventory_checks WHERE id = NEW.check_id) IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'inventory check is closed'); END;
CREATE TRIGGER inventory_closed_no_item_update BEFORE UPDATE ON inventory_check_items
WHEN (SELECT closed_at FROM inventory_checks WHERE id = OLD.check_id) IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'inventory check is closed'); END;
CREATE TRIGGER inventory_closed_no_item_delete BEFORE DELETE ON inventory_check_items
WHEN (SELECT closed_at FROM inventory_checks WHERE id = OLD.check_id) IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'inventory check is closed'); END;
-- รอบที่ปิดแล้วแก้ไม่ได้ ยกเว้นการเปิดกลับโดย admin ภายใน 7 วันหลังปิด ซึ่งต้องบันทึกเหตุผลใน inventory_reopens ก่อน
-- (เปลี่ยนได้แค่ closed_at/closed_by เป็น NULL เท่านั้น ช่องอื่นต้องเหมือนเดิม)
CREATE TRIGGER inventory_closed_no_update BEFORE UPDATE ON inventory_checks
WHEN OLD.closed_at IS NOT NULL AND NOT (NEW.closed_at IS NULL AND NEW.closed_by IS NULL AND NEW.id = OLD.id AND NEW.fiscal_year IS OLD.fiscal_year AND NEW.started_at IS OLD.started_at AND NEW.committee IS OLD.committee AND NEW.created_by IS OLD.created_by AND NEW.created_at IS OLD.created_at AND date('now', '+7 hours') <= date(OLD.closed_at, '+7 days') AND EXISTS (SELECT 1 FROM inventory_reopens r WHERE r.check_id = OLD.id AND r.used = 0 AND r.closed_at_before = OLD.closed_at))
BEGIN SELECT RAISE(ABORT, 'inventory check is closed'); END;
CREATE TRIGGER inventory_reopen_used AFTER UPDATE OF closed_at ON inventory_checks
WHEN OLD.closed_at IS NOT NULL AND NEW.closed_at IS NULL
BEGIN UPDATE inventory_reopens SET used = 1 WHERE check_id = OLD.id AND used = 0; END;
CREATE TRIGGER inventory_closed_no_delete BEFORE DELETE ON inventory_checks
WHEN OLD.closed_at IS NOT NULL
BEGIN SELECT RAISE(ABORT, 'inventory check is closed'); END;

-- บันทึกการเปิดรอบที่ปิดแล้วกลับ (admin, ภายใน 7 วัน, ต้องมีเหตุผล) แถวที่ใช้แล้วแก้/ลบไม่ได้
CREATE TABLE inventory_reopens (
    id               INTEGER PRIMARY KEY,
    check_id         INTEGER NOT NULL REFERENCES inventory_checks(id),
    reason           TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    closed_at_before DATE NOT NULL,
    closed_by_before INTEGER REFERENCES users(id),
    reopened_by      INTEGER REFERENCES users(id),
    reopened_at      DATETIME,             -- UTC
    used             INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER inventory_reopens_locked_update BEFORE UPDATE ON inventory_reopens
WHEN OLD.used = 1
BEGIN SELECT RAISE(ABORT, 'reopen log is read-only'); END;
CREATE TRIGGER inventory_reopens_locked_delete BEFORE DELETE ON inventory_reopens
WHEN OLD.used = 1
BEGIN SELECT RAISE(ABORT, 'reopen log is read-only'); END;

-- การจำหน่าย
CREATE TABLE disposals (
    id          INTEGER PRIMARY KEY,
    asset_id    INTEGER NOT NULL REFERENCES assets(id),
    disposed_at DATE NOT NULL,
    method      TEXT,                      -- ขาย / แลกเปลี่ยน / โอน / แปรสภาพหรือทำลาย
    doc_ref     TEXT,                      -- เลขที่หนังสืออนุมัติ (บังคับกรอกในหน้าจอ)
    amount      NUMERIC,                   -- จำนวนเงินที่ได้ จำหน่ายเป็นชุดเก็บยอดรวมไว้ที่แถวแรกของชุด
    note        TEXT,
    lot_id      INTEGER,                   -- id ของแถวแรกในชุดที่จำหน่ายพร้อมกันภายใต้หนังสืออนุมัติฉบับเดียว
    status_before TEXT,                    -- สถานะก่อนจำหน่าย (ใช้คืนค่าเมื่อยกเลิก)
    created_by  INTEGER REFERENCES users(id),
    created_at  DATETIME,                  -- UTC
    cancelled_at DATETIME,                 -- ยกเลิกการจำหน่าย (admin) แถวยังอยู่เป็นประวัติ
    cancelled_by INTEGER REFERENCES users(id),
    cancel_reason TEXT
);
CREATE UNIQUE INDEX idx_disposals_active ON disposals(asset_id) WHERE cancelled_at IS NULL;

-- ผู้ใช้ระบบ
CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin','editor','viewer')),
    active        INTEGER NOT NULL DEFAULT 1,  -- 0 = ปิดใช้งาน (ห้ามลบ เพราะ audit_log อ้างถึง)
    session_version INTEGER NOT NULL DEFAULT 0, -- เพิ่มทุกครั้งที่รีเซ็ตรหัสผ่าน: session เก่าที่ถือเลขเดิมใช้ไม่ได้
    must_change_password INTEGER NOT NULL DEFAULT 0 -- 1 = ต้องเปลี่ยนรหัสก่อนใช้หน้าอื่น (admin สร้าง/รีเซ็ตให้) migration 011
);

-- นับการล็อกอินผิดต่อชื่อผู้ใช้ (รวมชื่อที่ไม่มีจริง เพื่อไม่ให้เดาได้ว่าชื่อไหนมีอยู่)
CREATE TABLE login_attempts (
    username     TEXT PRIMARY KEY,
    failed_count INTEGER NOT NULL DEFAULT 0,
    locked_until TEXT                       -- เวลา UTC แบบ ISO ถ้ายังไม่ถึงเวลานี้ ล็อกอินไม่ได้
);

-- บันทึกการแก้ไข (ใครแก้อะไรเมื่อไร)
CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id),
    table_name TEXT NOT NULL,
    record_id  INTEGER NOT NULL,
    action     TEXT NOT NULL,              -- insert / update / delete / review / review_undo / migrate / label / inventory / close / reopen / repair_* / dispose* / export (table_name exports, record_id 0)
    changes    TEXT,                       -- JSON ค่าเดิม -> ค่าใหม่
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- รูปแบบแผ่นสติกเกอร์ (หน่วย มม.) สำหรับพิมพ์ QR บนกระดาษ A4
CREATE TABLE label_presets (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    n_cols      INTEGER NOT NULL CHECK (n_cols > 0),
    n_rows      INTEGER NOT NULL CHECK (n_rows > 0),
    label_w     REAL NOT NULL CHECK (label_w > 0),
    label_h     REAL NOT NULL CHECK (label_h > 0),
    margin_top  REAL NOT NULL DEFAULT 0,
    margin_left REAL NOT NULL DEFAULT 0,
    gap_x       REAL NOT NULL DEFAULT 0,
    gap_y       REAL NOT NULL DEFAULT 0,
    builtin     INTEGER NOT NULL DEFAULT 0  -- 1 = preset เริ่มต้นของระบบ ลบไม่ได้
);
INSERT INTO label_presets (name, n_cols, n_rows, label_w, label_h, margin_top, margin_left, gap_x, gap_y, builtin)
VALUES ('A4 3x8 (70x37 มม.)', 3, 8, 70, 37, 0.5, 0, 0, 0, 1);

-- รวมห้องที่ซ้ำกัน (migration 009): ครุภัณฑ์ของห้องที่ถูกรวมย้ายไปห้องที่เก็บไว้ ยกเลิกได้ภายใน 1 วัน
CREATE TABLE location_merges (
    id          INTEGER PRIMARY KEY,
    kept_id     INTEGER NOT NULL REFERENCES locations(id),  -- ห้องที่เก็บไว้
    merged      TEXT NOT NULL,                 -- JSON [{"id", "name", "active"}] ห้องที่ถูกรวม + สถานะเดิม (ใช้ตอนยกเลิก)
    merged_by   INTEGER REFERENCES users(id),
    merged_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,  -- UTC
    undone_at   DATETIME,                      -- ยกเลิกการรวมแล้ว (UTC)
    undone_by   INTEGER REFERENCES users(id)
);
-- ครุภัณฑ์แต่ละชิ้นที่ย้ายตอนรวมห้อง: มาจากห้องไหน (ยกเลิกการรวมแล้วคืนกลับห้องนี้)
CREATE TABLE location_merge_items (
    merge_id         INTEGER NOT NULL REFERENCES location_merges(id),
    asset_id         INTEGER NOT NULL REFERENCES assets(id),
    from_location_id INTEGER NOT NULL REFERENCES locations(id),
    PRIMARY KEY (merge_id, asset_id)
);

-- ข้อมูลที่รู้แล้ว: ข้อความ "ใช้ประจำที่" เดิม -> ห้องที่ถูกต้อง (+ผู้รับผิดชอบ) ใช้ก่อนกฎตัวย่อตอนเสนอกลุ่ม
-- (แก้ได้ที่ ตั้งค่า > จัดห้อง · ค่าเริ่มต้นของหน่วยงานนี้ใส่ด้วย migration 009 · ดู docs/ROOM_DECISIONS.md)
CREATE TABLE location_text_rules (
    id          INTEGER PRIMARY KEY,
    match_text  TEXT NOT NULL,                 -- ข้อความเดิม
    exact       INTEGER NOT NULL DEFAULT 0,    -- 1 = ต้องตรงทุกตัวอักษร, 0 = เทียบหลัง normalize (ตัดช่องว่าง ตัวย่อ ฯลฯ)
    room_name   TEXT NOT NULL,                 -- ชื่อห้องที่ถูกต้อง (ยังไม่มีห้องนี้ก็ได้ จะเสนอให้สร้าง)
    holder_hint TEXT,                          -- ผู้รับผิดชอบที่เสนอ (ชื่อเล่น ชื่อจริง หรือตำแหน่ง)
    note        TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    UNIQUE (match_text, exact)
);

-- ค่าตั้งของระบบที่ต้องจำไว้ เช่น ช่องลงชื่อท้ายรายงาน (value เป็น JSON)
CREATE TABLE app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ---------------------------------------------------------------- วัสดุสิ้นเปลือง (migration 011)
-- ไม่มีราคาหรือมูลค่า · ยอดคงเหลือ = SUM(supply_moves.qty) เท่านั้น (หน่วยหลัก จำนวนเต็ม) ไม่มีช่องยอดที่แก้ได้
-- supply_moves แก้/ลบไม่ได้ ยกเลิกเอกสาร = เพิ่มแถว kind 'reversal' · ยอดสิ้นวันห้ามติดลบทุกวัน (trigger)
CREATE TABLE supply_categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE supply_items (
    id            INTEGER PRIMARY KEY,
    category_id   INTEGER NOT NULL REFERENCES supply_categories(id),
    name          TEXT NOT NULL,
    base_unit     TEXT NOT NULL,
    reorder_point INTEGER CHECK (reorder_point IS NULL OR reorder_point >= 0),
    active        INTEGER NOT NULL DEFAULT 1,
    note          TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (category_id, name)
);
CREATE TABLE supply_units (
    id      INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES supply_items(id),
    name    TEXT NOT NULL,
    factor  INTEGER NOT NULL CHECK (factor > 1),
    active  INTEGER NOT NULL DEFAULT 1,
    UNIQUE (item_id, name)
);
CREATE TRIGGER supply_units_factor_locked BEFORE UPDATE OF factor, item_id ON supply_units
WHEN NEW.factor IS NOT OLD.factor OR NEW.item_id IS NOT OLD.item_id
BEGIN SELECT RAISE(ABORT, 'unit factor is read-only'); END;
CREATE TABLE supply_import_batches (
    id           INTEGER PRIMARY KEY,
    file_name    TEXT NOT NULL,
    file_sha256  TEXT NOT NULL,
    opening_date DATE,
    summary      TEXT,
    created_by   INTEGER NOT NULL REFERENCES users(id),
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    undone_at    DATETIME,
    undone_by    INTEGER REFERENCES users(id),
    undo_reason  TEXT
);
CREATE TABLE supply_receipts (
    id             INTEGER PRIMARY KEY,
    received_date  DATE NOT NULL,
    source         TEXT NOT NULL,
    doc_no         TEXT,
    note           TEXT,
    created_by     INTEGER NOT NULL REFERENCES users(id),
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    cancelled_at   DATETIME,
    cancelled_by   INTEGER REFERENCES users(id),
    cancel_reason  TEXT,
    copied_from_id INTEGER REFERENCES supply_receipts(id)
);
CREATE TABLE supply_receipt_lines (
    id         INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES supply_receipts(id),
    item_id    INTEGER NOT NULL REFERENCES supply_items(id),
    unit_name  TEXT NOT NULL,
    unit_qty   INTEGER NOT NULL CHECK (unit_qty > 0),
    factor     INTEGER NOT NULL CHECK (factor >= 1),
    qty        INTEGER NOT NULL CHECK (qty = unit_qty * factor)
);
CREATE INDEX idx_supply_receipt_lines ON supply_receipt_lines(receipt_id);
CREATE TABLE supply_issues (
    id                INTEGER PRIMARY KEY,
    issue_date        DATE NOT NULL,
    department_id     INTEGER NOT NULL REFERENCES departments(id),
    requester_name    TEXT NOT NULL,
    doc_no            TEXT,
    approver_staff_id INTEGER REFERENCES staff(id),
    approver_name     TEXT NOT NULL,
    approved_date     DATE NOT NULL,
    note              TEXT,
    created_by        INTEGER NOT NULL REFERENCES users(id),
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    cancelled_at      DATETIME,
    cancelled_by      INTEGER REFERENCES users(id),
    cancel_reason     TEXT,
    copied_from_id    INTEGER REFERENCES supply_issues(id)
);
CREATE INDEX idx_supply_issues_date ON supply_issues(issue_date);
CREATE TABLE supply_issue_lines (
    id            INTEGER PRIMARY KEY,
    issue_id      INTEGER NOT NULL REFERENCES supply_issues(id),
    item_id       INTEGER NOT NULL REFERENCES supply_items(id),
    requested_qty INTEGER NOT NULL CHECK (requested_qty > 0),
    issued_qty    INTEGER NOT NULL CHECK (issued_qty >= 0 AND issued_qty <= requested_qty)
);
CREATE INDEX idx_supply_issue_lines ON supply_issue_lines(issue_id);
CREATE TABLE supply_counts (
    id           INTEGER PRIMARY KEY,
    count_date   DATE NOT NULL,
    counter_name TEXT NOT NULL,
    note         TEXT,
    opened_by    INTEGER NOT NULL REFERENCES users(id),
    opened_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    closed_at    DATETIME,
    closed_by    INTEGER REFERENCES users(id)
);
CREATE TABLE supply_count_lines (
    id          INTEGER PRIMARY KEY,
    count_id    INTEGER NOT NULL REFERENCES supply_counts(id),
    item_id     INTEGER NOT NULL REFERENCES supply_items(id),
    counted_qty INTEGER NOT NULL CHECK (counted_qty >= 0),
    system_qty  INTEGER NOT NULL,
    counted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    counted_by  INTEGER REFERENCES users(id),
    reason      TEXT,
    UNIQUE (count_id, item_id)
);
CREATE TABLE supply_moves (
    id              INTEGER PRIMARY KEY,
    item_id         INTEGER NOT NULL REFERENCES supply_items(id),
    move_date       DATE NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('opening', 'receipt', 'issue', 'adjust', 'reversal')),
    qty             INTEGER NOT NULL CHECK (qty != 0),
    receipt_line_id INTEGER REFERENCES supply_receipt_lines(id),
    issue_line_id   INTEGER REFERENCES supply_issue_lines(id),
    count_line_id   INTEGER REFERENCES supply_count_lines(id),
    import_batch_id INTEGER REFERENCES supply_import_batches(id),
    reverses_id     INTEGER REFERENCES supply_moves(id),
    note            TEXT,
    created_by      INTEGER NOT NULL REFERENCES users(id),
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_supply_moves_item ON supply_moves(item_id, move_date, id);
CREATE UNIQUE INDEX ux_supply_moves_reverses ON supply_moves(reverses_id) WHERE reverses_id IS NOT NULL;
CREATE TRIGGER supply_moves_no_update BEFORE UPDATE ON supply_moves
BEGIN SELECT RAISE(ABORT, 'supply moves are read-only'); END;
CREATE TRIGGER supply_moves_no_delete BEFORE DELETE ON supply_moves
BEGIN SELECT RAISE(ABORT, 'supply moves are read-only'); END;
CREATE TRIGGER supply_moves_no_negative AFTER INSERT ON supply_moves
WHEN EXISTS (SELECT 1 FROM (SELECT SUM(SUM(qty)) OVER (ORDER BY move_date) AS bal FROM supply_moves
                            WHERE item_id = NEW.item_id GROUP BY move_date) WHERE bal < 0)
BEGIN SELECT RAISE(ABORT, 'supply balance below zero'); END;

-- migration ที่รันกับ db นี้แล้ว (scripts/migrate.py รันเฉพาะตัวที่ยังไม่มีในตารางนี้)
-- db ที่สร้างจากไฟล์นี้มีโครงสร้างล่าสุดแล้ว จึงถือว่าผ่านทุกตัว: เพิ่ม migration ใหม่ต้องเพิ่มแถวที่นี่ด้วย
CREATE TABLE schema_migrations (
    name       TEXT PRIMARY KEY,
    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    note       TEXT
);
INSERT INTO schema_migrations (name, note) VALUES
    ('001', 'สร้างจาก docs/schema.sql'), ('002', 'สร้างจาก docs/schema.sql'), ('003', 'สร้างจาก docs/schema.sql'),
    ('004', 'สร้างจาก docs/schema.sql'), ('005', 'สร้างจาก docs/schema.sql'), ('006', 'สร้างจาก docs/schema.sql'),
    ('007', 'สร้างจาก docs/schema.sql'), ('008', 'สร้างจาก docs/schema.sql'), ('009', 'สร้างจาก docs/schema.sql'),
    ('010', 'สร้างจาก docs/schema.sql'), ('011', 'สร้างจาก docs/schema.sql');
