// สคริปต์เล็ก ๆ ของระบบ (ไม่มี build tool) ทำงานร่วมกับ htmx
(function () {
  "use strict";

  function each(root, selector, fn) {
    if (root.matches && root.matches(selector)) fn(root);
    if (root.querySelectorAll) root.querySelectorAll(selector).forEach(fn);
  }

  // แถบ "ตรวจแล้ว · ยกเลิก": เอาออกเมื่อครบเวลา (ถ้ากำลังกดยกเลิกอยู่ รอให้เสร็จก่อน)
  function expireLater(el) {
    setTimeout(function check() {
      if (!el.isConnected) return;
      if (el.querySelector(".htmx-request") || el.classList.contains("htmx-request")) {
        setTimeout(check, 500);
        return;
      }
      el.remove();
    }, Number(el.dataset.expiresIn) || 5000);
  }

  // dropdown ที่มีตัวเลือก "สร้างใหม่": แสดงช่องกรอกเมื่อเลือก, ซ่อนเมื่อเลือกอย่างอื่น
  function syncNewFields(select) {
    var target = document.getElementById(select.dataset.newToggle);
    if (!target) return;
    var on = select.value === "__new__";
    target.hidden = !on;
    target.querySelectorAll("input").forEach(function (i) { i.disabled = !on; });
  }

  // หน้าพิมพ์สติกเกอร์: แสดงเฉพาะส่วนของวิธีเลือกที่ติ๊กอยู่
  function syncModes() {
    var checked = document.querySelector("input[data-mode-tab]:checked");
    document.querySelectorAll(".mode-panel").forEach(function (p) {
      p.hidden = !checked || p.dataset.mode !== checked.value;
    });
  }

  // กรองรายการติ๊กเลือก (ซ่อนแถวที่ไม่ตรง ไม่ได้ยกเลิกการติ๊ก)
  function filterList(input) {
    var list = document.querySelector(input.dataset.filterList);
    var words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    list.querySelectorAll("li").forEach(function (li) {
      var text = (li.dataset.text || "").toLowerCase();
      li.hidden = !words.every(function (w) { return text.indexOf(w) !== -1; });
    });
  }

  function setChecks(selector, on, onlyVisible) {
    document.querySelectorAll(selector + " li").forEach(function (li) {
      if (onlyVisible && li.hidden) return;
      li.querySelector("input[type=checkbox]").checked = on;
    });
    var form = document.getElementById("label-form");
    if (form) form.dispatchEvent(new Event("change", {bubbles: true}));
  }

  // ช่องวันที่และช่องเดือน: datepicker.js

  function setup(root) {
    each(root, "[data-expires-in]", expireLater);
    each(root, "input[data-mode-tab]", function (r) { r.addEventListener("change", syncModes); });
    each(root, "input[data-filter-list]", function (i) { i.addEventListener("input", function () { filterList(i); }); });
    each(root, "[data-check-visible]", function (b) {
      b.addEventListener("click", function () { setChecks(b.dataset.checkVisible, true, true); });
    });
    each(root, "[data-uncheck-all]", function (b) {
      b.addEventListener("click", function () { setChecks(b.dataset.uncheckAll, false, false); });
    });
    each(root, "[data-fill-result]", function (b) {
      b.addEventListener("click", function () { fillResult(b); });
    });
    each(root, "select[data-new-toggle]", function (s) {
      syncNewFields(s);
      s.addEventListener("change", function () { syncNewFields(s); });
    });
  }

  // หน้ากรอกผลตรวจสอบพัสดุทั้งห้อง: ติ๊ก "พบ ใช้งานได้" ให้ทุกแถวที่ยังไม่ได้เลือกผล (ยังไม่บันทึกจนกว่าจะกดบันทึก)
  function fillResult(button) {
    var form = button.closest("form");
    var names = {};
    form.querySelectorAll('input[type=radio][name^="r-"]').forEach(function (r) {
      names[r.name] = names[r.name] || r.checked;
    });
    var n = 0;
    Object.keys(names).forEach(function (name) {
      if (names[name]) return;
      var radio = form.querySelector('input[type=radio][name="' + name + '"][value="' + button.dataset.fillResult + '"]');
      if (radio) { radio.checked = true; n++; }
    });
    button.textContent = n ? "ติ๊ก “" + button.dataset.fillResult + "” ให้ " + n + " ชิ้นแล้ว (ยังไม่บันทึก)" : "ทุกชิ้นมีผลแล้ว";
  }

  // สร้างห้อง/ผู้รับผิดชอบใหม่จากแถวหนึ่งแล้ว ให้ dropdown ของแถวอื่นเลือกได้ทันที (เซิร์ฟเวอร์ส่ง HX-Trigger มา)
  function addOption(kind) {
    return function (evt) {
      var d = evt.detail || {};
      document.querySelectorAll('select[data-options="' + kind + '"]').forEach(function (sel) {
        if (sel.querySelector('option[value="' + d.id + '"]')) return;
        var opt = new Option(d.name, d.id);
        var newOpt = sel.querySelector('option[value="__new__"]');
        sel.insertBefore(opt, newOpt);
      });
    };
  }

  // สคริปต์นี้โหลดแบบ defer ต่อจาก htmx: ลงทะเบียนตอนนี้เลย (ก่อน DOMContentLoaded) เพื่อให้ทันเหตุการณ์
  // htmx:load ของทั้งหน้า ซึ่ง htmx ส่งตอน DOMContentLoaded
  if (window.htmx) {
    htmx.onLoad(setup);  // รันกับทั้งหน้าตอนโหลด และกับเนื้อหาใหม่ที่ htmx ใส่เข้ามา
  } else {
    document.addEventListener("DOMContentLoaded", function () { setup(document.body); });
  }
  document.body.addEventListener("krupan:location-added", addOption("locations"));
  document.body.addEventListener("krupan:staff-added", addOption("staff"));
})();

// เมนู "เพิ่มเติม ▾": ปิดเมื่อคลิกที่อื่นหรือกด Esc
document.addEventListener("click", (e) => {
  document.querySelectorAll("details.nav-more[open]").forEach((d) => {
    if (!d.contains(e.target)) d.removeAttribute("open");
  });
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  document.querySelectorAll("details.nav-more[open]").forEach((d) => {
    d.removeAttribute("open");
    d.querySelector("summary").focus();
  });
});

// เมนู "เพิ่มเติม ▾": ถ้าเปิดแล้วล้นขอบขวาของจอ ให้ชิดขวาของปุ่มแทน
document.addEventListener("toggle", (e) => {
  const d = e.target;
  if (!(d instanceof HTMLDetailsElement) || !d.classList.contains("nav-more") || !d.open) return;
  const menu = d.querySelector(".nav-more-menu");
  menu.classList.remove("align-right");
  if (menu.getBoundingClientRect().right > document.documentElement.clientWidth - 8) menu.classList.add("align-right");
}, true);
