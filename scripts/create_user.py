"""สร้างผู้ใช้ระบบจาก command line (ถามรหัสผ่านโดยไม่แสดงบนจอ)

ตัวอย่าง:
    uv run python scripts/create_user.py somchai --name "สมชาย ใจดี" --role editor
    uv run python scripts/create_user.py somchai --update            # เปลี่ยนรหัสผ่าน
    uv run python scripts/create_user.py somchai --update --role admin   # เปลี่ยนสิทธิ์ (ถามรหัสผ่านใหม่ด้วย)

สิทธิ์: admin / editor = แก้ไขข้อมูลได้, viewer = ดูอย่างเดียว
"""
import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import connect, db_path  # noqa: E402
from app.security import MIN_PASSWORD_LENGTH, ROLES, USERNAME_RE, hash_password  # noqa: E402


def ask_password():
    for _ in range(3):
        pw = getpass.getpass("รหัสผ่าน (ไม่แสดงบนจอ): ")
        if len(pw) < MIN_PASSWORD_LENGTH:
            print(f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD_LENGTH} ตัวอักษร")
            continue
        if pw != getpass.getpass("พิมพ์รหัสผ่านอีกครั้ง: "):
            print("รหัสผ่านสองครั้งไม่ตรงกัน")
            continue
        return pw
    sys.exit("ยกเลิก: ใส่รหัสผ่านไม่สำเร็จ 3 ครั้ง")


def save_user(conn, username, password, role, display_name=None, update=False):
    """สร้างหรือแก้ผู้ใช้ (แยกออกมาเพื่อให้ test เรียกได้โดยไม่ต้องพิมพ์รหัส)"""
    exists = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    with conn:
        if exists and update:
            conn.execute(
                "UPDATE users SET password_hash = ?, role = ?, display_name = COALESCE(?, display_name),"
                " session_version = session_version + 1 WHERE id = ?",
                (hash_password(password), role, display_name, exists[0]))
            return exists[0]
        if exists:
            raise ValueError(f"มีผู้ใช้ {username} อยู่แล้ว (ถ้าจะเปลี่ยนรหัสผ่านให้ใส่ --update)")
        if update:
            raise ValueError(f"ไม่พบผู้ใช้ {username}")
        cur = conn.execute("INSERT INTO users (username, password_hash, display_name, role) VALUES (?, ?, ?, ?)",
                           (username, hash_password(password), display_name, role))
        return cur.lastrowid


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="สร้างผู้ใช้ระบบทะเบียนครุภัณฑ์")
    ap.add_argument("username", help="ชื่อผู้ใช้ภาษาอังกฤษตัวเล็ก ตัวเลข . _ - (3-32 ตัว)")
    ap.add_argument("--name", help="ชื่อที่แสดงบนเมนู เช่น 'สมชาย ใจดี'")
    ap.add_argument("--role", choices=ROLES, default="editor", help="สิทธิ์ (ค่าเริ่มต้น editor)")
    ap.add_argument("--update", action="store_true", help="แก้ผู้ใช้ที่มีอยู่แล้ว (ตั้งรหัสผ่านใหม่และสิทธิ์)")
    args = ap.parse_args()

    username = args.username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        sys.exit("ชื่อผู้ใช้ใช้ได้เฉพาะ a-z 0-9 . _ - ยาว 3-32 ตัว")

    conn = connect()
    try:
        exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if exists and not args.update:
            sys.exit(f"มีผู้ใช้ {username} อยู่แล้ว ถ้าจะเปลี่ยนรหัสผ่านให้ใส่ --update")
        if not exists and args.update:
            sys.exit(f"ไม่พบผู้ใช้ {username}")
        print(f"ฐานข้อมูล: {db_path()}")
        print(f"{'แก้' if exists else 'สร้าง'}ผู้ใช้ {username} สิทธิ์ {args.role}")
        password = ask_password()
        uid = save_user(conn, username, password, args.role, args.name, update=args.update)
    except (ValueError, sqlite3.Error) as e:
        sys.exit(f"ไม่สำเร็จ: {e}")
    finally:
        conn.close()
    print(f"เรียบร้อย (id {uid})")


if __name__ == "__main__":
    main()
