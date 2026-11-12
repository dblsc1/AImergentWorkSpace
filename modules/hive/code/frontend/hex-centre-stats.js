// hive · hex-centre-stats.js —— 中心环（人 + 代理总计时）、左上角大肥鱼头像（hive.stage.v1「八」）。
// 纯函数（可在 node 里测）：fmtHours / efficiencyLine / peakParallel / totals / tickerLines；
// 接线：mount(ctx) 画环、头像（点开 Agent 页，「十三」）；点环 = 人 + agent 混合泳道页（hex-agent-page.js 的 mixed，「十七」）。
(function () {
  "use strict";
  var AP = function () { return window.NexusTableHexAgentPage; };   // 点头像开的 Agent 页（hex-agent-page.js，「十三」）
  var TICK_MS = 4000, FISH_PACK = "deepseek", WEEK_MS = 7 * 24 * 3600000, FISH_REFRESH_MS = 300000;

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function ymd(t) { var d = new Date(t); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  // 60 分钟以下写分钟，以上写小时（一位小数，整数不带 .0）
  function fmtHours(sec) {
    var m = Math.round(Math.max(0, sec) / 60);
    if (m < 60) return m + " 分钟";
    return Math.round(m / 6) / 10 + " 小时";
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
  function tickerLines(s) {
    var out = [], eff = efficiencyLine(s.humanSec, s.agentSec);
    if (s.humanNow) out.push(s.humanNow);
    if (s.working > 0) out.push(s.working + " 个代理并行");
    if (eff) out.push(eff.line);
    out.push("你 " + fmtHours(s.humanSec) + "\n代理 " + fmtHours(s.agentSec));
    if (s.focus) out.push(s.focus.name + "\n" + s.focus.text);
    return out;
  }
  var api = { fmtHours: fmtHours, efficiencyLine: efficiencyLine, peakParallel: peakParallel, totals: totals, tickerLines: tickerLines, TICK_MS: TICK_MS };

  // ── 接线 ─────────────────────────────────────────────
  // ctx: { layer, ring, clock, getJson, snapshot(now) → {humanNow, working, humanSec, focus}, projectName, cellColor, placeholder, base }
  function mount(ctx) {
    var today = null, todayAt = 0;
    var reduce = !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);

    function fetchLanes(from, to) { return ctx.getJson("views/time-lanes?from=" + ymd(from) + "&to=" + ymd(to)); }

    // 中心环：今天的人 / 代理占比两段弧 + 轮播短句
    var ring = ctx.ring;
    ring.innerHTML = '<svg viewBox="0 0 200 200" aria-hidden="true"><circle cx="100" cy="100" r="96" fill="var(--panel)"/>' +
      '<circle cx="100" cy="100" r="86" fill="none" stroke="var(--panel-2)" stroke-width="12"/>' +
      '<circle class="st-arc st-arc-h" cx="100" cy="100" r="86" fill="none" stroke="var(--pr-human)" stroke-width="12" pathLength="100" stroke-dasharray="0 100" transform="rotate(-90 100 100)"/>' +
      '<circle class="st-arc st-arc-a" cx="100" cy="100" r="86" fill="none" stroke="var(--accent)" stroke-width="12" pathLength="100" stroke-dasharray="0 100" transform="rotate(-90 100 100)"/></svg>' +
      '<div class="st-face"><div class="st-tk"></div></div>';
    ring.setAttribute("role", "button"); ring.setAttribute("tabindex", "0"); ring.setAttribute("aria-label", "统计");
    ring.addEventListener("click", function () { centrePage.open(); });
    ring.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); centrePage.open(); } });
    var tk = ring.querySelector(".st-tk"), tkText = null;
    // 环缩得太小时轮播字看不清：挪到环下方的小条里（环里只留两段弧）
    var cap = document.createElement("div"); cap.className = "st-tk-cap"; cap.hidden = true; ring.parentNode.appendChild(cap);

    function renderRing(now) {
      if (now - todayAt > 15000) {
        todayAt = now;
        fetchLanes(now, now).then(function (r) { if (r && r.agents) { today = r; renderRing(Date.now()); } });
      }
      refreshFish(now);
      var snap = ctx.snapshot(now), agentSec = today ? totals(today).agentSec : 0;
      var lines = tickerLines({ humanNow: snap.humanNow, working: snap.working, humanSec: snap.humanSec, agentSec: agentSec, focus: snap.focus });
      var text = lines[Math.floor(now / TICK_MS) % lines.length];
      ring.dataset.lines = JSON.stringify(lines);
      var k = +(ring.dataset.k || 1), small = k < 0.7;
      tk.style.visibility = small ? "hidden" : "";
      cap.hidden = !small;
      if (small) {
        cap.style.left = ring.style.left;
        cap.style.top = (parseFloat(ring.style.top) || 0) + 70 * k + 4 + "px";
        cap.textContent = String(text || "").replace(/\n/g, " · ");
      }
      if (text !== tkText) {
        tkText = text; tk.textContent = text;
        if (!reduce) { tk.classList.remove("swap"); void tk.offsetWidth; tk.classList.add("swap"); }
      }
      var sum = snap.humanSec + agentSec, h = sum ? snap.humanSec / sum * 100 : 0;
      ring.querySelector(".st-arc-h").setAttribute("stroke-dasharray", h + " 100");
      var a = ring.querySelector(".st-arc-a");
      a.setAttribute("stroke-dasharray", (sum ? 100 - h : 0) + " 100"); a.setAttribute("stroke-dashoffset", String(-h));
    }

    // 左上角头像：最近 7 天干活最久的那个 tool 的宠物包（没有数据用缺省包），idle 第一段视频停在第一帧、放大后只露头；没有包用占位形象
    var fish = document.createElement("button");
    fish.type = "button"; fish.className = "st-fish"; fish.id = "stFish"; fish.setAttribute("aria-label", "Agent");
    fish.innerHTML = ctx.placeholder;
    ctx.layer.appendChild(fish);
    var weekRuns = null, weekAt = 0;
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
        ctx.getJson("views/time-lanes?since=" + encodeURIComponent(new Date(now - WEEK_MS).toISOString())).then(function (r) { if (r && r.agents) weekRuns = r.agents; });
      }
      var tool = AP().topTool(weekRuns), kind = tool ? ctx.packOfTool(tool) : FISH_PACK;
      if (fish.dataset.pack !== kind) paintFish(kind);
    }
    fish.addEventListener("click", function () { agentPage.open(); });
    var agentPage = AP().mount(ctx);

    var centrePage = AP().mount(ctx, { mixed: true });

    return { renderRing: renderRing, close: function () { agentPage.close(); centrePage.close(); }, redraw: function () { agentPage.redraw(); centrePage.redraw(); } };
  }
  api.mount = mount;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexCentreStats = api;
})();
