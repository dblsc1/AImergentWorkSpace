// hive · hex-centre-stats.js —— 中心环（人 + 代理总计时）、左上角大肥鱼头像、统计页（hive.stage.v1「八」）。
// 纯函数（可在 node 里测）：fmtHours / efficiencyLine / peakParallel / totals / tickerLines / weekStart / rangeOf；
// 接线：mount(ctx) 画环、头像、飞入动画与统计页。
(function () {
  "use strict";
  var Pr = (typeof window !== "undefined" && window.NexusTableHexProjectRuler) || (typeof require !== "undefined" ? require("./hex-project-ruler.js") : null);
  var TICK_MS = 4000, FLY_MS = 500, FISH_PACK = "deepseek";

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function hm(t) { var d = new Date(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function md(t) { var d = new Date(t); return pad(d.getMonth() + 1) + "/" + pad(d.getDate()); }
  function ymd(t) { var d = new Date(t); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function pct(x) { return Math.round(x * 100000) / 1000 + "%"; }

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
  // 本周从周一起；返回周一 0 点的毫秒
  function weekStart(now) {
    var d = new Date(now), back = (d.getDay() + 6) % 7;
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() - back).getTime();
  }
  function rangeOf(range, now) {
    if (range === "week") { var a = weekStart(now); return { from: a, scale: Pr.linearScale(a, 7) }; }
    return { from: now, scale: Pr.linearScale(now, 1) };
  }

  var api = { fmtHours: fmtHours, efficiencyLine: efficiencyLine, peakParallel: peakParallel, totals: totals, tickerLines: tickerLines, weekStart: weekStart, rangeOf: rangeOf, TICK_MS: TICK_MS };

  // ── 接线 ─────────────────────────────────────────────
  // ctx: { layer, ring, clock, getJson, snapshot(now) → {humanNow, working, humanSec, focus}, projectName, cellColor, placeholder, base }
  function mount(ctx) {
    var today = null, todayAt = 0, mode = null, range = "day", pageLanes = null;
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
    ring.addEventListener("click", openMixedFlying);
    ring.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openMixedFlying(); } });
    var tk = ring.querySelector(".st-tk"), tkText = null;
    // 环缩得太小时轮播字看不清：挪到环下方的小条里（环里只留两段弧）
    var cap = document.createElement("div"); cap.className = "st-tk-cap"; cap.hidden = true; ring.parentNode.appendChild(cap);

    function renderRing(now) {
      if (now - todayAt > 15000) {
        todayAt = now;
        fetchLanes(now, now).then(function (r) { if (r && r.agents) { today = r; renderRing(Date.now()); } });
      }
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

    // 左上角大肥鱼头像：宠物包里 idle 的第一段视频停在第一帧；没有包就用占位形象
    var fish = document.createElement("button");
    fish.type = "button"; fish.className = "st-fish"; fish.id = "stFish"; fish.setAttribute("aria-label", "代理记录");
    fish.innerHTML = ctx.placeholder;
    ctx.layer.appendChild(fish);
    fetch(ctx.base + "hive/pets/" + FISH_PACK + "/pack.json").then(function (r) { return r.ok ? r.json() : null; }).then(function (p) {
      var moods = (p && p.moods) || {};
      var list = moods.idle && moods.idle.length ? moods.idle : [].concat.apply([], Object.keys(moods).map(function (k) { return moods[k]; }));
      var f = list.filter(function (x) { return typeof x === "string" && x.split("/").every(function (g) { return g && g !== "." && g !== ".."; }); })[0];
      if (f) fish.innerHTML = '<video src="' + esc(ctx.base + "hive/pets/" + FISH_PACK + "/" + f.split("/").map(encodeURIComponent).join("/")) + '#t=0.1" muted playsinline preload="metadata" aria-hidden="true"></video>';
    }).catch(function () { /* 没有素材：占位 */ });
    fish.addEventListener("click", function () { openPage("agents"); });

    // 飞入：时钟与头像的副本飞到中心合并（原件不动），再开统计页
    function openMixedFlying() {
      if (mode) return;
      var c = ring.getBoundingClientRect(), cx = c.left + c.width / 2, cy = c.top + c.height / 2, pending = 0;
      [ctx.clock, fish].forEach(function (src) {
        if (reduce || !src.animate) return;
        var r = src.getBoundingClientRect(), el = src.cloneNode(true);
        el.removeAttribute("id"); el.classList.add("st-fly");
        el.style.cssText = "position:fixed;left:" + r.left + "px;top:" + r.top + "px;width:" + r.width + "px;height:" + r.height + "px;margin:0;right:auto;zoom:1;";
        document.body.appendChild(el);
        pending++;
        var an = el.animate([{ transform: "none", opacity: 1 },
          { transform: "translate(" + (cx - r.left - r.width / 2) + "px," + (cy - r.top - r.height / 2) + "px) scale(.25)", opacity: 0.15 }],
          { duration: FLY_MS, easing: "cubic-bezier(.5,0,.2,1)", fill: "forwards" });
        an.onfinish = function () { el.remove(); if (--pending <= 0) openPage("mixed"); };
      });
      if (pending) mode = "flying"; else openPage("mixed");
    }

    // 统计页
    var veil = document.createElement("div");
    veil.className = "cs-veil"; veil.hidden = true; veil.id = "csVeil";
    veil.innerHTML = '<div class="cs-page st-card" id="csPage" role="dialog" aria-modal="true"></div>';
    document.body.appendChild(veil);
    var page = veil.firstChild, timer = 0;

    function openPage(m) {
      mode = m; range = "day"; pageLanes = null; page.__html = "";
      veil.hidden = false; veil.classList.toggle("cs-from-centre", !reduce);
      draw(); load();
      timer = setInterval(load, 5000);
      var x = page.querySelector(".cs-x"); if (x) x.focus();
    }
    function close() { if (!mode || mode === "flying") return; mode = null; veil.hidden = true; clearInterval(timer); }
    function load() {
      var now = Date.now(), key = range;
      fetchLanes(rangeOf(range, now).from, now).then(function (res) { if (mode && range === key && res && res.agents) { pageLanes = res; draw(); } });
    }
    function zoneColor(pid) { return (pid && ctx.cellColor(pid)) || "var(--accent)"; }
    function seg(L, s, who, color, withDate) {
      var a = Date.parse(s.start), b = Date.parse(s.end), r = Pr.spanRect(L, a, b);
      if (!r) return "";
      var pj = s.projectId ? ctx.projectName(s.projectId) : "";
      var tip = (withDate ? md(a) + " " : "") + hm(a) + "–" + hm(b) + " " + who + (pj ? " " + pj : "");
      return '<i class="cs-seg" style="left:' + pct(r.left) + ";width:" + pct(r.width) + ";background:" + esc(color) + '" title="' + esc(tip) + '"></i>';
    }
    function ruler(L) {
      var WEEK = ["日", "一", "二", "三", "四", "五", "六"];
      if (L.days.length === 1) return [0, 6, 12, 18, 24].map(function (h) { return '<span class="cs-tick" style="left:' + pct(h / 24) + '">' + pad(h) + "</span>"; }).join("");
      return L.days.map(function (d) { return '<span class="cs-tick cs-day" style="left:' + pct(d.x0) + ";width:" + pct(d.w) + '">周' + WEEK[new Date(d.start).getDay()] + "</span>"; }).join("");
    }
    function grid(L) { return L.days.map(function (d) { return '<i class="cs-grid" style="left:' + pct(d.x0) + '"></i>'; }).join(""); }
    function draw() {
      if (!mode || mode === "flying") return;
      var L = rangeOf(range, Date.now()).scale, d = pageLanes, mixed = mode === "mixed", withDate = L.days.length > 1;
      var tt = totals(d), eff = efficiencyLine(tt.humanSec, tt.agentSec);
      var head = (mixed ? "你 " + fmtHours(tt.humanSec) + " · " : "") + "代理 " + fmtHours(tt.agentSec) + (mixed && eff ? " · " + eff.line : "") + " · 代理并行峰值 " + tt.peak;
      var rows = "";
      if (mixed) rows += '<div class="cs-row cs-me"><div class="cs-who">你</div><div class="cs-lane">' + grid(L) + ((d && d.human) || []).map(function (s) { return seg(L, s, "你", "var(--pr-human)", withDate); }).join("") + "</div></div>";
      ((d && d.agents) || []).forEach(function (a) {
        var name = a.name || a.agent || "代理";
        rows += '<div class="cs-row"><div class="cs-who" title="' + esc(name) + '">' + esc(name) + '</div><div class="cs-lane">' + grid(L) +
          (a.segments || []).map(function (s) { return seg(L, s, name, zoneColor(s.projectId), withDate); }).join("") + "</div></div>";
      });
      var html = '<div class="cs-hd"><h2>' + (mixed ? "你 + 代理" : "代理") + '</h2><div class="cs-range" role="group">' +
        ["day", "week"].map(function (k) { return '<button type="button" data-range="' + k + '"' + (k === range ? ' class="on"' : "") + ">" + (k === "day" ? "今天" : "本周") + "</button>"; }).join("") +
        '</div><button type="button" class="st-x cs-x" aria-label="关闭">✕</button></div><div class="cs-sum">' + esc(head) + '</div>' +
        '<div class="cs-row cs-axis"><div class="cs-who"></div><div class="cs-ruler">' + ruler(L) + '</div></div><div class="cs-rows">' + rows + "</div>";
      if (html !== page.__html) { page.__html = html; page.innerHTML = html; if (document.activeElement === document.body) { var x = page.querySelector(".cs-x"); if (x) x.focus(); } }
    }
    page.addEventListener("click", function (ev) {
      if (ev.target.closest(".cs-x")) close();
      var b = ev.target.closest("[data-range]");
      if (b && b.dataset.range !== range) { range = b.dataset.range; pageLanes = null; draw(); load(); }
    });
    veil.addEventListener("pointerdown", function (ev) { if (ev.target === veil) close(); });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && mode) close(); });

    return { renderRing: renderRing, close: close };
  }
  api.mount = mount;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexCentreStats = api;
})();
