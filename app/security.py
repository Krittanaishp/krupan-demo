"""hash รหัสผ่านด้วย argon2 (ไม่พึ่ง FastAPI เพื่อให้สคริปต์ command line ใช้ได้)"""
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

ROLES = ("admin", "editor", "viewer")
EDIT_ROLES = ("admin", "editor")
MIN_PASSWORD_LENGTH = 8
USERNAME_RE = re.compile(r"[a-z0-9._-]{3,32}")

_hasher = PasswordHasher()
# hash ไว้เทียบเมื่อไม่พบชื่อผู้ใช้ ให้เวลาตอบใกล้เคียงกับกรณีรหัสผิด (เดาชื่อผู้ใช้จากเวลาไม่ได้)
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")


def hash_password(password):
    return _hasher.hash(password)


def verify_password(password_hash, password):
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash):
    return _hasher.check_needs_rehash(password_hash)


def burn_time(password):
    verify_password(_DUMMY_HASH, password)
