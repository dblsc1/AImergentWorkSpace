// hive · hex-human-page.js —— 右上角「人」的页面：左边立绘 + 纵置三张卡（hive.stage.v1「十四」）。
// 上半是纯函数（给 hex-human-page.test.js 用），下半是 DOM（文字一律 textContent）。
(function () {
  "use strict";

  var HOUR = 3600000, DAY = 24 * HOUR, WEEK = 7 * DAY, DEFAULT_HOURS = 3, FOLD = 5, MAX_BLOCKS = 40, MERGE_GAP = 5000, POLL_MS = 30000;
  var HIDDEN_TITLE = /^(窗口名|路径)\d+$/;
  var TIMER_SOURCES = { "timer-backend": 1, "manual-backfill": 1 };   // 手动计时留下的段；其余来源（按窗口块记的等）算窗口聚焦
  function ms(s) { var t = s ? Date.parse(s) : NaN; return isNaN(t) ? 0 : t; }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function midnight(t, plusDays) { var d = new Date(t); return new Date(d.getFullYear(), d.getMonth(), d.getDate() + (plusDays || 0)).getTime(); }
  function ymd(t) { var d = new Date(t); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function hm(t) { var d = new Date(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function md(t) { var d = new Date(t); return pad(d.getMonth() + 1) + "/" + pad(d.getDate()); }

  // ── 范围：滚动，往前倒推 ──
  function parseHours(text) {
    var t = String(text == null ? "" : text).trim();
    if (!/^\d{1,2}$/.test(t)) return null;
    var n = parseInt(t, 10);
    return n >= 1 && n <= 24 ? n : null;
  }
  function rangeOf(kind, hours, now) {
    var span = kind === "week" ? WEEK : kind === "day" ? DAY : (parseHours(hours) || DEFAULT_HOURS) * HOUR;
    return { kind: kind, from: now - span, to: now };
  }
  // [from, to] 跨的本地日，按每 7 天一段（views/lanes 的跨度上限）
  function dateChunks(from, to) {
    var out = [], d = midnight(from), last = midnight(to);
    while (d <= last) {
      var e = Math.min(midnight(d, 6), last);
      out.push({ from: ymd(d), to: ymd(e) });
      d = midnight(e, 1);
    }
    return out;
  }
  // 窗口块只读今天和昨天（范围起点在今天 0 点之前才要昨天）
  function blockDates(range) {
    var today = midnight(range.to), out = [{ date: ymd(today), start: today }];
    if (range.from < today) out.push({ date: ymd(midnight(today, -1)), start: midnight(today, -1) });
    return out;
  }
  function clip(range, a, b) { a = Math.max(a, range.from); b = Math.min(b, range.to); return b > a ? [a, b] : null; }
  function pos(range, a, b) { var w = range.to - range.from, left = Math.min((a - range.from) / w * 100, 100); return { left: left, width: Math.max(0, Math.min((b - a) / w * 100, 100 - left)) }; }
  // 正在跑的这一段：画面的右端按分钟取整（range.to），但正在跑的计时算到真正的此刻（range.now），刚开始的计时不会因为取整而不见
  function runSpan(range, running) {
    var a = Math.max(ms(running.startAt), range.from), b = range.now || range.to;
    return b > a ? [a, b] : null;
  }

  // ── 计时段 ──
  function isTimerSession(s) { return !!TIMER_SOURCES[s.source]; }
  // 分段读来的 human.sessions 合起来；同一条（跨零点的会在两段里各出现一次）只留一条
  function mergeSessions(lists) {
    var seen = {}, out = [];
    (lists || []).forEach(function (l) {
      (l || []).forEach(function (s) {
        var k = [s.startAt, s.endAt, s.source || "", s.taskId || "", s.projectId || ""].join("|");
        if (!seen[k]) { seen[k] = 1; out.push(s); }
      });
    });
    return out.sort(function (a, b) { return ms(a.startAt) - ms(b.startAt); });
  }
  function totalSeconds(range, sessions, running) {
    var sum = 0;
    (sessions || []).forEach(function (s) { var c = clip(range, ms(s.startAt), ms(s.endAt)); if (c) sum += (c[1] - c[0]) / 1000; });
    if (running) { var r = runSpan(range, running); if (r) sum += (r[1] - r[0]) / 1000; }
    return Math.floor(sum);
  }
  // 逐任务：labelOf(taskId, projectId, live) → {key, name, projectId}（同 key 并成一张）。正在跑的置顶，其余最近聚焦的在前
  function taskCards(range, sessions, running, labelOf) {
    var by = {}, list = [];
    function add(taskId, projectId, c, live) {
      if (!c) return;
      var l = labelOf(taskId || null, projectId || null, live), g = by[l.key];
      if (!g) { g = by[l.key] = { key: l.key, name: l.name, projectId: l.projectId || null, seconds: 0, lastAt: 0, running: false }; list.push(g); }
      g.seconds += (c[1] - c[0]) / 1000; g.lastAt = Math.max(g.lastAt, c[1]);
      if (live) g.running = true;
    }
    (sessions || []).forEach(function (s) { add(s.taskId, s.projectId, clip(range, ms(s.startAt), ms(s.endAt)), false); });
    if (running) add(running.taskId, running.projectId, runSpan(range, running), true);
    list.forEach(function (g) { g.seconds = Math.floor(g.seconds); });
    return list.sort(function (a, b) { return (b.running - a.running) || (b.lastAt - a.lastAt) || (b.seconds - a.seconds); });
  }

  // ── 窗口块 ──
  function splitKey(key, apps) {
    var app = (apps || []).filter(function (a) { return key.indexOf(a + " · ") === 0; })[0] || key.split(" · ")[0];
    var title = key.indexOf(app + " · ") === 0 ? key.slice(app.length + 3) : "";
    return { app: app, title: title && !HIDDEN_TITLE.test(title) ? title : null, hidden: HIDDEN_TITLE.test(title) };
  }
  function windowLabel(app, title, hidden) { return hidden ? app + " · 标题已隐去" : title ? app + " · " + title : app; }
  function classified(item) { return !!item && (item.status === "assigned" || item.status === "generic") && !!(item.parts && item.parts.length); }
  // 块整体算哪个项目：秒数最多的一段；没归类 = null
  function blockProject(item) {
    if (!classified(item)) return null;
    var best = item.parts.slice().sort(function (a, b) { return (b.seconds || 0) - (a.seconds || 0); })[0];
    return best.projectId || null;
  }
  function recordProject(item, ts) {
    if (!classified(item)) return null;
    var hit = item.parts.filter(function (p) { return (p.fromTs == null || ts >= p.fromTs) && (p.toTs == null || ts < p.toTs); })[0];
    return hit ? hit.projectId || null : null;
  }
  // 泳道下行：各块原始记录裁到范围；相邻同窗口同去向的并成一段。blocks: [{item, records}]
  function focusSegments(range, blocks) {
    var rows = [];
    (blocks || []).forEach(function (b) {
      (b.records || []).forEach(function (r) {
        var c = clip(range, ms(r.startAt), ms(r.endAt)), title = r.title || "";
        if (c) rows.push({ from: c[0], to: c[1], app: r.app || "", title: title, hidden: HIDDEN_TITLE.test(title), projectId: recordProject(b.item, c[0]) });
      });
    });
    rows.sort(function (a, b) { return a.from - b.from; });
    var out = [];
    rows.forEach(function (r) {
      var last = out[out.length - 1];
      if (last && last.app === r.app && last.title === r.title && last.projectId === r.projectId && r.from - last.to <= MERGE_GAP) last.to = Math.max(last.to, r.to);
      else out.push(r);
    });
    return out;
  }
  // 第三张卡的行：每块一行，按范围内用时从多到少
  function windowRows(range, blocks) {
    var rows = [];
    (blocks || []).forEach(function (b) {
      var item = b.item || {}, secs = 0, last = 0, k = splitKey(String(item.blockKey || ""), item.apps);
      if (b.records) {
        b.records.forEach(function (r) { var c = clip(range, ms(r.startAt), ms(r.endAt)); if (c) { secs += (c[1] - c[0]) / 1000; last = Math.max(last, c[1]); } });
      } else if (ms(item.lastAt) >= range.from) { secs = item.seconds || 0; last = ms(item.lastAt); }
      if (secs > 0) rows.push({ app: k.app, title: k.title, hidden: k.hidden, seconds: Math.floor(secs), lastAt: last, blockKey: item.blockKey, projectId: blockProject(item) });
    });
    return rows.sort(function (a, b) { return b.seconds - a.seconds; });
  }
  function foldRows(rows, expanded) { return expanded ? rows : rows.slice(0, FOLD); }

  // ── 第一张卡的泳道 ──
  // o: {sessions, running, segments（focusSegments）, coverFrom（原始记录覆盖到的最早时刻）}
  function laneOf(range, o) {
    var timer = [], wins = [];
    (o.sessions || []).forEach(function (s) {
      if (isTimerSession(s)) {
        var c = clip(range, ms(s.startAt), ms(s.endAt));
        if (c) timer.push(Object.assign({ a: c[0], b: c[1], projectId: s.projectId || null }, pos(range, c[0], c[1])));
      } else {
        // 有原始记录的时段由记录画（带窗口名）；更早的只能用台账里的段
        var w = clip(range, ms(s.startAt), Math.min(ms(s.endAt), o.coverFrom == null ? Infinity : o.coverFrom));
        if (w) wins.push(Object.assign({ a: w[0], b: w[1], projectId: s.projectId || null, label: null }, pos(range, w[0], w[1])));
      }
    });
    if (o.running) {
      var r = runSpan(range, o.running);
      if (r) timer.push(Object.assign({ a: r[0], b: r[1], projectId: o.running.projectId || null, live: true }, pos(range, r[0], r[1])));
    }
    (o.segments || []).forEach(function (s) {
      wins.push(Object.assign({ a: s.from, b: s.to, projectId: s.projectId, label: windowLabel(s.app, s.title, s.hidden) }, pos(range, s.from, s.to)));
    });
    function byStart(x, y) { return x.a - y.a; }
    return { timer: timer.sort(byStart), windows: wins.sort(byStart) };
  }
  function durText(sec) {
    var m = Math.floor(Math.max(0, sec) / 60), h = Math.floor(m / 60);
    return h ? h + " 小时 " + (m % 60) + " 分钟" : m + " 分钟";
  }
  function spanText(range, a, b) { return (range.kind === "week" ? md(a) + " " : "") + hm(a) + "–" + hm(b); }

  var P = { DEFAULT_HOURS: DEFAULT_HOURS, FOLD: FOLD, MAX_BLOCKS: MAX_BLOCKS, parseHours: parseHours, rangeOf: rangeOf, dateChunks: dateChunks, blockDates: blockDates,
    mergeSessions: mergeSessions, totalSeconds: totalSeconds, taskCards: taskCards, splitKey: splitKey, windowLabel: windowLabel, blockProject: blockProject,
    recordProject: recordProject, focusSegments: focusSegments, windowRows: windowRows, foldRows: foldRows, laneOf: laneOf, durText: durText, spanText: spanText };
  if (typeof module !== "undefined" && module.exports) { module.exports = P; return; }
  window.HexHumanPage = P;

  // ── DOM ──
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; }

  // o: {now(), getLanes(), getJson(path), projectName(id), projectColor(id), taskLabel(taskId, projectId, live), fillIssues(box, list),
  //     fillFigure(slot), pickHuman(), openProject(pid), dragTimer(card), timerRunning()}
  P.create = function (o) {
    var state = { kind: "hours", hours: DEFAULT_HOURS, expanded: false }, issues = [], poll = 0, dead = false, seq = 0;
    var first = (o.getLanes() || {}).human;
    var data = { humans: first ? [first] : [], blocks: [], coverFrom: Infinity };
    var root = el("div", "hp"), figure = el("button", "hp-figure"), slot = el("span", "hp-figure-slot"), cards = el("div", "hp-cards");
    figure.type = "button"; figure.setAttribute("aria-label", "更换我的形象"); figure.appendChild(slot);
    figure.addEventListener("click", function () { o.pickHuman(); });
    root.appendChild(figure); root.appendChild(cards);

    function colorOf(pid) { return (pid && o.projectColor(pid)) || ""; }
    function tint(node, pid) { var c = colorOf(pid); if (c) node.style.setProperty("--hp-c", c); return !!c; }

    function loadBlocks(range) {
      var dates = blockDates(range);
      return Promise.all(dates.map(function (d) { return o.getJson("activity/blocks?date=" + d.date + "&status=all&limit=200"); })).then(function (rs) {
        var seen = {}, picks = [], cover = Infinity;
        rs.forEach(function (r, i) {
          if (!r) return;
          cover = Math.min(cover, dates[i].start);
          (r.items || []).filter(function (b) { return !b.lastAt || ms(b.lastAt) >= range.from; }).slice(0, MAX_BLOCKS).forEach(function (b) {
            var date = r.date || dates[i].date, k = date + "|" + b.blockKey;
            if (!seen[k]) { seen[k] = 1; picks.push({ date: date, item: b }); }
          });
        });
        return Promise.all(picks.map(function (p) {
          return o.getJson("activity/blocks/records?date=" + encodeURIComponent(p.date) + "&blockKey=" + encodeURIComponent(p.item.blockKey))
            .then(function (x) { return { item: p.item, records: x && x.items ? x.items : null }; });
        })).then(function (blocks) { return { blocks: blocks, coverFrom: cover }; });
      });
    }
    function load() {
      var now = o.now(), range = rangeOf(state.kind, state.hours, now), mine = ++seq;
      Promise.all([
        Promise.all(dateChunks(range.from, now).map(function (c) { return o.getJson("views/lanes?from=" + c.from + "&to=" + c.to); })),
        loadBlocks(range)
      ]).then(function (r) {
        if (dead || mine !== seq) return;
        data = { humans: r[0].filter(Boolean).map(function (l) { return l.human; }).filter(Boolean), blocks: r[1].blocks, coverFrom: r[1].coverFrom };
        if (!data.humans.length && first) data.humans = [first];
        update(true);
      });
    }

    function seg(cls, s, tip) {
      var n = el("i", "hp-seg " + cls);
      n.style.left = s.left + "%"; n.style.width = s.width + "%"; n.title = tip;
      return n;
    }
    function build() {
      var now = Math.floor(o.now() / 60000) * 60000, range = rangeOf(state.kind, state.hours, now);
      range.now = o.now();
      var live = (o.getLanes() || {}).human, running = live && live.running || null;
      var sessions = mergeSessions(data.humans.map(function (h) { return h.sessions; }));
      var box = document.createElement("div");

      // 第一张：用时
      var c1 = el("section", "hp-card"); c1.dataset.card = "time"; c1.appendChild(el("h3", "", "用时"));
      var bar = el("div", "hp-range");
      function pick(kind, label) {
        var b = el("button", "hp-range-btn", label); b.type = "button"; b.dataset.range = kind; b.setAttribute("aria-pressed", state.kind === kind ? "true" : "false");
        b.addEventListener("click", function () { state.kind = kind; load(); update(true); });
        bar.appendChild(b);
      }
      pick("day", "日"); pick("week", "周");
      if (state.kind === "hours") {
        var g = el("span", "hp-range-btn hp-range-hours"), inp = el("input", "hp-hours");
        inp.type = "text"; inp.inputMode = "numeric"; inp.maxLength = 2; inp.setAttribute("value", String(state.hours)); inp.setAttribute("aria-label", "最近几小时");
        g.setAttribute("aria-pressed", "true"); g.appendChild(el("span", "", "最近")); g.appendChild(inp); g.appendChild(el("span", "", "小时"));
        function commit() {
          var n = parseHours(inp.value);
          if (n == null) { inp.value = String(state.hours); return; }
          if (n !== state.hours) { state.hours = n; load(); update(true, true); }
        }
        inp.addEventListener("change", commit);
        inp.addEventListener("keydown", function (ev) { ev.stopPropagation(); if (ev.key === "Enter") { ev.preventDefault(); commit(); } });
        bar.appendChild(g);
      } else {
        pick("hours", "最近 " + state.hours + " 小时");
      }
      c1.appendChild(bar);
      c1.appendChild(el("div", "hp-total", durText(totalSeconds(range, sessions, running))));
      var lane = laneOf(range, { sessions: sessions, running: running, coverFrom: data.coverFrom,
        segments: focusSegments(range, data.blocks) }), laneEl = el("div", "hp-lane"), up = el("div", "hp-lane-timer"), down = el("div", "hp-lane-win");
      lane.timer.forEach(function (s) {
        var n = seg("hp-seg-timer", s, spanText(range, s.a, s.b) + " 手动计时" + (s.projectId ? " / " + o.projectName(s.projectId) : ""));
        up.appendChild(n);
      });
      lane.windows.forEach(function (s) {
        var n = seg("hp-seg-win", s, spanText(range, s.a, s.b) + " " + [s.projectId ? o.projectName(s.projectId) : null, s.label].filter(Boolean).join(" / "));
        if (s.projectId && tint(n, s.projectId)) n.classList.add("is-filed");
        down.appendChild(n);
      });
      laneEl.appendChild(up); laneEl.appendChild(down); c1.appendChild(laneEl);
      var axis = el("div", "hp-axis");
      [0, 1 / 3, 2 / 3, 1].forEach(function (f) {
        var t = range.from + (range.to - range.from) * f, s = el("span", "", (range.kind === "week" ? md(t) + " " : "") + hm(t));
        s.style.left = f * 100 + "%"; if (f === 1) s.style.transform = "translateX(-100%)"; else if (f > 0) s.style.transform = "translateX(-50%)";
        axis.appendChild(s);
      });
      c1.appendChild(axis);
      box.appendChild(c1);

      // 第二张：逐任务用时
      var c2 = el("section", "hp-card"); c2.dataset.card = "tasks"; c2.appendChild(el("h3", "", "逐任务用时"));
      var tasks = taskCards(range, sessions, running, o.taskLabel), list = el("div", "hp-tasks");
      if (!tasks.length) list.appendChild(el("div", "hp-empty", "这段时间没有记录"));
      tasks.forEach(function (t) {
        var card = el("button", "hp-task" + (t.running ? " is-running" : ""));
        card.type = "button"; card.dataset.key = t.key; card.title = t.name;
        card.appendChild(el("b", "", t.name)); card.appendChild(el("span", "hp-dur", durText(t.seconds)));
        tint(card, t.projectId);
        if (t.running) {
          o.dragTimer(card, t.projectId);
          card.addEventListener("keydown", function (ev) { if ((ev.key === "Enter" || ev.key === " ") && t.projectId) { ev.preventDefault(); o.openProject(t.projectId); } });
        } else if (t.projectId) card.addEventListener("click", function () { o.openProject(t.projectId); });
        else card.disabled = true;
        list.appendChild(card);
      });
      c2.appendChild(list); box.appendChild(c2);

      // 第三张：窗口记录
      var c3 = el("section", "hp-card"); c3.dataset.card = "windows"; c3.appendChild(el("h3", "", "窗口记录"));
      var rows = windowRows(range, data.blocks), wl = el("div", "hp-wins");
      if (!rows.length) wl.appendChild(el("div", "hp-empty", "没有记录"));
      foldRows(rows, state.expanded).forEach(function (w) {
        var row = el("div", "hp-win");
        row.appendChild(el("b", "", windowLabel(w.app, w.title, w.hidden))); row.appendChild(el("span", "hp-dur", durText(w.seconds)));
        if (w.projectId) { row.title = o.projectName(w.projectId); if (tint(row, w.projectId)) row.classList.add("is-filed"); }
        wl.appendChild(row);
      });
      c3.appendChild(wl);
      if (rows.length > FOLD) {
        var more = el("button", "hp-more", state.expanded ? "收起" : "展开全部"); more.type = "button";
        more.addEventListener("click", function () { state.expanded = !state.expanded; update(true); });
        c3.appendChild(more);
      }
      if (issues.length) { var ib = el("div", "hp-issues"); ib.appendChild(el("h4", "", "窗口信息不全")); c3.appendChild(ib); o.fillIssues(ib, issues); }
      box.appendChild(c3);
      return box;
    }

    // 内容没变就不动 DOM；拖着计时卡 / 在改小时数时不重画（force：自己刚改了界面状态，必须画）
    function update(force, keepFocus) {
      if (dead) return;
      o.fillFigure(slot);
      var typing = cards.querySelector(".hp-hours") === document.activeElement;
      if (cards.querySelector(".st-dragging") || (typing && !force)) return;
      var next = build();
      if (next.innerHTML === cards.innerHTML) return;
      cards.textContent = "";
      while (next.firstChild) cards.appendChild(next.firstChild);
      if (keepFocus || typing) { var i = cards.querySelector(".hp-hours"); if (i) { i.focus(); i.select(); } }
    }
    update(true);
    return {
      el: root, update: function () { update(false); },
      load: function () { load(); clearInterval(poll); poll = setInterval(load, POLL_MS); },
      setIssues: function (list) { issues = list || []; update(true); },
      destroy: function () { dead = true; clearInterval(poll); }
    };
  };
})();
