// การทำงานเล็ก ๆ ที่เดิมเขียนเป็น inline (onclick / onchange / javascript: / style="") ย้ายมาไว้ที่นี่
// เพราะ Content-Security-Policy ของระบบไม่อนุญาต inline script และ style attribute
// โหลดทุกหน้า (base.html, q/base.html) และหน้าพิมพ์ที่ไม่ใช้ base.html
//
//   <button data-print>พิมพ์</button>               พิมพ์หน้านี้
//   <input type="checkbox" data-autosubmit>          เปลี่ยนค่าแล้วส่งฟอร์มทันที
//   <a href="/ที่สำรอง" data-back>ยกเลิก</a>          ย้อนกลับหน้าก่อน (ไม่มีประวัติ = ไปตาม href)
//   <span data-pct="42"></span>                       แถบความคืบหน้า: ความกว้าง 42%
(function () {
  "use strict";

  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-print], a[data-back]") : null;
    if (!t) return;
    if (t.hasAttribute("data-print")) {
      e.preventDefault();
      window.print();
    } else if (window.history.length > 1 && document.referrer) {
      e.preventDefault();
      window.history.back();
    }
  });

  document.addEventListener("change", function (e) {
    var t = e.target;
    if (t && t.hasAttribute && t.hasAttribute("data-autosubmit") && t.form) {
      if (t.form.requestSubmit) t.form.requestSubmit(); else t.form.submit();
    }
  });

  // ตั้งค่าผ่าน CSSOM (element.style) ซึ่ง CSP อนุญาต ต่างจาก style="" ใน HTML ที่ถูกบล็อก
  function apply(root) {
    if (!root.querySelectorAll) return;
    root.querySelectorAll("[data-pct]").forEach(function (el) {
      var n = parseFloat(el.getAttribute("data-pct"));
      if (!isNaN(n)) el.style.width = Math.max(0, Math.min(100, n)) + "%";
    });
    // หน้าแก้ไขครุภัณฑ์: ปิดช่องวัน/เดือน/ปีที่ไม่ใช้ตามระดับความละเอียดที่เลือก (เซิร์ฟเวอร์ไม่สนใจช่องเหล่านี้อยู่แล้ว)
    if (root.querySelector('input[name="date_precision"]')) syncDateParts();
  }

  var USED = {day: ["day", "month", "year"], month: ["month", "year"], year: ["year"], none: []};
  function syncDateParts() {
    var p = (document.querySelector('input[name="date_precision"]:checked') || {}).value || "none";
    document.querySelectorAll(".date-parts [data-part]").forEach(function (el) {
      var on = (USED[p] || []).indexOf(el.dataset.part) !== -1;
      el.hidden = !on;
      var field = el.querySelector("input, select");
      if (field) field.disabled = !on;
    });
  }
  document.addEventListener("change", function (e) {
    if (e.target && e.target.name === "date_precision") syncDateParts();
  });

  if (window.htmx) htmx.onLoad(apply);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { apply(document); });
  } else {
    apply(document);
  }
})();
