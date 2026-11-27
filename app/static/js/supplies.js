/* ฟอร์มรับเข้า / เบิกจ่ายวัสดุ: กรอกด้วยคีย์บอร์ดอย่างเดียวได้
   - Enter = ไปช่องถัดไป (ไม่ส่งฟอร์ม) · บรรทัดสุดท้ายมีข้อมูลแล้วเพิ่มบรรทัดใหม่ให้เอง · Ctrl+Enter = บันทึก
   - ช่องวัสดุ: รายการค้นหาของระบบเอง (combobox.js) ต้องเลือกจากรายการ · Enter เลือกแล้วไปช่องถัดไป
   - เบิกจ่าย: จำนวนที่จ่ายเติมให้ = min(ที่ขอ, คงเหลือ) จนกว่าจะแก้เอง · ฝ่ายกดเลข 1–9 ได้ · ชื่อผู้เบิกเสนอตามฝ่าย
   - รับเข้า: เลือกหน่วยใหญ่ได้ แสดงจำนวนที่คิดเป็นหน่วยหลัก
   ปิด JavaScript แล้วฟอร์มยังส่งได้ตามปกติ (เซิร์ฟเวอร์ตรวจและคำนวณเองทั้งหมด) */
(function () {
  "use strict";
  var form = document.querySelector("form[data-supply-form]");
  if (!form) return;
  var kind = form.getAttribute("data-supply-form");
  var dataEl = document.getElementById("supply-data");
  var items = dataEl ? JSON.parse(dataEl.textContent || "{}") : {};
  var tbody = form.querySelector("[data-lines]");
  var maxLines = parseInt(tbody.getAttribute("data-max"), 10) || 50;

  function fmt(n) { return Number(n).toLocaleString("en-US"); }
  function toInt(v) {
    v = String(v || "").replace(/[๐-๙]/g, function (c) { return "๐๑๒๓๔๕๖๗๘๙".indexOf(c); }).replace(/,/g, "").trim();
    return /^\d+$/.test(v) ? parseInt(v, 10) : null;
  }

  // ---------------------------------------------------------------- แต่ละบรรทัด
  function refresh(tr) {
    if (!tr) return;
    var it = items[(tr.querySelector("[data-item]").value || "").trim()] || null;
    if (kind === "receive") refreshReceive(tr, it); else refreshIssue(tr, it);
  }

  function refreshReceive(tr, it) {
    var sel = tr.querySelector("[data-unit]");
    var keep = sel.value && sel.value !== "base" ? sel.value : sel.getAttribute("data-selected") || "base";
    var opts = [["base", it ? it.unit : "หน่วยหลัก"]];
    if (it) it.units.forEach(function (u) { opts.push([String(u.id), u.name + " (" + fmt(u.factor) + " " + it.unit + ")"]); });
    sel.innerHTML = "";
    opts.forEach(function (o) {
      var op = document.createElement("option");
      op.value = o[0]; op.textContent = o[1];
      if (o[0] === keep) op.selected = true;
      sel.appendChild(op);
    });
    var q = toInt(tr.querySelector("[data-qty]").value);
    var out = tr.querySelector("[data-convert]");
    var factor = 1;
    if (it && sel.value !== "base") it.units.forEach(function (u) { if (String(u.id) === sel.value) factor = u.factor; });
    out.textContent = it && q ? "= " + fmt(q * factor) + " " + it.unit : "";
  }

  function refreshIssue(tr, it) {
    var bal = tr.querySelector("[data-balance]");
    var req = tr.querySelector("[data-req]");
    var out = tr.querySelector("[data-out]");
    var note = tr.querySelector("[data-short]");
    bal.textContent = it ? fmt(it.balance) + " " + it.unit : "";
    bal.classList.toggle("danger-text", !!it && it.balance <= 0);
    var r = toInt(req.value);
    if (it && r !== null && out.getAttribute("data-manual") !== "1") out.value = String(Math.min(r, Math.max(it.balance, 0)));
    var o = toInt(out.value);
    var over = it && o !== null && (o > it.balance || (r !== null && o > r));
    out.setAttribute("aria-invalid", over ? "true" : "false");
    note.hidden = !(it && r !== null && o !== null && o < r && !over);
    note.textContent = over ? "เกินยอด" : "จ่ายได้ไม่ครบ";
    if (over) note.hidden = false;
  }

  // ---------------------------------------------------------------- เพิ่มบรรทัด
  function lines() { return Array.prototype.slice.call(tbody.querySelectorAll("[data-line]")); }
  function hasData(tr) {
    return Array.prototype.some.call(tr.querySelectorAll("input"), function (i) { return i.value.trim() !== ""; });
  }
  function addLine() {
    var all = lines();
    if (all.length >= maxLines) return null;
    var last = all[all.length - 1];
    var n = all.length + 1;
    var tr = last.cloneNode(true);
    tr.querySelectorAll("[id]").forEach(function (el) { el.id = el.id.replace(/_\d+$/, "_" + n); });
    tr.querySelectorAll("[for]").forEach(function (el) { el.htmlFor = el.htmlFor.replace(/_\d+$/, "_" + n); });
    tr.querySelectorAll("[name]").forEach(function (el) { el.name = el.name.replace(/_\d+$/, "_" + n); });
    tr.querySelectorAll("label.sr-only").forEach(function (el) { el.textContent = el.textContent.replace(/\d+$/, n); });
    tr.querySelectorAll("[data-cb-error]").forEach(function (el) { el.remove(); });
    tr.querySelectorAll("input").forEach(function (el) {
      el.value = ""; el.removeAttribute("data-manual"); el.removeAttribute("aria-invalid");
      el.removeAttribute("aria-describedby"); el.removeAttribute("aria-activedescendant");
      if (el.hasAttribute("aria-expanded")) el.setAttribute("aria-expanded", "false");
    });
    tr.querySelectorAll("select").forEach(function (el) { el.setAttribute("data-selected", "base"); });
    var no = tr.querySelector(".line-no"); if (no) no.textContent = n;
    tbody.appendChild(tr);
    refresh(tr);
    return tr;
  }
  function ensureSpare() {
    var all = lines();
    if (hasData(all[all.length - 1])) addLine();
  }

  // ---------------------------------------------------------------- คีย์บอร์ด
  function focusables() {
    return Array.prototype.filter.call(form.querySelectorAll("input, select, button[type=submit]"), function (el) {
      if (el.type === "hidden" || el.disabled || el.hidden || el.offsetParent === null) return false;
      if (el.type === "radio") return el.checked || !form.querySelector('input[type=radio][name="' + el.name + '"]:checked') &&
        el === form.querySelector('input[type=radio][name="' + el.name + '"]');
      return true;
    });
  }
  function next(el) {
    var list = focusables();
    var i = list.indexOf(el);
    if (i >= 0 && i + 1 < list.length) { list[i + 1].focus(); if (list[i + 1].select) list[i + 1].select(); }
  }

  form.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") {
      // ฝ่ายที่เบิก: กดเลข 1-9 เลือกฝ่ายตามลำดับ
      if (/^[1-9]$/.test(e.key) && e.target.closest && e.target.closest("[data-dept-group]")) {
        var radios = form.querySelectorAll("[data-dept-group] input[type=radio]");
        var r = radios[parseInt(e.key, 10) - 1];
        if (r) { r.checked = true; r.focus(); r.dispatchEvent(new Event("change", { bubbles: true })); e.preventDefault(); }
      }
      return;
    }
    if (e.ctrlKey || e.metaKey) { e.preventDefault(); if (form.requestSubmit) form.requestSubmit(); else form.submit(); return; }
    var t = e.target;
    if (t.tagName === "BUTTON" || t.tagName === "TEXTAREA" || t.tagName === "A") return;
    e.preventDefault();  // Enter ในช่องกรอกไม่ส่งฟอร์ม (กันบันทึกก่อนกรอกครบ)
    if (t.hasAttribute("data-item") && t.value.trim() && !items[t.value.trim()]) return;  // ยังไม่ได้เลือกจากรายการ: อยู่ช่องเดิม
    var tr = t.closest("[data-line]");
    if (tr) {
      ensureSpare();
      var cells = Array.prototype.slice.call(tr.querySelectorAll("input, select"));
      if (cells.indexOf(t) === cells.length - 1) {  // ช่องสุดท้ายของบรรทัด -> ช่องวัสดุของบรรทัดถัดไป
        var nxt = tr.nextElementSibling || addLine();
        if (nxt && hasData(tr)) { nxt.querySelector("[data-item]").focus(); return; }
      }
    }
    next(t);
  });

  form.addEventListener("input", function (e) {
    var tr = e.target.closest("[data-line]");
    if (!tr) return;
    if (e.target.hasAttribute("data-out")) e.target.setAttribute("data-manual", e.target.value.trim() ? "1" : "0");
    refresh(tr);
    ensureSpare();
  });
  form.addEventListener("change", function (e) {
    var tr = e.target.closest("[data-line]");
    if (tr) {
      refresh(tr);
    }
    if (e.target.name === "department_id") fillRequesters();
  });

  // ---------------------------------------------------------------- ชื่อผู้เบิกที่เคยบันทึก (แยกตามฝ่าย)
  var reqData = document.getElementById("requester-data");
  var reqNames = reqData ? JSON.parse(reqData.textContent || "{}") : {};
  function fillRequesters() {
    var list = document.getElementById("requester-names");
    if (!list) return;
    var dep = form.querySelector("input[name=department_id]:checked");
    list.innerHTML = "";
    ((dep && reqNames[dep.value]) || []).forEach(function (n) {
      var op = document.createElement("option"); op.value = n; list.appendChild(op);
    });
  }

  // ค่าที่เซิร์ฟเวอร์ส่งกลับมา (เช่น ยอดเปลี่ยน ระบบเสนอจำนวนใหม่) ถือว่าเป็นค่าที่ตั้งเอง ไม่เติมทับ
  form.querySelectorAll("[data-out]").forEach(function (o) { if (o.value.trim()) o.setAttribute("data-manual", "1"); });
  lines().forEach(refresh);
  fillRequesters();
  ensureSpare();
})();
