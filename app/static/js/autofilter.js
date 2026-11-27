// ตัวกรองอัตโนมัติ (ส่วนกลาง) ใช้กับฟอร์มตัวกรองทุกหน้า
//
// <form data-autofilter method="get" action="/x" hx-get="/x" hx-trigger="krupan:filter"
//       hx-target="#ผล" hx-swap="outerHTML" hx-select="#ผล" hx-indicator="#ผล" hx-sync="this:replace">
//   ... ช่องตัวกรอง ...
//   <noscript><button type="submit">แสดง</button></noscript>
// </form>
// <a data-filter-clear="#id-ฟอร์ม" href="/x">ล้างตัวกรอง</a>
//
// - ช่องเลือก/ช่องติ๊ก: กรองทันทีเมื่อเปลี่ยน · ช่องวันที่/เดือน และช่องพิมพ์: กรองหลังหยุดประมาณ 0.3 วินาที
// - Enter ในช่องพิมพ์ไม่ส่งฟอร์มซ้ำ (กรองทันทีถ้ายังไม่ได้กรอง ถ้าผลตรงกับคำค้นอยู่แล้วไม่ทำอะไร)
// - URL เปลี่ยนตามตัวกรอง: เปลี่ยนตัวกรองเพิ่มประวัติใหม่ (กดย้อนกลับได้) ส่วนการพิมพ์ต่อเนื่องแทนที่ประวัติเดิม
//   ไม่ให้ทุกตัวอักษรกลายเป็นหนึ่งหน้า · ช่องที่ว่างไม่ใส่ใน URL
// - กดย้อนกลับ: htmx-config ใน base.html ให้โหลดหน้าใหม่จาก URL (ไม่ใช้ภาพหน้าเก่าที่ค่าในช่องอาจไม่ตรงกับผล)
// - ปิด JavaScript: ฟอร์มส่งแบบ GET ปกติด้วยปุ่มใน <noscript>
(function () {
  "use strict";
  var DELAY = 300;
  var state = new WeakMap();

  function query(form) {
    var p = new URLSearchParams();
    new FormData(form).forEach(function (v, k) {
      if (typeof v === "string" && v.trim() !== "") p.append(k, v);
    });
    return p.toString();
  }

  function remember(form) {
    if (!state.has(form)) state.set(form, {last: query(form), timer: null, typing: false});
    return state.get(form);
  }

  function run(form, typing) {
    var s = remember(form);
    clearTimeout(s.timer);
    s.timer = null;
    var q = query(form);
    if (q === s.last) return;
    var replace = typing && s.typing;
    s.typing = typing;
    s.last = q;
    form.dataset.filterHistory = replace ? "replace" : "push";
    form.setAttribute(replace ? "hx-replace-url" : "hx-push-url", "true");
    form.removeAttribute(replace ? "hx-push-url" : "hx-replace-url");
    htmx.trigger(form, "krupan:filter");
  }

  function later(form, typing) {
    var s = remember(form);
    clearTimeout(s.timer);
    s.timer = setTimeout(function () { run(form, typing); }, DELAY);
  }

  function formOf(el) {
    return el && el.form && el.form.hasAttribute("data-autofilter") ? el.form : null;
  }

  function isTyping(el) {
    return el.tagName === "INPUT" && /^(search|text|number)$/.test(el.type);
  }

  function isDate(el) {
    return el.tagName === "INPUT" && /^(date|month)$/.test(el.type);
  }

  document.addEventListener("input", function (e) {
    var form = formOf(e.target);
    if (!form) return;
    if (isTyping(e.target)) later(form, true);
    else if (isDate(e.target)) later(form, false);
  });

  document.addEventListener("change", function (e) {
    var form = formOf(e.target);
    if (!form) return;
    if (isTyping(e.target)) run(form, true);  // ออกจากช่อง (blur) ระหว่างรอ: กรองเลย
    else if (isDate(e.target)) later(form, false);
    else run(form, false);
  });

  // ปุ่ม × ในช่องค้นหา (Chromium/Edge)
  document.addEventListener("search", function (e) {
    var form = formOf(e.target);
    if (form) run(form, true);
  }, true);

  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.hasAttribute || !form.hasAttribute("data-autofilter") || !window.htmx) return;
    e.preventDefault();
    var active = document.activeElement;
    run(form, !!(active && active.form === form && isTyping(active)));
  }, true);

  // ล้างตัวกรอง: คืนทุกช่องเป็นค่าว่าง (ยกเว้น hidden / data-filter-keep) แล้วกรองทันที · ช่องวันที่แสดงค่าว่างตาม (krupanRefreshDates)
  // ฟอร์มที่มีตัวกรองแบบชิป (data-filter-chip) เปิดลิงก์ตามปกติ เพราะชิปต้องหายไปด้วย
  document.addEventListener("click", function (e) {
    var link = e.target.closest && e.target.closest("a[data-filter-clear]");
    if (!link || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var form = document.querySelector(link.dataset.filterClear);
    if (!form || !window.htmx || form.querySelector("[data-filter-chip]")) return;
    e.preventDefault();
    Array.prototype.forEach.call(form.elements, function (el) {
      // ช่องซ่อนที่เก็บค่าของช่องวันที่/เดือน (datepicker.js) ล้างด้วย ส่วนช่องซ่อนอื่นคงไว้
      if (!el.name || (el.type === "hidden" && !el.hasAttribute("data-dp-value")) || el.hasAttribute("data-filter-keep")) return;
      if (el.tagName === "SELECT") {
        var i = Array.prototype.findIndex.call(el.options, function (o) { return o.value === ""; });
        el.selectedIndex = i < 0 ? 0 : i;
      } else if (el.type === "checkbox" || el.type === "radio") {
        el.checked = false;
      } else if (el.tagName !== "BUTTON") {
        el.value = "";
      }
    });
    if (window.krupanRefreshDates) window.krupanRefreshDates(form);
    run(form, false);
  });

  // ส่งเฉพาะช่องที่มีค่า (URL สะอาด แชร์ต่อได้) และบอกเซิร์ฟเวอร์ว่าประวัติควรเพิ่มหรือแทนที่
  document.addEventListener("htmx:configRequest", function (e) {
    var form = e.detail.elt;
    if (!form || !form.hasAttribute || !form.hasAttribute("data-autofilter")) return;
    var params = e.detail.parameters;
    var empty = [];
    params.forEach(function (v, k) { if (typeof v === "string" && v.trim() === "") empty.push(k); });
    empty.forEach(function (k) { params.delete(k); });
    e.detail.headers["HX-Filter-History"] = form.dataset.filterHistory || "push";
  });

  function setup(root) {
    var forms = [];
    if (root.matches && root.matches("form[data-autofilter]")) forms.push(root);
    if (root.querySelectorAll) forms.push.apply(forms, root.querySelectorAll("form[data-autofilter]"));
    forms.forEach(remember);
  }
  if (window.htmx) htmx.onLoad(setup);
  window.krupanAutofilterQuery = query;
})();
