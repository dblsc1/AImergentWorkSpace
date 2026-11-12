// hive · hex-project-ruler.js —— 项目面板：任务条 + 人 / 代理时间标尺（hive.stage.v1「七」）。
// 两层：
//   1) 标尺（纯函数，无 DOM，可复用）：layout(now) 定 7 天的绝对刻度（前 6 天各占 70%/6，今天占 30%），
//      xOf / spanRect 把时间戳换成 0..1 的横向位置；rulerHtml / lanesHtml 画出来。别处要画「人 / 代理时间标尺表」就用这几个。
//   2) 面板（接线）：createPanel(ctx) —— 每个任务一条长条（左：任务，右：上半人、下半代理两条泳道），
//      拖动时的弹簧展开（在格子上停 1 秒 = 点开）、任务条落点、在空白处停 1 秒 = 新建任务。
(function () {
  "use strict";
  var PAST_DAYS = 6, PAST_SHARE = 0.7, TODAY_SHARE = 0.3, HOLD_MS = 1000, HOLD_SLOP = 10;
  var WEEK = ["日", "一", "二", "三", "四", "五", "六"];

  // ── 标尺：纯函数 ────────────────────────────────────
  function midnight(ms, plusDays) {
    var d = new Date(ms);
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() + (plusDays || 0)).getTime();
  }
  // now → 7 天（本地日）；days[i].x0 / w 是这一天在整条尺子上的起点与宽度（0..1），今天最后一天
  function layout(now) {
    var days = [];
    for (var i = -PAST_DAYS; i <= 0; i++) {
      var past = i < 0, k = i + PAST_DAYS;
      days.push({ start: midnight(now, i), end: midnight(now, i + 1), today: !past,
        x0: past ? k * PAST_SHARE / PAST_DAYS : PAST_SHARE, w: past ? PAST_SHARE / PAST_DAYS : TODAY_SHARE });
    }
    return { from: days[0].start, to: days[PAST_DAYS].end, days: days };
  }
  // 等宽刻度（单日 / 一周的统计页）：从 startMs 所在本地日起 n 天，每天 w = 1/n；形状同 layout，xOf / spanRect 通用
  function linearScale(startMs, n) {
    var days = [];
    for (var i = 0; i < n; i++) days.push({ start: midnight(startMs, i), end: midnight(startMs, i + 1), today: false, x0: i / n, w: 1 / n });
    return { from: days[0].start, to: days[n - 1].end, days: days };
  }
  // 滚动窗口（Agent 页）：[fromMs, toMs] 一整段，不按天切；形状同 layout，xOf / spanRect 通用
  function rollingScale(fromMs, toMs) {
    return { from: fromMs, to: toMs, days: [{ start: fromMs, end: toMs, today: false, x0: 0, w: 1 }] };
  }
  // 时间戳 → 0..1（窗口外钳到两头）。天与天的交界处连续，所以跨零点的段仍是一条
  function xOf(L, t) {
    t = Math.min(Math.max(t, L.from), L.to);
    for (var i = 0; i < L.days.length; i++) {
      var d = L.days[i];
      if (t < d.end || i === L.days.length - 1) return d.x0 + d.w * (t - d.start) / (d.end - d.start);
    }
    return 1;
  }
  // 一段 [a, b)（毫秒）→ {left, width}（0..1）；完全在窗口外 = null。最小可见宽度（3px）由样式 min-width 保证
  function spanRect(L, a, b) {
    if (!(b > a) || b <= L.from || a >= L.to) return null;
    var l = xOf(L, a);
    return { left: l, width: xOf(L, b) - l };
  }

  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function hm(t) { var d = new Date(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function md(t) { var d = new Date(t); return pad(d.getMonth() + 1) + "/" + pad(d.getDate()); }
  function ymd(t) { var d = new Date(t); return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function pct(x) { return Math.round(x * 100000) / 1000 + "%"; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  // 悬停提示：MM/DD HH:MM–HH:MM 你|代理名
  function tip(a, b, who) { return md(a) + " " + hm(a) + "–" + hm(b) + " " + who; }

  function dividers(L) {
    return L.days.slice(1).map(function (d) { return '<i class="pr-div" style="left:' + pct(d.x0) + '"></i>'; }).join("");
  }
  // 标尺：上排每天一格（过去几天写周几，今天写「今天」），下排是今天里的钟点刻度
  function rulerHtml(L) {
    var top = L.days.map(function (d) {
      return '<span class="pr-day' + (d.today ? " is-today" : "") + '" style="left:' + pct(d.x0) + ";width:" + pct(d.w) + '">' +
        (d.today ? "今天" : "周" + WEEK[new Date(d.start).getDay()]) + "</span>";
    }).join("");
    var t = L.days[PAST_DAYS], ticks = [0, 6, 12, 18, 24].map(function (h) {
      return '<span class="pr-tick" style="left:' + pct(t.x0 + t.w * h / 24) + '">' + pad(h) + "</span>";
    }).join("");
    return '<div class="pr-ruler">' + dividers(L) + top + '<div class="pr-ticks">' + ticks + "</div></div>";
  }
  function segs(L, list, who, cls) {
    return list.map(function (s) {
      var a = Date.parse(s.start), b = Date.parse(s.end), r = spanRect(L, a, b);
      return r ? '<i class="pr-seg ' + cls + '" style="left:' + pct(r.left) + ";width:" + pct(r.width) + '" title="' + esc(tip(a, b, who(s))) + '"></i>' : "";
    }).join("");
  }
  // 一个对象（任务 / 项目）的两条泳道：上半 = 人（红），下半 = 代理（color = 项目色）
  function lanesHtml(L, human, agent, color) {
    return '<div class="pr-lanes"' + (color ? ' style="--pr-agent:' + esc(color) + '"' : "") + ">" + dividers(L) +
      '<div class="pr-lane pr-human">' + segs(L, human || [], function () { return "你"; }, "pr-h") + "</div>" +
      '<div class="pr-lane pr-agent">' + segs(L, agent || [], function (s) { return s.agent || "代理"; }, "pr-a") + "</div></div>";
  }

  function sumSec(list) {
    return list.reduce(function (n, s) { return n + Math.max(0, Date.parse(s.end) - Date.parse(s.start)); }, 0) / 1000;
  }

  // 「最近完成」一行里的时长（hive.stage.v1「十二」3）：0 写「0 分钟」，大于 0 不足 1 分钟写「不到 1 分钟」
  function minutesText(sec) {
    return sec >= 60 ? Math.round(sec / 60) + " 分钟" : sec > 0 ? "不到 1 分钟" : "0 分钟";
  }
  // 「完成 · 人花了 X 分钟 · agent 花了 Y 分钟」（任务名由卡片自己写在前面）；取消的写「取消」
  function spentText(canceled, humanSec, agentSec) {
    return (canceled ? "取消" : "完成") + " · 人花了 " + minutesText(humanSec) + " · agent 花了 " + minutesText(agentSec);
  }

  // ── 面板 ────────────────────────────────────────────
  // ctx: { tree() → views/tree, getJson(path), dur(sec), now(), open(pid), taskApi() → NexusTableData }
  function createPanel(ctx) {
    var cur = null, payload = null, loadedAt = 0, loading = false, creating = null;
    var hold = { key: null, x: 0, y: 0, timer: null }, lit = null;

    function project(pid) { return ((ctx.tree() || {}).projects || []).filter(function (p) { return p.id === pid; })[0] || null; }
    function colorOf(pid) {
      // 蜂巢上实际显示的分区色（默认色的分区由页面自动配色，库里的 color 是灰的默认值）
      var shown = ctx.cellColor && ctx.cellColor(pid);
      if (shown) return shown;
      var p = project(pid), z = p && ((ctx.tree() || {}).zones || []).filter(function (x) { return x.id === p.zoneId; })[0];
      return (z && z.color) || "";
    }
    function load(pid, now) {
      if (loading) return;
      loading = true; loadedAt = now;
      var L = layout(now);
      ctx.getJson("views/project-time?projectId=" + encodeURIComponent(pid) + "&from=" + ymd(L.from) + "&to=" + ymd(now)).then(function (res) {
        loading = false;
        if (cur !== pid) return;
        payload = res; if (ctx.changed) ctx.changed();
      });
    }

    // 这个任务上人 / 代理在已读到的窗口里各多少秒（没读到 = null）
    function spent(taskId) {
      var t = ((payload && payload.tasks) || []).filter(function (x) { return (x.taskId || "") === taskId; })[0];
      return payload ? { human: sumSec((t && t.human) || []), agent: sumSec((t && t.agent) || []) } : null;
    }
    // 面板主体的 HTML（页面的 renderPanel 把它填进大六边形里的 .hex-ruler-slot）；顺带按需重读
    function body(pid, now) {
      if (cur !== pid) { cur = pid; payload = null; loadedAt = 0; }
      if (!loading && now - loadedAt > 5000) load(pid, now);
      var L = layout(now), byTask = {};
      var p = project(pid), color = colorOf(pid);
      var rows = ((p && p.tasks) || []).map(function (t) { return { id: t.id, name: t.name }; }).concat([{ id: "", name: "没挂任务" }]);
      var shown = {}; rows.forEach(function (r) { shown[r.id] = 1; });
      // 列表里没有的任务（项目自带的隐藏「未分类」任务 t_unc_*、已删任务）并进「没挂任务」，时间不丢
      ((payload && payload.tasks) || []).forEach(function (t) {
        var k = shown[t.taskId || ""] ? (t.taskId || "") : "", d = byTask[k] || (byTask[k] = { human: [], agent: [] });
        d.human = d.human.concat(t.human || []); d.agent = d.agent.concat(t.agent || []);
      });
      var list = rows.map(function (r) {
        var d = byTask[r.id] || { human: [], agent: [] };
        return '<div class="pr-row"' + (color ? ' style="--pr-agent:' + esc(color) + '"' : "") + ' data-task="' + esc(r.id) + '" data-project="' + esc(pid) + '"><div class="pr-task"><b>' + esc(r.name) + "</b>" +
          '<span class="pr-sum"><i class="pr-k pr-h"></i>' + ctx.dur(sumSec(d.human)) + ' <i class="pr-k pr-a"></i>' + ctx.dur(sumSec(d.agent)) + "</span></div>" +
          lanesHtml(L, d.human, d.agent, color) + "</div>";
      }).join("");
      // 末尾固定一格「＋ 新建任务」：拖着东西松在它上面 = 新建任务并记进去；点它 = 新建任务（hive.stage.v1「十二」）
      return '<div class="pr-head"><div></div>' + rulerHtml(L) + '</div><div class="pr-list">' + list +
        '<button type="button" class="pr-newslot" data-project="' + esc(pid) + '">＋ 新建任务</button></div>';
    }
    function frozen() { return !!creating; }   // 新建输入开着时不重画，免得输入框被换掉
    function invalidate() { loadedAt = 0; }
    function reset() { cancelHold(); clearLit(); creating = null; document.body.classList.remove("st-spring"); cur = null; payload = null; }

    // ── 拖动：弹簧展开、任务条落点、空白处新建 ──
    function panelOpen() { return !!cur && !!document.querySelector(".hex-cell.is-expanded .hex-ruler-slot"); }
    function hits(x, y) { return document.elementsFromPoint(x, y); }
    function inPanel(x, y) { return hits(x, y).some(function (e) { return e.classList && e.classList.contains("hex-ruler-slot"); }); }
    function rowAt(x, y) { var h = hits(x, y), i; for (i = 0; i < h.length; i++) if (h[i].classList && h[i].classList.contains("pr-row")) return h[i]; return null; }
    function slotAt(x, y) { var h = hits(x, y), i; for (i = 0; i < h.length; i++) if (h[i].classList && h[i].classList.contains("pr-newslot")) return h[i]; return null; }
    function listAt(x, y) { var h = hits(x, y), i; for (i = 0; i < h.length; i++) if (h[i].classList && h[i].classList.contains("pr-list")) return h[i]; return null; }
    function clearLit() { if (lit) { lit.classList.remove("is-drop"); lit = null; } }
    function cancelHold() { if (hold.timer) clearTimeout(hold.timer); hold.timer = null; hold.key = null; }

    function openCreate(list) {
      if (creating || !list) return;
      var box = document.createElement("div");
      box.className = "pr-new"; box.innerHTML = '<input class="pr-new-name" maxlength="64" aria-label="新任务名" placeholder="新任务名，回车建立">';
      list.insertBefore(box, list.querySelector(".pr-newslot")); box.scrollIntoView({ block: "nearest" });
      creating = { box: box, input: box.firstChild, pid: cur };
    }
    function fire(kind, arg) {
      hold.timer = null;
      if (kind === "cell") ctx.open(arg);
      else if (kind === "blank") openCreate(document.querySelector(".hex-ruler-slot .pr-list"));
    }
    // 拖动中每次指针移动都来一下：cell = 指针下的项目格（没有 = null）
    function dragOver(x, y, cell) {
      var open = panelOpen(), over = open && inPanel(x, y), row = over ? rowAt(x, y) : null, kind = null, arg = null, key = null;
      var slot = over && !row ? slotAt(x, y) : null, target = row || slot;
      document.body.classList.toggle("st-spring", open);
      if (target !== lit) { clearLit(); lit = target; if (target) target.classList.add("is-drop"); }
      if (over) {
        var list = listAt(x, y);
        if (!row && !slot && list && !creating) { kind = "blank"; arg = list; key = "blank:" + cur; }
      } else if (cell) { kind = "cell"; arg = cell.dataset.projectId; key = "cell:" + arg; }
      if (kind === "cell" && open && arg === cur) { kind = null; key = null; }   // 本来就开着这一个
      if (key !== hold.key || Math.abs(x - hold.x) > HOLD_SLOP || Math.abs(y - hold.y) > HOLD_SLOP) {
        cancelHold(); hold.key = key; hold.x = x; hold.y = y;
        if (kind) hold.timer = setTimeout(function () { fire(kind, arg); }, HOLD_MS);
      }
      return over;
    }
    function overPanel(x, y) { return panelOpen() && inPanel(x, y); }
    // 松手：落在任务条 / 新建任务的输入上 → { projectId, taskId } / { projectId, create: true }；否则 null
    function dropAt(x, y) {
      if (!panelOpen() || !inPanel(x, y)) return null;
      var row = rowAt(x, y);
      if (row) return { projectId: row.dataset.project, taskId: row.dataset.task || null };
      if (slotAt(x, y)) { openCreate(document.querySelector(".hex-ruler-slot .pr-list")); creating.waiting = true; return { projectId: creating.pid, create: true }; }
      if (creating && (listAt(x, y) || creating.box.contains(document.elementFromPoint(x, y)))) { creating.waiting = true; return { projectId: creating.pid, create: true }; }
      return null;
    }
    function dragEnd() {
      cancelHold(); clearLit(); document.body.classList.remove("st-spring");
      if (creating && !creating.waiting) { creating.box.remove(); creating = null; }   // 没落在新建输入上：放弃
      if (ctx.changed) ctx.changed();
    }
    // 输入框接着等人打名字：回车建任务并回调 done(taskId)；Esc / 空着失焦放弃（cancel）
    function awaitName(done, cancel) {
      var c = creating; if (!c) { cancel(); return; }
      c.waiting = true; c.input.focus();
      var finish = function () { if (creating === c) creating = null; c.box.remove(); if (ctx.changed) ctx.changed(); };
      c.input.addEventListener("keydown", function (ev) {
        if (ev.key === "Escape") { ev.stopPropagation(); finish(); cancel(); }
        else if (ev.key === "Enter") {
          ev.preventDefault(); ev.stopPropagation();
          var name = c.input.value.trim();
          if (!name) { c.input.classList.add("is-bad"); return; }
          c.input.disabled = true;
          ctx.taskApi().createTask(c.pid, name).then(function (r) {
            if (!r.ok) { c.input.disabled = false; c.input.classList.add("is-bad"); return; }
            finish(); done(r.data && r.data.id);
          });
        }
      });
      c.input.addEventListener("blur", function () { if (!c.input.value.trim() && !c.input.disabled) { finish(); cancel(); } });
    }

    // 点「＋ 新建任务」：长出名字输入，回车建任务（没有要记进去的东西）
    function startCreate() {
      openCreate(document.querySelector(".hex-ruler-slot .pr-list"));
      if (creating) awaitName(function () { if (ctx.created) ctx.created(); }, function () {});
    }
    return { startCreate: startCreate, body: body, spent: spent, frozen: frozen, invalidate: invalidate, reset: reset,
             dragOver: dragOver, dragEnd: dragEnd, dropAt: dropAt, overPanel: overPanel, awaitName: awaitName };
  }

  var api = { minutesText: minutesText, spentText: spentText, layout: layout, linearScale: linearScale, rollingScale: rollingScale, xOf: xOf, spanRect: spanRect, rulerHtml: rulerHtml, lanesHtml: lanesHtml, createPanel: createPanel,
              PAST_SHARE: PAST_SHARE, TODAY_SHARE: TODAY_SHARE };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexProjectRuler = api;
})();
