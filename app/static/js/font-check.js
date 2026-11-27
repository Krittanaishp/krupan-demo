/* หน้ารายงาน: ถ้าเบราว์เซอร์ไม่ได้ใช้ฟอนต์ TH SarabunIT๙ (เครื่องนี้ไม่ได้ติดตั้ง) ให้แสดงคำเตือน #font-warn
   document.fonts.check() คืน true เสมอถ้าชื่อนั้นไม่มี @font-face (ถือเป็นฟอนต์ระบบ) report-fonts.css จึงประกาศ
   @font-face ชื่อนี้ที่ใช้ได้เฉพาะ local() แล้วตรวจว่าโหลดสำเร็จจริงด้วย */
(function () {
  var warn = document.getElementById("font-warn");
  if (!warn || !document.fonts || !document.fonts.load) return;
  var family = "TH SarabunIT๙";
  var spec = '16pt "' + family + '"';
  var sample = "ทะเบียนครุภัณฑ์ 0123";

  function loaded() {
    var ok = false;
    document.fonts.forEach(function (face) {
      if (face.family.replace(/["']/g, "") === family && face.status === "loaded") ok = true;
    });
    return ok;
  }

  function decide() {
    if (!(document.fonts.check(spec, sample) && loaded())) warn.hidden = false;
    document.documentElement.setAttribute("data-report-font", warn.hidden ? "it9" : "fallback");
  }

  document.fonts.load(spec, sample).then(decide, decide);
})();
