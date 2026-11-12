// hive · hex-clock-menu.js —— 右上角时钟的短按「人的页面」/ 长按计时气泡（hive.stage.v1「九」「十四」；停靠左下已由「十」的眼睛取代）。
// 上半是纯函数（给 hex-clock-menu.test.js 用），下半是 DOM（文字一律 textContent）。
(function () {
  "use strict";

  var HOLD_MS = 520, HOLD_SLOP = 10;   // 与 hex-timer.js 的 LONGPRESS_MS / LONGPRESS_SLOP 同值（那边是计时页，不在本页加载）
  var PRESETS = [25, 45, 60], MAX_MIN = 1440;
  function pad(n) { return n < 10 ? "0" + n : "" + n; }

  // ── 纯逻辑 ──
  // 按下后：挪远了 = move（想拖）；按满 HOLD_MS = hold；否则 tap
  function pressKind(heldMs, dx, dy) {
    if (Math.hypot(dx, dy) > HOLD_SLOP) return "move";
    return heldMs >= HOLD_MS ? "hold" : "tap";
  }
  function remainingSeconds(deadlineMs, now) { return Math.max(0, Math.ceil((deadlineMs - now) / 1000)); }
  function formatRemaining(sec) {
    sec = Math.max(0, Math.floor(sec));
    var h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60;
    return (h ? h + ":" + pad(m) : pad(m)) + ":" + pad(sec % 60);
  }
  // 自定义分钟：整数 1..1440，否则 null
  function parseMinutes(text) {
    var t = String(text == null ? "" : text).trim();
    if (!/^\d{1,4}$/.test(t)) return null;
    var n = parseInt(t, 10);
    return n >= 1 && n <= MAX_MIN ? n : null;
  }
  function endedText(startMs, deadlineMs) { return "倒计时结束 · " + Math.max(1, Math.round((deadlineMs - startMs) / 60000)) + " 分钟已记下"; }

  var H = { HOLD_MS: HOLD_MS, PRESETS: PRESETS, pressKind: pressKind, remainingSeconds: remainingSeconds, formatRemaining: formatRemaining, parseMinutes: parseMinutes,
    endedText: endedText };
  if (typeof module !== "undefined" && module.exports) { module.exports = H; return; }
  window.HexClockMenu = H;

  // ── DOM ──
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  // o: {clock, startClock(sec), stopClock(), timerRunning(), pickHuman(), 其余同 hex-human-page.js 的 create(o)}
  H.mount = function (o) {
    var clock = o.clock, panel = null, bubbles = null, view = null, issues = [], hold = { id: null, x: 0, y: 0, t: 0, fired: false };

    // 长按：与拖动并行监听，按满不动 = 弹计时气泡，并吃掉随后的短按
    clock.addEventListener("pointerdown", function (ev) {
      if (ev.button > 0) return;
      clearTimeout(hold.t); hold.fired = false; hold.id = ev.pointerId; hold.x = ev.clientX; hold.y = ev.clientY;
      hold.t = setTimeout(function () { if (hold.id != null) { hold.fired = true; hold.id = null; H.showActions(); } }, HOLD_MS);
    });
    clock.addEventListener("pointermove", function (ev) {
      if (hold.id === ev.pointerId && pressKind(0, ev.clientX - hold.x, ev.clientY - hold.y) === "move") { clearTimeout(hold.t); hold.id = null; }
    });
    function release(ev) { if (hold.id === ev.pointerId) { clearTimeout(hold.t); hold.id = null; } }
    clock.addEventListener("pointerup", release); clock.addEventListener("pointercancel", release);

    function anchor(box) {   // 挂在 body 上用视口坐标：时钟在右上 → 菜单在下；停靠在左下 → 菜单在上
      var r = clock.getBoundingClientRect(), w = box.offsetWidth, hgt = box.offsetHeight;
      var x = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8));
      var y = r.bottom + 10 + hgt <= window.innerHeight - 8 ? r.bottom + 10 : Math.max(8, r.top - 10 - hgt);
      box.style.left = x + "px"; box.style.top = y + "px";
    }
    function closeAll() {
      [panel, bubbles].forEach(function (n) { if (n) n.remove(); });
      if (view) view.destroy();
      panel = bubbles = view = null;
      document.removeEventListener("pointerdown", onOutside, true); document.removeEventListener("keydown", onKey, true);
      clock.setAttribute("aria-expanded", "false");
    }
    function onOutside(ev) {
      if ((panel && panel.contains(ev.target)) || (bubbles && bubbles.contains(ev.target)) || (ev.target.closest && ev.target.closest(".st-confirm"))) return;
      if (!clock.contains(ev.target)) closeAll();
    }
    function onKey(ev) { if (ev.key === "Escape") { ev.stopPropagation(); closeAll(); clock.focus(); } }
    function arm() { document.addEventListener("pointerdown", onOutside, true); document.addEventListener("keydown", onKey, true); clock.setAttribute("aria-expanded", "true"); }

    // ── 长按：气泡 ──
    H.showActions = function () {
      closeAll();
      bubbles = el("div", "cm-bubbles st-card"); bubbles.setAttribute("role", "menu");
      function pill(text, fn) { var b = el("button", "cm-bubble", text); b.type = "button"; b.setAttribute("role", "menuitem"); b.addEventListener("click", fn); bubbles.appendChild(b); return b; }
      if (o.timerRunning()) pill("停止", function () { closeAll(); o.stopClock(); });
      else {
        pill("正计时", function () { closeAll(); o.startClock(0); });
        pill("倒计时", function () { expand(); });
      }
      document.body.appendChild(bubbles); anchor(bubbles); arm();
      var first = bubbles.querySelector("button"); if (first) first.focus();
    };
    function expand() {
      bubbles.textContent = "";
      var row = el("div", "cm-presets");
      PRESETS.forEach(function (m) {
        var b = el("button", "cm-bubble", m + " 分"); b.type = "button";
        b.addEventListener("click", function () { closeAll(); o.startClock(m * 60); });
        row.appendChild(b);
      });
      bubbles.appendChild(row);
      var form = el("form", "cm-custom"), inp = el("input"), go = el("button", "cm-bubble", "开始");
      inp.type = "text"; inp.inputMode = "numeric"; inp.placeholder = "分钟"; inp.setAttribute("aria-label", "自定义分钟"); inp.maxLength = 4;
      go.type = "submit";
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var m = parseMinutes(inp.value);
        if (m == null) { inp.classList.add("is-bad"); return; }
        closeAll(); o.startClock(m * 60);
      });
      inp.addEventListener("input", function () { inp.classList.remove("is-bad"); });
      form.appendChild(inp); form.appendChild(go); bubbles.appendChild(form);
      anchor(bubbles); row.firstChild.focus();
    }

    // ── 短按：人的页面（hex-human-page.js；时钟下方右对齐，高度跟着窗口） ──
    H.open = function () {
      if (panel) { closeAll(); return; }
      closeAll();
      panel = el("div", "cm-panel st-card"); panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "人");
      view = window.HexHumanPage.create(Object.assign({}, o, { pickHuman: function () { closeAll(); o.pickHuman(); } }));
      view.setIssues(issues);
      panel.appendChild(view.el);
      document.body.appendChild(panel);
      var r = clock.getBoundingClientRect(), w = panel.offsetWidth, y = r.bottom + 10;
      panel.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + "px"; panel.style.top = y + "px";
      panel.style.maxHeight = Math.max(240, window.innerHeight - y - 8) + "px";
      arm(); view.load();
    };
    H.refresh = function () { if (view) view.update(); };
    H.setIssues = function (list) { issues = list || []; clock.classList.toggle("has-issue", issues.length > 0); if (view) view.setIssues(issues); };
    H.close = closeAll;
    H.consumedHold = function () { var f = hold.fired; hold.fired = false; return f; };

    // 倒计时到点的提醒卡：点一下收起，10 秒后自收
    H.remind = function (text) {
      var old = document.getElementById("cmAlert"); if (old) old.remove();
      var a = el("div", "cm-alert st-card", text); a.id = "cmAlert"; a.setAttribute("role", "status"); a.tabIndex = 0;
      document.body.appendChild(a); anchor(a);
      var gone = function () { a.remove(); };
      a.addEventListener("click", gone); setTimeout(gone, 10000);
    };
    return H;
  };
})();
