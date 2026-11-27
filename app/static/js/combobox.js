/* ช่องค้นหาพร้อมรายการที่ระบบวาดเอง (แทน <datalist> ของเบราว์เซอร์ที่อ่านยากบน Edge) · ส่วนกลาง ใช้ซ้ำได้ทุกหน้า
 *
 * ใช้: macro combobox() ใน templates/_combobox.html (ช่อง + ปุ่ม ▼ "ดูทั้งหมด")
 *      <script type="application/json" id="ID">[{"value", "name", "sub", "right", "tag", "tagKind", "dim", "group", "keys"}, ...]</script>
 *   value = ข้อความที่ใส่ในช่องเมื่อเลือก (ค่าที่ส่งไปเซิร์ฟเวอร์) · name = ชื่อตัวหนา · sub = บรรทัดเล็กสีเทา
 *   right = ข้อความชิดขวา · tag/tagKind = ป้าย ("danger" แดง / "warn" ส้ม) · dim = ตัวหนังสือจาง (ยังเลือกได้)
 *   group = หัวกลุ่ม/ปุ่มกรอง (เรียงตามลำดับที่พบในข้อมูล) · keys = ข้อความเพิ่มที่ใช้ค้นหา (เช่น ชื่อเดิม)
 *   data-combobox-unique = ห้ามเลือกค่าเดียวกันซ้ำในช่องอื่นของฟอร์มเดียวกันที่ใช้ข้อมูลชุดเดียวกัน
 *   data-combobox-error / -empty / -all = ข้อความ: ไม่ได้เลือก / ไม่พบ / ปุ่มดูทั้งหมด
 *
 * พฤติกรรม
 * - คลิก/พิมพ์แล้วรายการเปิด · ปุ่ม ▼ เปิดรายการทั้งหมด (ไม่สนข้อความในช่อง) กดอีกครั้งปิด · Alt+↓ = ปุ่ม ▼
 * - มีหัวกลุ่มคั่น (เลือกไม่ได้ ลูกศรข้าม) และปุ่มกรองกลุ่มด้านบน ใช้ร่วมกับคำค้นได้
 * - ค้นหาทนพิมพ์ผิด: ไม่สนช่องว่าง ตัวพิมพ์เล็กใหญ่ วรรณยุกต์/การันต์/ไม้ไต่คู้ เลขไทย · หลายคำได้ทุกลำดับ
 *   ค้นใน name + sub + group + keys · ไม่เจอเลยจึงลองแบบเสียงใกล้กัน (ต/ท/ด, ก/ค/ข, ...) · ยังไม่เจอ: เสนอ 3 รายการที่ใกล้ที่สุด
 * - ↑↓ เลื่อน · Enter เลือก (แล้วปล่อยให้ฟอร์มจัดการ Enter ต่อ เช่น ไปช่องถัดไป) · Esc ปิด
 * - ต้องเลือกจากรายการเท่านั้น: พิมพ์ค้างไว้แล้ว Enter ไม่เลือกให้ (ยกเว้นตรงรายการเดียว หรือชื่อตรงพอดี)
 *   ออกจากช่องโดยไม่ได้เลือก = ขอบแดง + ข้อความ และส่งฟอร์มไม่ได้
 * - เลือกแล้วส่ง event input + change (bubbles) ให้สคริปต์ของหน้าอัปเดตต่อ
 * ใช้ event delegation ที่ document: ช่องที่เพิ่มทีหลัง (เช่น คัดลอกบรรทัด) ใช้ได้ทันที
 * รายการวาดชุดเดียวต่อหน้า (#cb-popup ต่อท้าย body: ปุ่มกรอง + #cb-listbox) จึงไม่ถูกตัดโดยตารางที่เลื่อนได้ */
(function () {
  "use strict";
  var MAX_VISIBLE = 8;
  var cache = {};
  var popup = null, cats = null, list = null;
  var current = null;      // ช่องที่รายการเปิดอยู่
  var shown = [];          // แถวที่เลือก/เลื่อนได้ [{opt, li, disabled, action}]
  var active = -1;
  var mode = { all: false, group: null };   // all = เปิดจากปุ่ม ▼ (ไม่กรองด้วยข้อความในช่อง)

  // ---------------------------------------------------------------- ทำข้อความให้เทียบกันได้
  var THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙";
  var MARKS = /[็-๎]/;          // ไม้ไต่คู้ วรรณยุกต์ ทัณฑฆาต นิคหิต ยามักการ
  var SOUND = {};                          // พยัญชนะเสียงใกล้กัน (ใช้รอบสองเมื่อรอบแรกไม่เจอ)
  ["กขคฆ", "ดตทธฎฏฐฑฒ", "จชซศษส", "บปพภฟ", "นณญรลฬ", "ไใ"].forEach(function (g) {
    for (var i = 0; i < g.length; i++) SOUND[g[i]] = g[0];
  });
  function norm(s) { return String(s || "").replace(/\s+/g, " ").trim().toLowerCase(); }
  /* คืน {s, map}: s = ข้อความที่ตัดช่องว่าง/เครื่องหมาย เป็นตัวเล็ก · map[i] = ตำแหน่งในข้อความเดิม (ใช้เน้นคำ) */
  function fold(text, sound) {
    var s = "", map = [];
    text = String(text || "");
    for (var i = 0; i < text.length; i++) {
      var c = text[i];
      if (/\s/.test(c) || MARKS.test(c)) continue;
      var d = THAI_DIGITS.indexOf(c);
      if (d >= 0) c = String(d);
      c = c.toLowerCase();
      if (sound && SOUND[c]) c = SOUND[c];
      s += c;
      map.push(i);
    }
    return { s: s, map: map };
  }
  function words(q) { return norm(q).split(" ").filter(Boolean); }
  function haystack(o, wide) { return (wide ? [o.name, o.keys, o.sub, o.group] : [o.name, o.keys]).filter(Boolean).join(" "); }
  /* ตรงกันไหม: ทุกคำต้องอยู่ในข้อความ (ลำดับไหนก็ได้) หรือทั้งคำค้นต่อกันอยู่ในข้อความ
     wide = รวมหมวดด้วย (ใช้เมื่อไม่มีรายการไหนตรงที่ชื่อ/หมายเหตุ เช่น พิมพ์ชื่อหมวด) */
  function matches(o, ws, sound, wide) {
    var hay = fold(haystack(o, wide), sound).s;
    var all = ws.every(function (w) { var f = fold(w, sound).s; return !f || hay.indexOf(f) !== -1; });
    return all || hay.indexOf(fold(ws.join(""), sound).s) !== -1;
  }
  function bigrams(s) { var out = {}; for (var i = 0; i < s.length - 1; i++) out[s.substr(i, 2)] = (out[s.substr(i, 2)] || 0) + 1; return out; }
  function similarity(a, b) {
    if (!a || !b) return 0;
    if (a.length < 2 || b.length < 2) return b.indexOf(a) !== -1 ? 0.5 : 0;
    var x = bigrams(a), y = bigrams(b), both = 0, n = 0, m = 0, k;
    for (k in x) { n += x[k]; if (y[k]) both += Math.min(x[k], y[k]); }
    for (k in y) m += y[k];
    return (2 * both) / (n + m);
  }
  function suggestions(input, q, limit) {
    var f = fold(q, true).s;
    return options(input).map(function (o) {
      var best = Math.max(similarity(f, fold(o.name, true).s), similarity(f, fold(o.keys, true).s) * 0.9);
      return { o: o, score: best };
    }).filter(function (x) { return x.score > 0.05; })
      .sort(function (a, b) { return b.score - a.score; }).slice(0, limit).map(function (x) { return x.o; });
  }

  // ---------------------------------------------------------------- ข้อมูล
  function options(input) {
    var id = input.getAttribute("data-combobox");
    if (!cache[id]) {
      var el = document.getElementById(id);
      try { cache[id] = el ? JSON.parse(el.textContent || "[]") : []; } catch (e) { cache[id] = []; }
    }
    return cache[id];
  }
  function groups(input) {
    var seen = [];
    options(input).forEach(function (o) { if (o.group && seen.indexOf(o.group) === -1) seen.push(o.group); });
    return seen;
  }
  function byValue(input, value) {
    var v = String(value || "").trim();
    var all = options(input);
    for (var i = 0; i < all.length; i++) if (all[i].value === v) return all[i];
    return null;
  }
  function exactName(input) {
    // ชื่อตรงพอดี (ไม่สนช่องว่าง ตัวพิมพ์ วรรณยุกต์) กับรายการเดียว: เลือกให้เอง
    var q = fold(input.value).s;
    if (!q) return null;
    var hits = options(input).filter(function (o) { return fold(o.name).s === q || fold(o.value).s === q; });
    return hits.length === 1 ? hits[0] : null;
  }
  function takenBy(input, value) {
    // ช่องอื่นในฟอร์มเดียวกัน (ข้อมูลชุดเดียวกัน) ที่เลือกค่านี้ไว้แล้ว
    if (!input.hasAttribute("data-combobox-unique") || !input.form) return null;
    var src = input.getAttribute("data-combobox");
    var others = input.form.querySelectorAll('input[data-combobox="' + src + '"]');
    for (var i = 0; i < others.length; i++) {
      if (others[i] !== input && others[i].value.trim() === value) return others[i];
    }
    return null;
  }
  function lineLabel(el) {
    var tr = el.closest("[data-line]");
    var no = tr && tr.querySelector(".line-no");
    return no ? "บรรทัด " + no.textContent.trim() : "ช่องอื่น";
  }
  function toggleOf(input) { var f = input.closest(".cb-field"); return f && f.querySelector("[data-combobox-toggle]"); }

  // ---------------------------------------------------------------- ข้อความผิดพลาดใต้ช่อง
  function setError(input, text) {
    var id = input.id + "-cb-error";
    var el = document.getElementById(id);
    var desc = (input.getAttribute("aria-describedby") || "").split(/\s+/).filter(function (x) { return x && x !== id; });
    if (!text) {
      if (el) el.remove();
      input.removeAttribute("aria-invalid");
      if (desc.length) input.setAttribute("aria-describedby", desc.join(" ")); else input.removeAttribute("aria-describedby");
      return;
    }
    if (!el) {
      el = document.createElement("span");
      el.id = id;
      el.className = "cb-error field-error";
      el.setAttribute("data-cb-error", "");
      (input.closest(".cb-field") || input).insertAdjacentElement("afterend", el);
    }
    el.textContent = text;
    input.setAttribute("aria-invalid", "true");
    desc.push(id);
    input.setAttribute("aria-describedby", desc.join(" "));
  }
  function validate(input) {
    var v = input.value.trim();
    if (!v) { setError(input, null); return true; }
    var opt = byValue(input, v) || exactName(input);
    if (!opt) { setError(input, input.getAttribute("data-combobox-error") || "เลือกจากรายการ"); return false; }
    if (opt.value !== v) choose(input, opt, true);
    var other = takenBy(input, opt.value);
    if (other) { setError(input, "เลือกซ้ำ: วัสดุนี้อยู่ใน" + lineLabel(other) + " แล้ว"); return false; }
    setError(input, null);
    return true;
  }

  // ---------------------------------------------------------------- รายการ
  function ensureList() {
    if (popup) return popup;
    popup = document.createElement("div");
    popup.id = "cb-popup";
    popup.className = "cb-popup";
    popup.hidden = true;
    cats = document.createElement("div");
    cats.className = "cb-cats";
    cats.setAttribute("role", "group");
    cats.setAttribute("aria-label", "กรองตามหมวด");
    list = document.createElement("ul");
    list.id = "cb-listbox";
    list.className = "cb-list";
    list.setAttribute("role", "listbox");
    popup.appendChild(cats);
    popup.appendChild(list);
    // กดในรายการ/ปุ่มกรองไม่ทำให้ช่องหลุด focus (ไม่งั้น blur ปิดรายการก่อนเลือก)
    popup.addEventListener("mousedown", function (e) { e.preventDefault(); });
    cats.addEventListener("click", function (e) {
      var b = e.target.closest("button[data-group]");
      if (!b || !current) return;
      mode.group = b.getAttribute("data-group") || null;
      render(current);
      place();
    });
    list.addEventListener("click", function (e) {
      var li = e.target.closest("li[data-index]");
      if (!li || !current) return;
      pick(shown[parseInt(li.getAttribute("data-index"), 10)]);
    });
    list.addEventListener("mousemove", function (e) {
      var li = e.target.closest("li[data-index]");
      if (li) setActive(parseInt(li.getAttribute("data-index"), 10), false);
    });
    document.body.appendChild(popup);
    return popup;
  }
  function pick(item) {
    if (!item || item.disabled || !current) return false;
    if (item.action === "all") { mode = { all: true, group: null }; render(current); place(); return true; }
    choose(current, item.opt);
    close();
    return true;
  }
  function highlight(parent, text, ws, sound) {
    // เน้นส่วนที่ตรงกับแต่ละคำค้น (เทียบแบบเดียวกับการค้นหา แล้วแปลงตำแหน่งกลับเป็นข้อความเดิม)
    text = String(text || "");
    var f = fold(text, sound), marks = [];
    ws.forEach(function (w) {
      var q = fold(w, sound).s;
      var i = q ? f.s.indexOf(q) : -1;
      if (i < 0) return;
      var start = f.map[i], end = f.map[i + q.length - 1] + 1;
      while (end < text.length && MARKS.test(text[end])) end++;   // รวมวรรณยุกต์ที่ตามหลัง
      marks.push([start, end]);
    });
    marks.sort(function (a, b) { return a[0] - b[0]; });
    var pos = 0;
    marks.forEach(function (m) {
      if (m[0] < pos) m[0] = pos;
      if (m[1] <= m[0]) return;
      parent.appendChild(document.createTextNode(text.slice(pos, m[0])));
      var mark = document.createElement("mark");
      mark.textContent = text.slice(m[0], m[1]);
      parent.appendChild(mark);
      pos = m[1];
    });
    parent.appendChild(document.createTextNode(text.slice(pos)));
  }
  function optionLi(input, o, sel, ws, sound, extraClass) {
    var other = takenBy(input, o.value);
    var idx = shown.length;
    var li = document.createElement("li");
    li.id = "cb-opt-" + idx;
    li.setAttribute("role", "option");
    li.setAttribute("data-index", idx);
    li.className = "cb-opt" + (o.dim ? " cb-dim" : "") + (other ? " cb-taken" : "") + (extraClass ? " " + extraClass : "");
    if (other) li.setAttribute("aria-disabled", "true");
    li.setAttribute("aria-selected", sel && sel.value === o.value ? "true" : "false");
    var main = document.createElement("span"); main.className = "cb-main";
    var name = document.createElement("span"); name.className = "cb-name"; highlight(name, o.name, ws, sound);
    main.appendChild(name);
    if (o.sub) { var sub = document.createElement("span"); sub.className = "cb-sub"; sub.textContent = o.sub; main.appendChild(sub); }
    li.appendChild(main);
    var side = document.createElement("span"); side.className = "cb-side";
    if (other) {
      var t0 = document.createElement("span"); t0.className = "badge cb-tag"; t0.textContent = "อยู่" + lineLabel(other) + " แล้ว";
      side.appendChild(t0);
    } else if (o.tag) {
      var t = document.createElement("span"); t.className = "badge cb-tag badge-" + (o.tagKind || "warn"); t.textContent = o.tag;
      side.appendChild(t);
    }
    if (o.right) { var r = document.createElement("span"); r.className = "cb-right"; r.textContent = o.right; side.appendChild(r); }
    li.appendChild(side);
    shown.push({ opt: o, li: li, disabled: !!other });
    return li;
  }
  function note(cls, text) {
    var li = document.createElement("li");
    li.className = cls;
    li.setAttribute("role", "presentation");
    li.textContent = text;
    return li;
  }
  function renderCats(input) {
    cats.innerHTML = "";
    var gs = groups(input);
    cats.hidden = gs.length < 2;
    [""].concat(gs).forEach(function (g) {
      var b = document.createElement("button");
      b.type = "button";
      b.tabIndex = -1;
      b.className = "cb-chip";
      b.setAttribute("data-group", g);
      b.setAttribute("aria-pressed", (mode.group || "") === g ? "true" : "false");
      b.textContent = g || "ทั้งหมด";
      cats.appendChild(b);
    });
  }
  function render(input) {
    ensureList();
    renderCats(input);
    list.innerHTML = "";
    shown = [];
    var sel = byValue(input, input.value);
    var ws = mode.all || sel ? [] : words(input.value);   // เลือกไว้แล้ว/เปิดจากปุ่ม: แสดงทั้งหมด (เปลี่ยนวัสดุได้)
    var pool = options(input).filter(function (o) { return !mode.group || o.group === mode.group; });
    var sound = false, hits = pool;
    if (ws.length) {
      // ลองทีละขั้น ใช้ขั้นแรกที่เจอ: ชื่อ/หมายเหตุ → ชื่อ/หมายเหตุแบบเสียงใกล้กัน → รวมหมวด → รวมหมวดแบบเสียงใกล้กัน
      var steps = [[false, false], [true, false], [false, true], [true, true]];
      for (var k = 0; k < steps.length; k++) {
        var st = steps[k];
        hits = pool.filter(function (o) { return matches(o, ws, st[0], st[1]); });
        if (hits.length) { sound = st[0]; break; }
      }
    }
    var lastGroup = null;
    hits.forEach(function (o) {
      if (o.group && o.group !== lastGroup) { list.appendChild(note("cb-group", o.group)); lastGroup = o.group; }
      list.appendChild(optionLi(input, o, sel, ws, sound));
    });
    if (!hits.length) {
      var q = norm(input.value);
      list.appendChild(note("cb-none", input.getAttribute("data-combobox-empty") || "ไม่พบรายการ"));
      var near = q ? suggestions(input, q, 3) : [];
      if (near.length) {
        list.appendChild(note("cb-did-you-mean", "หมายถึง…ไหม"));
        near.forEach(function (o) { list.appendChild(optionLi(input, o, sel, [], false, "cb-suggest")); });
      }
      var all = document.createElement("li");
      all.id = "cb-opt-" + shown.length;
      all.className = "cb-show-all";
      all.setAttribute("role", "option");
      all.setAttribute("data-index", shown.length);
      all.setAttribute("aria-selected", "false");
      all.textContent = (input.getAttribute("data-combobox-all") || "ดูทั้งหมด") + " ▼";
      list.appendChild(all);
      shown.push({ li: all, action: "all" });
    }
    active = -1;
    if (sel) {
      shown.forEach(function (s, i) { if (s.opt && s.opt.value === sel.value) active = i; });
    } else if (ws.length) {
      var usable = shown.filter(function (s) { return !s.disabled && s.opt && !/cb-suggest/.test(s.li.className); });
      if (usable.length === 1) active = shown.indexOf(usable[0]);  // ตรงรายการเดียว: Enter เลือกได้เลย
    }
    setActive(active, true);
    var t = toggleOf(input);
    if (t) t.setAttribute("aria-pressed", mode.all ? "true" : "false");
  }
  function setActive(i, scroll) {
    shown.forEach(function (s, j) { s.li.classList.toggle("cb-active", j === i); });
    active = i;
    if (current) {
      if (i >= 0 && shown[i]) current.setAttribute("aria-activedescendant", shown[i].li.id);
      else current.removeAttribute("aria-activedescendant");
    }
    if (scroll && i >= 0 && shown[i]) {
      var li = shown[i].li;
      var head = li.previousElementSibling && li.previousElementSibling.classList.contains("cb-group") ? li.previousElementSibling : li;
      if (head.offsetTop < list.scrollTop) list.scrollTop = head.offsetTop;
      else if (li.offsetTop + li.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = li.offsetTop + li.offsetHeight - list.clientHeight;
    }
  }
  function move(step) {
    if (!shown.length) return;
    var i = active;
    for (var n = 0; n < shown.length; n++) {
      i = i + step;
      if (i < 0) i = shown.length - 1;
      if (i >= shown.length) i = 0;
      if (!shown[i].disabled) { setActive(i, true); return; }
    }
  }
  function wantHeight() {
    // ความสูงที่ต้องการ: ปุ่มกรอง + รายการ ไม่เกิน MAX_VISIBLE รายการ (เกินนั้นเลื่อนดูในรายการ)
    var first = list.querySelector("li.cb-opt") || list.querySelector("li");
    var itemH = first ? first.offsetHeight : 48;
    var catsH = cats.hidden ? 0 : cats.offsetHeight;
    return { h: catsH + Math.min(list.scrollHeight, itemH * MAX_VISIBLE + 10), itemH: itemH, catsH: catsH };
  }
  function anchor(input) { return input.closest(".cb-field") || input; }
  function place() {
    if (!current || !popup || popup.hidden) return;
    var r = anchor(current).getBoundingClientRect();
    var de = document.documentElement;
    popup.style.width = r.width + "px";
    popup.style.left = (r.left + window.scrollX) + "px";
    list.style.maxHeight = "";
    var w = wantHeight();
    var below = de.clientHeight - r.bottom - 8, above = r.top - 8;
    var up = below < w.h && above > below;
    var room = Math.max(w.catsH + w.itemH * 2, up ? above : below);
    var h = Math.min(w.h, room);
    list.style.maxHeight = Math.max(w.itemH, h - w.catsH - 2) + "px";
    h = popup.offsetHeight;
    popup.classList.toggle("cb-up", up);
    popup.style.top = (up ? r.top + window.scrollY - h - 2 : r.bottom + window.scrollY + 2) + "px";
  }
  function open(input, all) {
    if (input !== current) mode = { all: false, group: null };
    if (all !== undefined) mode.all = all;
    current = input;
    ensureList();
    render(input);
    popup.hidden = false;
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", list.id);
    var t = toggleOf(input);
    if (t) t.setAttribute("aria-expanded", "true");
    // มือถือ: ถ้าใต้ช่องที่ไม่พอ เลื่อนหน้าให้ช่องขึ้นไปใกล้ขอบบน รายการจะได้เปิดลงล่างและแตะเลือกง่าย
    var de = document.documentElement, r = anchor(input).getBoundingClientRect();
    list.style.maxHeight = "";
    if (de.clientWidth <= 640 && de.clientHeight - r.bottom - 8 < wantHeight().h && r.top > 96) {
      window.scrollBy(0, r.top - 80);
    }
    place();
  }
  function close() {
    if (current) {
      current.setAttribute("aria-expanded", "false");
      current.removeAttribute("aria-activedescendant");
      var t = toggleOf(current);
      if (t) { t.setAttribute("aria-expanded", "false"); t.setAttribute("aria-pressed", "false"); }
    }
    if (popup) popup.hidden = true;
    current = null;
    shown = [];
    active = -1;
    mode = { all: false, group: null };
  }
  function isOpenFor(input) { return input === current && popup && !popup.hidden; }
  function choose(input, opt, quiet) {
    input.value = opt.value;
    if (!quiet) setError(input, null);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    if (!quiet) {
      var other = takenBy(input, opt.value);
      if (other) setError(input, "เลือกซ้ำ: วัสดุนี้อยู่ใน" + lineLabel(other) + " แล้ว");
    }
  }
  function openAll(input) {
    // ปุ่ม ▼: เปิดรายการทั้งหมด (ไม่สนข้อความในช่อง) · เปิดอยู่แล้วกดอีกครั้ง = ปิด
    if (isOpenFor(input) && mode.all) { close(); return; }
    if (document.activeElement !== input) { skipFocusOpen = true; input.focus(); skipFocusOpen = false; }
    open(input, true);
  }

  function isCombo(el) { return el && el.matches && el.matches("input[data-combobox]"); }
  var skipFocusOpen = false;

  // ---------------------------------------------------------------- event (delegation)
  document.addEventListener("mousedown", function (e) {
    // ปุ่ม ▼ ไม่แย่ง focus จากช่อง (กดแล้วช่องยังพิมพ์ต่อได้)
    if (e.target.closest && e.target.closest("[data-combobox-toggle]")) e.preventDefault();
  });
  document.addEventListener("click", function (e) {
    var t = e.target.closest && e.target.closest("[data-combobox-toggle]");
    if (t) {
      var input = t.closest(".cb-field").querySelector("input[data-combobox]");
      if (input) openAll(input);
      return;
    }
    if (isCombo(e.target) && !isOpenFor(e.target)) open(e.target);
  });
  document.addEventListener("focusin", function (e) {
    if (isCombo(e.target) && e.target !== current && !skipFocusOpen) open(e.target);
  });
  document.addEventListener("input", function (e) {
    if (!isCombo(e.target) || !e.isTrusted) return;
    setError(e.target, null);
    if (!isOpenFor(e.target)) open(e.target, false);
    else { mode.all = false; render(e.target); place(); }
  });
  document.addEventListener("focusout", function (e) {
    if (!isCombo(e.target)) return;
    if (e.target === current) close();
    validate(e.target);
  });
  // capture: ทำงานก่อนตัวจัดการ Enter ของฟอร์ม (เลือกก่อน แล้วฟอร์มค่อยไปช่องถัดไป)
  document.addEventListener("keydown", function (e) {
    var input = e.target;
    if (!isCombo(input) || e.ctrlKey || e.metaKey) return;
    var isOpen = isOpenFor(input);
    if (e.altKey) {
      if (e.key === "ArrowDown") { e.preventDefault(); openAll(input); }
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!isOpen) { open(input); if (active < 0) move(e.key === "ArrowDown" ? 1 : -1); return; }
      move(e.key === "ArrowDown" ? 1 : -1);
    } else if (e.key === "Enter") {
      if (isOpen && active >= 0 && shown[active] && !shown[active].disabled) {
        if (shown[active].action === "all") { e.preventDefault(); e.stopPropagation(); pick(shown[active]); return; }
        pick(shown[active]);
        return;
      }
      var exact = exactName(input);
      if (exact && !takenBy(input, exact.value)) { choose(input, exact); close(); return; }
      if (input.value.trim() && !byValue(input, input.value)) {
        e.preventDefault(); e.stopPropagation();  // ยังไม่ได้เลือก: อยู่ช่องเดิม ไม่ส่งฟอร์ม
        if (!isOpen) open(input);
      } else if (isOpen) close();
    } else if (e.key === "Escape") {
      if (isOpen) { e.preventDefault(); e.stopPropagation(); close(); }
    } else if (e.key === "Tab") {
      var a = isOpen && active >= 0 ? shown[active] : null;
      if (a && a.opt && !a.disabled && norm(input.value) && !byValue(input, input.value)) {
        choose(input, a.opt);  // Tab ตอนมีรายการเดียวที่ตรง (หรือเลื่อนไปเลือกไว้): เลือกให้
      }
      close();
    }
  }, true);
  // ส่งฟอร์มไม่ได้ถ้ายังมีช่องที่ไม่ได้เลือกจากรายการ (หรือเลือกซ้ำ)
  document.addEventListener("submit", function (e) {
    var bad = null;
    e.target.querySelectorAll("input[data-combobox]").forEach(function (i) { if (!validate(i) && !bad) bad = i; });
    if (bad) { e.preventDefault(); e.stopImmediatePropagation(); close(); bad.focus(); }
  }, true);
  window.addEventListener("resize", place);
  window.addEventListener("scroll", function (e) { if (e.target !== list && e.target !== cats) place(); }, true);

  window.KrupanCombobox = { validate: validate, close: close, open: open, openAll: openAll, normalize: norm, fold: fold };
})();
