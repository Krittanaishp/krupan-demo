"""BASE_URL (ที่อยู่ที่ฝังใน QR) และ IP ของเครื่องที่รันระบบ"""
import ipaddress
import socket
import time
from urllib.parse import urlparse

from app.config import get_setting


def base_url():
    """ที่อยู่ของระบบตามที่มือถือในวงแลนเห็น เช่น http://192.168.1.50:8000 (ไม่มี / ท้าย) หรือ '' ถ้ายังไม่ตั้ง"""
    return (get_setting("BASE_URL") or "").strip().rstrip("/")


def local_ipv4s():
    """IP v4 ของเครื่องนี้ (ไม่รวม 127.x) ตัวแรกคือ IP ที่ใช้ออกเครือข่ายจริง"""
    found = []
    try:
        # UDP connect ไม่ส่งแพ็กเก็ตจริง แค่ให้ระบบเลือกการ์ดเครือข่ายที่ใช้ออกไปข้างนอก
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    out = []
    for ip in found:
        if ip not in out and not ip.startswith("127.") and not ip.startswith("169.254."):
            out.append(ip)
    return out


def resolve_host(host):
    """hostname -> IP v4 (ถ้าเป็น IP อยู่แล้วคืนตัวเดิม) แปลงไม่ได้คืน []"""
    if is_ip(host):
        return [host]
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None, socket.AF_INET)})
    except OSError:
        return []


def is_ip(host):
    try:
        ipaddress.IPv4Address(host)
        return True
    except ValueError:
        return False


MDNS_NOTE = ("ชื่อที่ลงท้ายด้วย .local ใช้ mDNS ซึ่งมือถือ Android หลายรุ่นแปลงเป็น IP ไม่ได้ (สแกน QR แล้วเปิดไม่ขึ้น) "
             "แนะนำให้ตั้งชื่อใน local DNS ของ router แทน หรือใช้ IP ที่จองไว้")


def inspect(url, ips, resolver=None):
    """ตรวจ BASE_URL เทียบกับ IP ของเครื่อง รับได้ทั้ง IP และชื่อ (เช่น http://krupan.klongyai:8000 จาก local DNS ของ router)
    ชื่อจะถูกแปลงเป็น IP ด้วย DNS ของเครื่องนี้ แล้วต้องตรงกับ IP ของเครื่องอย่างน้อย 1 ตัว
    คืน dict: ok, message, host, is_name, resolved (IP ที่แปลงได้), note (คำแนะนำเพิ่ม ไม่ทำให้ ok เป็น False)"""
    resolver = resolver or resolve_host
    out = {"ok": False, "host": "", "is_name": False, "resolved": [], "note": ""}
    if not url:
        return out | {"message": "ยังไม่ได้ตั้ง BASE_URL ในไฟล์ .env QR จะใช้ที่อยู่ที่เปิดหน้าเว็บอยู่ ซึ่งมือถืออาจเปิดไม่ได้"}
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return out | {"message": "BASE_URL ต้องขึ้นต้นด้วย http:// หรือ https:// เช่น http://192.168.1.50:8000"}
    host = p.hostname
    out.update(host=host, is_name=not is_ip(host))
    if host == "localhost" or host.startswith("127."):
        return out | {"message": "BASE_URL ชี้ไปที่ localhost มือถือเปิดไม่ได้ ใช้ IP หรือชื่อของเครื่องในวงแลนแทน"}
    if out["is_name"] and host.endswith(".local"):
        out["note"] = MDNS_NOTE
    resolved = resolver(host)
    out["resolved"] = resolved
    match = set(resolved) & set(ips)
    if not out["is_name"]:
        if not match:
            return out | {"message": "BASE_URL ไม่ตรงกับ IP ของเครื่องนี้"}
        return out | {"ok": True, "message": "BASE_URL ตรงกับ IP ของเครื่องนี้"}
    if not resolved:
        return out | {"message": f"แปลงชื่อ {host} ใน BASE_URL เป็น IP ไม่ได้ (router ยังไม่ได้ตั้ง local DNS ให้ชื่อนี้ "
                                 "หรือเครื่องนี้ไม่ได้ใช้ DNS ของ router) มือถือจะเปิด QR ไม่ได้"}
    if not match:
        return out | {"message": f"ชื่อ {host} ใน BASE_URL แปลงเป็น {', '.join(resolved)} ซึ่งไม่ใช่ IP ของเครื่องนี้"}
    return out | {"ok": True, "message": f"ชื่อ {host} ใน BASE_URL แปลงเป็น IP ของเครื่องนี้ ({', '.join(sorted(match))})"}


def check(url, ips):
    """ตรวจ BASE_URL เทียบกับ IP ของเครื่อง คืน (ok, ข้อความ)"""
    r = inspect(url, ips)
    return r["ok"], r["message"]


_cache = {"at": 0.0, "url": None, "value": None}
CACHE_SECONDS = 30


def warning():
    """None ถ้า BASE_URL ตรงกับ IP ของเครื่อง ไม่อย่างนั้น {"message", "base_url", "ips"} สำหรับแถบเตือนของ admin
    เก็บผลไว้ 30 วินาที (แถบเตือนแสดงทุกหน้า ไม่ต้องตรวจเครือข่ายทุก request) แก้ .env แล้วเห็นผลทันที"""
    url = base_url()
    now = time.monotonic()
    if _cache["url"] == url and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    ips = local_ipv4s()
    ok, message = check(url, ips)
    value = None if ok else {"message": message, "base_url": url, "ips": ips}
    _cache.update(at=now, url=url, value=value)
    return value


def clear_cache():
    _cache.update(at=0.0, url=None, value=None)
