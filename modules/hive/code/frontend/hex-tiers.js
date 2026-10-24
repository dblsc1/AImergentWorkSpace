// hive · hex-tiers.js —— 三级分类打开时（activity/settings.tiers === true）「AI 决策」面板换成一栏决策栏。
// 一栏按卡片类型分段，顺序固定：AI 问题、待分类窗口块、待批准规则、待批准日程改动、自动化分类历史；没有的段不显示。
// 由 hex-decide.js 在读到 tiers 之后调 start(ctx)：这里只接管 render / load（hooks），滑动 / 拖拽 / 撤销计时 / 提示条都复用 hex-decide 的。
// 数据端点：activity/blocks、blocks/records、blocks/assign|split|generic|ignore|reset、proposals（windowRule / blockRule / blockAction / question）。
// 上半是纯函数（给 hex-tiers.test.js 用），下半是 DOM（文字一律 textContent）。
(function () {
  "use strict";

  function clip(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n) + "…" : s; }
  function two(n) { return ("0" + n).slice(-2); }
  function hhmm(ms) { var d = new Date(ms); return isNaN(d.getTime()) ? "" : two(d.getHours()) + ":" + two(d.getMinutes()); }
  function dur(sec) {
    var m = Math.round((Number(sec) || 0) / 60);
    if (m < 1) return "不到 1 分钟";
    return m < 60 ? m + " 分钟" : Math.floor(m / 60) + " 小时 " + two(m % 60) + " 分";
  }

  // 块列表 → 待分类（pending，以及标了通用窗口还没分的）与自动化历史（今天 assigned 且 via 是规则 / AI 直接施加）
  function splitBlocks(items) {
    var pending = [], hist = [];
    (items || []).forEach(function (b) {
      var parts = b.parts && b.parts.length;
      if (b.status === "pending" || (b.status === "generic" && !parts)) pending.push(b);
      else if (b.status === "assigned" && (b.via === "rule" || b.via === "ai-direct")) hist.push(b);
    });
    hist.sort(function (a, b) { return (Date.parse(b.lastAt) || 0) - (Date.parse(a.lastAt) || 0); });
    return { pending: pending, hist: hist };
  }

  // proposals 响应里各个键下的数组摊平，按 kind 分（老服务端没有这些 kind = 全空）
  var KINDS = { windowRule: "windowRules", blockRule: "blockRules", blockAction: "blockActions" };
  function proposalKinds(resp) {
    var out = { windowRules: [], blockRules: [], blockActions: [] };
    Object.keys(resp || {}).forEach(function (k) {
      if (!Array.isArray(resp[k])) return;
      resp[k].forEach(function (p) {
        if (p && p.id && KINDS[p.kind] && (p.status == null || p.status === "pending")) out[KINDS[p.kind]].push(p);
      });
    });
    return out;
  }

  // GET activity/questions 的响应 → 待回答的问题（没有这个端点 / 读取失败 = null → 空）
  function pendingQuestions(resp) {
    return (resp && Array.isArray(resp.items) ? resp.items : []).filter(function (q) { return q && q.id && q.title && (q.status == null || q.status === "pending"); });
  }
  function qRange(q, now) {   // 问题的日期不是今天时，前面加「MM-DD」
    var a = Date.parse(q.from), b = Date.parse(q.to);
    if (!a || !b) return "";
    var d = new Date(a), t = now || new Date(), day = d.getFullYear() !== t.getFullYear() || d.getMonth() !== t.getMonth() || d.getDate() !== t.getDate();
    return (day ? two(d.getMonth() + 1) + "-" + two(d.getDate()) + " " : "") + hhmm(a) + "–" + hhmm(b);
  }
  function optSig(opts) { return JSON.stringify((opts || []).map(function (o) { return [o.label, o.target || null, !!o.ignore]; })); }

  // 块的建议：同一天同一块名的 blockAction 提议（assign 给目标；别的动作只贡献说明）
  function suggestion(block, date, actions) {
    var out = { target: null, reason: "" };
    (actions || []).forEach(function (a) {
      if (a.blockKey !== block.blockKey || (a.date && date && a.date !== date)) return;
      if (a.reason && !out.reason) out.reason = String(a.reason);
      if (!out.target && (a.action || a.op) === "assign" && (a.taskId || a.projectId)) out.target = a.taskId ? { taskId: a.taskId } : { projectId: a.projectId };
    });
    return out;
  }

  // 下拉框的值 "t:<任务 id>" / "p:<项目 id>"（只到项目 = 项目的未分类）↔ {taskId} / {projectId}
  function parseTarget(v) {
    var m = /^([tp]):(.+)$/.exec(v || "");
    return m ? (m[1] === "t" ? { taskId: m[2] } : { projectId: m[2] }) : null;
  }
  function targetValue(t) { return t ? (t.taskId ? "t:" + t.taskId : t.projectId ? "p:" + t.projectId : "") : ""; }
  function blockTarget(b) {   // 已分好的块的第一段去向
    var p = b.parts && b.parts[0];
    return p && (p.taskId || p.projectId) ? (p.taskId ? { taskId: p.taskId, projectId: p.projectId } : { projectId: p.projectId }) : null;
  }

  // 拆分条：记录的时间范围（毫秒）、默认拆分点、拖动时的夹取、拆分点 → parts
  function rangeOf(records) {
    var t0 = Infinity, t1 = -Infinity, list = [];
    (records || []).forEach(function (r) {
      var s = Date.parse(r.startAt), e = Date.parse(r.endAt);
      if (isNaN(s) || isNaN(e)) return;
      list.push({ s: s, e: Math.max(e, s), app: r.app, title: r.title });
      t0 = Math.min(t0, s); t1 = Math.max(t1, e);
    });
    if (!list.length) return null;
    if (t1 - t0 < 2000) t1 = t0 + 2000;
    return { t0: t0, t1: t1, list: list };
  }
  var MIN_GAP = 1000;
  // 拆分点落进记录之间的空隙时吸附到最近的记录边界（空隙两端）；在记录里面就不动
  function snapPoint(list, ts) {
    var prevEnd = null, nextStart = null;
    for (var i = 0; i < list.length; i++) {
      var r = list[i];
      if (ts >= r.s && ts <= r.e) return ts;
      if (r.e < ts && (prevEnd == null || r.e > prevEnd)) prevEnd = r.e;
      if (r.s > ts && (nextStart == null || r.s < nextStart)) nextStart = r.s;
    }
    if (prevEnd == null) return nextStart == null ? ts : nextStart;
    if (nextStart == null) return prevEnd;
    return ts - prevEnd <= nextStart - ts ? prevEnd : nextStart;
  }
  // 每一段里都得有记录（不发空段）
  function partsHaveRecords(list, points, t0, t1) {
    var edges = [t0].concat(points, [t1]);
    for (var i = 0; i + 1 < edges.length; i++) {
      var a = edges[i], b = edges[i + 1], on = i + 2 === edges.length;
      if (!list.some(function (r) { return (r.e > a && r.s < b) || (r.s >= a && (r.s < b || (on && r.s <= b))); })) return false;
    }
    return true;
  }
  function addPoint(points, t0, t1, list) {   // 加在最宽的那一段正中
    var edges = [t0].concat(points, [t1]), best = 0;
    for (var i = 1; i < edges.length - 1; i++) if (edges[i + 1] - edges[i] > edges[best + 1] - edges[best]) best = i;
    if (edges[best + 1] - edges[best] < 2 * MIN_GAP) return points;
    var p = Math.round((edges[best] + edges[best + 1]) / 2);
    if (list) p = snapPoint(list, p);
    if (edges.some(function (e) { return Math.abs(e - p) < MIN_GAP; })) return points;
    var out = points.concat([p]);
    return out.sort(function (a, b) { return a - b; });
  }
  function clampPoint(points, i, ts, t0, t1) {   // 不越过相邻拆分点 / 两端，至少隔 1 秒
    var lo = (i > 0 ? points[i - 1] : t0) + MIN_GAP, hi = (i < points.length - 1 ? points[i + 1] : t1) - MIN_GAP;
    return Math.round(Math.min(Math.max(ts, lo), Math.max(lo, hi)));
  }
  // 首段的 fromTs / 末段的 toTs 填块里实际的首尾（毫秒），不用 null
  function partsFromPoints(points, targets, t0, t1) {
    return targets.map(function (t, i) {
      var o = { fromTs: i === 0 ? t0 : points[i - 1], toTs: i === points.length ? t1 : points[i] }, g = parseTarget(t) || {};
      if (g.taskId) o.taskId = g.taskId; else if (g.projectId) o.projectId = g.projectId;
      return o;
    });
  }

  var api = { optSig: optSig, pendingQuestions: pendingQuestions, qRange: qRange, splitBlocks: splitBlocks, proposalKinds: proposalKinds, suggestion: suggestion, parseTarget: parseTarget, targetValue: targetValue,
    blockTarget: blockTarget, rangeOf: rangeOf, addPoint: addPoint, clampPoint: clampPoint, partsFromPoints: partsFromPoints, snapPoint: snapPoint, partsHaveRecords: partsHaveRecords, dur: dur, hhmm: hhmm };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined" || typeof window === "undefined") return;
  window.NexusTableHexTiers = api;

  // ── DOM ───────────────────────────────────────────────────────────
  api.start = function (X) {
    var panel = X.panel, el = X.el, btn = X.btn, toast = X.toast, App = X.App, ACT = X.ACT, DEC = window.NexusTableHexDecide;
    var S = { questions: [], report: null, reportOpen: false, qerr: {}, res: null, failed: false, date: "", chosen: {}, busy: {}, open: {}, splits: {}, histAll: false };
    var dragHandle = false, scrollTop = 0, reqN = 0, qN = 0, writing = 0, pendingRender = false;
    panel.dataset.tiers = "1";
    panel.setAttribute("aria-label", "AI 决策");

    function tb(text, act, cls) { var b = el("button", cls || "hd-btn", text); b.type = "button"; b.dataset.ht = act; return b; }
    function dcOf(c) { return c.dc || { key: c.key, name: c.name, verb: "rule", n: 1, items: [{ path: "proposals/" + c.key, newItem: false }] }; }
    function tree() { return App.getState().tree; }
    function resolve(t) { return DEC.lookup(tree(), t && t.projectId, t && t.taskId); }
    function pathOf(t) { return t ? DEC.pathText(resolve(t)) : "↓ ？"; }

    // ── 请求 ──
    function post(path, body) {
      return fetch(ACT + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(function (res) {
        return res.json().catch(function () { return null; }).then(function (b) { return { ok: res.ok, status: res.status, body: b }; });
      }).catch(function () { return { ok: false, status: 0, body: null }; });
    }
    function why(r) { return r.status === 0 ? "连不上服务" : (r.body && typeof r.body.detail === "string") ? r.body.detail : "请求失败（" + r.status + "）"; }
    function loadBlocks() {
      var n = ++reqN;
      return X.getJson(ACT + "blocks?status=all&limit=500").then(function (r) {
        if (n !== reqN) return;   // 更晚发出的请求已经回来了：这份是过期的
        if (r && Array.isArray(r.items)) { S.res = r; S.date = r.date || S.date; S.failed = false; } else S.failed = true;
        X.render();
      });
    }
    // AI 问题 / 秘书报告：404 = 端点不存在 = 什么都没有；别的失败保留旧值
    function loadAI() {
      var n = ++qN;
      return Promise.all([X.getRaw(ACT + "questions?status=pending"), X.getRaw(ACT + "secretary-reports")]).then(function (r) {
        if (n !== qN) return;
        if (r[0].status === 404) S.questions = []; else if (r[0].body) S.questions = api.pendingQuestions(r[0].body);
        if (r[1].status === 404) S.report = null;
        else if (r[1].body) { var rep = Array.isArray(r[1].body.items) ? r[1].body.items[0] : null; S.report = rep && rep.text ? rep : null; }
        X.render();
      });
    }
    function items() { return S.failed || !S.res ? [] : S.res.items; }
    function curItem(key) {
      return ((S.res && S.res.items) || []).filter(function (x) { return x.blockKey === key; })[0] || null;
    }
    function pendingLike(b) { return b.status === "pending" || (b.status === "generic" && !(b.parts && b.parts.length)); }
    // 写块：按 date+blockKey 串行（上一步没回来就不发第二个）；成功 → 把响应并进「现在」的那一块（不是发请求时捕获的旧对象）；
    // 409 → 重读，块还是待处理就用新 seq 自动重试一次，否则轻提示；别的失败 → {err}。opts.quiet = 409 不提示（撤销自己报错）
    function write(kind, key, extra, opts) {
      opts = opts || {};
      var date = S.date, bk = date + "|" + key;
      if (S.busy[bk]) return Promise.resolve({ ok: false, busy: true, err: "上一步还没完成" });
      S.busy[bk] = 1; writing++;
      function once(attempt) {
        var it = curItem(key), body = { date: date, blockKey: key, ifSeq: it ? it.seq : 0 };
        Object.keys(extra || {}).forEach(function (k) { body[k] = extra[k]; });
        return post("blocks/" + kind, body).then(function (r) {
          if (r.status !== 409) return r;
          return loadBlocks().then(function () {
            var n = curItem(key);
            if (attempt === 0 && !opts.quiet && kind !== "reset" && n && pendingLike(n)) return once(1);
            r.conflict = true; return r;
          });
        });
      }
      return once(0).then(function (r) {
        S.busy[bk] = 0; writing--;
        if (r.conflict) { if (!opts.quiet) toast("这一块刚被改过，已刷新"); return { ok: false, err: why(r), conflict: true }; }
        if (!r.ok) return { ok: false, err: why(r) };
        var b = r.body || {}, item = curItem(key);
        if (kind === "assign") delete S.chosen[bk];
        if (item && !(typeof b.seq === "number" && item.seq > b.seq)) {
          item.seq = typeof b.seq === "number" ? b.seq : (item.seq || 0) + 1;
          if (b.status !== undefined) { item.status = b.status; item.via = b.via; item.parts = b.parts; }
          else if (kind === "reset") { item.status = item.generic ? "generic" : "pending"; item.parts = null; item.via = null; }
          else if (kind === "assign") { if (item.status !== "generic") item.status = "assigned"; item.via = "human"; item.parts = [Object.assign({ fromTs: null, toTs: null }, extra.taskId ? { taskId: extra.taskId } : {}, extra.projectId ? { projectId: extra.projectId } : {})]; }
          else if (kind === "ignore") { item.status = "ignored"; item.via = "human"; }
        }
        return { ok: true, body: b };
      });
    }
    function fail(name, r) { if (!r.conflict && !r.busy) toast("没办成：" + name + "：" + r.err, { error: true, ms: 6000 }); X.render(); }

    // 撤销 = 按 date+blockKey 取「现在」的块再 reset；失败如实说撤销失败
    function undoToast(text, key, name) {
      var t = toast(text, { ms: X.UNDO_MS, undo: function () {
        t.remove();
        write("reset", key, {}, { quiet: true }).then(function (r) {
          if (!r.ok) { toast("撤销失败：" + name, { error: true, ms: 6000 }); X.render(); return; }
          return loadBlocks();
        });
      } });
    }

    // ── 块卡片 ──
    function blockCards() {
      var sp = api.splitBlocks(items()), acts = S.acts || [];
      return sp.pending.map(function (b) {
        var sg = api.suggestion(b, S.date, acts), key = "blk:" + b.blockKey;
        var picked = S.chosen[S.date + "|" + b.blockKey] ? api.parseTarget(S.chosen[S.date + "|" + b.blockKey]) : null;
        return { key: key, name: b.blockKey, item: b, generic: b.status === "generic", target: picked || sg.target, reason: sg.reason };
      });
    }
    function approveBlock(c, cardEl) {
      if (!c.target) { toast("先选去向"); return false; }
      if (S.busy[S.date + "|" + c.name]) return false;
      X.collapseEl(cardEl);
      write("assign", c.name, { taskId: c.target.taskId, projectId: c.target.taskId ? undefined : c.target.projectId, remember: !c.generic }).then(function (r) {
        if (!r.ok) { fail(c.name, r); return; }
        undoToast("已批准：" + c.name, c.name, c.name); X.render();
      });
    }
    function ignoreBlock(c, cardEl) {
      if (S.busy[S.date + "|" + c.name]) return false;
      X.collapseEl(cardEl);
      write("ignore", c.name, { forever: false }).then(function (r) {
        if (!r.ok) { fail(c.name, r); return; }
        undoToast("已忽略：" + c.name, c.name, c.name); X.render();
      });
    }
    function swipeBlock(c, decision, cardEl) { return decision === "approve" ? approveBlock(c, cardEl) : ignoreBlock(c, cardEl); }

    function targetSelect(value, onChange) {
      var s = document.createElement("select"), t = tree() || {};
      s.className = "ht-select"; s.setAttribute("aria-label", "去向");
      s.appendChild(new Option("选去向…", ""));
      (t.projects || []).forEach(function (p) {
        var g = document.createElement("optgroup"); g.label = p.name;
        g.appendChild(new Option(p.name + " - 未分类", "p:" + p.id));
        (p.tasks || []).forEach(function (k) { if (!k.done) g.appendChild(new Option(p.name + " - " + k.name, "t:" + k.id)); });
        s.appendChild(g);
      });
      s.value = value || "";
      s.addEventListener("change", function () { s.blur(); onChange(s.value); });   // 先失焦：选完的这次重画不被「select 有焦点就推迟」拦住
      return s;
    }

    function blockCard(c) {
      var b = c.item, n = el("div", "hd-card ht-block" + (c.target ? "" : " is-ignore")), key = c.name;
      n.dataset.key = c.key; n.dataset.block = key; n.tabIndex = 0;
      n.setAttribute("aria-label", key + "，按 A 批准，按 R 忽略");
      var ok = el("div", "hd-under is-ok", "批准"), no = el("div", "hd-under is-no is-skip", "忽略");
      ok.hidden = no.hidden = true;
      var f = el("div", "hd-face");
      if (c.generic) f.appendChild(el("span", "ht-badge", "通用窗口"));
      f.appendChild(el("div", "hd-win", clip(key, 80)));
      f.appendChild(el("div", "hd-to", clip(pathOf(c.target), 100)));
      var line = dur(b.seconds) + (b.records > 1 ? " · " + b.records + " 段" : "");
      f.appendChild(el("div", "hd-why", c.reason ? "AI：" + clip(c.reason, 100) + " · " + line : line));
      var acts = el("div", "hd-acts");
      [["split", "拆分"], [c.generic ? "ungeneric" : "generic", c.generic ? "取消通用窗口" : "标为通用窗口"], ["dest", "改去向"]].concat(c.target ? [["approve", "批准"]] : [])
        .forEach(function (a) { acts.appendChild(tb(a[1], a[0])); });
      f.appendChild(acts);
      if (S.open[key] === "dest") {
        var row = el("div", "ht-edit");
        row.appendChild(targetSelect(api.targetValue(c.target), function (v) { var ck = S.date + "|" + key; if (v) S.chosen[ck] = v; else delete S.chosen[ck]; S.open[key] = null; X.render(); }));
        f.appendChild(row);
      }
      if (S.open[key] === "split") f.appendChild(splitView(c));
      n.appendChild(ok); n.appendChild(no); n.appendChild(f);
      if (c.target) { var tg = resolve(c.target); X.tint(n, tg.projectId, tg.zoneId); }
      X.bindSwipe(n, f, ok, no, c, swipeBlock);
      return n;
    }

    // ── 拆分 ──
    function openSplit(key, c) {
      if (S.open[key] === "split") return;
      S.open[key] = "split"; S.splits[key] = { loading: true };
      X.render();
      X.getJson(ACT + "blocks/records?date=" + encodeURIComponent(S.date) + "&blockKey=" + encodeURIComponent(key)).then(function (r) {
        var rg = r && api.rangeOf(r.records || r.items);
        if (!rg) { toast("读不到这一块的记录", { error: true }); delete S.open[key]; delete S.splits[key]; X.render(); return; }
        var pts = api.addPoint([], rg.t0, rg.t1, rg.list);
        S.splits[key] = { rg: rg, points: pts, targets: [c.target ? api.targetValue(c.target) : "", ""] };
        X.render();
      });
    }
    function closeSplit(key) { delete S.open[key]; delete S.splits[key]; X.render(); }

    function splitView(c) {
      var key = c.name, st = S.splits[key], w = el("div", "ht-split");
      if (!st || st.loading) { w.appendChild(el("div", "hd-why", "…")); return w; }
      var rg = st.rg, span = rg.t1 - rg.t0, bar = el("div", "ht-bar"), handles = [];
      var bars = rg.list.map(function (r) {
        var x = el("div", "ht-rec");
        x.style.left = ((r.s - rg.t0) / span * 100) + "%"; x.style.width = (Math.max(r.e - r.s, 0) / span * 100) + "%";
        x.title = clip((r.app || "") + (r.title ? " · " + r.title : ""), 80);
        bar.appendChild(x); return x;
      });
      var times = el("div", "ht-times"), parts = el("div", "ht-parts"), labels = [];
      function segOf(ts) { var i = 0; while (i < st.points.length && ts >= st.points[i]) i++; return i; }
      function sync() {
        handles.forEach(function (h, i) {
          h.style.left = ((st.points[i] - rg.t0) / span * 100) + "%";
          h.setAttribute("aria-valuenow", String(st.points[i])); h.setAttribute("aria-valuetext", hhmm(st.points[i]));
          h.title = hhmm(st.points[i]);
        });
        bars.forEach(function (x, i) {
          var tg = resolve(api.parseTarget(st.targets[segOf(rg.list[i].s)])), cell = tg.projectId && X.cellFor(tg.projectId, tg.zoneId);
          x.style.background = cell ? "color-mix(in srgb, " + cell.color + " 70%, black)" : "var(--ink-3)";
        });
        times.textContent = [hhmm(rg.t0)].concat(st.points.map(hhmm), [hhmm(rg.t1)]).join(" · ");
        labels.forEach(function (l, i) {
          var a = i === 0 ? rg.t0 : st.points[i - 1], b = i === st.points.length ? rg.t1 : st.points[i];
          l.textContent = "第 " + (i + 1) + " 块 · " + hhmm(a) + "–" + hhmm(b) + " · " + dur((b - a) / 1000);
        });
      }
      st.points.forEach(function (p, i) {
        var h = el("div", "ht-handle"); h.tabIndex = 0; h.setAttribute("role", "slider");
        h.setAttribute("aria-label", "拆分点 " + (i + 1)); h.setAttribute("aria-valuemin", String(rg.t0)); h.setAttribute("aria-valuemax", String(rg.t1));
        function moveTo(ts, snap) { st.points[i] = api.clampPoint(st.points, i, snap ? api.snapPoint(rg.list, ts) : ts, rg.t0, rg.t1); sync(); }
        h.addEventListener("pointerdown", function (ev) {
          if (ev.button !== undefined && ev.button !== 0) return;
          ev.preventDefault(); ev.stopPropagation(); dragHandle = true;
          try { h.setPointerCapture(ev.pointerId); } catch (e) { /* 合成事件没有活动指针 */ }
        });
        h.addEventListener("pointermove", function (ev) {
          if (!dragHandle) return;
          var box = bar.getBoundingClientRect();
          moveTo(rg.t0 + (ev.clientX - box.left) / Math.max(box.width, 1) * span, true);   // 落进无记录的空隙就吸附到记录边界
        });
        function up() { dragHandle = false; flushRender(); }
        h.addEventListener("pointerup", up); h.addEventListener("pointercancel", up); h.addEventListener("lostpointercapture", up);   // 指针丢了也要放开，不然所有刷新被挡
        h.addEventListener("keydown", function (ev) {
          var d = ev.key === "ArrowLeft" ? -1 : ev.key === "ArrowRight" ? 1 : 0;
          if (d) { ev.preventDefault(); moveTo(st.points[i] + d * Math.max(1000, Math.round(span / 100))); }
        });
        handles.push(h); bar.appendChild(h);
      });
      st.targets.forEach(function (v, i) {
        var cell = el("div", "ht-part"); labels.push(el("div", "ht-part-l"));
        cell.appendChild(labels[i]);
        cell.appendChild(targetSelect(v, function (nv) { st.targets[i] = nv; var tg = resolve(api.parseTarget(nv)); if (tg.projectId) X.tint(cell, tg.projectId, tg.zoneId); sync(); }));
        var tg0 = resolve(api.parseTarget(v)); if (tg0.projectId) X.tint(cell, tg0.projectId, tg0.zoneId);
        parts.appendChild(cell);
      });
      var acts = el("div", "hd-acts");
      [["split-ok", "确认拆分"], ["split-add", "再加一个拆分点"], ["split-cancel", "取消"]].forEach(function (a) {
        var x = tb(a[1], a[0]); if (a[0] === "split-add" && st.targets.length >= 20) x.disabled = true; acts.appendChild(x);
      });
      w.appendChild(bar); w.appendChild(times); w.appendChild(parts); w.appendChild(acts);
      sync();
      return w;
    }
    function confirmSplit(key) {
      var st = S.splits[key];
      if (!st || !st.rg) return;
      if (st.targets.some(function (t) { return !t; })) { toast("每一块都要选去向"); return; }
      if (!api.partsHaveRecords(st.rg.list, st.points, st.rg.t0, st.rg.t1)) { toast("有一段里没有记录，请挪动拆分点"); return; }
      write("split", key, { parts: api.partsFromPoints(st.points, st.targets, st.rg.t0, st.rg.t1) }).then(function (r) {
        if (!r.ok) { if (r.conflict) closeSplit(key); else fail(key, r); return; }
        delete S.open[key]; delete S.splits[key];
        undoToast("已拆分：" + key, key, key); X.render();
      });
    }

    // ── 规则卡片 ──
    function ruleCards(k) {
      var out = [];
      k.windowRules.forEach(function (p) {
        var r = p.rule || {}, del = p.op === "delete";
        out.push({ key: p.id, p: p, level: 1, tone: del ? "del" : "", name: (r.app || "") + (r.title ? (r.app ? " 标题含「" : "标题含「") + r.title + "」" : "") || "窗口规则",
          to: del ? "↓ 删除这条规则" : "↓ 收进窗口块「" + (r.block || "") + "」", reason: p.reason, target: null, block: r.block || "" });
      });
      k.blockRules.forEach(function (p) {
        var del = p.op === "delete", t = p.taskId ? { taskId: p.taskId } : p.projectId ? { projectId: p.projectId } : null;
        out.push({ key: p.id, p: p, level: 2, tone: del ? "del" : "", name: "窗口块「" + (p.blockKey || "") + "」", to: del ? "↓ 删除这条规则" : pathOf(t), reason: p.reason, target: t });
      });
      return out;
    }
    function ruleCard(c) {
      var n = el("div", "hd-card ht-rule" + (c.tone ? " is-" + c.tone : "")), p = c.p;
      n.dataset.key = c.key; n.tabIndex = 0;
      n.setAttribute("aria-label", c.name + "，按 A 批准，按 R 忽略");
      var ok = el("div", "hd-under is-ok", "批准"), no = el("div", "hd-under is-no is-skip", "忽略");
      ok.hidden = no.hidden = true;
      var f = el("div", "hd-face");
      f.appendChild(el("span", "ht-badge", c.level === 1 ? "第一级 · 收纳" : "第二级 · 去向"));
      f.appendChild(el("div", "hd-win", clip(c.name, 80)));
      f.appendChild(el("div", "hd-to", clip(c.to, 100)));
      if (c.reason) f.appendChild(el("div", "hd-why", clip("AI：" + c.reason, 140)));
      if (S.open[c.key] === "fix") {
        var row = el("div", "ht-edit"), go = tb("确定", "fix-ok"), inp;
        if (c.level === 1) { inp = document.createElement("input"); inp.type = "text"; inp.maxLength = 64; inp.value = c.block; inp.setAttribute("aria-label", "块名"); inp.className = "ht-input"; }
        else { inp = targetSelect(api.targetValue(c.target), function () { /* 点「确定」才提交 */ }); }
        inp.dataset.fix = "1"; row.appendChild(inp); row.appendChild(go); f.appendChild(row);
      }
      var acts = el("div", "hd-acts");
      [["rule-ok", "批准"], ["rule-fix", "更正"], ["rule-no", "忽略"]].forEach(function (a) { acts.appendChild(tb(a[1], a[0])); });
      f.appendChild(acts);
      n.appendChild(ok); n.appendChild(no); n.appendChild(f);
      var t = c.target ? resolve(c.target) : null;
      if (t && !c.tone) X.tint(n, t.projectId, t.zoneId);
      X.bindSwipe(n, f, ok, no, dcOf(c));
      return n;
    }
    // 批准时更正（rule-fix / sess-ok 共用）：按 id 防重复点，进行中按钮禁用
    function approveEdit(id, edit, name, button) {
      var bk = "fix:" + id;
      if (S.busy[bk]) return;
      S.busy[bk] = 1; writing++; if (button) button.disabled = true;
      post("proposals/" + encodeURIComponent(id) + "/approve", { edit: edit }).then(function (r) {
        S.busy[bk] = 0; writing--;
        if (!r.ok) { if (button) button.disabled = false; toast("没办成：" + name + "：" + why(r), { error: true, ms: 6000 }); flushRender(); return; }
        delete S.open[id]; toast("已批准（更正）：" + name); X.refreshAll();
      });
    }
    function fixRule(c, cardEl) {
      var inp = cardEl.querySelector("[data-fix]"), edit;
      if (c.level === 1) { var name = inp.value.trim().slice(0, 64); if (!name) return; edit = { rule: { block: name } }; }
      else { var t = api.parseTarget(inp.value); if (!t) { toast("先选去向"); return; } edit = t; }
      approveEdit(c.key, edit, c.name, cardEl.querySelector('[data-ht="fix-ok"]'));
    }

    // ── 其它卡片 ──
    function questionCard(q) {
      var n = el("div", "hd-card ht-question is-ignore"), f = el("div", "hd-face"), rg = api.qRange(q), busy = !!S.busy["q:" + q.id];
      n.dataset.key = q.id; n.__opts = q.options || []; n.__sig = api.optSig(q.options);   // 渲染时的选项快照：按钮下标对的是它
      f.appendChild(el("span", "ht-badge", "AI 提问"));
      f.appendChild(el("div", "hd-win", clip(q.title, 120)));
      if (q.text) f.appendChild(el("div", "hd-why ht-qtext", clip(q.text, 1000)));
      if (rg) f.appendChild(el("div", "hd-why", rg));
      var opts = el("div", "ht-opts");
      n.__opts.forEach(function (o, i) {
        var b = tb("", "q-opt", "hd-btn ht-opt"); b.dataset.i = String(i);
        b.appendChild(el("span", null, clip(o.label, 40)));
        if (o.target && rg) {   // 没有时间段的问题，回答不会记时间：不写去向
          b.appendChild(el("span", "ht-opt-to", pathOf(o.target) + " · " + rg));
          if (!resolve(o.target).projectName) b.disabled = true;
        }
        if (busy) b.disabled = true;
        opts.appendChild(b);
      });
      if (opts.firstChild) f.appendChild(opts);
      if (q.allowText) {
        var row = el("div", "ht-edit"), inp = document.createElement("input"), go = tb("提交", "q-text");
        inp.type = "text"; inp.maxLength = 1000; inp.className = "ht-input ht-qinput"; inp.setAttribute("aria-label", "回答");
        if (busy) go.disabled = true;
        row.appendChild(inp); row.appendChild(go); f.appendChild(row);
      }
      if (S.qerr[q.id]) { var er = el("div", "hd-err", S.qerr[q.id]); er.setAttribute("role", "alert"); f.appendChild(er); }
      var acts = el("div", "hd-acts"), no = tb("撤下", "q-no"); if (busy) no.disabled = true;
      acts.appendChild(no); f.appendChild(acts);
      n.appendChild(f); return n;
    }
    function answerQ(card, body) {
      var id = card.dataset.key, bk = "q:" + id, q = S.questions.filter(function (x) { return x.id === id; })[0];
      if (!q || S.busy[bk] || X.held[id]) return;
      if (api.optSig(q.options) !== card.__sig) { toast("问题已更新，请重选"); X.render(); return; }
      var o = body.option != null ? card.__opts[body.option] : null, title = clip(q.title, 40);
      if (body.option != null && !o) return;
      S.busy[bk] = 1; writing++; delete S.qerr[id];
      [].forEach.call(card.querySelectorAll("button"), function (x) { x.disabled = true; });
      post("questions/" + encodeURIComponent(id) + "/answer", body).then(function (r) {
        S.busy[bk] = 0; writing--;
        if (!r.ok) {
          S.qerr[id] = why(r);
          toast("没办成：" + title + "：" + S.qerr[id], { error: true, ms: 6000 });
          if (r.status > 0) loadAI(); else X.render();
          return;
        }
        var a = r.body && r.body.answer;
        toast(a && a.recorded && o && o.target ? "已记到 " + pathOf(o.target).slice(2) : "已回答：" + title);
        qN++; S.questions = S.questions.filter(function (x) { return x.id !== id; }); delete S.qerr[id];
        if (card.contains(document.activeElement)) {   // 焦点在本卡里：交给下一张卡（没有就面板标题）；别的控件的焦点不动
          var all = [].slice.call(panel.querySelectorAll(".hd-card")), i = all.indexOf(card), nx = all[i + 1] || all[i - 1] || panel.querySelector(".hd-head");
          if (nx) nx.focus({ preventScroll: true });
        }
        X.render();
      });
    }
    function reportCard(r) {
      var open = S.reportOpen, n = el("div", "hd-card ht-report"), f = el("div", "hd-face");
      n.dataset.key = "report";
      f.appendChild(el("span", "ht-badge", "秘书报告"));
      f.appendChild(el("div", "hd-why ht-rtext" + (open ? " is-open" : ""), clip(r.text, 8000)));
      var acts = el("div", "hd-acts"), tg = tb(open ? "收起" : "展开", "report-toggle");
      tg.setAttribute("aria-expanded", String(open)); acts.appendChild(tg); f.appendChild(acts);
      n.appendChild(f); return n;
    }
    // 待批准日程改动里的 sessionChange 卡：pendCard 原样，多一个「更正」（批准时换去向）
    function sessCard(c) {
      var n = X.pendCard(c);
      if (c.verb !== "session") return n;
      var f = n.querySelector(".hd-face"), acts = n.querySelector(".hd-acts");
      if (S.open[c.key] === "fix") {
        var row = el("div", "ht-edit"), sel = targetSelect("", function () { /* 点「确定」才提交 */ });
        sel.dataset.fix = "1"; row.appendChild(sel); row.appendChild(tb("确定", "sess-ok")); f.insertBefore(row, acts);
      }
      acts.appendChild(tb("更正", "sess-fix"));
      return n;
    }
    function histCard(b) {
      var n = el("div", "hd-card hd-hcard ht-hist"), f = el("div", "hd-face"), t = api.blockTarget(b), tg = resolve(t);
      n.dataset.block = b.blockKey;
      f.appendChild(el("div", "hd-win", clip(b.blockKey, 80)));
      f.appendChild(el("div", "hd-to", clip(pathOf(t), 100)));
      var m = el("div", "hd-meta");
      m.appendChild(el("span", "hd-tag", b.via === "ai-direct" ? "AI 直接施加" : "第二级规则"));
      m.appendChild(el("span", null, hhmm(Date.parse(b.lastAt)) + " · " + dur(b.seconds)));
      m.appendChild(tb("撤销", "undo"));
      f.appendChild(m); n.appendChild(f);
      X.tint(n, tg.projectId, tg.zoneId);
      var it = { title: b.blockKey, app: "", eventId: "blk:" + b.blockKey };
      it.send = function (body) {
        return write("assign", b.blockKey, { taskId: body.taskId, projectId: body.taskId ? undefined : body.projectId, remember: false }).then(function (r) { return r.ok ? null : r.err; });
      };
      it.onMoved = function (body) {
        var g = resolve(body);
        toast("已改归属 → " + (g.projectName ? DEC.pathText(g).slice(2) : "所选的目标")); X.render();
      };
      X.bindDrag(f, it);
      return n;
    }

    // ── 汇总 / 渲染 ──
    function compute() {
      var k = api.proposalKinds(X.raw().p), sp = api.splitBlocks(items());
      S.acts = k.blockActions;
      var held = X.held;
      return [
        { id: "q", title: "AI 问题", cards: S.questions.filter(function (q) { return !held[q.id]; }), make: questionCard },
        { id: "b", title: "待分类窗口块", cards: blockCards(), make: blockCard },
        { id: "r", title: "待批准规则", cards: ruleCards(k).filter(function (c) { return !held[c.key]; }), make: ruleCard },
        { id: "s", title: "待批准日程改动", cards: X.model().cards.filter(function (c) { return !held[c.key]; }), make: sessCard },
        { id: "h", title: "自动化分类历史", cards: sp.hist, make: histCard }
      ].map(function (s) { if (s.count == null) s.count = s.cards.length; return s; });
    }
    function total(secs) { return secs.reduce(function (n, s) { return n + (s.id === "h" ? 0 : s.count); }, 0); }
    // 计数用 render 时算好的那份（每次 updateBadge 不再全量重算）；只把撤销窗口里的规则 / 日程卡再扣一遍
    function recount() {
      var secs = S.cache;
      if (!secs) return;
      var counts = {};
      secs.forEach(function (x) { counts[x.id] = (x.id === "r" || x.id === "s" || x.id === "q") ? x.cards.filter(function (c) { return !X.held[c.key]; }).length : x.count; });
      [].forEach.call(panel.querySelectorAll(".ht-cnt[data-sec]"), function (n) {
        n.textContent = n.dataset.sec === "h" ? "今天 " + counts.h + " 条" : String(counts[n.dataset.sec]);
      });
      var b = panel.querySelector(".hd-badge");
      if (b) b.textContent = String(counts.q + counts.b + counts.r + counts.s);
    }
    function uiBusy() {
      var a = document.activeElement;
      return dragHandle || !!(a && panel.contains(a) && /^(SELECT|INPUT)$/.test(a.tagName));
    }
    function keyOf(n) { return n.dataset.key || n.dataset.block; }

    function render() {
      if (uiBusy()) { pendingRender = true; return; }   // 下拉开着 / 正在拖把手：别整栏重画，结束后补一次
      pendingRender = false;
      var secs = compute(), body0 = panel.querySelector(".ht-body");
      S.cache = secs;
      if (body0) scrollTop = body0.scrollTop;
      var fa = document.activeElement, fk = null, fh = null;
      if (fa && panel.contains(fa)) {
        var fc = fa.closest("[data-key],[data-block]");
        fk = fc && keyOf(fc); fh = fa.dataset && fa.dataset.ht;
        if (fa.classList.contains("hd-head")) fh = "__head";
      }
      var sum = total(secs), hist = secs[4].count;
      panel.hidden = !sum && !hist && !S.failed;
      panel.textContent = "";
      panel.dataset.collapsed = X.collapsed() ? "1" : "0";
      var head = btn("hd-head", "", "toggle");
      head.setAttribute("aria-expanded", String(!X.collapsed()));
      head.appendChild(el("span", "hd-title", "AI 决策"));
      head.appendChild(el("span", "hd-badge", String(sum)));
      head.appendChild(el("span", "hd-chev", X.collapsed() ? "▾" : "▴"));
      panel.appendChild(head);
      if (X.collapsed()) { if (fh === "__head") head.focus({ preventScroll: true }); return; }
      var body = el("div", "ht-body"), any = false;
      if (S.failed) { var er = el("div", "ht-err", "读取失败，稍后重试"); er.setAttribute("role", "alert"); body.appendChild(er); any = true; }
      secs.forEach(function (s) {
        var rep = s.id === "q" && S.report;
        if (!s.count && !rep) return;
        any = true;
        var h = el("div", "ht-sec");
        h.appendChild(el("span", "ht-sec-t", s.title));
        if (s.count) { var cnt = el("span", "ht-cnt", s.id === "h" ? "今天 " + s.count + " 条" : String(s.count)); cnt.dataset.sec = s.id; h.appendChild(cnt); }
        var wrap = el("section", "ht-secbody"); wrap.dataset.sec = s.id; wrap.appendChild(h);
        if (rep) wrap.appendChild(reportCard(S.report));
        var list = s.id === "h" && !S.histAll ? s.cards.slice(0, 5) : s.cards;
        list.forEach(function (c) { wrap.appendChild(s.make(c)); });
        if (s.id === "h" && s.cards.length > 5 && !S.histAll) { wrap.appendChild(tb("展开全部", "hist-all", "hd-btn ht-more")); }
        body.appendChild(wrap);
      });
      if (!any) body.appendChild(el("div", "hd-empty", "没有待决策的"));
      panel.appendChild(body);
      var rt = body.querySelector(".ht-rtext");
      if (rt && !S.reportOpen && rt.scrollHeight <= rt.clientHeight + 1) { var ra = body.querySelector('[data-ht="report-toggle"]'); if (ra) ra.parentNode.remove(); }   // 三行以内不用展开
      body.scrollTop = scrollTop;
      if (fh === "__head") head.focus({ preventScroll: true });
      else if (fk) {   // 重画前焦点在哪张卡（哪个按钮）上，就还给新画出来的那张
        var nc = [].filter.call(panel.querySelectorAll("[data-key],[data-block]"), function (n) { return keyOf(n) === fk; })[0];
        var tgt = nc && ((fh && nc.querySelector('[data-ht="' + fh + '"]')) || nc);
        if (tgt && tgt.focus) tgt.focus({ preventScroll: true });
      }
    }
    function flushRender() { window.setTimeout(function () { if (pendingRender && !uiBusy()) X.render(); }, 0); }
    panel.addEventListener("focusout", flushRender);

    // ── 事件 ──
    function blockOf(node) {
      var card = node.closest(".ht-block"), key = card && card.dataset.block;
      return { card: card, c: key && blockCards().filter(function (x) { return x.name === key; })[0] };
    }
    function ruleOf(node) {
      var card = node.closest(".ht-rule"), key = card && card.dataset.key;
      return { card: card, c: key && ruleCards(api.proposalKinds(X.raw().p)).filter(function (x) { return x.key === key; })[0] };
    }
    panel.addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-ht]"), kind = b && b.dataset.ht;
      if (!kind) return;
      var k = blockOf(b);
      if (k.card && k.card.__swiped) { k.card.__swiped = false; return; }
      if (kind === "report-toggle") { S.reportOpen = !S.reportOpen; X.render(); return; }
      if (kind.indexOf("q-") === 0) {
        var qc = b.closest(".ht-question");
        if (!qc) return;
        if (kind === "q-opt") answerQ(qc, { option: Number(b.dataset.i) });
        else if (kind === "q-text") { var tx = qc.querySelector(".ht-qinput").value.trim(); if (tx) answerQ(qc, { text: tx }); }
        else {
          var qq = S.questions.filter(function (x) { return x.id === qc.dataset.key; })[0];
          if (qq && !S.busy["q:" + qq.id]) X.decide({ key: qq.id, name: clip(qq.title, 40), verb: "question", n: 1, items: [{ path: "questions/" + encodeURIComponent(qq.id), newItem: false }] }, "dismiss", qc);
        }
        return;
      }
      if (kind === "sess-fix") { var sk = b.closest(".hd-card").dataset.key; S.open[sk] = S.open[sk] === "fix" ? null : "fix"; X.render(); return; }
      if (kind === "sess-ok") {
        var sc = b.closest(".hd-card"), sid = sc.dataset.key, st0 = api.parseTarget(sc.querySelector("[data-fix]").value);
        if (!st0) { toast("先选去向"); return; }
        approveEdit(sid, st0, "日程改动", b);
        return;
      }
      if (kind === "hist-all") { S.histAll = true; X.render(); return; }
      if (kind === "undo") {
        var hk = b.closest(".ht-hist").dataset.block;
        write("reset", hk, {}, { quiet: true }).then(function (r) { if (!r.ok) { toast("撤销失败：" + hk, { error: true, ms: 6000 }); X.render(); return; } toast("已撤销：" + hk); return loadBlocks(); });
        return;
      }
      if (kind.indexOf("rule-") === 0 || kind === "fix-ok") {
        var rk = ruleOf(b);
        if (!rk.c) return;
        if (kind === "rule-ok") X.decide(dcOf(rk.c), "approve", rk.card);
        else if (kind === "rule-no") X.decide(dcOf(rk.c), "reject", rk.card);
        else if (kind === "rule-fix") { S.open[rk.c.key] = S.open[rk.c.key] === "fix" ? null : "fix"; X.render(); }
        else fixRule(rk.c, rk.card);
        return;
      }
      if (!k.c) return;
      var key = k.c.name;
      if (kind === "approve") approveBlock(k.c, k.card);
      else if (kind === "dest") { S.open[key] = S.open[key] === "dest" ? null : "dest"; X.render(); }
      else if (kind === "split") openSplit(key, k.c);
      else if (kind === "split-cancel") closeSplit(key);
      else if (kind === "split-add") {
        var st = S.splits[key];
        if (st && st.rg) { var np = api.addPoint(st.points, st.rg.t0, st.rg.t1, st.rg.list); if (np.length !== st.points.length) { st.points = np; st.targets.push(""); } X.render(); }
      } else if (kind === "split-ok") confirmSplit(key);
      else if (kind === "generic" || kind === "ungeneric") {
        post("blocks/generic", { blockKey: key, generic: kind === "generic" }).then(function (r) {
          if (!r.ok) { toast("没办成：" + key + "：" + why(r), { error: true, ms: 6000 }); return; }
          var on = kind === "generic";
          var t = toast((on ? "已标为通用窗口：" : "已取消通用窗口：") + key, { ms: X.UNDO_MS, undo: function () {
            t.remove(); post("blocks/generic", { blockKey: key, generic: !on }).then(loadBlocks);
          } });
          loadBlocks();
        });
      }
    });
    panel.addEventListener("keydown", function (ev) {
      if (ev.isComposing || ev.keyCode === 229) return;   // 输入法选词的回车不算提交
      if (ev.key === "Enter" && ev.target.classList && ev.target.classList.contains("ht-qinput")) { var qb = ev.target.closest(".ht-question").querySelector('[data-ht="q-text"]'); qb.click(); return; }
      if (ev.ctrlKey || ev.metaKey || ev.altKey || ev.target.closest("input, select, [role=slider]")) return;
      var kb = blockOf(ev.target);
      if (kb.c && ev.target === kb.card) {
        if (ev.key === "a" || ev.key === "A") approveBlock(kb.c, kb.card);
        else if (ev.key === "r" || ev.key === "R") ignoreBlock(kb.c, kb.card);
      }
      var kr = ruleOf(ev.target);
      if (kr.c && ev.target === kr.card && (ev.key === "a" || ev.key === "A" || ev.key === "r" || ev.key === "R")) {
        X.decide(dcOf(kr.c), /a/i.test(ev.key) ? "approve" : "reject", kr.card);
      }
    });

    X.hooks.render = render;
    X.hooks.recount = recount;
    X.hooks.load = function () { return Promise.all([loadBlocks(), loadAI()]); };
    X.hooks.busy = function () { return uiBusy() || writing > 0; };
  };
})();
