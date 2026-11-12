/* 时钟的计时读数与悬停信息卡 —— 蜂巢页右上角时钟与共享顶栏的计时胶囊共用这一份（hive.stage.v1「二十七」）。
 *
 * 网关在 navbar.js 之前注入本文件（同 focus.js）；纯函数不发请求、不读时钟，DOM 部分只有 attach / paintDial。
 * 对外只挂 window.HoneycombClock（node 里 require 得到同一个对象，给单测用）：
 *   clockText(sec) / dur(sec)          mm:ss（满 1 小时 h:mm:ss）/ x 小时 x 分
 *   timerArc(timer, now)               红弧比例 0–100：正计时 60 分钟一圈，倒计时 剩余 ÷ 总时长
 *   clockFace(s)                       正在计时时钟旁的字 {arc, big, small}；没在计时 null
 *   topProjects(sessions, running, from, now, n)   今天人用时最多的项目
 *   clockCardLines(s)                  悬停卡的行 [{text, dot?}]
 *   DIAL / paintDial(root, now, arc, countdown)   小钟面（含红弧）的 svg 字符串 / 摆指针与红弧
 *   attach(trigger, {lines, blocked, side})  悬停 300 毫秒出卡、移开消失、键盘聚焦同样出、按下立刻收起
 */
(function () {
  "use strict";
  if (typeof window !== "undefined" && window.HoneycombClock) return;
  var SHOW_MS = 300, TOP_N = 3, WORKER_ROWS = 3, HOUR = 3600;
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function ms(s) { var t = s ? Date.parse(s) : NaN; return isNaN(t) ? 0 : t; }
  // 与 hex-clock-menu.js 的 formatRemaining 同写法：mm:ss，满 1 小时 h:mm:ss
  function clockText(sec) {
    sec = Math.max(0, Math.floor(sec));
    var h = Math.floor(sec / HOUR), m = Math.floor(sec / 60) % 60;
    return (h ? h + ":" + pad(m) : pad(m)) + ":" + pad(sec % 60);
  }
  function dur(sec) { var m = Math.floor(Math.max(0, sec) / 60), h = Math.floor(m / 60); return h ? h + " 小时 " + (m % 60) + " 分" : m + " 分"; }
  function join(parts) { return parts.filter(Boolean).join(" · "); }

  // 红弧比例 0–100：正计时 = 已走 ÷ 60 分钟（满一圈从头再走）；倒计时 = 剩余 ÷ 总时长
  function timerArc(timer, now) {
    if (!timer) return 0;
    if (timer.deadlineAt) {
      var total = timer.deadlineAt - timer.startAt;
      return total > 0 ? Math.max(0, Math.min(100, (timer.deadlineAt - now) / total * 100)) : 0;
    }
    return (Math.max(0, now - timer.startAt) / 1000 % HOUR) / HOUR * 100;
  }
  // 时钟旁的字：s = {timer:{startAt, deadlineAt}|null, now, projectName, todaySec}；没在计时 = null（调用方照旧画）
  function clockFace(s) {
    var t = s.timer;
    if (!t) return null;
    var big = t.deadlineAt ? clockText(Math.max(0, Math.ceil((t.deadlineAt - s.now) / 1000))) : clockText((s.now - t.startAt) / 1000);
    return { arc: timerArc(t, s.now), big: big, small: join([s.projectName, "今天 " + dur(s.todaySec)]) };
  }
  // 今天各项目的人的用时，多到少前 n 个：sessions 取与 [from, now] 相交的部分，running 算到此刻；没归项目的不列
  function topProjects(sessions, running, from, now, n) {
    var by = {};
    function add(pid, a, b) { a = Math.max(a, from); b = Math.min(b, now); if (pid && b > a) by[pid] = (by[pid] || 0) + (b - a) / 1000; }
    (sessions || []).forEach(function (x) { add(x.projectId, ms(x.startAt), ms(x.endAt)); });
    if (running) add(running.projectId, ms(running.startAt), now);
    return Object.keys(by).map(function (pid) { return { projectId: pid, sec: Math.floor(by[pid]) }; })
      .sort(function (a, b) { return b.sec - a.sec; }).slice(0, n == null ? TOP_N : n);
  }
  // 时钟卡的行：s = {timer:{startAt, deadlineAt}|null, now, projectName, todaySec, focus:{afk, project, window}|null, top:[{name, color, sec}]}
  function clockCardLines(s) {
    var out = [], t = s.timer;
    if (!t) out.push({ text: "没在计时" });
    else if (t.deadlineAt) out.push({ text: join(["倒计时", s.projectName, "剩 " + clockText(Math.max(0, Math.ceil((t.deadlineAt - s.now) / 1000)))]) });
    else out.push({ text: join(["正在计时", s.projectName, clockText((s.now - t.startAt) / 1000)]) });
    if (s.todaySec != null) out.push({ text: "今天 " + dur(s.todaySec) });   // 顶栏读不到今天的用时：不写这一行
    var f = s.focus;
    if (f) { var line = f.afk ? "离开" : join([f.project && f.window ? f.project + " / " + f.window : f.project || f.window]); if (line) out.push({ text: line }); }
    (s.top || []).slice(0, TOP_N).forEach(function (p) { out.push({ dot: p.color || "", text: p.name + " " + dur(p.sec) }); });
    return out;
  }
  // 小钟面：viewBox 100，外圈 r=48.5 一圈红弧（--pr-human），其里是钟盘与三根针
  var DIAL = '<svg viewBox="0 0 100 100" class="st-dial" aria-hidden="true"><circle cx="50" cy="50" r="46" fill="var(--panel-2)" stroke="var(--line)" stroke-width="3"/>' +
    '<circle class="st-arc-timer" cx="50" cy="50" r="48.5" fill="none" stroke="var(--pr-human)" stroke-width="3" pathLength="100" stroke-dasharray="0 100" transform="rotate(-90 50 50)"/>' +
    '<line class="hh" x1="50" y1="50" x2="50" y2="28" stroke="var(--ink)" stroke-width="4" stroke-linecap="round"/>' +
    '<line class="hm" x1="50" y1="50" x2="50" y2="16" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>' +
    '<line class="hs" x1="50" y1="56" x2="50" y2="14" stroke="var(--fact)" stroke-width="1.5"/><circle cx="50" cy="50" r="3" fill="var(--fact)"/></svg>';
  function paintDial(root, now, arc, countdown) {
    ensureStyle();
    var d = new Date(now), h = d.getHours() % 12, m = d.getMinutes(), sec = d.getSeconds(), a = root.querySelector(".st-arc-timer"), was = parseFloat(a.dataset.v) || 0;
    root.querySelector(".hh").setAttribute("transform", "rotate(" + (h + m / 60) * 30 + " 50 50)");
    root.querySelector(".hm").setAttribute("transform", "rotate(" + (m * 6 + sec / 10) + " 50 50)");
    root.querySelector(".hs").setAttribute("transform", "rotate(" + sec * 6 + " 50 50)");
    a.classList.toggle("is-reset", arc < was && !countdown);   // 正计时满一圈从头再走：不倒着转回去
    a.dataset.v = arc; a.setAttribute("stroke-dasharray", arc + " 100");
  }
  var api = { SHOW_MS: SHOW_MS, clockText: clockText, dur: dur, timerArc: timerArc, clockFace: clockFace, topProjects: topProjects, clockCardLines: clockCardLines, DIAL: DIAL, paintDial: paintDial };

  // ── DOM ──────────────────────────────────────────────
  // o: {lines() → 行数组, blocked() → 为真时不出, side: "below" | "beside"}。返回 {update, hide}
  var CSS = ".st-hover-card{position:fixed;z-index:2147483000;pointer-events:none;width:max-content;max-width:320px;box-sizing:border-box;padding:8px 12px;font-size:var(--fs-sub,13px);color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}" +
    ".st-hc-row{line-height:1.5;overflow-wrap:anywhere}.st-hc-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--ink-3)}" +
    ".st-arc-timer{stroke-linecap:round}@media (prefers-reduced-motion:no-preference){.st-arc-timer{transition:stroke-dasharray 1s linear}.st-arc-timer.is-reset{transition:none}}";
  function ensureStyle() {   // 页面脚本之前注入时 head 还没有：用到（attach / paintDial）时再挂
    if (typeof document === "undefined" || !document.head || document.getElementById("hc-clockface-css")) return;
    var st = document.createElement("style"); st.id = "hc-clockface-css"; st.textContent = CSS; document.head.appendChild(st);
  }
  function attach(trigger, o) {
    ensureStyle();
    var card = null, timer = 0, off = false, key = "";   // off：按下后到指针离开前不再出
    var canHover = !(window.matchMedia && matchMedia("(hover: none)").matches);
    function paint() {
      var lines = o.lines(), k = JSON.stringify(lines);
      if (k === key) return;
      key = k; card.textContent = "";
      lines.forEach(function (l) {
        var row = document.createElement("div"); row.className = "st-hc-row";
        if (l.dot !== undefined) { var d = document.createElement("i"); d.className = "st-hc-dot"; if (l.dot) d.style.background = l.dot; row.appendChild(d); }
        row.appendChild(document.createTextNode(l.text)); card.appendChild(row);
      });
      place();
    }
    function place() {
      var r = trigger.getBoundingClientRect(), w = card.offsetWidth, h = card.offsetHeight, vw = window.innerWidth, vh = window.innerHeight, x, y;
      if (o.side === "beside") {
        x = r.right + 10 + w <= vw - 8 ? r.right + 10 : r.left - 10 - w;
        y = r.top + r.height / 2 - h / 2;
      } else { x = r.right - w; y = r.bottom + 8; }
      card.style.left = Math.max(8, Math.min(x, vw - w - 8)) + "px";
      card.style.top = Math.max(8, Math.min(y, vh - h - 8)) + "px";
    }
    function show() {
      if (card || off || o.blocked()) return;
      card = document.createElement("div"); card.className = "st-hover-card st-card"; card.setAttribute("role", "tooltip");
      document.body.appendChild(card); key = ""; paint();
    }
    function hide() { clearTimeout(timer); timer = 0; if (card) { card.remove(); card = null; key = ""; } }
    function arm() { if (!canHover || card || timer || off) return; timer = setTimeout(function () { timer = 0; show(); }, SHOW_MS); }
    trigger.addEventListener("pointerenter", function (ev) { if (ev.pointerType === "mouse" || ev.pointerType === "pen") arm(); });
    trigger.addEventListener("pointerleave", function () { off = false; hide(); });
    trigger.addEventListener("pointerdown", function () { off = true; hide(); });   // 点 / 长按 / 拖：立刻收起，不挡操作
    trigger.addEventListener("focus", function () { if (trigger.matches(":focus-visible")) { off = false; show(); } });
    trigger.addEventListener("blur", hide);
    return { hide: hide, update: function () { if (!card) return; if (o.blocked()) hide(); else paint(); } };
  }
  api.attach = attach;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.HoneycombClock = api;
})();
