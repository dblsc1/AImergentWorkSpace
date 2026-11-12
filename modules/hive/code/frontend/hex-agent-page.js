// hive · hex-agent-page.js —— 左上角 Agent 页：一个 agent 一张卡（hive.stage.v1「十三」）。
// 纯函数（node 里测）：windowOf / groupRuns / segmentsOf / focusSpans / currentProject / rankGroups / ticks / summary / subtitle / topTool；
// 接线：mount(ctx) 建页面、读 views/time-lanes?since=、画卡片、换位过渡、飞入飞回。
(function () {
  "use strict";
  var Pr = (typeof window !== "undefined" && window.NexusTableHexProjectRuler) || (typeof require !== "undefined" ? require("./hex-project-ruler.js") : null);
  var Cs = (typeof window !== "undefined" && window.NexusTableHexCentreStats) || (typeof require !== "undefined" ? require("./hex-centre-stats.js") : null);
  var HOUR = 3600000, RANK_MS = 3 * HOUR, REFRESH_MS = 15000, FLY_MS = 500, SWAP_MS = 300, DEFAULT_HOURS = 3;
  var WEEK = ["日", "一", "二", "三", "四", "五", "六"];

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function hm(t) { var d = new Date(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function md(t) { var d = new Date(t); return pad(d.getMonth() + 1) + "/" + pad(d.getDate()); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function pct(x) { return Math.round(x * 100000) / 1000 + "%"; }
  function ms(s) { return Date.parse(s); }

  // ── 纯函数 ───────────────────────────────────────────
  // 滚动窗口 [now − 范围, now]：日 24 小时，周 7×24 小时，拖动条 1–24 小时
  function windowOf(mode, hours, now) {
    var h = mode === "day" ? 24 : mode === "week" ? 168 : Math.min(24, Math.max(1, Math.round(hours) || DEFAULT_HOURS));
    return { mode: mode, hours: h, from: now - h * HOUR, to: now, label: mode === "day" ? "今天" : mode === "week" ? "最近 7 天" : "最近 " + h + " 小时" };
  }
  function firstStart(r) { return r.segments && r.segments.length ? ms(r.segments[0].start) : 0; }
  function lastEnd(r) { return (r.segments || []).reduce(function (m, s) { return Math.max(m, ms(s.end)); }, 0); }
  // 同一个代理身份（laneKey，沿用 views/lanes 的泳道分组）的运行并成一组；组里的运行按开始先后
  function groupRuns(runs) {
    var by = {}, out = [];
    (runs || []).forEach(function (r) {
      var k = r.laneKey || r.runId;
      if (!by[k]) { by[k] = { key: k, runs: [] }; out.push(by[k]); }
      by[k].runs.push(r);
    });
    out.forEach(function (g) {
      g.runs.sort(function (a, b) { return firstStart(a) - firstStart(b); });
      var latest = g.runs.slice().sort(function (a, b) { return lastEnd(b) - lastEnd(a); })[0];
      g.name = latest.name || latest.agent || "代理"; g.tool = latest.tool || latest.agent || null;
    });
    return out;
  }
  // 工作段：每段自带它当时所属的项目（null = 没有项目），已按开始先后
  function segmentsOf(group) {
    var out = [];
    group.runs.forEach(function (r) { (r.segments || []).forEach(function (s) { out.push({ start: ms(s.start), end: ms(s.end), projectId: s.projectId || null, taskId: s.taskId || null }); }); });
    return out.sort(function (a, b) { return a.start - b.start; });
  }
  // 人聚焦在这个 agent 上的区间：各运行 attend 的并集，裁到窗口，相连的并成一段
  function focusSpans(group, win) {
    var all = [];
    group.runs.forEach(function (r) {
      (r.attend || []).forEach(function (s) {
        var a = Math.max(ms(s.start), win.from), b = Math.min(ms(s.end), win.to);
        if (b > a) all.push([a, b]);
      });
    });
    all.sort(function (x, y) { return x[0] - y[0]; });
    var out = [];
    all.forEach(function (s) { var l = out[out.length - 1]; if (l && s[0] <= l.end) l.end = Math.max(l.end, s[1]); else out.push({ start: s[0], end: s[1] }); });
    return out;
  }
  function live(r) { return r.running && !r.lost; }
  // 卡片的颜色：现在所属的项目 = 在跑的运行里最近开始的那条；没有在跑的取最近开始的那条
  function currentProject(group) {
    var pool = group.runs.filter(live);
    if (!pool.length) pool = group.runs;
    return pool[pool.length - 1].projectId || null;
  }
  // 档位同 views/lanes 的 lane_order：在等人 0 → 干活 1 → 出错 2 → 空闲 3 → 失联 3.5 → 已结束 4
  function tierOf(r) {
    if (!r.running) return 4;
    if (r.lost) return 3.5;
    return r.phase === "waiting_input" || r.phase === "waiting_permission" ? 0 : r.phase === "working" ? 1 : r.phase === "error" ? 2 : 3;
  }
  // 活跃度排序：档位 → 近 3 小时干活秒数多的在前 → 最近一次干活结束得晚的在前 → 名字
  function rankGroups(groups, now) {
    var key = groups.map(function (g) {
      var active = 0, last = 0;
      segmentsOf(g).forEach(function (s) { active += Math.max(0, Math.min(s.end, now) - Math.max(s.start, now - RANK_MS)); last = Math.max(last, s.end); });
      return { g: g, tier: Math.min.apply(null, g.runs.map(tierOf)), active: active, last: last };
    });
    key.sort(function (a, b) { return a.tier - b.tier || b.active - a.active || b.last - a.last || (a.g.name < b.g.name ? -1 : a.g.name > b.g.name ? 1 : 0); });
    return key.map(function (k) { return k.g; });
  }
  // 标尺刻度：不超过 36 小时取整点（跨度越长间隔越大），更长取每天零点写周几
  function ticks(from, to) {
    var out = [], span = to - from, t = new Date(from);
    if (span > 36 * HOUR) {
      for (var d = new Date(t.getFullYear(), t.getMonth(), t.getDate() + 1); d.getTime() <= to; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1))
        out.push({ t: d.getTime(), label: "周" + WEEK[d.getDay()] });
      return out;
    }
    var step = span <= 6 * HOUR ? 1 : span <= 12 * HOUR ? 2 : 3;
    for (var c = new Date(t.getFullYear(), t.getMonth(), t.getDate(), t.getHours() + 1); c.getTime() <= to; c = new Date(c.getFullYear(), c.getMonth(), c.getDate(), c.getHours() + 1))
      if (c.getHours() % step === 0) out.push({ t: c.getTime(), label: pad(c.getHours()) });
    return out;
  }
  function summary(groups, runs) {
    var tt = Cs.totals({ agents: runs });
    return { sec: tt.agentSec, peak: tt.peak, working: groups.filter(function (g) { return g.runs.some(function (r) { return live(r) && r.phase === "working"; }); }).length };
  }
  function fmtHm(sec) { var m = Math.round(Math.max(0, sec) / 60); return Math.floor(m / 60) + " 小时 " + m % 60 + " 分钟"; }
  function subtitle(win, s) { return "agent " + win.label + "工作了 " + fmtHm(s.sec) + "，并行峰值 " + s.peak + "，现在有 " + s.working + " 个正在工作"; }
  // 窗口里干活最久的 tool（左上角头像用）；没有 = null
  function topTool(runs) {
    var by = {}, best = null;
    (runs || []).forEach(function (r) {
      var k = r.tool || r.agent; if (!k) return;
      by[k] = (by[k] || 0) + Cs.totals({ agents: [r] }).agentSec;
    });
    Object.keys(by).sort().forEach(function (k) { if (by[k] > 0 && (best === null || by[k] > by[best])) best = k; });
    return best;
  }

  var api = { windowOf: windowOf, groupRuns: groupRuns, segmentsOf: segmentsOf, focusSpans: focusSpans, currentProject: currentProject, rankGroups: rankGroups,
    ticks: ticks, summary: summary, subtitle: subtitle, topTool: topTool, fmtHm: fmtHm, DEFAULT_HOURS: DEFAULT_HOURS, REFRESH_MS: REFRESH_MS };

  // ── 接线 ─────────────────────────────────────────────
  // ctx: { getJson, cellColor(pid), projectName(pid), packOfTool(tool), figureOf(pack) → {key, html}, petEl(runId) → .st-pet | null, placeholder }
  function mount(ctx) {
    var mode = "hours", hours = DEFAULT_HOURS, opened = false, timer = 0, seq = 0, cards = {}, flights = 0, hidden = [], flyPending = false;
    var reduced = function () { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches); };
    var veil = document.createElement("div");
    veil.className = "ag-veil"; veil.id = "agVeil"; veil.hidden = true;
    veil.innerHTML = '<div class="ag-page st-card" id="agPage" role="dialog" aria-modal="true" aria-label="Agent">' +
      '<div class="ag-hd"><div class="ag-title"><h2 class="ag-h"><b>Agent</b></h2><div class="ag-sub" id="agSub"></div></div>' +
      '<div class="ag-range" role="group"><button type="button" data-mode="day">日</button><button type="button" data-mode="week">周</button>' +
      '<label class="ag-hours"><input type="range" id="agHours" min="1" max="24" step="1" aria-label="最近几小时"><output id="agHoursOut"></output></label></div>' +
      '<button type="button" class="st-x ag-x" aria-label="关闭">✕</button></div>' +
      '<div class="ag-axis"><div class="ag-ruler" id="agRuler"></div></div>' +
      '<div class="ag-scroll"><div class="ag-stack" id="agStack"><div class="ag-plot" id="agPlot"><div class="ag-now" id="agNow"><span></span></div></div></div></div></div>';
    document.body.appendChild(veil);
    var page = veil.firstChild, sub = page.querySelector("#agSub"), range = page.querySelector("#agHours"), out = page.querySelector("#agHoursOut"),
        ruler = page.querySelector("#agRuler"), stack = page.querySelector("#agStack"), plot = page.querySelector("#agPlot"), nowEl = page.querySelector("#agNow");
    range.value = String(hours);

    function syncControls() {
      page.querySelectorAll("[data-mode]").forEach(function (b) { b.classList.toggle("on", b.dataset.mode === mode); });
      page.querySelector(".ag-hours").classList.toggle("on", mode === "hours");
      out.textContent = "最近 " + hours + " 小时";
    }
    function zoneColor(pid) { return (pid && ctx.cellColor(pid)) || ""; }
    function fill(el, key, html, shared) { if (el.__key !== key) { el.__key = key; el.innerHTML = html; } }

    function cardOf(g) {
      var c = cards[g.key];
      if (c) return c;
      c = document.createElement("div");
      c.className = "ag-card"; c.dataset.key = g.key;
      c.innerHTML = '<div class="ag-fig"></div><div class="ag-name"></div><div class="ag-lane"></div>';
      cards[g.key] = c; c.__top = null;
      return c;
    }
    function figure(c, g) {
      var f = ctx.figureOf(ctx.packOfTool(g.tool)), fig = c.firstChild;
      if (fig.__key === f.key) return;
      fig.__key = f.key; fig.innerHTML = f.html;
      var v = fig.querySelector("video");
      if (v) v.addEventListener("error", function () { fig.innerHTML = ctx.placeholder; });
    }
    function laneHtml(g, win, L) {
      var wide = win.hours > 24, tipDay = function (a) { return wide ? md(a) + " " : ""; }, html = "";
      focusSpans(g, win).forEach(function (s) {
        var r = Pr.spanRect(L, s.start, s.end);
        if (r) html += '<i class="ag-focus" style="left:' + pct(r.left) + ";width:" + pct(r.width) + '" title="' + esc(tipDay(s.start) + hm(s.start) + "–" + hm(s.end) + " 聚焦") + '"></i>';
      });
      segmentsOf(g).forEach(function (s) {
        var r = Pr.spanRect(L, s.start, s.end);
        if (!r) return;
        var pj = s.projectId ? ctx.projectName(s.projectId) : "", col = zoneColor(s.projectId);
        html += '<i class="ag-seg' + (col ? "" : " ag-neutral") + '" style="left:' + pct(r.left) + ";width:" + pct(r.width) + (col ? ";background:" + esc(col) : "") +
          '" title="' + esc(tipDay(s.start) + hm(s.start) + "–" + hm(s.end) + " " + g.name + (pj ? " " + pj : "")) + '"></i>';
      });
      return html;
    }
    // 画：卡片按键保留（视频不重放），按排序重排，换位走位移过渡
    function render(res, now) {
      var win = windowOf(mode, hours, now), L = Pr.rollingScale(win.from, win.to), runs = (res && res.agents) || [];
      var groups = rankGroups(groupRuns(runs), now), s = summary(groups, runs);
      sub.textContent = subtitle(win, s); sub.style.visibility = res ? "" : "hidden";
      var tk = ticks(win.from, win.to);
      ruler.innerHTML = tk.map(function (t) { return '<span class="ag-tick" style="left:' + pct(Pr.xOf(L, t.t)) + '">' + esc(t.label) + "</span>"; }).join("");
      plot.querySelectorAll(".ag-tickline").forEach(function (e) { e.remove(); });
      tk.forEach(function (t) { var i = document.createElement("i"); i.className = "ag-tickline"; i.style.left = pct(Pr.xOf(L, t.t)); plot.insertBefore(i, nowEl); });
      nowEl.firstChild.textContent = hm(now);
      var before = {};
      Object.keys(cards).forEach(function (k) { if (cards[k].isConnected) before[k] = cards[k].offsetTop; });
      var keep = {};
      groups.forEach(function (g, i) {
        var c = cardOf(g); keep[g.key] = 1;
        var col = zoneColor(currentProject(g));
        c.style.setProperty("--ag-c", col || "var(--ink-3)");
        c.classList.toggle("ag-neutral", !col);
        c.querySelector(".ag-name").textContent = g.name; c.querySelector(".ag-name").title = g.name;
        c.__group = g; figure(c, g);
        var h = laneHtml(g, win, L), lane = c.querySelector(".ag-lane");
        if (lane.__html !== h) { lane.__html = h; lane.innerHTML = h; }
        if (stack.children[i + 1] !== c) stack.insertBefore(c, stack.children[i + 1] || null);
      });
      Object.keys(cards).forEach(function (k) { if (!keep[k]) { cards[k].remove(); delete cards[k]; } });
      if (!reduced()) groups.forEach(function (g) {
        var c = cards[g.key], d = before[g.key] - c.offsetTop;
        if (before[g.key] != null && d && c.animate) c.animate([{ transform: "translateY(" + d + "px)" }, { transform: "none" }], { duration: SWAP_MS, easing: "ease-out" });
      });
    }

    var last = { res: null, now: Date.now() };
    function load() {
      var now = Date.now(), win = windowOf(mode, hours, now), my = ++seq;
      ctx.getJson("views/time-lanes?since=" + encodeURIComponent(new Date(win.from).toISOString())).then(function (res) {
        if (!opened || my !== seq || !res || !res.agents) return;
        last = { res: res, now: now }; render(res, now);
        if (flyPending) { flyPending = false; flyIn(); }
      });
    }

    // 飞：媒体的副本从 a 的位置飞到 b 的位置（缩放对齐中心）
    function flyer(srcMedia, from, to, done) {
      var el = srcMedia.cloneNode(true);
      el.classList.add("ag-flyer");
      el.style.cssText = "position:fixed;left:" + from.left + "px;top:" + from.top + "px;width:" + from.width + "px;height:" + from.height + "px;margin:0;";
      document.body.appendChild(el);
      var dx = to.left + to.width / 2 - from.left - from.width / 2, dy = to.top + to.height / 2 - from.top - from.height / 2, k = to.width / from.width || 1;
      flights++;
      var an = el.animate([{ transform: "none" }, { transform: "translate(" + dx + "px," + dy + "px) scale(" + k + ")" }], { duration: FLY_MS, easing: "cubic-bezier(.5,0,.2,1)", fill: "forwards" });
      an.onfinish = function () { el.remove(); flights--; done(); };
    }
    function petOf(g) {
      for (var i = g.runs.length - 1; i >= 0; i--) { var p = ctx.petEl(g.runs[i].runId); if (p && p.isConnected) return p; }
      return null;
    }
    function flyIn() {
      Object.keys(cards).forEach(function (k) {
        var c = cards[k], p = petOf(c.__group), src = p && p.querySelector(".st-media"), dst = c.querySelector(".ag-fig .st-media") || c.querySelector(".ag-fig");
        if (!src || !dst || reduced() || !src.animate) { if (p) { p.style.visibility = "hidden"; hidden.push(p); } return; }
        var from = src.getBoundingClientRect(), to = dst.getBoundingClientRect();
        p.style.visibility = "hidden"; hidden.push(p);
        c.classList.add("ag-pending");
        flyer(src, from, to, function () { c.classList.remove("ag-pending"); });
      });
    }
    function flyBack() {
      var list = hidden; hidden = [];
      var jobs = Object.keys(cards).map(function (k) { return cards[k]; }).filter(function (c) { return c.isConnected && c.__group; });
      jobs.forEach(function (c) {
        var p = petOf(c.__group), src = c.querySelector(".ag-fig .st-media"), dst = p && p.querySelector(".st-media");
        if (!p || !src || !dst || reduced() || !src.animate) return;
        var from = src.getBoundingClientRect(), to = dst.getBoundingClientRect(), idx = list.indexOf(p);
        if (idx < 0) return;
        list.splice(idx, 1);
        flyer(src, from, to, function () { p.style.visibility = ""; });
      });
      list.forEach(function (p) { p.style.visibility = ""; });
    }

    function open() {
      if (opened) return;
      opened = true; syncControls();
      veil.hidden = false; render(last.res, Date.now());   // 上次的卡片先摆着，新数据到了再换
      flyPending = true; load();
      if (Object.keys(cards).length) { flyPending = false; flyIn(); }
      timer = setInterval(load, REFRESH_MS);
      page.querySelector(".ag-x").focus();
    }
    function close() {
      if (!opened) return;
      opened = false; clearInterval(timer); seq++;
      flyBack(); veil.hidden = true;
    }
    function setMode(m) { mode = m; syncControls(); load(); }
    page.addEventListener("click", function (ev) {
      if (ev.target.closest(".ag-x")) close();
      var b = ev.target.closest("[data-mode]"); if (b) setMode(b.dataset.mode);
    });
    // 拖动时只改数字，松手才重读重画
    range.addEventListener("input", function () { hours = +range.value; out.textContent = "最近 " + hours + " 小时"; });
    range.addEventListener("change", function () { hours = +range.value; setMode("hours"); });
    veil.addEventListener("pointerdown", function (ev) { if (ev.target === veil) close(); });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && opened) close(); });
    return { open: open, close: close, isOpen: function () { return opened; }, redraw: function () { if (opened && last.res) render(last.res, last.now); } };
  }
  api.mount = mount;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexAgentPage = api;
})();
