/* หน้าตรวจก่อนนำเข้าวัสดุ: ช่องยอดยกมา
   - Enter = ไปช่องยอดยกมาถัดไป (ไม่กดยืนยันนำเข้าโดยไม่ตั้งใจ)
   - ค่าที่ต่างจากในไฟล์: แสดง "ในไฟล์ N" และไฮไลต์ช่อง
   ปิด JavaScript แล้วฟอร์มยังส่งได้ (เซิร์ฟเวอร์ตรวจค่าเองทั้งหมด) */
(function () {
  "use strict";
  var form = document.querySelector("form[data-import-preview]");
  if (!form) return;
  var inputs = Array.prototype.slice.call(form.querySelectorAll("input.opening-input"));

  function toInt(v) {
    v = String(v || "").replace(/[๐-๙]/g, function (c) { return "๐๑๒๓๔๕๖๗๘๙".indexOf(c); }).replace(/,/g, "").trim();
    if (v === "") return 0;
    return /^\d+$/.test(v) ? parseInt(v, 10) : null;
  }
  function mark(input) {
    var changed = toInt(input.value) !== toInt(input.getAttribute("data-file"));
    input.classList.toggle("changed", changed);
    var note = input.parentNode.querySelector("[data-opening-note]");
    if (note) note.hidden = !changed;
  }
  inputs.forEach(function (input, i) {
    mark(input);
    input.addEventListener("input", function () { mark(input); });
    input.addEventListener("keydown", function (e) {
      if (e.key !== "Enter") return;
      e.preventDefault();
      var next = inputs[i + 1];
      if (next) { next.focus(); next.select(); }
    });
  });
  // ช่องวันที่: Enter ไม่ส่งฟอร์ม
  var d = form.querySelector("#opening_date");
  if (d) d.addEventListener("keydown", function (e) { if (e.key === "Enter") e.preventDefault(); });
})();
