// hive · hex-centre-stats.js —— 中心环（人 + 代理总计时）、左上角大肥鱼头像（hive.stage.v1「八」）。
// 纯函数（可在 node 里测）：fmtHours / efficiencyLine / peakParallel / totals / tickerLines；
// 接线：mount(ctx) 画环、头像（点开 Agent 页，「十三」）；点环 = 人 + agent 混合泳道页（hex-agent-page.js 的 mixed，「十七」）。
(function () {
  "use strict";
  var AP = function () { return window.NexusTableHexAgentPage; };   // 点头像开的 Agent 页（hex-agent-page.js，「十三」）
  var TICK_MS = 8000,   /* 仓主 2026-11-15：轮播太快，4 秒 → 8 秒（「三十三」4）*/  FISH_PACK = "deepseek", WEEK_MS = 7 * 24 * 3600000, FISH_REFRESH_MS = 300000;

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function ymd(t) { var d = new Date(t); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  // 60 分钟以下写分钟，以上写小时（一位小数，整数不带 .0）
  function fmtHours(sec) {
    var m = Math.round(Math.max(0, sec) / 60);
    if (m < 60) return m + " 分钟";
    return Math.round(m / 6) / 10 + " 小时";
  }
  // 环里的短写法（「三十三」）：不满 1 小时写「25m」，满了写「2.8h」
  function fmtShort(sec) {
    var m = Math.round(Math.max(0, sec) / 60);
    return m < 60 ? m + "m" : Math.round(m / 6) / 10 + "h";
  }
  // 效率 = 今天代理干活时间 ÷ 你专注的时间，写成「你 1 小时，带出代理 3.2 小时的活」；没有可比的数据 = null
  function efficiencyLine(humanSec, agentSec) {
    if (!(humanSec >= 60) || !(agentSec > 0)) return null;
    return { ratio: agentSec / humanSec, line: "你 1 小时，带出代理 " + Math.round(agentSec / humanSec * 10) / 10 + " 小时的活" };
  }
  function secOf(list) { return (list || []).reduce(function (n, s) { return n + Math.max(0, Date.parse(s.end) - Date.parse(s.start)); }, 0) / 1000; }
  // 代理同时在干活的最大条数（按运行数；同一条运行首尾相接的段不叠）
  function peakParallel(agents) {
    var ev = [];
    (agents || []).forEach(function (a) { (a.segments || []).forEach(function (s) { ev.push([Date.parse(s.start), 1], [Date.parse(s.end), -1]); }); });
    ev.sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
    var n = 0, max = 0;
    ev.forEach(function (e) { n += e[1]; if (n > max) max = n; });
    return max;
  }
  function totals(lanes) {
    var agent = ((lanes && lanes.agents) || []).reduce(function (n, a) { return n + secOf(a.segments); }, 0);
    return { humanSec: secOf(lanes && lanes.human), agentSec: agent, peak: peakParallel(lanes && lanes.agents) };
  }
  // 中心环里轮播的短句：s = {humanNow, working, humanSec, agentSec, focus:{name, text}|null}
  // 每句带它的种类（环上画什么弧跟着它走，「二十七」B）：human / parallel / efficiency / totals / agent
  function tickerEntries(s) {
    var out = [], eff = efficiencyLine(s.humanSec, s.agentSec);
    if (s.humanNow) out.push({ kind: "human", text: s.humanNow });
    if (s.working > 0) out.push({ kind: "parallel", text: s.working + " 个代理并行" });
    if (eff) out.push({ kind: "efficiency", text: "效率 ×" + Math.round(eff.ratio * 10) / 10 });
    out.push({ kind: "totals", text: "你 " + fmtShort(s.humanSec) + "\n代理 " + fmtShort(s.agentSec) });
    if (s.focus) out.push({ kind: "agent", text: s.focus.name + "\n" + s.focus.text });
    return out;
  }
  function tickerLines(s) { return tickerEntries(s).map(function (e) { return e.text; }); }

  // 环上的弧（「二十七」B）：按轮播到的那一句画。s = {humanSec, agentSec, agents:[{sec, color}], workers:[{color}], focusColor}；
  // 返回 [{color, share}]，share 为占整圈的比例（总和 ≤ 1）。没有颜色的用中性色
  var HUMAN_COLOR = "var(--pr-human)", AGENT_COLOR = "var(--accent)", NEUTRAL_COLOR = "var(--ink-3)", MAX_AGENT_ARCS = 6;
  function ringArcs(kind, s) {
    var out = [], sum;
    if (kind === "totals") {
      var ag = (s.agents || []).filter(function (a) { return a.sec > 0; }).sort(function (a, b) { return b.sec - a.sec; });
      sum = s.humanSec + ag.reduce(function (n, a) { return n + a.sec; }, 0);
      if (!(sum > 0)) return [];
      if (s.humanSec > 0) out.push({ color: HUMAN_COLOR, share: s.humanSec / sum });
      var keep = ag.length > MAX_AGENT_ARCS ? ag.slice(0, MAX_AGENT_ARCS - 1) : ag, rest = ag.slice(keep.length);
      keep.forEach(function (a) { out.push({ color: a.color || NEUTRAL_COLOR, share: a.sec / sum }); });
      if (rest.length) out.push({ color: NEUTRAL_COLOR, share: rest.reduce(function (n, a) { return n + a.sec; }, 0) / sum });
      return out;
    }
    if (kind === "parallel") return (s.workers || []).slice(0, 12).map(function (w, _, all) { return { color: w.color || NEUTRAL_COLOR, share: 1 / all.length }; });
    if (kind === "agent") return [{ color: s.focusColor || NEUTRAL_COLOR, share: 1 }];
    sum = s.humanSec + s.agentSec;
    if (!(sum > 0)) return [];
    return [{ color: HUMAN_COLOR, share: s.humanSec / sum }, { color: AGENT_COLOR, share: s.agentSec / sum }];
  }
  // 环卡的行（「二十七」7）：s = {humanSec, agentSec, workers:[{name, project}], waiting, peak}
  function ringCardLines(s) {
    var C = (typeof window !== "undefined" && window.HoneycombClock) || require("../../../nginx-docker/static/clockface.js"), dur = C.dur;
    var out = [{ text: "今天：你 " + dur(s.humanSec) + " · 代理 " + dur(s.agentSec) }], w = s.workers || [];
    if (!w.length) out.push({ text: "现在没有 agent 在干活" });
    else {
      out.push({ text: "现在 " + w.length + " 个 agent 在干活" });
      w.slice(0, 3).forEach(function (x) { out.push({ text: [x.name, x.project].filter(Boolean).join(" · ") }); });
      if (w.length > 3) out.push({ text: "+" + (w.length - 3) });
    }
    if (s.waiting > 0) out.push({ text: s.waiting + " 个在等你" });
    if (s.peak > 0) out.push({ text: "今天代理并行峰值 " + s.peak });
    return out;
  }
  var api = { fmtHours: fmtHours, fmtShort: fmtShort, efficiencyLine: efficiencyLine, peakParallel: peakParallel, totals: totals, tickerLines: tickerLines, tickerEntries: tickerEntries, ringArcs: ringArcs, ringCardLines: ringCardLines, TICK_MS: TICK_MS };

  // ── 接线 ─────────────────────────────────────────────
  // ctx: { layer, ring, clock, getJson, snapshot(now) → {humanNow, working, humanSec, focus}, projectName, cellColor, placeholder, base }
  function mount(ctx) {
    var today = null, todayAt = 0, todayAgentSec = 0, todayPeak = 0, todayAgents = [];
    var reduce = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

    function fetchLanes(from, to) { return ctx.getJson("views/time-lanes?from=" + ymd(from) + "&to=" + ymd(to)); }

    // 中心环：今天的人 / 代理占比两段弧 + 轮播短句
    var ring = ctx.ring;
    ring.innerHTML = '<svg viewBox="0 0 200 200" aria-hidden="true"><circle cx="100" cy="100" r="96" fill="var(--panel)"/>' +
      '<circle cx="100" cy="100" r="86" fill="none" stroke="var(--panel-2)" stroke-width="12"/>' +
      '</svg>' +
      '<div class="st-face"><div class="st-tk"></div></div>';
    ring.setAttribute("role", "button"); ring.setAttribute("tabindex", "0"); ring.setAttribute("aria-label", "统计");
    ring.addEventListener("click", function () { centrePage.open(); });
    ring.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); centrePage.open(); } });
    var tk = ring.querySelector(".st-tk"), tkText = null, lastSnap = null;
    // 悬停环出粗略信息（「二十七」7）：中心页 / Agent 页开着时不出
    var ringHover = window.HoneycombClock && window.HoneycombClock.attach(ring, { side: "beside",
      blocked: function () { return agentPage.isOpen() || centrePage.isOpen(); },
      lines: function () { return ringCardLines({ humanSec: lastSnap.humanSec, agentSec: todayAgentSec, workers: lastSnap.workers, waiting: lastSnap.waiting, peak: todayPeak }); } });

    // 一条代理今天的用时与它最近所在的项目（环上的弧取这个项目的颜色，同 Agent 页卡片的 --ag-c）
    function agentToday(a) {
      var segs = a.segments || [], pid = null;
      segs.forEach(function (s) { if (s.projectId) pid = s.projectId; });
      return { sec: secOf(segs), pid: pid };
    }
    function colorOf(pid) { return (pid && ctx.cellColor(pid)) || ""; }
    var arcEls = [];
    for (var ai = 0; ai < 14; ai++) {
      var arcEl = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      arcEl.setAttribute("class", "st-arc"); arcEl.setAttribute("cx", "100"); arcEl.setAttribute("cy", "100"); arcEl.setAttribute("r", "86"); arcEl.setAttribute("fill", "none");
      arcEl.setAttribute("stroke-width", "12"); arcEl.setAttribute("pathLength", "100"); arcEl.setAttribute("stroke-dasharray", "0 100"); arcEl.setAttribute("transform", "rotate(-90 100 100)");
      ring.querySelector("svg").appendChild(arcEl); arcEls.push(arcEl);
    }
    function paintArcs(arcs) {
      var at = 0;
      arcEls.forEach(function (c, i) {
        var a = arcs[i];
        if (!a) { c.setAttribute("stroke-dasharray", "0 100"); return; }
        var len = Math.max(0, a.share * 100 - (arcs.length > 1 ? 0.6 : 0));
        c.setAttribute("stroke", a.color); c.setAttribute("stroke-dasharray", len + " 100"); c.setAttribute("stroke-dashoffset", String(-at));
        at += a.share * 100;
      });
      ring.dataset.arcs = JSON.stringify(arcs);
    }
    function renderRing(now) {
      if (now - todayAt > 15000) {
        todayAt = now;
        fetchLanes(now, now).then(function (r) { if (r && r.agents) { today = r; var tt = totals(r); todayAgentSec = tt.agentSec; todayPeak = tt.peak; todayAgents = (r.agents || []).map(agentToday); renderRing(Date.now()); } });
      }
      refreshFish(now);
      var snap = lastSnap = ctx.snapshot(now), agentSec = todayAgentSec;   // 合计只在读到新数据时算一次：每秒重算（逐段 Date.parse）是空闲时的大头
      var entries = tickerEntries({ humanNow: snap.humanNow, working: snap.working, humanSec: snap.humanSec, agentSec: agentSec, focus: snap.focus });
      var lines = entries.map(function (e) { return e.text; }), cur = entries[Math.floor(now / TICK_MS) % entries.length], text = cur.text;
      ring.dataset.lines = JSON.stringify(lines);
      if (text !== tkText) {
        tkText = text; tk.textContent = text;
        if (!reduce) { tk.classList.remove("swap"); void tk.offsetWidth; tk.classList.add("swap"); }
      }
      paintArcs(ringArcs(cur.kind, { humanSec: snap.humanSec, agentSec: agentSec, agents: todayAgents.map(function (a) { return { sec: a.sec, color: colorOf(a.pid) }; }),
        workers: (snap.workers || []).map(function (w) { return { color: colorOf(w.projectId) }; }), focusColor: snap.focus && colorOf(snap.focus.projectId) }));
      if (ringHover) ringHover.update();
    }

    // 左上角头像：最近 7 天干活最久的那个 tool 的宠物包（没有数据用缺省包），idle 第一段视频停在第一帧、放大后只露头；没有包用占位形象
    var fish = document.createElement("button");
    fish.type = "button"; fish.className = "st-fish"; fish.id = "stFish"; fish.setAttribute("aria-label", "Agent");
    fish.innerHTML = ctx.placeholder;
    ctx.layer.appendChild(fish);
    var weekRuns = null, weekAt = 0, weekTool = null;
    function paintFish(kind) {
      fish.dataset.pack = kind; fish.innerHTML = ctx.placeholder;
      fetch(ctx.base + "hive/pets/" + encodeURIComponent(kind) + "/pack.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (p) {
        if (fish.dataset.pack !== kind) return;
        var moods = (p && p.moods) || {}, ok = function (x) { return typeof x === "string" && x.split("/").every(function (g) { return g && g !== "." && g !== ".."; }); };
        var list = moods.idle && moods.idle.length ? moods.idle : [].concat.apply([], Object.keys(moods).map(function (k) { return moods[k]; }));
        var f = list.filter(ok)[0], base = ctx.base + "hive/pets/" + encodeURIComponent(kind) + "/";
        if (f) fish.innerHTML = '<video src="' + esc(base + f.split("/").map(encodeURIComponent).join("/")) + '#t=0.1" muted playsinline preload="metadata" aria-hidden="true"></video>';
        else if (p && ok(p.image)) fish.innerHTML = '<img alt="" src="' + esc(base + p.image.split("/").map(encodeURIComponent).join("/")) + '">';
      }).catch(function () { /* 没有素材：占位 */ });
    }
    function refreshFish(now) {
      if (now - weekAt > FISH_REFRESH_MS) {
        weekAt = now;
        ctx.getJson("views/time-lanes?since=" + encodeURIComponent(new Date(now - WEEK_MS).toISOString())).then(function (r) { if (r && r.agents) { weekRuns = r.agents; weekTool = AP().topTool(weekRuns); } });
      }
      var kind = weekTool ? ctx.packOfTool(weekTool) : FISH_PACK;   // 最常用的 tool 只在读到新数据时算一次
      if (fish.dataset.pack !== kind) paintFish(kind);
    }
    fish.addEventListener("click", function () { agentPage.open(); });
    var agentPage = AP().mount(ctx);

    var centrePage = AP().mount(ctx, { mixed: true });

    return { renderRing: renderRing, openAgent: function (runId) { centrePage.close(); agentPage.openRun(runId); }, close: function () { agentPage.close(); centrePage.close(); }, redraw: function () { agentPage.redraw(); centrePage.redraw(); } };
  }
  api.mount = mount;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexCentreStats = api;
})();
