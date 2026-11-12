// hive · hex-agent-page.js —— 左上角 Agent 页：一个 agent 一张卡（hive.stage.v1「十三」）；
// mount(ctx, {mixed: true}) = 点中心环打开的「人 + agent 混合泳道」页：同一套卡片，最上面多一条人的泳道（「十七」）。
// 纯函数（node 里测）：windowOf / scaleOf / pickRange / groupRuns / segmentsOf / focusSpans / currentProject / rankGroups / ticks / dayTicks / summary / subtitle / mixedSubtitle / topTool / statusText / sourceText / durText / historyOf / placeOptions / splitRetained（卡片详情与只列活跃，「二十」）；
// 接线：mount(ctx, opts) 建页面、读 views/time-lanes?since=、画卡片、换位过渡、飞入飞回。
(function () {
  "use strict";
  var Pr = (typeof window !== "undefined" && window.NexusTableHexProjectRuler) || (typeof require !== "undefined" ? require("./hex-project-ruler.js") : null);
  var Cs = (typeof window !== "undefined" && window.NexusTableHexCentreStats) || (typeof require !== "undefined" ? require("./hex-centre-stats.js") : null);
  var HOUR = 3600000, RANK_MS = 3 * HOUR, REFRESH_MS = 15000, FLY_MS = 500, SWAP_MS = 300, DETAIL_MS = 300, DEFAULT_HOURS = 3;
  var WEEK = ["日", "一", "二", "三", "四", "五", "六"];

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function hm(t) { var d = new Date(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function md(t) { var d = new Date(t); return pad(d.getMonth() + 1) + "/" + pad(d.getDate()); }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function pct(x) { return Math.round(x * 100000) / 1000 + "%"; }
  function ms(s) { return Date.parse(s); }

  // ── 纯函数 ───────────────────────────────────────────
  // 范围（「十七」2）：默认 = 7 天不等宽标尺 Pr.layout(now)，日 / 周 / 最近 x 小时都不选中；
  // 日 / 周 / 拖动条 = 滚动窗口 [now − 范围, now]（等宽）：日 24 小时，周 7×24 小时，拖动条 1–24 小时
  function windowOf(mode, hours, now) {
    if (mode === "default") { var L = Pr.layout(now); return { mode: mode, hours: (L.to - L.from) / HOUR, from: L.from, to: L.to, now: now, label: "最近 7 天" }; }
    var h = mode === "day" ? 24 : mode === "week" ? 168 : Math.min(24, Math.max(1, Math.round(hours) || DEFAULT_HOURS));
    return { mode: mode, hours: h, from: now - h * HOUR, to: now, label: mode === "day" ? "今天" : mode === "week" ? "最近 7 天" : "最近 " + h + " 小时", now: now };
  }
  function scaleOf(win) { return win.mode === "default" ? Pr.layout(win.now) : Pr.rollingScale(win.from, win.to); }
  // 点了一个范围按钮：已选中的再点一次 = 回默认
  function pickRange(cur, clicked) { return clicked === cur ? "default" : clicked; }
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
  // 默认标尺的刻度：每天一格（今天写「今天」，其余写周几），x / w 取自标尺
  function dayTicks(L) {
    return L.days.map(function (d) { return { x: d.x0, w: d.w, label: d.today ? "今天" : "周" + WEEK[new Date(d.start).getDay()] }; });
  }
  function summary(groups, runs) {
    var tt = Cs.totals({ agents: runs });
    return { sec: tt.agentSec, peak: tt.peak, working: groups.filter(function (g) { return g.runs.some(function (r) { return live(r) && r.phase === "working"; }); }).length };
  }
  function fmtHm(sec) { var m = Math.round(Math.max(0, sec) / 60); return Math.floor(m / 60) + " 小时 " + m % 60 + " 分钟"; }
  function subtitle(win, s) { return "agent " + win.label + "工作了 " + fmtHm(s.sec) + "，并行峰值 " + s.peak + "，现在有 " + s.working + " 个正在工作"; }
  // 混合页小字（「十七」1）：你 x · 代理 y [· 效率] · 代理并行峰值 n · 现在有 m 个正在工作
  function mixedSubtitle(humanSec, s) {
    var eff = Cs.efficiencyLine(humanSec, s.sec);
    return "你 " + Cs.fmtHours(humanSec) + " · 代理 " + Cs.fmtHours(s.sec) + (eff ? " · " + eff.line : "") + " · 代理并行峰值 " + s.peak + " · 现在有 " + s.working + " 个正在工作";
  }
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

  // ── 卡片详情（「二十」）──────────────────────────────
  var PHASE_TEXT = { working: "正在工作", waiting_input: "等输入", waiting_permission: "等授权", idle: "空闲", error: "出错" };
  function statusText(r) { return !r.running ? "已结束" : r.lost ? "失联" : PHASE_TEXT[r.phase] || "空闲"; }
  var BEAT_TEXT = { wrapper: "命令包装器心跳", companion: "伴随进程心跳", monitor: "监视进程心跳" };
  // 来源：运行只能由钩子 / 适配器经 agents/start 开；心跳标签与「人聚焦的窗口认出了它的会话」(attend) 是能从 time-lanes 读到的其余线索
  function sourceText(r) {
    var out = [r.unverified ? "agent 钩子上报（未验证）" : "agent 钩子上报"];
    if (r.beatSource) out.push(BEAT_TEXT[r.beatSource] || "心跳 " + r.beatSource);
    if (r.attend && r.attend.length) out.push("窗口识别");
    return out.join(" · ");
  }
  function durText(sec) { var m = Math.floor(Math.max(0, sec) / 60), h = Math.floor(m / 60); return h ? h + " 小时 " + m % 60 + " 分钟" : m + " 分钟"; }
  // 一个 agent 卡里每条运行 → 一张小卡的数据，开始晚的在前。失联的运行时长算到最后一段干活区间的终点
  function historyOf(group, now) {
    return group.runs.map(function (r) {
      var start = r.startedAt ? ms(r.startedAt) : firstStart(r), end = r.running ? null : r.endedAt ? ms(r.endedAt) : lastEnd(r) || null;
      var stop = end != null ? end : r.lost && lastEnd(r) ? lastEnd(r) : now, segs = r.segments || [];
      return { runId: r.runId, start: start, end: end, sec: Math.max(0, (stop - start) / 1000), status: statusText(r), source: sourceText(r),
        projectId: r.projectId || (segs.length && segs[segs.length - 1].projectId) || null, taskId: r.taskId || null };
    }).sort(function (a, b) { return b.start - a.start; });
  }
  // 只列活跃的（「二十」5）：一张卡里有一条运行 retained（后端按 lane_active 口径，v2.58）就列出；一条都没有的收进「其余 N 个」。已排好序的顺序不变
  function splitRetained(groups) {
    var shown = [], rest = [];
    groups.forEach(function (g) { (g.runs.some(function (r) { return r.retained !== false; }) ? shown : rest).push(g); });
    return { shown: shown, rest: rest };
  }
  // 归属下拉：项目全列；任务 = 所选项目没做完的任务（不含「未分类」桶），第一项「不挂任务」；当前任务已完成也留着
  function placeOptions(projects, projectId, taskId) {
    var list = projects.map(function (p) { return { id: p.id, name: p.name }; });
    if (projectId && !list.some(function (p) { return p.id === projectId; })) list.unshift({ id: projectId, name: projectId });
    var pj = projects.filter(function (p) { return p.id === projectId; })[0], tasks = [{ id: "", name: "不挂任务" }];
    ((pj && pj.tasks) || []).forEach(function (t) { if ((!t.done && t.id !== pj.unclassifiedTaskId) || t.id === taskId) tasks.push({ id: t.id, name: t.name }); });
    return { projects: list, project: projectId || "", tasks: tasks, task: taskId && tasks.some(function (t) { return t.id === taskId; }) ? taskId : "" };
  }

  var api = { splitRetained: splitRetained, statusText: statusText, sourceText: sourceText, durText: durText, historyOf: historyOf, placeOptions: placeOptions, windowOf: windowOf, scaleOf: scaleOf, pickRange: pickRange, dayTicks: dayTicks, mixedSubtitle: mixedSubtitle, groupRuns: groupRuns, segmentsOf: segmentsOf, focusSpans: focusSpans, currentProject: currentProject, rankGroups: rankGroups,
    ticks: ticks, summary: summary, subtitle: subtitle, topTool: topTool, fmtHm: fmtHm, DEFAULT_HOURS: DEFAULT_HOURS, REFRESH_MS: REFRESH_MS };

  // ── 接线 ─────────────────────────────────────────────
  // ctx: { getJson, cellColor(pid), projectName(pid), packOfTool(tool), figureOf(pack) → {key, html}, petEl(runId) → .st-pet | null, placeholder, getLanes()（混合页要正在跑的计时）,
  //        projectChoices() → [{id, name, unclassifiedTaskId, tasks:[{id, name, done}]}], place(runId, agent, projectId, taskId) → Promise<boolean>（失败页面自己提示）, fillFigure(slot, pack, phase, lost)（大立绘） }
  // opts.mixed：中心页（「十七」），页首多一条人的泳道；ids 前缀 cp，Agent 页前缀 ag
  function mount(ctx, opts) {
    var mixed = !!(opts && opts.mixed), ID = mixed ? "cp" : "ag", HP = function () { return window.HexHumanPage; };
    var mode = "default", hours = DEFAULT_HOURS, opened = false, timer = 0, seq = 0, cards = {}, flights = 0, hidden = [], flyPending = false;
    var reduced = function () { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches); };
    var veil = document.createElement("div");
    veil.className = "ag-veil"; veil.id = ID + "Veil"; veil.hidden = true;
    var title = mixed ? "你 + 代理" : "Agent";
    veil.innerHTML = '<div class="ag-page st-card" id="' + ID + 'Page" role="dialog" aria-modal="true" aria-label="' + title + '">' +
      '<div class="ag-hd"><div class="ag-title"><h2 class="ag-h"><b>' + title + '</b></h2><div class="ag-sub" id="' + ID + 'Sub"></div></div>' +
      '<div class="ag-range" role="group"><button type="button" data-mode="day">日</button><button type="button" data-mode="week">周</button>' +
      '<label class="ag-hours"><input type="range" id="' + ID + 'Hours" min="1" max="24" step="1" aria-label="最近几小时"><output id="' + ID + 'HoursOut"></output></label></div>' +
      '<button type="button" class="st-x ag-x" aria-label="关闭">✕</button></div>' +
      '<div class="ag-axis"><div class="ag-ruler" id="' + ID + 'Ruler"></div></div>' +
      '<div class="ag-scroll"><div class="ag-stack" id="' + ID + 'Stack"><div class="ag-plot" id="' + ID + 'Plot"><div class="ag-now" id="' + ID + 'Now"><span></span></div></div></div></div></div>';
    document.body.appendChild(veil);
    var q = function (k) { return page.querySelector("#" + ID + k); };
    var page = veil.firstChild, sub = q("Sub"), range = q("Hours"), out = q("HoursOut"), ruler = q("Ruler"), stack = q("Stack"), plot = q("Plot"), nowEl = q("Now"), me = null, first = mixed ? 2 : 1;
    range.value = String(hours);
    var moreOpen = false, wantRun = null, moreEl = document.createElement("button");
    moreEl.type = "button"; moreEl.className = "ag-more"; moreEl.hidden = true;
    stack.appendChild(moreEl);

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
      c.className = "ag-card"; c.dataset.key = g.key; c.tabIndex = 0; c.setAttribute("role", "button");
      c.innerHTML = '<div class="ag-fig"></div><div class="ag-name"></div><div class="ag-lane"></div>';
      cards[g.key] = c; c.__top = null;
      return c;
    }
    function release(root) { if (window.HexReleaseVideos) window.HexReleaseVideos(root); }   // 视频播放器用完就放（hex-stage-page.js）
    function figure(c, g) {
      var f = ctx.figureOf(ctx.packOfTool(g.tool)), fig = c.firstChild;
      if (fig.__key === f.key) return;
      fig.__key = f.key; release(fig); fig.innerHTML = f.html;
      var v = fig.querySelector("video");
      if (v) v.addEventListener("error", function () { if (v.getAttribute("src")) { release(fig); fig.innerHTML = ctx.placeholder; } });
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
    // 混合页第一张卡：人的泳道。手动计时段贴上沿，窗口聚焦段在下（数据与分段都用 hex-human-page.js 的纯函数）
    function humanRange(win, now) { return { from: win.from, to: Math.min(win.to, now), now: now }; }
    function humanLane(h, win, L, now) {
      var hp = HP(), range = humanRange(win, now), live = (ctx.getLanes && ctx.getLanes() || {}).human, running = (live && live.running) || null;
      var lane = hp.laneOf(range, { sessions: h.sessions, running: running, coverFrom: h.coverFrom, segments: hp.focusSegments(range, h.blocks) });
      var wide = win.hours > 24, day = function (a) { return wide ? md(a) + " " : ""; }, html = "";
      lane.timer.forEach(function (x) {
        var r = Pr.spanRect(L, x.a, x.b);
        if (r) html += '<i class="ag-timer" style="left:' + pct(r.left) + ";width:" + pct(r.width) + '" title="' + esc(day(x.a) + hm(x.a) + "–" + hm(x.b) + " 手动计时" + (x.projectId ? " / " + ctx.projectName(x.projectId) : "")) + '"></i>';
      });
      lane.windows.forEach(function (x) {
        var r = Pr.spanRect(L, x.a, x.b), col = zoneColor(x.projectId);
        if (r) html += '<i class="ag-seg' + (col ? "" : " ag-lite") + '" style="left:' + pct(r.left) + ";width:" + pct(r.width) + (col ? ";background:" + esc(col) : "") +
          '" title="' + esc(day(x.a) + hm(x.a) + "–" + hm(x.b) + " 聚焦在 " + [x.projectId ? ctx.projectName(x.projectId) : null, x.label].filter(Boolean).join(" / ")) + '"></i>';
      });
      return { html: html, sec: hp.totalSeconds(range, h.sessions, running), dur: hp.durText };
    }
    function paintMe(h, win, L, now) {
      if (!me) {
        me = document.createElement("div");
        me.className = "ag-card ag-me ag-neutral";
        me.innerHTML = '<div class="ag-who"><div class="ag-name">你</div><div class="ag-dur"></div></div><div class="ag-lane"></div>';
        stack.insertBefore(me, stack.children[1] || null);
      }
      var m = h ? humanLane(h, win, L, now) : { html: "", sec: 0, dur: HP().durText };
      me.querySelector(".ag-dur").textContent = h ? m.dur(m.sec) : "";
      var lane = me.querySelector(".ag-lane");
      if (lane.__html !== m.html) { lane.__html = m.html; lane.innerHTML = m.html; }
      return m.sec;
    }
    // 画：卡片按键保留（视频不重放），按排序重排，换位走位移过渡
    function render(res, now, human) {
      var win = windowOf(mode, hours, now), L = scaleOf(win), runs = (res && res.agents) || [];
      var groups = rankGroups(groupRuns(runs), now), s = summary(groups, runs);
      var humanSec = mixed ? paintMe(human, win, L, now) : 0;
      sub.textContent = mixed ? mixedSubtitle(humanSec, s) : subtitle(win, s); sub.style.visibility = res ? "" : "hidden";
      var tk = win.mode === "default" ? dayTicks(L) : ticks(win.from, win.to).map(function (t) { return { x: Pr.xOf(L, t.t), label: t.label }; });
      ruler.innerHTML = tk.map(function (t) { return '<span class="ag-tick' + (t.w ? " ag-day" : "") + '" style="left:' + pct(t.x) + (t.w ? ";width:" + pct(t.w) : "") + '">' + esc(t.label) + "</span>"; }).join("");
      plot.querySelectorAll(".ag-tickline").forEach(function (e) { e.remove(); });
      tk.slice(win.mode === "default" ? 1 : 0).forEach(function (t) { var i = document.createElement("i"); i.className = "ag-tickline"; i.style.left = pct(t.x); plot.insertBefore(i, nowEl); });
      nowEl.style.left = pct(Pr.xOf(L, now));   // 默认标尺的右端是明天零点，现在线不在最右
      nowEl.firstChild.textContent = hm(now);
      var before = {};
      Object.keys(cards).forEach(function (k) { if (cards[k].isConnected) before[k] = cards[k].offsetTop; });
      var keep = {}, sp = splitRetained(groups);
      var hasRun = function (g) { return g.runs.some(function (r) { return r.runId === wantRun; }); };
      if (wantRun && sp.rest.some(hasRun)) moreOpen = true;
      var drawn = sp.shown.concat(moreOpen ? sp.rest : []);
      if (detailKey && !drawn.some(function (g) { return g.key === detailKey; })) drawn = drawn.concat(groups.filter(function (g) { return g.key === detailKey; }));   // 开着详情的卡不因折叠而消失
      moreEl.hidden = !sp.rest.length; moreEl.textContent = (moreOpen ? "收起其余 " : "其余 ") + sp.rest.length + " 个"; moreEl.setAttribute("aria-expanded", moreOpen ? "true" : "false");
      stack.appendChild(moreEl);
      drawn.forEach(function (g, i) {
        var c = cardOf(g); keep[g.key] = 1;
        var col = zoneColor(currentProject(g));
        c.style.setProperty("--ag-c", col || "var(--ink-3)");
        c.classList.toggle("ag-neutral", !col);
        c.querySelector(".ag-name").textContent = g.name; c.querySelector(".ag-name").title = g.name;
        c.__group = g; figure(c, g);
        var h = laneHtml(g, win, L), lane = c.querySelector(".ag-lane");
        if (lane.__html !== h) { lane.__html = h; lane.innerHTML = h; }
        if (stack.children[i + first] !== c) stack.insertBefore(c, stack.children[i + first] || null);
      });
      Object.keys(cards).forEach(function (k) { if (!keep[k]) { release(cards[k]); cards[k].remove(); delete cards[k]; } });
      if (detailKey) paintDetail();
      var want = wantRun && res ? drawn.filter(hasRun)[0] : null;
      if (want) { wantRun = null; if (detailKey !== want.key) openDetail(cards[want.key]); }
      if (!reduced()) drawn.forEach(function (g) {
        var c = cards[g.key], d = before[g.key] - c.offsetTop;
        if (before[g.key] != null && d && c.animate) c.animate([{ transform: "translateY(" + d + "px)" }, { transform: "none" }], { duration: SWAP_MS, easing: "ease-out" });
      });
    }

    var last = { res: null, now: Date.now(), human: null };

    // ── 卡片详情（「二十」）：卡片放大成一块盖住标尺与卡片栏的面板，左大立绘，右边每条运行一张小卡 ──
    var detailKey = null, detailEl = null, dirty = false, lastDetailKey = "";
    function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
    function buildDetail() {
      detailEl = mk("div", "ag-detail"); detailEl.hidden = true;
      var big = mk("button", "ag-big"), slot = mk("span", "ag-big-slot"), right = mk("div", "ag-dt"), hd = mk("div", "ag-dt-hd"), title = mk("button", "ag-dt-title"), x = mk("button", "st-x ag-dt-x", "✕");
      title.type = "button"; title.appendChild(mk("b")); x.type = "button"; x.setAttribute("aria-label", "收起");
      hd.appendChild(title); hd.appendChild(x);
      big.appendChild(slot); right.appendChild(hd); right.appendChild(mk("h3", "ag-dt-h", "活跃历史")); right.appendChild(mk("div", "ag-dt-list"));
      detailEl.appendChild(big); detailEl.appendChild(right); page.appendChild(detailEl);
      title.addEventListener("click", function () { closeDetail(); }); x.addEventListener("click", function () { closeDetail(); });
      // 点大立绘 = 换这个 agent 的形象（「二十六」2；长按小人不再换形象）
      big.type = "button"; big.setAttribute("aria-label", "更换形象");
      big.addEventListener("click", function () { var g = cards[detailKey] && cards[detailKey].__group; if (g && ctx.pickFigure) ctx.pickFigure(g.tool || null, big); });
      // 下拉获得焦点期间不重画，失去焦点后补画
      detailEl.addEventListener("focusout", function () { setTimeout(function () { if (dirty && detailKey && !selecting()) paintDetail(); }, 0); });
      detailEl.addEventListener("change", function (ev) { var sel = ev.target.closest("select"); if (sel) onPlace(sel.closest(".ag-run"), sel.classList.contains("ag-sel-project")); });
    }
    function selecting() { var a = document.activeElement; return !!(a && a.tagName === "SELECT" && detailEl && detailEl.contains(a)); }
    function fillSelect(sel, list, value) {
      sel.textContent = "";
      list.forEach(function (o) { var op = mk("option", "", o.name); op.value = o.id; sel.appendChild(op); });
      sel.value = value;
    }
    function fillRun(card, h) {
      var o = placeOptions(ctx.projectChoices ? ctx.projectChoices() : [], h.projectId, h.taskId);
      fillSelect(card.querySelector(".ag-sel-project"), o.projects, o.project);
      fillSelect(card.querySelector(".ag-sel-task"), o.tasks, o.task);
    }
    function runCard(h, now) {
      var c = mk("div", "ag-run"), top = mk("div", "ag-run-top"), when = h.end == null ? (md(h.start) + " " + hm(h.start) + " – 现在") : (md(h.start) + " " + hm(h.start) + " – " + (md(h.end) !== md(h.start) ? md(h.end) + " " : "") + hm(h.end));
      c.dataset.runId = h.runId; c.__h = h;
      top.appendChild(mk("span", "ag-run-time", when)); top.appendChild(mk("span", "ag-run-dur", durText(h.sec))); top.appendChild(mk("span", "ag-run-st", h.status));
      var src = mk("div", "ag-run-row"), own = mk("div", "ag-run-row"), sp = mk("select", "ag-sel-project"), st = mk("select", "ag-sel-task");
      sp.setAttribute("aria-label", "项目"); st.setAttribute("aria-label", "任务");
      src.appendChild(mk("span", "ag-run-k", "信息来源")); src.appendChild(mk("span", "ag-run-v", h.source));
      own.appendChild(mk("span", "ag-run-k", "归属")); own.appendChild(sp); own.appendChild(st);
      c.appendChild(top); c.appendChild(src); c.appendChild(own); fillRun(c, h);
      return c;
    }
    function paintDetail() {
      var c = cards[detailKey], g = c && c.__group;
      if (!g) { closeDetail(true); return; }
      if (selecting()) { dirty = true; return; }
      dirty = false;
      var now = last.now, hs = historyOf(g, now), newest = g.runs[g.runs.length - 1];
      detailEl.style.setProperty("--ag-c", c.style.getPropertyValue("--ag-c"));
      detailEl.classList.toggle("ag-neutral", c.classList.contains("ag-neutral"));
      detailEl.querySelector(".ag-dt-title b").textContent = g.name;
      ctx.fillFigure(detailEl.querySelector(".ag-big-slot"), ctx.packOfTool(g.tool), newest.running ? newest.phase : "idle", !!newest.lost);
      var list = detailEl.querySelector(".ag-dt-list"), top = list.scrollTop, sig = JSON.stringify([hs, ctx.projectChoices ? ctx.projectChoices() : []]);
      if (sig === lastDetailKey) return;
      lastDetailKey = sig;
      var busy = {}; [].forEach.call(list.children, function (e) { if (e.__busy) busy[e.dataset.runId] = e; });
      list.textContent = "";
      hs.forEach(function (h) { list.appendChild(busy[h.runId] || runCard(h, now)); });
      list.scrollTop = top;
    }
    function onPlace(card, projectChanged) {
      var h = card.__h, pid = card.querySelector(".ag-sel-project").value, tid = projectChanged ? "" : card.querySelector(".ag-sel-task").value;
      card.__busy = true;
      if (projectChanged) { var o = placeOptions(ctx.projectChoices ? ctx.projectChoices() : [], pid, null); fillSelect(card.querySelector(".ag-sel-task"), o.tasks, ""); }   // 任务下拉先随新项目换
      ctx.place(h.runId, (cards[detailKey] && cards[detailKey].__group.tool) || null, pid, tid || null).then(function (ok) {
        card.__busy = false;
        if (ok) load(); else { lastDetailKey = ""; fillRun(card, h); }   // 失败：下拉改回去（提示由页面底部提示报）
      });
    }
    function openDetail(card) {
      if (!card || card === me || !card.__group) return;
      if (detailKey === card.dataset.key) { closeDetail(); return; }
      if (!detailEl) buildDetail();
      var instant = detailKey != null;
      detailKey = card.dataset.key; lastDetailKey = ""; dirty = false;
      detailEl.hidden = false;
      detailEl.style.top = page.querySelector(".ag-axis").offsetTop + "px";
      detailEl.getAnimations().forEach(function (a) { a.cancel(); });
      paintDetail();
      if (!reduced() && !instant && detailEl.animate) {
        var from = card.getBoundingClientRect(), to = detailEl.getBoundingClientRect();
        detailEl.animate([{ clipPath: "inset(" + [from.top - to.top, to.right - from.right, to.bottom - from.bottom, from.left - to.left].map(function (v) { return Math.round(v) + "px"; }).join(" ") + " round 12px)", opacity: 0.3 },
          { clipPath: "inset(0px round 12px)", opacity: 1 }], { duration: DETAIL_MS, easing: "cubic-bezier(.2,.8,.2,1)" });
      }
      detailEl.querySelector(".ag-dt-x").focus({ preventScroll: true });
    }
    function closeDetail(instant) {
      if (detailKey == null) return;
      var card = cards[detailKey], el = detailEl;
      detailKey = null;
      var done = function () { if (detailKey == null) { release(el); el.hidden = true; el.querySelector(".ag-big-slot").textContent = ""; delete el.querySelector(".ag-big-slot").dataset.key; el.querySelector(".ag-dt-list").textContent = ""; lastDetailKey = ""; } };
      if (instant || reduced() || !el.animate || !card || !card.isConnected) { done(); return; }
      var from = card.getBoundingClientRect(), to = el.getBoundingClientRect();
      el.animate([{ clipPath: "inset(0px round 12px)", opacity: 1 }, { clipPath: "inset(" + [from.top - to.top, to.right - from.right, to.bottom - from.bottom, from.left - to.left].map(function (v) { return Math.round(v) + "px"; }).join(" ") + " round 12px)", opacity: 0.3 }],
        { duration: DETAIL_MS, easing: "cubic-bezier(.2,.8,.2,1)" }).onfinish = done;
      if (card.isConnected) card.focus({ preventScroll: true });
    }
    // 人的数据：views/lanes 的计时段（按 7 天分段读）+ 今天昨天的窗口块与原始记录；读不到不挡 agent 卡片
    function loadHuman(win, now) {
      var hp = HP(), range = humanRange(win, now);
      return Promise.all([
        Promise.all(hp.dateChunks(range.from, now).map(function (c) { return ctx.getJson("views/lanes?from=" + c.from + "&to=" + c.to); })),
        hp.loadBlocks(ctx.getJson, range)
      ]).then(function (r) {
        return { sessions: hp.mergeSessions(r[0].filter(Boolean).map(function (l) { return l.human && l.human.sessions; })), blocks: r[1].blocks, coverFrom: r[1].coverFrom };
      }).catch(function () { return null; });
    }
    function load() {
      var now = Date.now(), win = windowOf(mode, hours, now), my = ++seq;
      Promise.all([ctx.getJson("views/time-lanes?since=" + encodeURIComponent(new Date(win.from).toISOString())), mixed ? loadHuman(win, now) : null]).then(function (r) {
        var res = r[0];
        if (!opened || my !== seq || !res || !res.agents) return;
        last = { res: res, now: now, human: r[1] }; render(res, now, r[1]);
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
      an.onfinish = function () { release(el); el.remove(); flights--; done(); };
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
      veil.hidden = false; render(last.res, Date.now(), last.human);   // 上次的卡片先摆着，新数据到了再换
      flyPending = true; load();
      if (Object.keys(cards).length) { flyPending = false; flyIn(); }
      timer = setInterval(load, REFRESH_MS);
      page.querySelector(".ag-x").focus();
    }
    function close() {
      if (!opened) return;
      opened = false; wantRun = null; clearInterval(timer); seq++;
      closeDetail(true);
      flyBack(); veil.hidden = true;
      // 页面关着时卡片里的小人不该还在后台播：飞回动画走完就把它们的播放器放掉，下次打开再建
      setTimeout(function () {
        if (opened) return;
        Object.keys(cards).forEach(function (k) { var fig = cards[k].firstChild; release(fig); fig.innerHTML = ""; fig.__key = null; });
      }, 900);
    }
    function setMode(m) { mode = m; syncControls(); load(); }
    page.addEventListener("click", function (ev) {
      if (ev.target.closest(".ag-x")) close();
      var card = ev.target.closest(".ag-stack > .ag-card"); if (card) openDetail(card);
      if (ev.target.closest(".ag-more")) { moreOpen = !moreOpen; if (last.res) render(last.res, last.now, last.human); }
      var b = ev.target.closest("[data-mode]"); if (b) setMode(pickRange(mode, b.dataset.mode));
      if (ev.target === out && mode === "hours") setMode("default");
    });
    // 拖动时只改数字，松手才重读重画
    range.addEventListener("input", function () { hours = +range.value; out.textContent = "最近 " + hours + " 小时"; });
    range.addEventListener("change", function () { hours = +range.value; setMode("hours"); });
    veil.addEventListener("pointerdown", function (ev) { if (ev.target === veil) close(); });
    page.addEventListener("keydown", function (ev) { var card = ev.target.closest && ev.target.closest(".ag-stack > .ag-card"); if (card && ev.target === card && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); openDetail(card); } });
    document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && opened) { if (detailKey != null) closeDetail(); else close(); } });
    // 打开页面并直接放大某条运行所在的那张卡（长按台上的小人，「二十六」2）：卡被收在「其余 N 个」里的先展开
    function openRun(runId) { wantRun = runId; open(); if (last.res) render(last.res, last.now, last.human); }
    return { open: open, openRun: openRun, close: close, isOpen: function () { return opened; }, redraw: function () { if (opened && last.res) render(last.res, last.now, last.human); } };
  }
  api.mount = mount;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexAgentPage = api;
})();
