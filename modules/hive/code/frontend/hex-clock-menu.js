// hive · hex-clock-menu.js —— 右上角时钟的短按菜单 / 长按计时气泡（hive.stage.v1「九」；停靠左下已由「十」的眼睛取代）。
// 上半是纯函数（给 hex-clock-menu.test.js 用），下半是 DOM（文字一律 textContent）。
(function () {
  "use strict";

  var HOLD_MS = 520, HOLD_SLOP = 10;   // 与 hex-timer.js 的 LONGPRESS_MS / LONGPRESS_SLOP 同值（那边是计时页，不在本页加载）
  var PRESETS = [25, 45, 60], MAX_MIN = 1440;
  var HIDDEN_TITLE = /^(窗口名|路径)\d+$/;
  function ms(s) { var t = s ? Date.parse(s) : NaN; return isNaN(t) ? 0 : t; }
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

  // 今天人在每个项目上的用时：计时段裁到 [dayStart, now]，加上在跑的这一段；projectId null = 还没记到项目
  function allocation(lanes, now, dayStart) {
    var human = (lanes && lanes.human) || {}, by = {}, total = 0;
    function add(pid, a, b) {
      var sec = Math.floor((Math.min(b, now) - Math.max(a, dayStart)) / 1000);
      if (sec <= 0) return;
      by[pid || ""] = (by[pid || ""] || 0) + sec; total += sec;
    }
    (human.sessions || []).forEach(function (s) { add(s.projectId, ms(s.startAt), ms(s.endAt)); });
    if (human.running) add(human.running.projectId, ms(human.running.startAt), now);
    return Object.keys(by).map(function (k) { return { projectId: k || null, seconds: by[k], share: total ? by[k] / total : 0 }; })
      .sort(function (a, b) { return b.seconds - a.seconds; });
  }

  function splitKey(key, apps) {
    var app = (apps || []).filter(function (a) { return key.indexOf(a + " · ") === 0; })[0] || key.split(" · ")[0];
    var title = key.indexOf(app + " · ") === 0 ? key.slice(app.length + 3) : "";
    return { app: app, title: title && !HIDDEN_TITLE.test(title) ? title : null, hidden: HIDDEN_TITLE.test(title) };
  }
  // 今天打开过的窗口（activity/blocks 的块）：按秒数从多到少
  function windowRows(blocks) {
    return (blocks || []).map(function (b) {
      var k = splitKey(String(b.blockKey || ""), b.apps);
      return { app: k.app, title: k.title, hidden: k.hidden, seconds: b.seconds || 0, lastAt: b.lastAt || null, blockKey: b.blockKey, date: b.date };
    }).sort(function (a, b) { return b.seconds - a.seconds; });
  }
  // 聚焦时间线：各块的原始记录合在一起，新的在前；相邻同窗口并成一行
  function focusTimeline(records, limit) {
    var rows = (records || []).map(function (r) {
      return { from: ms(r.startAt), to: ms(r.endAt), app: r.app || "", title: r.title || "", hidden: HIDDEN_TITLE.test(r.title || "") };
    }).filter(function (r) { return r.to > r.from; }).sort(function (a, b) { return a.from - b.from; });
    var out = [];
    rows.forEach(function (r) {
      var last = out[out.length - 1];
      if (last && last.app === r.app && last.title === r.title && r.from - last.to <= 5000) last.to = Math.max(last.to, r.to);
      else out.push({ from: r.from, to: r.to, app: r.app, title: r.title, hidden: r.hidden });
    });
    return out.reverse().slice(0, limit || 40);
  }

  var H = { HOLD_MS: HOLD_MS, PRESETS: PRESETS, pressKind: pressKind, remainingSeconds: remainingSeconds, formatRemaining: formatRemaining, parseMinutes: parseMinutes,
    endedText: endedText, allocation: allocation, windowRows: windowRows, focusTimeline: focusTimeline };
  if (typeof module !== "undefined" && module.exports) { module.exports = H; return; }
  window.HexClockMenu = H;

  // ── DOM ──
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }
  function hm(t) { var d = new Date(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function dur(sec) { var m = Math.floor(Math.max(0, sec) / 60), h = Math.floor(m / 60); return h ? h + " 小时 " + (m % 60) + " 分" : m + " 分"; }
  function winLabel(w) { return w.hidden ? w.app + " · 标题已隐去" : w.title ? w.app + " · " + w.title : w.app; }

  // o: {clock, getLanes(), dayStart(), now(), projectName(id), getJson(path), fillIssues(box, list), timerRunning(), startClock(sec), stopClock()}
  H.mount = function (o) {
    var clock = o.clock, panel = null, bubbles = null, issues = [], hold = { id: null, x: 0, y: 0, t: 0, fired: false }, poll = 0, loaded = { rows: [], line: [] };

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
      panel = bubbles = null; clearInterval(poll);
      document.removeEventListener("pointerdown", onOutside, true); document.removeEventListener("keydown", onKey, true);
      clock.setAttribute("aria-expanded", "false");
    }
    function onOutside(ev) { if ((panel && panel.contains(ev.target)) || (bubbles && bubbles.contains(ev.target))) return; if (!clock.contains(ev.target)) closeAll(); }
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

    // ── 短按：窗口记录 / 人用时汇总 ──
    function fillPanel() {
      if (!panel) return;
      var body = panel.querySelector(".cm-body"), now = o.now(), alloc = allocation(o.getLanes(), now, o.dayStart());
      body.textContent = "";
      function section(title) { var s = el("section", "cm-sec"); s.appendChild(el("h4", "", title)); body.appendChild(s); return s; }
      var s1 = section("人用时");
      if (!alloc.length) s1.appendChild(el("div", "cm-empty", "今天还没有"));
      alloc.forEach(function (a) {
        var row = el("div", "cm-alloc"), bar = el("span", "cm-bar"), fill = el("i");
        fill.style.width = Math.max(2, Math.round(a.share * 100)) + "%"; bar.appendChild(fill);
        row.appendChild(el("b", "", a.projectId ? o.projectName(a.projectId) : "未分类"));
        row.appendChild(el("span", "cm-dur", dur(a.seconds))); row.appendChild(bar);
        s1.appendChild(row);
      });
      var s2 = section("今天打开的窗口");
      if (!loaded.rows.length) s2.appendChild(el("div", "cm-empty", "没有记录"));
      loaded.rows.slice(0, 12).forEach(function (w) {
        var row = el("div", "cm-win"); row.appendChild(el("b", "", winLabel(w))); row.appendChild(el("span", "cm-dur", dur(w.seconds))); s2.appendChild(row);
      });
      var s3 = section("聚焦时间线");
      if (!loaded.line.length) s3.appendChild(el("div", "cm-empty", "没有记录"));
      loaded.line.forEach(function (r) {
        var row = el("div", "cm-win"); row.appendChild(el("span", "cm-at", hm(r.from))); row.appendChild(el("b", "", winLabel(r)));
        row.appendChild(el("span", "cm-dur", dur((r.to - r.from) / 1000))); s3.appendChild(row);
      });
      if (issues.length) { var s4 = section("窗口信息不全"); o.fillIssues(s4, issues); }
      anchor(panel);   // 内容变高后重新贴位，免得压到时钟
    }
    function load() {
      o.getJson("activity/blocks?status=all&limit=200").then(function (r) {
        var items = ((r && r.items) || []).map(function (b) { return Object.assign({ date: r.date }, b); });
        loaded.rows = windowRows(items);
        var recent = items.slice().sort(function (a, b) { return ms(b.lastAt) - ms(a.lastAt); }).slice(0, 8);
        return Promise.all(recent.map(function (b) {
          return o.getJson("activity/blocks/records?date=" + encodeURIComponent(r.date) + "&blockKey=" + encodeURIComponent(b.blockKey)).then(function (x) { return (x && x.items) || []; });
        }));
      }).then(function (lists) { loaded.line = focusTimeline([].concat.apply([], lists || []), 40); fillPanel(); }, function () { fillPanel(); });
    }
    H.open = function () {
      if (panel) { closeAll(); return; }
      closeAll();
      panel = el("div", "cm-panel st-card"); panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "窗口记录 / 人用时汇总");
      panel.appendChild(el("h3", "", "窗口记录 / 人用时汇总")); panel.appendChild(el("div", "cm-body"));
      document.body.appendChild(panel); fillPanel(); anchor(panel); arm(); load();
      poll = setInterval(function () { load(); }, 15000);
    };
    H.refresh = function () { if (panel) fillPanel(); };
    H.setIssues = function (list) { issues = list || []; clock.classList.toggle("has-issue", issues.length > 0); if (panel) fillPanel(); };
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
