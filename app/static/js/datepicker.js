/* ช่องวันที่และช่องเดือนของระบบ (ส่วนกลาง ใช้กับทุก input type=date / type=month ทุกหน้า รวมเนื้อหาที่ htmx ใส่เข้ามา)
   ปฏิทินของเบราว์เซอร์แสดง ค.ศ. และถ้าพิมพ์ปี พ.ศ. ลงไป (27/11/2569) จะได้ ค.ศ. 2569 ผิด จึงวาดช่องและปฏิทินเอง:

   - ช่องที่เห็นเป็นช่องพิมพ์ (type=text) พิมพ์ "วว/ดด/ปปปป" แบบ พ.ศ. เช่น 27/11/2569, 1/2/2569, ๒๗/๑๑/๒๕๖๙, 27112569
     ช่องเดือน "ดด/ปปปป" เช่น 11/2569 · คั่นด้วย / - . หรือเว้นวรรคได้ · ไม่ใส่ปี = ปีนี้ · ปี 2 หลัก = 25xx
   - พิมพ์ปี ค.ศ. (1900–2200 เช่น 2026) แปลงเป็น พ.ศ. ให้เองและบอกใต้ช่องว่าแปลงแล้ว (จนกว่าจะแก้ช่องนั้นอีก)
   - ปีห่างจากปีนี้เกิน 100 ปี / วันที่ไม่มีจริง / เกิน max (เช่น วันในอนาคตในหน้านำเข้า): ขอบแดง + ข้อความใต้ช่อง และส่งฟอร์มไม่ได้
   - ตอนไม่ได้กรอกแสดง "27 พ.ย. 2569" / "พ.ย. 2569" · ตอนว่างแสดงความหมายของช่อง (data-empty-label เช่น "วันนี้", "ตั้งแต่ต้น")
   - ค่าที่ส่งไปเซิร์ฟเวอร์อยู่ในช่องซ่อน (name เดิม) เป็น ค.ศ. yyyy-mm-dd / yyyy-mm เหมือนเดิม
   - ปฏิทิน (#dp-popup ชุดเดียวต่อหน้า ต่อท้าย body): หัวเดือนไทย + ปี พ.ศ. เปลี่ยนเดือน/ปีได้ สัปดาห์เริ่มวันจันทร์ ปุ่ม "วันนี้" "ล้าง"
     เปิดด้วยการคลิกช่อง ปุ่มปฏิทินด้านขวา ลูกศรลง หรือ Alt+ลูกศรลง
   - คีย์บอร์ดตอนปฏิทินเปิด: ลูกศรเลื่อนวัน (ช่องเดือน: เลื่อนเดือน) PageUp/PageDown เดือนก่อน/ถัดไป (Shift = ปี)
     Home/End ต้น/ท้ายสัปดาห์ Enter เลือก Esc ปิด · ตอนปฏิทินปิด Enter ยืนยันสิ่งที่พิมพ์แล้วปล่อยให้ฟอร์มไปช่องถัดไปตามเดิม
     (Ctrl+Enter บันทึกได้ตามเดิม)
   - ไม่ต้องการ: ใส่ data-no-thai-date ที่ช่องนั้น · ปิด JavaScript: ใช้ช่องวันที่ของเบราว์เซอร์ตามเดิม (เซิร์ฟเวอร์ตรวจปีเองด้วย) */
(function () {
  "use strict";
  var MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
    "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"];
  var SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];
  var DAYS = ["จ", "อ", "พ", "พฤ", "ศ", "ส", "อา"];
  var DAY_NAMES = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"];
  var YEAR_RANGE = 100;
  var BE = 543;

  // ---------------------------------------------------------------- วันที่ (ไม่ใช้ Date เพื่อไม่ให้เขตเวลามายุ่ง)
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function daysIn(y, m) { return new Date(Date.UTC(y, m, 0)).getUTCDate(); }
  function weekday(y, m, d) { return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7; }  // 0 = จันทร์
  function iso(y, m, d) { return y + "-" + pad(m) + (d ? "-" + pad(d) : ""); }
  function parseIso(v) {
    var x = /^(\d{4})-(\d{2})(?:-(\d{2}))?$/.exec(v || "");
    return x ? { y: +x[1], m: +x[2], d: x[3] ? +x[3] : 0 } : null;
  }
  function addDays(p, n) {
    var t = new Date(Date.UTC(p.y, p.m - 1, p.d + n));
    return { y: t.getUTCFullYear(), m: t.getUTCMonth() + 1, d: t.getUTCDate() };
  }
  function addMonths(p, n) {
    var k = p.y * 12 + (p.m - 1) + n, y = Math.floor(k / 12), m = k - y * 12 + 1;
    return { y: y, m: m, d: p.d ? Math.min(p.d, daysIn(y, m)) : 0 };
  }
  function today() {
    var t = new Date();
    return { y: t.getFullYear(), m: t.getMonth() + 1, d: t.getDate() };
  }
  function thaiDigits(s) { return String(s).replace(/[๐-๙]/g, function (c) { return "๐๑๒๓๔๕๖๗๘๙".indexOf(c); }); }

  /* แปลงข้อความที่พิมพ์ -> {iso} / {empty} / {error} · month = ช่องเดือน
     ผลมี ce = ปี ค.ศ. ที่พิมพ์มา (ถ้าแปลงให้) */
  function parse(text, month, now) {
    now = now || today();
    var s = thaiDigits(text || "").trim();
    if (!s) return { empty: true };
    var parts, d, m, y, ce = null, raw;
    if (/^\d{4}-\d{1,2}(-\d{1,2})?$/.test(s)) {  // yyyy-mm-dd (ค.ศ. แบบที่ระบบใช้)
      parts = s.split("-");
      y = +parts[0]; m = +parts[1]; d = parts[2] ? +parts[2] : 0;
      if (y < 2300) { ce = y; y += BE; }
    } else {
      if (/^\d+$/.test(s)) {  // ไม่มีตัวคั่น: 27112569 / 271169 / 112569
        if (month) parts = s.length === 6 ? [s.slice(0, 2), s.slice(2)] : s.length === 5 ? [s.slice(0, 1), s.slice(1)] : null;
        else parts = s.length === 8 ? [s.slice(0, 2), s.slice(2, 4), s.slice(4)] :
          s.length === 6 ? [s.slice(0, 2), s.slice(2, 4), s.slice(4)] : null;
        if (!parts) return { error: "format" };
      } else {
        parts = s.split(/[\/\-.\s]+/).filter(Boolean);
      }
      if (parts.some(function (p) { return !/^\d+$/.test(p); })) return { error: "format" };
      if (month) {
        if (parts.length === 1) parts.push(String(now.y + BE));
        if (parts.length !== 2) return { error: "format" };
        d = 0; m = +parts[0]; raw = parts[1];
      } else {
        if (parts.length === 2) parts.push(String(now.y + BE));
        if (parts.length !== 3) return { error: "format" };
        d = +parts[0]; m = +parts[1]; raw = parts[2];
      }
      if (raw.length <= 2) y = 2500 + +raw;  // 69 -> 2569
      else if (raw.length === 4) y = +raw;
      else return { error: "format" };
      if (y >= 1900 && y <= 2200) { ce = y; y += BE; }  // พิมพ์ปี ค.ศ.: แปลงเป็น พ.ศ.
    }
    if (Math.abs(y - (now.y + BE)) > YEAR_RANGE) return { error: "year" };
    y -= BE;
    if (m < 1 || m > 12) return { error: "format" };
    if (!month && (d < 1 || d > daysIn(y, m))) return { error: "format" };
    return { iso: iso(y, m, month ? 0 : d), ce: ce };
  }

  function longText(v, month) {  // "27/11/2569" / "11/2569" (แบบที่พิมพ์)
    var p = parseIso(v);
    if (!p) return "";
    return month ? pad(p.m) + "/" + (p.y + BE) : pad(p.d) + "/" + pad(p.m) + "/" + (p.y + BE);
  }
  function shortText(v, month) {  // "27 พ.ย. 2569" / "พ.ย. 2569"
    var p = parseIso(v);
    if (!p || p.m < 1 || p.m > 12) return "";
    return (month ? "" : p.d + " ") + SHORT[p.m - 1] + " " + (p.y + BE);
  }
  function fullText(p, month) {
    return month ? MONTHS[p.m - 1] + " " + (p.y + BE) :
      DAY_NAMES[weekday(p.y, p.m, p.d)] + "ที่ " + p.d + " " + MONTHS[p.m - 1] + " " + (p.y + BE);
  }
  window.krupanDate = { parse: parse, longText: longText, shortText: shortText };
  window.krupanThaiDate = function (v) { return shortText(v, false); };
  window.krupanThaiMonth = function (v) { return shortText(v, true); };

  // ---------------------------------------------------------------- ช่อง
  var seq = 0;
  function isField(el) { return el && el.nodeType === 1 && el.hasAttribute("data-dp"); }
  function isMonth(input) { return input.getAttribute("data-dp") === "month"; }
  function hiddenOf(input) { return input.parentNode.querySelector("input[data-dp-value]"); }
  function emptyLabel(input) { return input.getAttribute("data-empty-label") || (isMonth(input) ? "ทุกเดือน" : "เลือกวันที่"); }
  function limits(input) {
    var now = today(), lo = addMonths(now, -YEAR_RANGE * 12), hi = addMonths(now, YEAR_RANGE * 12);
    var mode = isMonth(input);
    var min = input.getAttribute("data-min") || iso(lo.y, lo.m, mode ? 0 : 1);
    var max = input.getAttribute("data-max") || iso(hi.y, hi.m, mode ? 0 : daysIn(hi.y, hi.m));
    return { min: min, max: max };
  }
  function inRange(input, v) { var l = limits(input); return v >= l.min && v <= l.max; }

  function enhance(input) {
    if (isField(input) || input.hasAttribute("data-no-thai-date")) return;
    var type = input.type === "month" || input.getAttribute("type") === "month" ? "month" : "date";
    if (input.getAttribute("type") !== "date" && input.getAttribute("type") !== "month") return;
    if (!input.id) input.id = "dp" + (++seq);
    var hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = input.name;
    hidden.value = input.value || input.getAttribute("value") || "";
    hidden.setAttribute("data-dp-value", input.id);
    ["min", "max"].forEach(function (a) { if (input.hasAttribute(a)) { input.setAttribute("data-" + a, input.getAttribute(a)); input.removeAttribute(a); } });
    input.removeAttribute("name");
    input.removeAttribute("value");
    input.type = "text";
    input.setAttribute("data-dp", type);
    input.setAttribute("autocomplete", "off");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-haspopup", "grid");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", "dp-grid");
    input.setAttribute("aria-autocomplete", "none");
    var hint = document.createElement("span");
    hint.id = input.id + "-dp-hint";
    hint.className = "sr-only";
    hint.textContent = type === "month" ? "พิมพ์ ดด/ปปปป แบบ พ.ศ. เช่น 11/2569 หรือกดลูกศรลงเพื่อเลือกจากปฏิทิน"
      : "พิมพ์ วว/ดด/ปปปป แบบ พ.ศ. เช่น 27/11/2569 หรือกดลูกศรลงเพื่อเลือกจากปฏิทิน";
    var msg = document.createElement("span");
    msg.id = input.id + "-dp-msg";
    msg.className = "dp-msg";
    msg.setAttribute("aria-live", "polite");
    msg.hidden = true;
    var described = (input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
    described.push(hint.id, msg.id);
    input.setAttribute("aria-describedby", described.join(" "));
    var wrap = document.createElement("span");
    wrap.className = "dp-field";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "cb-toggle dp-toggle";
    btn.tabIndex = -1;
    btn.setAttribute("aria-label", type === "month" ? "เปิดตัวเลือกเดือน" : "เปิดปฏิทิน");
    btn.setAttribute("data-dp-toggle", input.id);
    btn.appendChild(icon());
    wrap.appendChild(btn);
    wrap.appendChild(hidden);
    wrap.appendChild(hint);
    wrap.appendChild(msg);
    show(input, false);
  }

  function icon() {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("width", "20");
    svg.setAttribute("height", "20");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    [["rect", { x: 3.5, y: 5, width: 17, height: 15, rx: 2 }], ["path", { d: "M3.5 10h17M8 3v4M16 3v4" }]].forEach(function (s) {
      var el = document.createElementNS(NS, s[0]);
      Object.keys(s[1]).forEach(function (k) { el.setAttribute(k, s[1][k]); });
      el.setAttribute("fill", "none");
      el.setAttribute("stroke", "currentColor");
      el.setAttribute("stroke-width", "1.8");
      el.setAttribute("stroke-linecap", "round");
      svg.appendChild(el);
    });
    return svg;
  }

  // แสดงค่าในช่องซ่อนในช่องที่เห็น: ตอนกรอกอยู่ "27/11/2569" ตอนไม่ได้กรอก "27 พ.ย. 2569" · ว่าง = ความหมายของช่อง
  function show(input, editing) {
    var hidden = hiddenOf(input);
    var v = hidden ? hidden.value : "";
    input.value = editing ? longText(v, isMonth(input)) : shortText(v, isMonth(input));
    input.placeholder = emptyLabel(input);
    input.classList.toggle("is-empty", !v);
  }

  function setMessage(input, text, kind) {
    var msg = document.getElementById(input.id + "-dp-msg");
    if (!msg) return;
    msg.textContent = text || "";
    msg.hidden = !text;
    msg.classList.toggle("dp-msg-error", kind === "error");
  }
  function setInvalid(input, text) {
    input.setAttribute("aria-invalid", text ? "true" : "false");
    input.setCustomValidity(text || "");
    setMessage(input, text, text ? "error" : null);
  }

  function errorText(input, kind) {
    if (kind === "year") return "ตรวจปี พ.ศ. อีกครั้ง";
    return isMonth(input) ? "ตรวจเดือนอีกครั้ง (ดด/ปปปป แบบ พ.ศ. เช่น 11/2569)" : "ตรวจวันที่อีกครั้ง (วว/ดด/ปปปป แบบ พ.ศ. เช่น 27/11/2569)";
  }
  function rangeText(input, v) {
    var l = limits(input), m = isMonth(input), t = today(), now = iso(t.y, t.m, m ? 0 : t.d);
    if (v > l.max) return l.max === now ? (m ? "เลือกเดือนในอนาคตไม่ได้" : "เลือกวันในอนาคตไม่ได้") : "ต้องไม่เกิน " + shortText(l.max, m);
    return "ต้องไม่ก่อน " + shortText(l.min, m);
  }

  /* อ่านสิ่งที่พิมพ์แล้วใส่ช่องซ่อน (ค.ศ.) · คืน true ถ้าถูกต้อง (รวมช่องว่าง) */
  function commit(input, editing) {
    var hidden = hiddenOf(input);
    if (!input.hasAttribute("data-dp-dirty")) {  // ไม่ได้พิมพ์อะไร: ค่าเดิมในช่องซ่อน (ข้อความที่เห็นเป็นแบบแสดงผล ไม่ต้องอ่านใหม่)
      if (hidden.value && !inRange(input, hidden.value)) { setInvalid(input, rangeText(input, hidden.value)); return false; }
      if (input.getAttribute("aria-invalid") !== "true") show(input, editing);
      return input.getAttribute("aria-invalid") !== "true";
    }
    var r = parse(input.value, isMonth(input));
    if (r.error) { setInvalid(input, errorText(input, r.error)); return false; }
    if (r.iso && !inRange(input, r.iso)) { setInvalid(input, rangeText(input, r.iso)); return false; }
    setInvalid(input, null);
    input.removeAttribute("data-dp-dirty");
    var v = r.iso || "";
    setValue(input, v, editing);
    if (r.ce) setMessage(input, "แปลงปี ค.ศ. " + r.ce + " เป็น พ.ศ. " + (+v.slice(0, 4) + BE) + " แล้ว", "note");
    return true;
  }

  // ตั้งค่า (ค.ศ.) จากโค้ด เช่น ปุ่มในปฏิทิน · ทดสอบ: window.krupanSetDate("#as_of", "2026-11-20")
  function setValue(input, v, editing) {
    var hidden = hiddenOf(input);
    setInvalid(input, null);
    input.removeAttribute("data-dp-dirty");
    var changed = hidden.value !== v;
    hidden.value = v;
    show(input, editing);
    if (changed) {
      hidden.dispatchEvent(new Event("input", { bubbles: true }));
      hidden.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  window.krupanRefreshDates = function (root) {
    (root || document).querySelectorAll("input[data-dp]").forEach(function (input) {
      setInvalid(input, null);
      input.removeAttribute("data-dp-dirty");
      show(input, input === document.activeElement);
    });
  };

  // ---------------------------------------------------------------- ปฏิทิน
  var popup = null, grid = null, monthSel = null, yearSel = null, title = null, current = null, view = null, active = null;

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function button(cls, text, label) {
    var b = el("button", cls, text);
    b.type = "button";
    b.tabIndex = -1;
    if (label) b.setAttribute("aria-label", label);
    return b;
  }

  function build() {
    if (popup) return popup;
    popup = el("div", "dp-popup");
    popup.id = "dp-popup";
    popup.hidden = true;
    var head = el("div", "dp-head");
    var prev = button("dp-nav", "‹", "ก่อนหน้า");
    prev.setAttribute("data-dp-step", "-1");
    var next = button("dp-nav", "›", "ถัดไป");
    next.setAttribute("data-dp-step", "1");
    title = el("div", "dp-title");
    title.id = "dp-title";
    title.setAttribute("aria-live", "polite");
    monthSel = el("select", "dp-month");
    monthSel.setAttribute("aria-label", "เดือน");
    MONTHS.forEach(function (n, i) { var o = el("option", null, n); o.value = String(i + 1); monthSel.appendChild(o); });
    yearSel = el("select", "dp-year");
    yearSel.setAttribute("aria-label", "ปี พ.ศ.");
    title.appendChild(monthSel);
    title.appendChild(yearSel);
    head.appendChild(prev);
    head.appendChild(title);
    head.appendChild(next);
    grid = el("div", "dp-grid");
    grid.id = "dp-grid";
    grid.setAttribute("role", "grid");
    grid.setAttribute("aria-labelledby", "dp-title");
    var foot = el("div", "dp-foot");
    var todayBtn = button("btn btn-secondary dp-today", "วันนี้");
    todayBtn.setAttribute("data-dp-today", "");
    var clearBtn = button("btn btn-secondary dp-clear", "ล้าง");
    clearBtn.setAttribute("data-dp-clear", "");
    foot.appendChild(todayBtn);
    foot.appendChild(clearBtn);
    popup.appendChild(head);
    popup.appendChild(grid);
    popup.appendChild(foot);
    // คลิกในปฏิทินไม่ทำให้ช่องเสียโฟกัส (ยกเว้นช่องเลือกเดือน/ปี ที่ต้องเปิดรายการของตัวเอง)
    popup.addEventListener("mousedown", function (e) { if (e.target.tagName !== "SELECT" && e.target.tagName !== "OPTION") e.preventDefault(); });
    popup.addEventListener("click", function (e) {
      if (!current) return;
      var t = e.target.closest("[data-dp-step], [data-dp-today], [data-dp-clear], [data-dp-cell]");
      if (!t) return;
      if (t.hasAttribute("data-dp-step")) { step(+t.getAttribute("data-dp-step")); return; }
      if (t.hasAttribute("data-dp-clear")) { choose(""); return; }
      if (t.hasAttribute("data-dp-today")) {
        var n = today();
        var v = iso(n.y, n.m, isMonth(current) ? 0 : n.d);
        if (inRange(current, v)) choose(v);
        return;
      }
      if (t.getAttribute("aria-disabled") === "true") return;
      choose(t.getAttribute("data-dp-cell"));
    });
    [monthSel, yearSel].forEach(function (s) {
      s.addEventListener("change", function () {
        var p = { y: +yearSel.value - BE, m: isMonth(current) ? (active ? active.m : 1) : +monthSel.value, d: 0 };
        if (!isMonth(current)) p.d = Math.min(active ? active.d : 1, daysIn(p.y, p.m));
        else p.m = active ? active.m : 1;
        active = p;
        view = { y: p.y, m: p.m };
        render();
      });
      s.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); var i = current; close(); if (i) i.focus(); }
      });
    });
    popup.addEventListener("focusout", function (e) {
      if (current && !popup.contains(e.relatedTarget) && e.relatedTarget !== current) { commit(current, false); close(); }
    });
    document.body.appendChild(popup);
    return popup;
  }

  function step(n) {
    var month = isMonth(current);
    if (month) active = addMonths(active, n * 12);
    else active = addMonths(active, n);
    render();
  }

  function yearOptions() {
    var t = today(), l = limits(current), lo = Math.max(+l.min.slice(0, 4), t.y - 30), hi = Math.min(+l.max.slice(0, 4), t.y + 10);
    lo = Math.min(lo, active.y);
    hi = Math.max(hi, active.y);
    if (+yearSel.getAttribute("data-lo") === lo && +yearSel.getAttribute("data-hi") === hi) return;
    yearSel.innerHTML = "";
    for (var y = hi; y >= lo; y--) { var o = el("option", null, String(y + BE)); o.value = String(y + BE); yearSel.appendChild(o); }
    yearSel.setAttribute("data-lo", lo);
    yearSel.setAttribute("data-hi", hi);
  }

  function render() {
    var month = isMonth(current);
    var hidden = hiddenOf(current);
    var selected = hidden.value;
    var t = today(), now = iso(t.y, t.m, month ? 0 : t.d);
    view = { y: active.y, m: active.m };
    popup.classList.toggle("dp-months", month);
    monthSel.hidden = month;
    monthSel.value = String(view.m);
    yearOptions();
    yearSel.value = String(view.y + BE);
    popup.querySelectorAll("[data-dp-step]").forEach(function (b) {
      var n = +b.getAttribute("data-dp-step");
      b.setAttribute("aria-label", month ? (n < 0 ? "ปีก่อน" : "ปีถัดไป") : (n < 0 ? "เดือนก่อน" : "เดือนถัดไป"));
    });
    var todayBtn = popup.querySelector("[data-dp-today]");
    todayBtn.textContent = month ? "เดือนนี้" : "วันนี้";
    todayBtn.disabled = !inRange(current, now);
    grid.innerHTML = "";
    grid.setAttribute("aria-label", month ? "ปี พ.ศ. " + (view.y + BE) : MONTHS[view.m - 1] + " " + (view.y + BE));
    var cells = [], row, i;
    if (month) {
      for (i = 1; i <= 12; i++) cells.push({ y: view.y, m: i, d: 0, label: SHORT[i - 1] });
    } else {
      var head = el("div", "dp-row dp-weekdays");
      head.setAttribute("role", "row");
      DAYS.forEach(function (d, k) {
        var h = el("div", "dp-wd", d);
        h.setAttribute("role", "columnheader");
        h.setAttribute("aria-label", DAY_NAMES[k]);
        h.title = DAY_NAMES[k];
        head.appendChild(h);
      });
      grid.appendChild(head);
      var first = { y: view.y, m: view.m, d: 1 };
      var start = addDays(first, -weekday(view.y, view.m, 1));
      for (i = 0; i < 42; i++) { var p = addDays(start, i); p.label = String(p.d); cells.push(p); }
    }
    var perRow = month ? 3 : 7;
    cells.forEach(function (p, k) {
      if (k % perRow === 0) { row = el("div", "dp-row"); row.setAttribute("role", "row"); grid.appendChild(row); }
      var v = iso(p.y, p.m, p.d);
      var c = el("div", "dp-cell", p.label);
      c.id = "dp-cell-" + v;
      c.setAttribute("role", "gridcell");
      c.setAttribute("data-dp-cell", v);
      c.setAttribute("aria-label", fullText(p, month));
      c.setAttribute("aria-selected", v === selected ? "true" : "false");
      if (v === now) { c.setAttribute("aria-current", "date"); c.classList.add("dp-now"); }
      if (!month && p.m !== view.m) c.classList.add("dp-other");
      if (!inRange(current, v)) c.setAttribute("aria-disabled", "true");
      if (p.y === active.y && p.m === active.m && p.d === active.d) c.classList.add("dp-active");
      row.appendChild(c);
    });
    current.setAttribute("aria-activedescendant", "dp-cell-" + iso(active.y, active.m, active.d));
    place();
  }

  function open(input) {
    build();
    if (current && current !== input) close();
    current = input;
    var r = parse(input.value, isMonth(input));
    var v = r.iso || hiddenOf(input).value;
    var p = parseIso(v);
    if (!p) {
      var t = today(), l = limits(input), now = iso(t.y, t.m, isMonth(input) ? 0 : t.d);
      p = parseIso(now > l.max ? l.max : now < l.min ? l.min : now);
    }
    active = isMonth(input) ? { y: p.y, m: p.m, d: 0 } : p;
    popup.hidden = false;
    input.setAttribute("aria-expanded", "true");
    var btn = input.parentNode.querySelector("[data-dp-toggle]");
    if (btn) btn.setAttribute("aria-pressed", "true");
    render();
  }

  function close() {
    if (!current) return;
    current.setAttribute("aria-expanded", "false");
    current.removeAttribute("aria-activedescendant");
    var btn = current.parentNode.querySelector("[data-dp-toggle]");
    if (btn) btn.setAttribute("aria-pressed", "false");
    current = null;
    if (popup) popup.hidden = true;
  }
  function isOpenFor(input) { return input === current && popup && !popup.hidden; }

  function choose(v) {
    var input = current;
    setValue(input, v, document.activeElement === input);
    close();
    if (document.activeElement !== input) input.focus();
    if (input.select) input.select();
  }

  function moveActive(fn) {
    var p = fn(active);
    var v = iso(p.y, p.m, p.d), l = limits(current);
    if (v < l.min || v > l.max) return;
    active = p;
    render();
  }

  // วางใต้ช่อง (ที่ไม่พอเปิดขึ้นบน) ไม่ล้นจอซ้าย/ขวา
  function place() {
    if (!current || !popup || popup.hidden) return;
    var box = current.parentNode.getBoundingClientRect(), de = document.documentElement;
    var w = popup.offsetWidth, h = popup.offsetHeight, vw = de.clientWidth, vh = window.innerHeight;
    var left = Math.max(8, Math.min(box.left, vw - w - 8));
    var up = box.bottom + h + 4 > vh && box.top - h - 4 > 0;
    popup.style.left = (left + window.scrollX) + "px";
    popup.style.top = (up ? box.top + window.scrollY - h - 2 : box.bottom + window.scrollY + 2) + "px";
  }

  // ---------------------------------------------------------------- เหตุการณ์
  document.addEventListener("focusin", function (e) {
    if (!isField(e.target)) return;
    var input = e.target;
    if (input.getAttribute("aria-invalid") === "true") return;  // กรอกผิดอยู่: คงสิ่งที่พิมพ์ไว้ให้แก้
    var before = input.value;
    show(input, true);
    if (input.value !== before) input.select();  // เปลี่ยนเป็นแบบที่พิมพ์แล้วเลือกทั้งหมด พิมพ์ทับได้ทันที
  });
  document.addEventListener("focusout", function (e) {
    if (!isField(e.target)) return;
    var input = e.target, to = e.relatedTarget;
    if (popup && to && popup.contains(to)) return;  // ไปช่องเลือกเดือน/ปีในปฏิทิน
    if (commit(input, false)) show(input, false);
    if (input === current) close();
  });
  // ปุ่มปฏิทิน: กดแล้วโฟกัสยังอยู่ที่ช่อง (กดซ้ำ = ปิด)
  document.addEventListener("mousedown", function (e) {
    if (e.target.closest && e.target.closest("[data-dp-toggle]")) e.preventDefault();
  });
  document.addEventListener("click", function (e) {
    var t = e.target;
    var tog = t.closest && t.closest("[data-dp-toggle]");
    if (tog) {
      var input = document.getElementById(tog.getAttribute("data-dp-toggle"));
      if (isOpenFor(input)) { close(); } else { input.focus(); open(input); }
      return;
    }
    if (isField(t)) { if (!isOpenFor(t)) open(t); return; }
    if (current && popup && !popup.contains(t) && t !== current) { close(); }
  });
  document.addEventListener("input", function (e) {
    if (!isField(e.target)) return;
    var input = e.target;
    input.setAttribute("data-dp-dirty", "");
    if (input.getAttribute("aria-invalid") === "true") setInvalid(input, null);
    setMessage(input, null);
    if (isOpenFor(input)) {  // พิมพ์ระหว่างปฏิทินเปิด: ปฏิทินเลื่อนตาม
      var r = parse(input.value, isMonth(input));
      if (r.iso) { var p = parseIso(r.iso); active = isMonth(input) ? { y: p.y, m: p.m, d: 0 } : p; render(); }
    }
  });
  // capture: ทำงานก่อนตัวจัดการ Enter ของฟอร์ม (supplies.js) · Enter ตอนปฏิทินเปิด = เลือก ไม่ไปช่องถัดไป
  document.addEventListener("keydown", function (e) {
    var input = e.target;
    if (!isField(input)) return;
    var isOpen = isOpenFor(input), month = isMonth(input);
    if (e.key === "Enter") {
      if (isOpen && !e.ctrlKey && !e.metaKey) {
        e.preventDefault(); e.stopImmediatePropagation();
        var v = iso(active.y, active.m, active.d);
        if (inRange(input, v)) choose(v);
        return;
      }
      if (isOpen) close();
      if (!commit(input, true)) { e.preventDefault(); e.stopImmediatePropagation(); }  // พิมพ์ผิด: อยู่ช่องเดิม ไม่ส่งฟอร์ม
      return;
    }
    if (e.key === "Escape") {
      if (isOpen) { e.preventDefault(); e.stopPropagation(); close(); }
      return;
    }
    if (e.key === "Tab") { if (isOpen) close(); return; }
    if (e.key === "ArrowDown" && (e.altKey || !isOpen)) { e.preventDefault(); if (!isOpen) open(input); return; }
    if (e.key === "ArrowUp" && e.altKey) { e.preventDefault(); if (isOpen) close(); return; }
    if (!isOpen || e.ctrlKey || e.metaKey || e.altKey) return;
    var moves = month ? {
      ArrowLeft: function (p) { return addMonths(p, -1); }, ArrowRight: function (p) { return addMonths(p, 1); },
      ArrowUp: function (p) { return addMonths(p, -3); }, ArrowDown: function (p) { return addMonths(p, 3); },
      PageUp: function (p) { return addMonths(p, -12); }, PageDown: function (p) { return addMonths(p, 12); },
      Home: function (p) { return { y: p.y, m: 1, d: 0 }; }, End: function (p) { return { y: p.y, m: 12, d: 0 }; }
    } : {
      ArrowLeft: function (p) { return addDays(p, -1); }, ArrowRight: function (p) { return addDays(p, 1); },
      ArrowUp: function (p) { return addDays(p, -7); }, ArrowDown: function (p) { return addDays(p, 7); },
      PageUp: function (p) { return addMonths(p, e.shiftKey ? -12 : -1); }, PageDown: function (p) { return addMonths(p, e.shiftKey ? 12 : 1); },
      Home: function (p) { return addDays(p, -weekday(p.y, p.m, p.d)); }, End: function (p) { return addDays(p, 6 - weekday(p.y, p.m, p.d)); }
    };
    if (moves[e.key]) { e.preventDefault(); moveActive(moves[e.key]); }
  }, true);
  // ส่งฟอร์มไม่ได้ถ้ามีช่องวันที่ที่กรอกผิด (รวมฟอร์ม novalidate)
  document.addEventListener("submit", function (e) {
    var bad = null;
    e.target.querySelectorAll("input[data-dp]").forEach(function (i) {
      if (!commit(i, i === document.activeElement) && !bad) bad = i;
    });
    if (bad) { e.preventDefault(); e.stopImmediatePropagation(); close(); bad.focus(); }
  }, true);
  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, true);

  function setup(root) {
    var list = [];
    if (root.matches && root.matches("input[type=date], input[type=month]")) list.push(root);
    if (root.querySelectorAll) list.push.apply(list, root.querySelectorAll("input[type=date], input[type=month]"));
    list.forEach(enhance);
  }
  if (window.htmx) htmx.onLoad(setup);
  else document.addEventListener("DOMContentLoaded", function () { setup(document); });
  window.krupanSetDate = function (sel, v) {
    var input = typeof sel === "string" ? document.querySelector(sel) : sel;
    setValue(input, v, document.activeElement === input);
  };
})();
