"""ตรวจหน้าตาเว็บอัตโนมัติด้วยเบราว์เซอร์จริง (Edge แบบไม่มีหน้าต่าง): ไม่ได้อยู่ในชุด pytest และไม่อยู่ใน zip ติดตั้ง

สิ่งที่ตรวจทุกหน้าที่แต่ละความกว้าง:
  - หน้าและตารางไม่เลื่อนแนวนอน · ปุ่มในแต่ละแถวของตารางต้องเห็นโดยไม่ต้องเลื่อน
  - ป้าย (.tag .flag .badge .count-pill .chip) ตัวเลือก radio/checkbox และหัวคอลัมน์ ไม่ตกบรรทัดกลางคำ
    (ปัญหาที่เป็นข้อความยาวทั้งประโยค .flag-long ตัดบรรทัดระหว่างคำได้ ไม่นับ)
  - ป้ายไม่ถูกตัดด้วย … · radio และ checkbox ในหน้าเดียวกันขนาดเท่ากัน

วิธีใช้ (ต้องมี Microsoft Edge ในเครื่อง ใช้อินเทอร์เน็ตครั้งแรกเพื่อโหลด playwright):
  uv run --with playwright python tests/ui_audit.py                       ใช้ข้อมูลสมมติของ test
  uv run --with playwright python tests/ui_audit.py --db <สำเนา db>       ใช้สำเนาข้อมูลจริง (ข้อความยาวจริง)
  uv run --with playwright python tests/ui_audit.py --widths 390,844       เฉพาะบางความกว้าง (ค่าเริ่มต้น 390,844,1024,1280,1366,1920)

ก่อนสร้าง zip ทุกครั้งต้องรันทุกความกว้าง (ค่าเริ่มต้น) ให้ได้ 0 ปัญหา ทั้งข้อมูลสมมติและสำเนาข้อมูลจริง

ห้ามชี้ --db ไปที่ data/krupan.db: สคริปต์คัดลอกไฟล์ที่ให้ไปไว้ในโฟลเดอร์ชั่วคราวอีกชั้น แล้วเพิ่มผู้ใช้ตรวจในสำเนานั้น
จบด้วย exit code 1 ถ้าพบปัญหา
"""
import argparse
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "data" / "krupan.db"
USER, PASSWORD = "ui-audit", "ui-audit-pass-123"
IMPORT_PREVIEW = "(ตรวจไฟล์นำเข้าวัสดุ)"  # หน้าแสดงตัวอย่างการนำเข้า เปิดด้วยการอัปโหลดไฟล์ที่มีทุกกรณี
SAMPLE = {}

CHECK_JS = r"""
() => {
  const out = {overflowPage: 0, tables: [], wraps: [], cut: [], sizes: {}};
  const de = document.documentElement;
  out.overflowPage = de.scrollWidth - de.clientWidth;
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = el => (el.innerText || el.value || '').trim().replace(/\s+/g, ' ').slice(0, 40);
  document.querySelectorAll('.table-wrap, .table-scroll').forEach(w => {
    if (!vis(w)) return;
    const wr = w.getBoundingClientRect();
    const over = w.scrollWidth - w.clientWidth;
    const hidden = [];
    w.querySelectorAll('tbody .btn, tbody button, tbody input[type=submit]').forEach(b => {
      if (!vis(b)) return;
      if (b.getBoundingClientRect().right > Math.min(wr.right, de.clientWidth) + 1) hidden.push(label(b));
    });
    if (over > 1 || hidden.length) out.tables.push({over, hidden: [...new Set(hidden)].slice(0, 5), n: hidden.length});
  });
  const sel = '.tag:not(.flag-long), .flag:not(.flag-long), .badge, .count-pill, .chip, th, label.check, .check > span, label.seg-opt';
  const one = [];
  document.querySelectorAll(sel).forEach(el => {
    if (!vis(el) || !el.innerText.trim() || el.closest('.sr-only') || el.querySelector(':scope > .sr-only:only-child')) return;
    let target = el;
    if (el.matches('label.check, label.seg-opt')) { const sp = el.querySelector('span'); if (sp) target = sp; }
    const s = getComputedStyle(target);
    let lh = parseFloat(s.lineHeight); if (isNaN(lh)) lh = parseFloat(s.fontSize) * 1.5;
    // ตกบรรทัด = ข้อความในกล่องเริ่มต่างบรรทัดกันเกินครึ่งความสูงบรรทัด (ไม่นับ padding พื้นที่แตะ)
    const tops = [];
    const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const n = walker.currentNode; if (!n.textContent.trim() || n.parentElement.closest('.sr-only')) continue;
      const rg = document.createRange(); rg.selectNodeContents(n);
      [...rg.getClientRects()].filter(r => r.width > 0).forEach(r => tops.push(r.top));
    }
    const spread = tops.length ? Math.max(...tops) - Math.min(...tops) : 0;
    if (spread > lh * 0.5 && target.innerText.trim().length < 60) one.push(el.tagName.toLowerCase() + ': ' + label(target));
  });
  out.wraps = [...new Set(one)].slice(0, 8); out.nWraps = one.length;
  const cut = [];
  document.querySelectorAll('.tag:not(.flag-long), .flag:not(.flag-long), .badge, .count-pill').forEach(el => {
    if (vis(el) && el.scrollWidth > el.clientWidth + 1) cut.push(label(el)); });
  out.cut = [...new Set(cut)].slice(0, 6); out.nCut = cut.length;
  // ช่องวันที่/เดือนของระบบ (datepicker.js): แสดง พ.ศ. ตรงกับค่า (ค.ศ.) ในช่องซ่อน ไม่ล้นกล่อง ปุ่มปฏิทินกดง่าย
  // ช่องที่เว้นว่างได้ต้องบอกความหมายตอนว่าง · ไม่มีช่องวันที่ของเบราว์เซอร์ (ปฏิทิน ค.ศ.) เหลืออยู่
  const M = ['ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
  const th = v => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v); return m ? (+m[3]) + ' ' + M[+m[2] - 1] + ' ' + (+m[1] + 543) : ''; };
  const thm = v => { const m = /^(\d{4})-(\d{2})$/.exec(v); return m ? M[+m[2] - 1] + ' ' + (+m[1] + 543) : ''; };
  out.dates = [];
  document.querySelectorAll('input[type=date], input[type=month]').forEach(i => {
    if (vis(i) && !i.hasAttribute('data-no-thai-date')) out.dates.push((i.id || i.name) + ': ยังเป็นช่องวันที่ของเบราว์เซอร์ (ปฏิทิน ค.ศ.)');
  });
  document.querySelectorAll('input[data-dp]').forEach(i => {
    if (!vis(i)) return;
    const id = i.id, month = i.dataset.dp === 'month';
    const h = i.parentElement.querySelector('input[data-dp-value]');
    if (!h || !h.name) { out.dates.push(id + ': ไม่มีช่องซ่อนที่เก็บค่า'); return; }
    if (i.name) out.dates.push(id + ': ช่องที่เห็นมี name (ค่า พ.ศ. จะถูกส่งไปเซิร์ฟเวอร์)');
    if (h.value && !(month ? /^\d{4}-\d{2}$/ : /^\d{4}-\d{2}-\d{2}$/).test(h.value)) out.dates.push(id + `: ค่าที่ส่งไม่ใช่ ค.ศ. ISO (${h.value})`);
    const want = month ? thm(h.value) : th(h.value);
    if (document.activeElement !== i && i.value !== want) out.dates.push(id + `: แสดง “${i.value}” ค่า ${h.value}`);
    if (!h.value && !i.placeholder) out.dates.push(id + ': ช่องว่างไม่มีข้อความบอกความหมาย');
    if (!i.required && !i.dataset.emptyLabel && !month) out.dates.push(id + ': ช่องวันที่เว้นว่างได้แต่ไม่มีข้อความตอนว่าง (data-empty-label)');
    if (i.value && i.scrollWidth > i.clientWidth + 1) out.dates.push(id + ': ข้อความล้นกล่อง');
    const t = i.parentElement.querySelector('[data-dp-toggle]');
    if (!t) { out.dates.push(id + ': ไม่มีปุ่มปฏิทิน'); return; }
    const a = i.getBoundingClientRect(), tb = t.getBoundingClientRect();
    if (tb.height < 44 && a.height >= 44) out.dates.push(id + `: ปุ่มปฏิทินเตี้ยกว่า 44px (${Math.round(tb.height)})`);
    if (tb.right > a.right + 1 || tb.left < a.left || tb.top < a.top - 1 || tb.bottom > a.bottom + 1) out.dates.push(id + ': ปุ่มปฏิทินล้นกล่อง');
  });
  // ---------------- ความเรียบร้อยของหน้า (out.layout: [ชนิด, รายละเอียด]) ----------------
  out.layout = [];
  const L = (kind, text) => out.layout.push([kind, text]);
  const rect = el => el.getBoundingClientRect();
  const name = el => (el.id ? '#' + el.id : '') || el.getAttribute('name') || label(el) || el.tagName.toLowerCase();
  const inCell = el => !!el.closest('td, th, .cb-popup, .sr-only, noscript, [hidden]');
  const sameRow = (a, b) => { const ov = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return ov > Math.min(a.height, b.height) * 0.5; };
  const layer = el => { for (let e = el; e && e !== document.body; e = e.parentElement) {
    const pos = getComputedStyle(e).position; if (pos === 'sticky' || pos === 'fixed') return e; } return null; };
  const rowsOf = items => { const res = []; items.sort((x, y) => x.r.top - y.r.top).forEach(it => {
    it.layer = it.layer === undefined ? layer(it.c) : it.layer;
    const row = res.find(rw => rw[0].layer === it.layer && rw.some(o => sameRow(o.r, it.r))); if (row) row.push(it); else res.push([it]); });
    return res; };
  // 1) ช่องกรอก/ปุ่มที่อยู่แถวเดียวกันในฟอร์ม: ขอบบน/ล่างตรงกัน (คลาดได้ 2px) และป้ายชื่ออยู่แนวเดียวกัน
  const CTRL = 'input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), select, button, a.btn';
  document.querySelectorAll('form').forEach(f => {
    if (!vis(f) || inCell(f)) return;
    const items = [...f.querySelectorAll(CTRL)].filter(c => vis(c) && !inCell(c) && !c.classList.contains('cb-toggle') &&
                  !c.closest('details:not([open]) > :not(summary)') && rect(c).height >= 30).map(c => ({c, r: rect(c)}));
    rowsOf(items).forEach(rw => {
      if (rw.length < 2) return;
      const tops = rw.map(o => o.r.top), bots = rw.map(o => o.r.bottom);
      if (Math.max(...tops) - Math.min(...tops) > 2 || Math.max(...bots) - Math.min(...bots) > 2)
        L('แถวไม่ตรง', rw.map(o => `${name(o.c)}@${Math.round(o.r.top)}-${Math.round(o.r.bottom)}`).join(', '));
      const labs = rw.map(o => o.c.closest('.field')).filter(fd => fd && fd.querySelector(':scope > label'))
                     .map(fd => ({fd, t: rect(fd.querySelector(':scope > label')).top}));
      if (labs.length > 1 && Math.max(...labs.map(x => x.t)) - Math.min(...labs.map(x => x.t)) > 2)
        L('ป้ายชื่อไม่ตรง', labs.map(x => `${label(x.fd.querySelector(':scope > label'))}@${Math.round(x.t)}`).join(', '));
    });
  });
  // 2) ปุ่มที่อยู่แถวเดียวกัน (ในกล่องเดียวกัน) สูงเท่ากัน · ปุ่มต้องไม่ขึ้นสองบรรทัด
  const btns = [...document.querySelectorAll('button, a.btn, .btn')].filter(b => vis(b) && !inCell(b) &&
               !b.classList.contains('cb-toggle') && !b.closest('.cb-field, .nav, .account'));
  const byParent = new Map();
  btns.forEach(b => { const k = b.parentElement; if (!byParent.has(k)) byParent.set(k, []); byParent.get(k).push({c: b, r: rect(b)}); });
  byParent.forEach(list => rowsOf(list).forEach(rw => {
    if (rw.length < 2) return;
    const hs = rw.map(o => o.r.height);
    if (Math.max(...hs) - Math.min(...hs) > 2) L('ปุ่มสูงไม่เท่ากัน', rw.map(o => `${label(o.c)}=${Math.round(o.r.height)}`).join(', '));
  }));
  btns.forEach(b => {
    const st = getComputedStyle(b); let lh = parseFloat(st.lineHeight); if (isNaN(lh)) lh = parseFloat(st.fontSize) * 1.5;
    const rg = document.createRange(); rg.selectNodeContents(b);
    const tops = [...rg.getClientRects()].filter(r => r.width > 1).map(r => r.top);
    if (tops.length && Math.max(...tops) - Math.min(...tops) > lh * 0.5) L('ปุ่มสองบรรทัด', label(b));
  });
  // 3) ช่องติ๊ก/ตัวเลือก กับข้อความกำกับอยู่กึ่งกลางแนวเดียวกัน (คลาดได้ 3px)
  document.querySelectorAll('input[type=checkbox], input[type=radio]').forEach(i => {
    if (!vis(i) || i.closest('td, th')) return;
    const lab = i.closest('label') || (i.id && document.querySelector(`label[for="${i.id}"]`));
    if (!lab) return;
    const walker = document.createTreeWalker(lab, NodeFilter.SHOW_TEXT);
    let first = null;
    while (walker.nextNode()) { const tn = walker.currentNode; if (tn.textContent.trim() && vis(tn.parentElement)) { first = tn; break; } }
    if (!first) return;
    const rg = document.createRange(); rg.selectNodeContents(first);
    const tr = [...rg.getClientRects()].find(r => r.width > 0); if (!tr) return;
    const ir = rect(i), ci = ir.top + ir.height / 2, ct = tr.top + tr.height / 2;
    if (Math.abs(ci - ct) > 3) L('ช่องติ๊กไม่ตรงข้อความ', `${label(lab)} (ห่าง ${Math.round(ci - ct)}px)`);
  });
  // 4) คอลัมน์ตัวเลขชิดขวา คอลัมน์ข้อความชิดซ้าย หัวคอลัมน์ชิดตามข้อมูล (ตารางที่แสดงเป็นตาราง ไม่ใช่การ์ด)
  const NUM = /^[-−+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?(\s+[^\s\d]{1,10})?$/;
  const align = el => { const a = getComputedStyle(el).textAlign; return a === 'end' || a === 'right' ? 'right' : (a === 'center' ? 'center' : 'left'); };
  const TH = {right: 'ขวา', center: 'กลาง', left: 'ซ้าย'};
  document.querySelectorAll('table').forEach(t => {
    if (!vis(t) || getComputedStyle(t).display !== 'table') return;
    const body = [...t.querySelectorAll(':scope > tbody > tr')].filter(r => vis(r) && ![...r.cells].some(c => c.colSpan > 1));
    const head = [...t.querySelectorAll(':scope > thead > tr')].find(r => ![...r.cells].some(c => c.colSpan > 1));
    const n = body.length ? Math.max(...body.map(r => r.cells.length)) : 0;
    for (let ci = 0; ci < n; ci++) {
      const cells = body.map(r => r.cells[ci]).filter(c => c && vis(c));
      // "—" / "ไม่ทราบ" = ค่าว่างในคอลัมน์ตัวเลข (เช่น ราคาที่ไม่ทราบเป็น NULL) ไม่นับเป็นข้อความ
      const texts = cells.map(c => ({c, t: (c.innerText || '').trim()})).filter(x => x.t && !['—', '-', 'ไม่ทราบ'].includes(x.t) &&
                    !x.c.querySelector('input, select, button, .btn, .badge, .tag, a.btn'));
      if (!texts.length) continue;
      const numeric = texts.filter(x => NUM.test(x.t)).length / texts.length >= 0.8;
      const want = numeric ? 'right' : 'left';
      const bad = texts.filter(x => align(x.c) !== want);
      const th_ = head && head.cells[ci];
      const hname = th_ ? (th_.innerText || '').trim().slice(0, 24) : `คอลัมน์ ${ci + 1}`;
      if (bad.length) L('คอลัมน์ชิดผิด', `${hname || 'คอลัมน์ ' + (ci + 1)}: ${numeric ? 'ตัวเลข' : 'ข้อความ'}แต่ชิด${TH[align(bad[0].c)]} (“${bad[0].t.slice(0, 20)}”)`);
      if (th_ && vis(th_) && (th_.innerText || '').trim() && align(th_) !== want)
        L('หัวคอลัมน์ชิดไม่ตรงข้อมูล', `${hname}: หัว${TH[align(th_)]} ข้อมูล${TH[want]}`);
    }
  });
  // 5) ระยะห่างระหว่างการ์ด/ส่วนที่เรียงต่อกันในกล่องเดียวกัน (แนวตั้ง คอลัมน์เดียวกัน) ต้องเท่ากัน (คลาดได้ 2px)
  const parents = new Set([...document.querySelectorAll('.card, main section')].map(c => c.parentElement));
  parents.forEach(pa => {
    if (!pa || ['grid', 'inline-grid'].includes(getComputedStyle(pa).display)) return;
    const shown = [...pa.children].filter(k => vis(k) && !['absolute', 'fixed'].includes(getComputedStyle(k).position));
    const isCard = k => (k.classList.contains('card') || k.tagName === 'SECTION') && !inCell(k);
    // ชุดของการ์ดที่อยู่ติดกัน (ไม่มี element อื่นคั่น) ตั้งแต่ 3 ใบขึ้นไป
    let run = [];
    const flush = () => {
      if (run.length >= 3) {
        const gaps = run.slice(1).map((k, i) => Math.round(rect(k).top - rect(run[i]).bottom));
        if (Math.max(...gaps) - Math.min(...gaps) > 2) L('ระยะห่างการ์ดไม่เท่ากัน', `${name(pa)}: ${gaps.join(', ')}px`);
      }
      run = [];
    };
    shown.forEach(k => { if (isCard(k)) run.push(k); else flush(); });
    flush();
  });
  // 6) ไม่มีคำที่ถูกตัดกลางคำ (ตัดคำด้วย Intl.Segmenter ภาษาไทย ใช้ได้ทั้งไทยและอังกฤษ/ตัวเลข)
  if (window.Intl && Intl.Segmenter) {
    const seg = new Intl.Segmenter('th', {granularity: 'word'});
    const walker = document.createTreeWalker(document.querySelector('main') || document.body, NodeFilter.SHOW_TEXT);
    let budget = 8000; const broken = [];
    while (walker.nextNode() && budget > 0) {
      const n = walker.currentNode, el = n.parentElement;
      if (!el || !n.textContent.trim() || el.closest('.sr-only, script, style, noscript, [hidden], svg, .cb-popup') || !vis(el)) continue;
      const rg0 = document.createRange(); rg0.selectNodeContents(n);
      const lines = new Set([...rg0.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top)));
      if (lines.size < 2) continue;  // ข้อความบรรทัดเดียว ไม่มีทางถูกตัดกลางคำ
      for (const w of seg.segment(n.textContent)) {
        if (!w.isWordLike || w.segment.length < 2) continue;
        if (--budget <= 0) break;
        const rg = document.createRange(); rg.setStart(n, w.index); rg.setEnd(n, w.index + w.segment.length);
        const tops = new Set([...rg.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top)));
        if (tops.size < 2) continue;
        const text = n.textContent;
        let a0 = w.index, b0 = w.index + w.segment.length;
        while (a0 > 0 && !/\s/.test(text[a0 - 1])) a0--;
        while (b0 < text.length && !/\s/.test(text[b0])) b0++;
        const tk = document.createRange(); tk.setStart(n, a0); tk.setEnd(n, b0);
        const tokenWidth = [...tk.getClientRects()].reduce((sum, r) => sum + r.width, 0);
        if (tokenWidth >= el.clientWidth - 4) continue;  // ข้อความติดกันยาวเกินกล่อง (ชื่อไฟล์/URL) ต้องตัดอยู่แล้ว
        broken.push(w.segment);
      }
    }
    if (broken.length) L('ตัดกลางคำ', [...new Set(broken)].slice(0, 6).join(', '));
  }
  for (const t of ['radio', 'checkbox']) {
    const s = new Set();
    document.querySelectorAll('input[type=' + t + ']').forEach(i => {
      if (vis(i)) { const r = i.getBoundingClientRect(); s.add(Math.round(r.width) + 'x' + Math.round(r.height)); } });
    out.sizes[t] = [...s];
  }
  return out;
}
"""


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def prepare_db(src, workdir):
    """คืน path สำเนา db ในโฟลเดอร์ชั่วคราว พร้อมผู้ใช้ admin สำหรับตรวจ"""
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("KRUPAN_SECRET_KEY", "ui-audit-" + "x" * 40)
    from scripts.create_user import save_user
    dst = workdir / "audit.db"
    if src:
        src = Path(src).resolve()
        if REAL_DB.exists() and src == REAL_DB.resolve():
            sys.exit("ห้ามใช้ data/krupan.db ตรง ๆ ให้คัดลอกไปไว้ที่อื่นก่อน")
        with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
            s.backup(d)
    else:
        os.environ["KRUPAN_DB"] = str(workdir / "unused.db")
        from tests.conftest import make_db
        make_db(dst)
    # สำเนาข้อมูลจริงที่ยังไม่ได้รัน migration ล่าสุด: รันกับสำเนาในโฟลเดอร์ชั่วคราวนี้เท่านั้น
    from scripts import migrate
    if migrate.pending(dst):
        migrate.run(dst, backup_dir=workdir / "migrate-backups", out=lambda *_: None)
    conn = sqlite3.connect(dst)
    if not conn.execute("SELECT 1 FROM users WHERE username = ?", (USER,)).fetchone():
        save_user(conn, USER, PASSWORD, "admin", display_name="ผู้ตรวจหน้าจอ")
    conn.close()
    seed_supplies(dst, workdir)
    from scripts.make_supply_sample import PROBLEMS, build
    SAMPLE["problems"] = str(workdir / "problems.xlsx")
    Path(SAMPLE["problems"]).write_bytes(build(PROBLEMS))
    return dst


def seed_supplies(dst, workdir):
    """ข้อมูลวัสดุสมมติในสำเนาชั่วคราว (ถ้ายังไม่มี): นำเข้าไฟล์ตัวอย่าง + ใบรับ 1 ใบ ใบเบิก 2 ใบ (ยกเลิก 1 ใบ)
    ชื่อยาวพิเศษ 1 รายการ เพื่อตรวจการตัดบรรทัด"""
    from datetime import timedelta

    from app import supplies as sp
    from app import supplies_import as si
    from app import timeutil
    from scripts.make_supply_sample import GOOD, build
    conn = sqlite3.connect(dst)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        if conn.execute("SELECT COUNT(*) FROM supply_items").fetchone()[0]:
            return
        os.environ["KRUPAN_BACKUP_DIR"] = str(workdir / "backups")
        uid = conn.execute("SELECT id FROM users WHERE username = ?", (USER,)).fetchone()[0]
        today = timeutil.today_th()
        long_name = ["อุปกรณ์สำนักงาน", "ตลับหมึกเครื่องพิมพ์เลเซอร์สีดำความจุสูงพิเศษสำหรับงานพิมพ์เอกสารราชการจำนวนมาก",
                     "ตลับ", 1, "แพ็ก", 2, None, None, 1, None]
        si.apply(conn, uid, build(GOOD + [long_name]), "ตัวอย่างข้อมูลสมมติ.xlsx", today - timedelta(days=7), set())
        if not conn.execute("SELECT 1 FROM departments WHERE issues_supplies = 1").fetchone():
            for i, name in enumerate(("สำนักงาน", "ปกครอง", "ทะเบียน", "ความมั่นคง", "การเงิน (เสมียนตรา)"), 1):
                conn.execute("INSERT OR IGNORE INTO departments (name, sort_order) VALUES (?, ?)", (name, 100 + i))
                conn.execute("UPDATE departments SET issues_supplies = 1 WHERE name = ?", (name,))
        if not conn.execute("SELECT 1 FROM staff WHERE active = 1").fetchone():
            conn.execute("INSERT INTO staff (full_name, position) VALUES ('นายผู้อนุมัติ ทดสอบหน้าจอ', 'ปลัดอำเภอ')")
        conn.commit()
        item = lambda name: sp.resolve_item(conn, name)  # noqa: E731
        form = {"received_date": (today - timedelta(days=3)).isoformat(), "source": "จัดซื้อ ร้านเครื่องเขียนตัวอย่าง (สมมติ)",
                "doc_no": "บส 99/2569", "item_1": item("A4 80 แกรม")["label"], "unit_1": str(item("A4 80 แกรม")["units"][0]["id"]),
                "qty_1": "4", "item_2": item("ปากกาลูกลื่น สีแดง")["label"], "qty_2": "50"}
        h, lines, errors, _ = sp.parse_receipt(conn, form)
        sp.record_receipt(conn, uid, h, lines)
        dep = conn.execute("SELECT id FROM departments WHERE issues_supplies = 1 ORDER BY sort_order LIMIT 1").fetchone()[0]
        staff = conn.execute("SELECT id FROM staff WHERE active = 1 ORDER BY id LIMIT 1").fetchone()[0]
        for n, (name, req) in enumerate((("ปากกาลูกลื่น สีน้ำเงิน", 12), ("Canon 337", 2))):
            form = {"issue_date": today.isoformat(), "department_id": str(dep), "requester_name": "นางสาวผู้เบิก ตัวอย่างหน้าจอ",
                    "approver_staff_id": str(staff), "approved_date": today.isoformat(), "doc_no": f"บก {n + 1}/2569",
                    "item_1": item(name)["label"], "req_1": str(req), "item_2": item("A4 80 แกรม")["label"], "req_2": "3"}
            h, lines, errors, _ = sp.parse_issue(conn, form)
            iid = sp.record_issue(conn, uid, h, lines)
        sp.cancel_issue(conn, uid, iid, "ตัวอย่างใบเบิกที่ยกเลิก (สมมติ)")
    finally:
        conn.close()


def pages(db):
    conn = sqlite3.connect(db)
    one = lambda sql: (conn.execute(sql).fetchone() or [None])[0]  # noqa: E731
    asset = one("SELECT id FROM assets ORDER BY id LIMIT 1")
    flagged = one("SELECT id FROM assets WHERE needs_review IS NOT NULL ORDER BY id LIMIT 1") or asset
    check = one("SELECT id FROM inventory_checks ORDER BY id DESC LIMIT 1")
    room = one("SELECT id FROM locations WHERE active = 1 ORDER BY id LIMIT 1")
    conn.close()
    out = ["/assets", "/assets?status=รายการซ้ำ", f"/assets/{asset}", f"/assets/{flagged}/edit",
           "/review", "/review?group=price", "/review?group=regno", "/review?group=dup", "/review?group=nodate",
           "/review?group=partial", "/review?group=excel_year", "/review?group=name", "/review?group=verify",
           "/review?group=other", "/places", "/places/mapping", "/places/merge", "/places/staff", "/types",
           "/reports", "/reports/summary", "/labels", "/settings", "/settings/backup", "/settings/network",
           "/settings/rooms", "/users", "/inventory",
           "/supplies", "/supplies?low=1", "/supplies?as_of=2026-11-01", "/supplies/low", "/supplies/monthly", "/supplies/monthly?month=2000-01", "/supplies/issue", "/supplies/issues", "/supplies/issues?month=2000-01", "/supplies/issues/1",
           "/supplies/issues/2", "/supplies/receive", "/supplies/receipts", "/supplies/receipts?month=2026-11", "/supplies/receipts/1", "/supplies/items",
           "/supplies/items/1", "/supplies/items/1?date_from=2026-10-01", "/supplies/items/1/edit", "/supplies/items/new", "/supplies/categories", "/supplies/import",
           "/settings/supplies", "/account/password", "/supplies/issue?copy=2", "/supplies/receive?copy=1", IMPORT_PREVIEW]
    if check:
        out += [f"/inventory/{check}", f"/inventory/{check}/close"] + ([f"/inventory/{check}/rooms/{room}"] if room else [])
    return out


def live_thai_date(page):
    """พิมพ์วันที่ พ.ศ. ในช่องวันที่แรกของหน้า (5/1/2569) แล้วออกจากช่อง: ต้องแสดง "5 ม.ค. 2569" และค่าที่ส่งเป็น 2026-01-05
    แล้วคืนค่าเดิม"""
    box = page.locator("input[data-dp=date]:visible")
    if not box.count():
        return []
    first = box.first
    hidden = first.locator("xpath=following-sibling::input[@data-dp-value]")
    old = hidden.input_value()
    first.fill("5/1/2569")
    first.press("Tab")
    got, value = first.input_value(), hidden.input_value()
    first.evaluate("(i, v) => window.krupanSetDate(i, v)", old)
    page.locator("body").click(position={"x": 1, "y": 1})
    page.wait_for_load_state("networkidle")
    return [] if (got, value) == ("5 ม.ค. 2569", "2026-01-05") else [f"ช่องวันที่ไม่อัปเดตเมื่อพิมพ์ (ได้ {got!r} / {value!r})"]


THAI_MONTHS_FULL = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
                    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


def calendar_open(page, width, url, shots):
    """ช่องวันที่/เดือนแรกของหน้า: กดปุ่มปฏิทิน ตรวจหัว (เดือนไทย + ปี พ.ศ.) ไม่ล้นจอ ช่องวันกดง่าย ชิดช่อง แล้วถ่ายภาพ"""
    box = page.locator("input[data-dp]:visible")
    if not box.count():
        return []
    inp = box.first
    month = inp.get_attribute("data-dp") == "month"
    inp.scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, -80)")
    field = inp.locator("xpath=..")
    field.locator("[data-dp-toggle]").click()
    pop = page.locator("#dp-popup")
    try:
        pop.wait_for(state="visible", timeout=3000)
    except Exception:
        return ["ปฏิทินไม่เปิด"]
    bad = []
    if inp.get_attribute("aria-expanded") != "true":
        bad.append("ปฏิทินเปิดแล้วแต่ aria-expanded ไม่เป็น true")
    value = field.locator("input[data-dp-value]").input_value()
    want_y = int(value[:4]) + 543 if value else None
    year = pop.locator(".dp-year option:checked").inner_text()
    if want_y and year != str(want_y):
        bad.append(f"หัวปฏิทินปี {year} ไม่ตรงค่า {value}")
    if not month:
        m = pop.locator(".dp-month option:checked").inner_text()
        if value and m != THAI_MONTHS_FULL[int(value[5:7]) - 1]:
            bad.append(f"หัวปฏิทินเดือน {m} ไม่ตรงค่า {value}")
        if pop.locator(".dp-wd").all_inner_texts() != ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"]:
            bad.append("วันในสัปดาห์ไม่ใช่ จ–อา")
    b, i = pop.bounding_box(), field.bounding_box()
    vw = page.evaluate("document.documentElement.clientWidth")
    if b["x"] < 0 or b["x"] + b["width"] > vw + 1:
        bad.append(f"ปฏิทินล้นจอ ({b['x']:.0f}+{b['width']:.0f} > {vw})")
    gap_below, gap_above = b["y"] - (i["y"] + i["height"]), i["y"] - (b["y"] + b["height"])
    if not (0 <= gap_below <= 4 or 0 <= gap_above <= 4):
        bad.append("ปฏิทินไม่ชิดช่อง")
    res = page.evaluate("""() => { const p = document.querySelector('#dp-popup'), out = [];
        const cells = [...p.querySelectorAll('.dp-cell')].map(c => c.getBoundingClientRect());
        const small = cells.filter(r => r.height < 44 || r.width < 40).length;
        p.querySelectorAll('.dp-head, .dp-foot, .dp-row').forEach(e => { if (e.scrollWidth > e.clientWidth + 1) out.push(e.className); });
        const btns = [...p.querySelectorAll('.dp-nav, .dp-foot .btn, .dp-title select')].filter(e => !e.hidden)
          .filter(e => e.getBoundingClientRect().height < 44).length;
        return {small, over: out, btns, sel: p.querySelectorAll('[aria-selected=true]').length}; }""")
    if res["small"]:
        bad.append(f"ช่องวันในปฏิทินเล็กกว่า 40x44 {res['small']} ช่อง")
    if res["over"]:
        bad.append(f"ปฏิทินล้นแนวนอน {res['over']}")
    if res["btns"]:
        bad.append(f"ปุ่มในปฏิทินเตี้ยกว่า 44px {res['btns']} ปุ่ม")
    if value and res["sel"] != 1:
        bad.append("ไม่มีวันที่ที่เลือกไว้ในปฏิทิน")
    if shots:
        name = url.strip("/").replace("/", "_").replace("?", "_").replace("&", "_").replace("=", "-") or "home"
        page.screenshot(path=str(shots / f"{name}_{width}_calendar.png"))
    page.keyboard.press("Escape")
    if pop.is_visible():
        bad.append("Esc ไม่ปิดปฏิทิน")
    page.locator("body").click(position={"x": 1, "y": 1})
    return bad


# จุดที่ตั้งใจให้ต่าง: (url ขึ้นต้นด้วย, ชนิดปัญหา, ข้อความในรายละเอียด, เหตุผล) · ใส่เหตุผลทุกครั้ง
LAYOUT_EXCEPTIONS = [
    ("/types", "คอลัมน์ชิดผิด", "รหัส:", "รหัสประเภท 4 หลักเป็นรหัส (ใช้ค้นและอ้างอิง) ไม่ใช่จำนวน ชิดซ้ายแบบเลขทะเบียน"),
    ("/types", "หัวคอลัมน์ชิดไม่ตรงข้อมูล", "รหัส:", "หัวคอลัมน์รหัสชิดซ้ายตามข้อมูลรหัส (ดูข้อข้างบน)"),
]

# สถานะ "กรอกแล้ว": หน้าที่ใส่ค่าในตัวกรอง/ช่องวันที่ไว้ครบ
FILLED = ["/supplies?q=ปากกา&category=1&low=1&as_of=2026-11-20", "/supplies/items?q=a&category=1&status=active",
          "/supplies/items/1?date_from=2026-11-01&date_to=2026-11-25", "/supplies/issues?month=2026-11&department=1",
          "/supplies/receipts?month=2026-11", "/supplies/monthly?month=2026-11", "/assets?q=ตู้&status=ใช้งาน&fy=2563",
          "/places?q=ห้อง&status=active", "/reports/summary?disposed=1"]
# สถานะ "มีข้อความ error": ส่งฟอร์มว่าง (ข้ามการตรวจของเบราว์เซอร์) แล้วตรวจหน้าที่ตอบกลับพร้อม error
ERROR_FORMS = [("/supplies/issue", "form[action='/supplies/issue']"), ("/supplies/receive", "form[action='/supplies/receive']"),
               ("/supplies/items/new", "form.edit-form"), ("/users", "form[action='/users']"),
               ("/inventory", "form[action='/inventory']"), ("/account/password", "form[action='/account/password']")]


def layout_problems(url, width, res):
    out = []
    for kind, text in res.get("layout", []):
        if any(url.startswith(u) and kind == k and t in text for u, k, t, _ in LAYOUT_EXCEPTIONS):
            continue
        out.append(f"{kind}: {text}")
    return out


COMBO_PAGES = ("/supplies/issue", "/supplies/receive")  # หน้าที่มีช่องเลือกวัสดุ: ถ่ายภาพตอนรายการเปิดอยู่ทุกความกว้าง


def combobox_open(page, width, url, shots):
    """ช่องเลือกวัสดุบรรทัดแรก: เปิดรายการ 4 แบบ ตรวจตำแหน่ง/ขนาด แล้วถ่ายภาพทุกแบบ
    all = คลิกช่อง · search = พิมพ์ค้นหา · toggle = กดปุ่ม ▼ (ดูทั้งหมด) · category = กรองหมวดที่ 2 จากปุ่มหมวด"""
    inp = page.locator("#item_1")
    if not inp.count():
        return ["ไม่พบช่องเลือกวัสดุ #item_1"]
    field = inp.locator("xpath=ancestor::span[contains(@class,'cb-field')]")
    toggle = field.locator("[data-combobox-toggle]")
    if not toggle.count():
        return ["ไม่พบปุ่ม ▼ ของช่องเลือกวัสดุ"]
    bad = []
    tb = toggle.bounding_box()
    if tb["width"] < 44 or tb["height"] < 44:
        bad.append(f"ปุ่ม ▼ เล็กกว่า 44px ({tb['width']:.0f}x{tb['height']:.0f})")
    inp.scroll_into_view_if_needed()
    page.evaluate("window.scrollBy(0, -80)")
    box = page.locator("#cb-popup")
    name = url.strip("/").replace("/", "_")
    for step in ("all", "search", "toggle", "category"):
        if step == "all":
            inp.click()
        elif step == "search":
            first = page.locator("#cb-listbox li.cb-opt .cb-name").first.text_content() or ""
            page.keyboard.type(first[:4])
        elif step == "toggle":
            page.keyboard.press("Escape")
            toggle.click()
        elif step == "category":
            chips = page.locator("#cb-popup .cb-chip")
            if chips.count() < 2:
                bad.append("ไม่มีปุ่มกรองหมวด")
                continue
            chips.nth(1).click()
        try:
            box.wait_for(state="visible", timeout=3000)
        except Exception:
            bad.append(f"รายการของช่องเลือกวัสดุไม่เปิด ({step})")
            continue
        b, i = box.bounding_box(), field.bounding_box()
        vw = page.evaluate("document.documentElement.clientWidth")
        if abs(b["width"] - i["width"]) > 1 or abs(b["x"] - i["x"]) > 1:
            bad.append(f"รายการกว้าง/ตำแหน่งไม่ตรงช่อง ({step}) {b['width']:.0f} vs {i['width']:.0f}")
        if b["x"] < 0 or b["x"] + b["width"] > vw + 1:
            bad.append(f"รายการล้นจอ ({step})")
        gap_below, gap_above = b["y"] - (i["y"] + i["height"]), i["y"] - (b["y"] + b["height"])
        if not (0 <= gap_below <= 4 or 0 <= gap_above <= 4):
            bad.append(f"รายการไม่ชิดช่อง ({step})")
        res = page.evaluate("""() => { const p = document.querySelector('#cb-popup'); const bad = [];
            p.querySelectorAll('li.cb-opt, li.cb-group').forEach(li => { if (li.scrollWidth > li.clientWidth + 1) bad.push(li.innerText.slice(0, 30)); });
            const cats = p.querySelector('.cb-cats');
            const pressed = [...p.querySelectorAll('.cb-chip[aria-pressed=true]')].map(c => c.textContent);
            const groups = [...new Set([...p.querySelectorAll('li.cb-group')].map(g => g.textContent))];
            return {bad, n: p.querySelectorAll('li.cb-opt').length, pressed, groups,
                    catsOver: cats && !cats.hidden && getComputedStyle(cats).flexWrap !== 'nowrap' && cats.scrollWidth > cats.clientWidth + 1}; }""")
        if res["bad"]:
            bad.append(f"รายการล้นแนวนอน {len(res['bad'])}: {res['bad'][:3]}")
        if res["catsOver"]:
            bad.append(f"ปุ่มหมวดล้นกล่อง ({step})")
        if not res["n"]:
            bad.append(f"รายการว่าง ({step})")
        if step == "category" and (len(res["pressed"]) != 1 or res["groups"] != res["pressed"]):
            bad.append(f"กรองหมวดไม่ถูก: กด {res['pressed']} แสดง {res['groups']}")
        if step == "toggle" and page.get_attribute("#item_1", "aria-expanded") != "true":
            bad.append("ปุ่ม ▼ เปิดแล้วแต่ aria-expanded ไม่เป็น true")
        if shots:
            page.screenshot(path=str(shots / f"{name}_{width}_{step}.png"))
    page.keyboard.press("Escape")
    inp.fill("")
    return bad


def audit(base, urls, widths, shots=None):
    from playwright.sync_api import sync_playwright
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        for width in widths:
            ctx = browser.new_context(viewport={"width": width, "height": 1000})
            page = ctx.new_page()
            # หน้าล็อกอินตอนยังไม่ล็อกอิน · demo เปิดทะเบียนครุภัณฑ์ได้โดยไม่ล็อกอิน: เมนูที่เห็นต้องเปิดได้จริงทุกเมนู
            r = page.goto(base + "/login", wait_until="load")
            res = page.evaluate(CHECK_JS)
            bad = [f"HTTP {r.status}"] if r.status >= 400 else []
            if res["overflowPage"] > 1:
                bad.append(f"หน้าเลื่อนแนวนอน {res['overflowPage']}px")
            if res["wraps"]:
                bad.append(f"ตกบรรทัด {res['nWraps']}: {res['wraps']}")
            links = sorted(set(page.locator("nav.nav a[href]").evaluate_all("a => a.map(x => x.getAttribute('href'))")))
            for href in links:
                if page.request.get(base + href, max_redirects=0).status != 200:
                    bad.append(f"เมนู {href} ต้องล็อกอิน แต่แสดงให้คนที่ยังไม่ล็อกอิน")
            print(f"{width:5} {'/login (ยังไม่ล็อกอิน)':38} " + ("ok" if not bad else " | ".join(bad)), flush=True)
            problems += [(width, "/login (ยังไม่ล็อกอิน)", b) for b in bad]
            page.goto(base + "/login")
            page.fill("#username", USER)
            page.fill("#password", PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_load_state("networkidle")
            for url in urls:
                if url.startswith("(error)"):
                    path = url[len("(error)"):]
                    page.goto(base + path, wait_until="load")
                    selector = dict(ERROR_FORMS)[path]
                    form = page.locator(selector).first
                    if not form.count():
                        print(f"{width:5} {url:38} ข้าม (ไม่พบฟอร์ม)", flush=True)
                        continue
                    with page.expect_navigation():
                        form.evaluate("""f => { f.noValidate = true;
                            f.querySelectorAll('input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea')
                             .forEach(i => { if (!i.readOnly) i.value = ''; });
                            f.requestSubmit(); }""")
                    page.wait_for_load_state("load")
                    has_error = page.locator(".form-error, .field-error, [aria-invalid=true]").count() > 0
                    r = type("R", (), {"status": 200 if has_error else 599})
                elif url == IMPORT_PREVIEW:
                    page.goto(base + "/supplies/import", wait_until="load")
                    page.set_input_files("#file", SAMPLE["problems"])
                    with page.expect_navigation():
                        page.click("form[action='/supplies/import/preview'] button[type=submit]")
                    r = type("R", (), {"status": 200 if "ตรวจก่อนนำเข้า" in page.content() else 500})
                else:
                    r = page.goto(base + url, wait_until="load")
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                res = page.evaluate(CHECK_JS)
                bad = []
                if r.status >= 400:
                    bad.append(f"HTTP {r.status}")
                if res["overflowPage"] > 1:
                    bad.append(f"หน้าเลื่อนแนวนอน {res['overflowPage']}px")
                for t in res["tables"]:
                    bad.append(f"ตารางเลื่อน {t['over']}px ปุ่มนอกจอ {t['n']} {t['hidden']}")
                if res["wraps"]:
                    bad.append(f"ตกบรรทัด {res['nWraps']}: {res['wraps']}")
                if res["cut"]:
                    bad.append(f"ป้ายถูกตัด {res['nCut']}: {res['cut']}")
                bad += [f"ช่องวันที่ {d}" for d in res["dates"]]
                bad += layout_problems(url, width, res)
                bad += [f"ปฏิทิน: {b}" for b in calendar_open(page, width, url, shots)]
                bad += live_thai_date(page)
                if url in COMBO_PAGES:
                    bad += [f"ช่องเลือกวัสดุ: {b}" for b in combobox_open(page, width, url, shots)]
                for kind, sizes in res["sizes"].items():
                    if len(sizes) > 1:
                        bad.append(f"{kind} ขนาดไม่เท่ากัน {sizes}")
                print(f"{width:5} {url:38} " + ("ok" if not bad else " | ".join(bad)), flush=True)
                problems += [(width, url, b) for b in bad]
            ctx.close()
        browser.close()
    return problems


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help="สำเนา db ที่จะใช้ตรวจ (ไม่ใส่ = ข้อมูลสมมติของ test)")
    ap.add_argument("--widths", default="390,844,1024,1280,1366,1920")
    ap.add_argument("--shots", help="โฟลเดอร์เก็บภาพตอนรายการเลือกวัสดุเปิดอยู่ (ค่าเริ่มต้น exports/ui_combobox/seed หรือ real)")
    args = ap.parse_args()
    shots = Path(args.shots) if args.shots else ROOT / "exports" / "ui_combobox" / ("real" if args.db else "seed")
    shots.mkdir(parents=True, exist_ok=True)
    widths = [int(w) for w in args.widths.split(",")]
    with tempfile.TemporaryDirectory(prefix="krupan-ui-", ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        db = prepare_db(args.db, tmp)
        port = free_port()
        env = dict(os.environ, KRUPAN_DB=str(db), KRUPAN_BACKUP_DIR=str(tmp / "backups"),
                   KRUPAN_USB_WATCH="off", KRUPAN_SECRET_KEY="ui-audit-" + "x" * 40,
                   QR_ENABLED="on")  # ตรวจหน้าส่วน QR ด้วย (ซ่อนไว้โดยค่าเริ่มต้น แต่ยังต้องไม่พังเมื่อเปิด)
        log = open(tmp / "server.log", "w", encoding="utf-8")
        server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                                   "--port", str(port)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(base + "/login", timeout=1)
                    break
                except Exception:
                    time.sleep(0.5)
            else:
                sys.exit("เปิด server ทดสอบไม่ขึ้น ดู log: " + (tmp / "server.log").read_text(encoding="utf-8")[-2000:])
            problems = audit(base, pages(db) + FILLED + ["(error)" + u for u, _ in ERROR_FORMS], widths, shots)
        finally:
            # Windows: python ของ venv เป็นตัวเรียกต่อ ต้องปิดทั้งต้นไม้ process ไม่งั้นไฟล์ log ยังถูกจับไว้
            subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
            server.wait(10)
            log.close()
    print("ภาพตอนรายการเลือกวัสดุ/ปฏิทินเปิดอยู่ (*_calendar.png):", shots)
    print("รวมปัญหา", len(problems))
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
