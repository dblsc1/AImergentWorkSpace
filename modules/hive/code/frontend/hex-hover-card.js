// hive · hex-hover-card.js —— 项目格悬停档里的内容（hive.stage.v1「二十五」）。
// 纯函数（可在 node 里测）：totals / pickCurrentTask / cardHtml；
// 接线：mount(ctx) 盯着蜂巢里的 .is-hover，格子真的长到悬停档才读 views/project-time（同一项目 30 秒内不重读）。
(function () {
  "use strict";
  var NOW_SLACK_MS = 60000, FRESH_MS = 30000;
  var Pr = typeof require === "function" ? require("./hex-project-ruler.js") : window.NexusTableHexProjectRuler;

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function ymd(t) { var d = new Date(t); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }

  var DAY_MS = 86400000;

  // 一串区间 {start, end} 落在 [from, to) 里的秒数
  function secWithin(list, from, to) {
    return (list || []).reduce(function (n, s) {
      var a = Math.max(Date.parse(s.start), from), b = Math.min(Date.parse(s.end), to);
      return b > a ? n + (b - a) : n;
    }, 0) / 1000;
  }
  function flat(payload, who) {
    return [].concat.apply([], ((payload && payload.tasks) || []).map(function (t) { return t[who] || []; }));
  }
  // 本周 / 本日 × 人 / agent（秒）：与上面那把尺子同一口径（「二十九」）—— 本日 = 此刻往前 24 小时，本周 = 往前 7 × 24 小时；agent 并行各自累加
  function totals(payload, now) {
    var h = flat(payload, "human"), a = flat(payload, "agent"), w = now - 7 * DAY_MS, d = now - DAY_MS;
    return { week: { human: secWithin(h, w, now), agent: secWithin(a, w, now) }, day: { human: secWithin(h, d, now), agent: secWithin(a, d, now) } };
  }

  // 正在进行的 1 个任务的名字（没有 = null）。tasks = 项目里的任务（含 done）。
  // 此刻在干（有一段止于 now 前 60 秒内）的里取最近开始的；没有就取最近有过活动的；taskId 为 null / 已完成 / 已删的不算
  function pickCurrentTask(payload, tasks, now) {
    var open = {}; (tasks || []).forEach(function (t) { if (!t.done) open[t.id] = t.name; });
    var cut = (payload && Date.parse(payload.now)) || now, live = null, last = null;
    ((payload && payload.tasks) || []).forEach(function (t) {
      if (!t.taskId || !(t.taskId in open)) return;
      (t.human || []).concat(t.agent || []).forEach(function (s) {
        var a = Date.parse(s.start), b = Date.parse(s.end);
        if (b >= cut - NOW_SLACK_MS && (!live || a > live.t)) live = { t: a, id: t.taskId };
        if (!last || b > last.t) last = { t: b, id: t.taskId };
      });
    });
    var hit = live || last;
    return hit ? open[hit.id] : null;
  }

  // 悬停档里的整块 HTML（骨架 = payload 为 null：标尺、空泳道，没有任务行和数字）
  function cardHtml(now, payload, tasks, color, dur) {
    var L = Pr.layout(now);
    var h = flat(payload, "human"), a = flat(payload, "agent");
    var out = Pr.rulerHtml(L) + Pr.lanesHtml(L, h, a, color).replace(/ title="[^"]*"/g, "");
    if (!payload) return out;
    var name = pickCurrentTask(payload, tasks, now), t = totals(payload, now);
    function row(label, x) {
      return '<div class="hv-sum"><b>' + label + '</b><i class="pr-k pr-h"></i>' + esc(dur(x.human)) + '<i class="pr-k pr-a"></i>' + esc(dur(x.agent)) + "</div>";
    }
    return out + (name ? '<div class="hv-task">' + esc(name) + "</div>" : "") + row("本周", t.week) + row("本日", t.day);
  }

  // ctx: { getJson(path), tree() → views/tree, cellColor(pid), dur(sec), now(), hive: #hive 元素 }
  function mount(ctx) {
    var cache = {}, shown = null;   // cache[pid] = {at, payload, pending}
    function tasksOf(pid) {
      var p = (((ctx.tree() || {}).projects) || []).filter(function (x) { return x.id === pid; })[0];
      return (p && p.tasks) || [];
    }
    function paint(el, pid) {
      var box = el.querySelector(".hv-card"), c = cache[pid], now = ctx.now();
      if (!box) return;
      box.innerHTML = cardHtml(now, c && c.payload, tasksOf(pid), ctx.cellColor(pid), ctx.dur);
      box.style.setProperty("--pr-now", Pr.nowPct(Pr.layout(now)));
    }
    function load(pid, el) {
      var c = cache[pid] || (cache[pid] = { at: 0, payload: null, pending: false }), now = ctx.now();
      if (c.pending || now - c.at < FRESH_MS) return;
      c.pending = true;
      var L = Pr.layout(now);
      Pr.readProjectTime(ctx.getJson, pid, L).then(function (res) {
        c.pending = false;
        if (!res) return;   // 读失败：留着骨架 / 上一次的数
        c.payload = res; c.at = ctx.now();
        if (shown === el && el.classList.contains("is-hover")) paint(el, pid);
      }, function () { c.pending = false; });
    }
    function check() {
      var el = ctx.hive.querySelector(".hex-cell.is-hover[data-project-id]");
      if (!el || document.body.classList.contains("hd-dragging")) { shown = null; return; }
      if (el === shown) return;
      shown = el;
      var pid = el.dataset.projectId;
      paint(el, pid); load(pid, el);
    }
    new MutationObserver(check).observe(ctx.hive, { subtree: true, attributes: true, attributeFilter: ["class"] });
    return { check: check };
  }

  var api = { totals: totals, pickCurrentTask: pickCurrentTask, cardHtml: cardHtml, mount: mount };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexHoverCard = api;
})();
