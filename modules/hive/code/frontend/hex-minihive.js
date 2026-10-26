// hive · hex-minihive.js —— 决策栏小卡右边的「小蜂巢」、悬停卡片时主蜂巢跟着指过去、批准时卡片飞进目标格。
// 上半是纯函数（目标解析 / 标记种类 / 小蜂巢几何 / 停留计时，给 hex-minihive.test.js 用），下半是 DOM（文字一律 textContent）。
// 小蜂巢画的是 App.getState() 里那份真的蜂巢（整张缩小），每次重画都读现在的树，不存快照；它自己不缩放。
(function () {
  "use strict";

  var SQ3 = Math.sqrt(3);
  var H = typeof module !== "undefined" && module.exports ? require("./hex-data.js") : window.NexusTableHexData;
  var MAX_DOTS = 12;

  // ── 目标解析：{kind, ok, zoneId, projectId, taskId}；kind = null 表示这个位置没有指定 ──
  function projOf(tree, id) { return id ? ((tree && tree.projects) || []).filter(function (p) { return p.id === id; })[0] || null : null; }
  function hasTask(p, id) { return (p.tasks || []).some(function (t) { return t.id === id; }) || id === p.unclassifiedTaskId || id === "t_unc_" + p.id; }
  function ref(tree, z, p, t) {
    var pr;
    if (t) {
      pr = p ? projOf(tree, p) : ((tree && tree.projects) || []).filter(function (x) { return hasTask(x, t); })[0] || null;
      return { kind: "task", ok: !!pr && hasTask(pr, t), zoneId: pr ? pr.zoneId : z || null, projectId: pr ? pr.id : p || null, taskId: t };
    }
    if (p) { pr = projOf(tree, p); return { kind: "project", ok: !!pr, zoneId: pr ? pr.zoneId : z || null, projectId: p, taskId: null }; }
    if (z) return { kind: "zone", ok: ((tree && tree.zones) || []).some(function (x) { return x.id === z; }), zoneId: z, projectId: null, taskId: null };
    return { kind: null, ok: true, zoneId: null, projectId: null, taskId: null };
  }
  // 标记种类：create 虚线框 / change 虚线框 + 从原位置连线 / delete 红框 / hl 实线框 / none 不标
  var TYPES = { create: "create", delete: "delete", change: "change", session: "change", rename: "hl", windowRule: "none" };
  function markType(op) { return TYPES[op] || "hl"; }
  function resolveSlot(tree, slot) {
    slot = slot || {};
    var type = markType(slot.op);
    if (!tree) return { type: type, missing: false, T: ref(null), O: ref(null) };
    var T = ref(tree, slot.tZone, slot.tProject, slot.tTask), O = ref(tree, slot.oZone, slot.oProject, slot.oTask);
    if (slot.del && !T.ok) T = ref(null);   // 删规则 = 清理：目标没了照样能批准，只是不标
    var missing = !T.ok || (slot.op === "change" && !O.ok);
    return { type: type, missing: missing, T: T, O: O };
  }
  var LEVEL = { task: "task", project: "project", zone: "zone" };
  function levelOf(r) { return LEVEL[r.kind] || null; }
  // 悬停 / 小蜂巢共用的标记计划：zoom = 要推近的项目 id（任务级的标记要打开任务列表才看得见）；ghost = 新项目要在分区里长一格
  function plan(slot, res) {
    if (!res || res.type === "none" || res.missing) return null;
    var T = res.T, O = res.O, type = res.type, dest = null, origin = null;
    if (type === "create") dest = slot.kind === "project" ? { level: "newProject", zoneId: T.zoneId } : T.kind === "project" ? { level: "newTask", projectId: T.projectId, zoneId: T.zoneId, name: slot.name } : null;
    else if (T.kind) {
      dest = { level: levelOf(T), projectId: T.projectId, taskId: T.taskId, zoneId: T.zoneId, name: slot.name };
      if (type === "change") {
        if (T.kind === "project" && slot.kind === "task") dest.level = "newTask";
        if (O.kind) origin = { level: levelOf(O), projectId: O.projectId, taskId: O.taskId, zoneId: O.zoneId };
      }
    }
    if (!dest) return null;
    var zoom = dest.level === "task" || dest.level === "newTask" ? dest.projectId : null;
    return { type: type, style: type === "delete" ? "del" : type === "hl" ? "hl" : "frame", dest: dest, origin: origin, zoom: zoom, ghost: dest.level === "newProject" };
  }
  function slotFromDataset(ds) {
    var m = { targetZoneId: "tZone", targetProjectId: "tProject", targetTaskId: "tTask", originZoneId: "oZone", originProjectId: "oProject", originTaskId: "oTask", op: "op", kind: "kind", block: "block", name: "name", del: "del" }, s = {};
    Object.keys(m).forEach(function (k) { if (ds && ds[k] != null) s[m[k]] = ds[k]; });
    return s;
  }

  // ── 小蜂巢几何：用主蜂巢同一套晶格（axialToPixel），整张按外框缩进 px×px ──
  function hexPoints(cx, cy, s) {
    var out = [];
    for (var k = 0; k < 6; k++) { var a = (30 + 60 * k) * Math.PI / 180; out.push((cx + s * Math.cos(a)).toFixed(2) + "," + (cy + s * Math.sin(a)).toFixed(2)); }
    return out.join(" ");
  }
  function geom(cells, size, px) {
    var list = (cells || []).filter(function (c) { return !c.__sprout; }), out = [], x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    list.forEach(function (c) {
      var p = H.axialToPixel(c, size);
      x0 = Math.min(x0, p.x - SQ3 / 2 * size); x1 = Math.max(x1, p.x + SQ3 / 2 * size); y0 = Math.min(y0, p.y - size); y1 = Math.max(y1, p.y + size);
      out.push({ q: c.q, r: c.r, cx: p.x, cy: p.y, zoneId: c.zoneId, projectId: c.project ? c.project.id : null, color: c.color, mix: c.mix, empty: !c.project });
    });
    if (!out.length) { x0 = y0 = -size; x1 = y1 = size; }
    x0 -= size; y0 -= size; x1 += size; y1 += size;   // 留一格的边：新项目的空位可能落在最外一圈之外
    var w = x1 - x0, h = y1 - y0, side = Math.max(w, h);
    return { S: size, cells: out, vb: [x0 - (side - w) / 2, y0 - (side - h) / 2, side, side], cellPx: px / side * SQ3 * size };
  }
  // 项目格里的任务点：1 个在正中，多个绕一圈；超过上限的不画（标记退回到整格）
  function taskDots(cx, cy, s, n) {
    var k = Math.min(n, MAX_DOTS), out = [];
    if (n <= 0) return out;
    if (k === 1) return [{ x: cx, y: cy }];
    for (var i = 0; i < k; i++) { var a = -Math.PI / 2 + 2 * Math.PI * i / k; out.push({ x: cx + 0.5 * s * Math.cos(a), y: cy + 0.5 * s * Math.sin(a) }); }
    return out;
  }
  function liveTasks(tree, projectId) { return (projOf(tree, projectId) || { tasks: [] }).tasks.filter(function (t) { return !t.done; }); }
  function cellOf(g, projectId) { return projectId ? g.cells.filter(function (c) { return c.projectId === projectId; })[0] || null : null; }
  // 新项目会落的格：分区里挨着已有格的第一个空位（环数小的优先）；→ {q, r, ring} | null
  var DIRS = [[1, 0], [0, 1], [-1, 1], [-1, 0], [0, -1], [1, -1]];
  function freeSlot(cells, zoneId) {
    var taken = { "0,0": 1 }, best = null;
    (cells || []).forEach(function (x) { taken[x.q + "," + x.r] = 1; });
    (cells || []).filter(function (x) { return x.zoneId === zoneId; }).forEach(function (x) {
      DIRS.forEach(function (d) {
        var q = x.q + d[0], r = x.r + d[1], ring = (Math.abs(q) + Math.abs(r) + Math.abs(q + r)) / 2;
        if (!taken[q + "," + r] && (!best || ring < best.ring)) best = { q: q, r: r, ring: ring };
      });
    });
    return best;
  }
  // 标记在小蜂巢里的位置：→ {kind: "dot"|"cell"|"zone"|"slot", x, y, cells?} | null
  function pointOf(g, tree, lv, rawCells) {
    if (!lv) return null;
    if (lv.level === "newProject") {
      var fs = freeSlot(rawCells, lv.zoneId);
      if (!fs) return null;
      var p = H.axialToPixel(fs, g.S);
      return { kind: "slot", x: p.x, y: p.y };
    }
    if (lv.level === "zone") {
      var zc = g.cells.filter(function (c) { return c.zoneId === lv.zoneId; });
      if (!zc.length) return null;
      return { kind: "zone", x: zc.reduce(function (a, c) { return a + c.cx; }, 0) / zc.length, y: zc.reduce(function (a, c) { return a + c.cy; }, 0) / zc.length, cells: zc };
    }
    var c = cellOf(g, lv.projectId);
    if (!c) return null;
    if (lv.level === "task" && g.cellPx >= 14) {
      var ts = liveTasks(tree, lv.projectId), i = ts.map(function (t) { return t.id; }).indexOf(lv.taskId);
      if (i >= 0 && i < MAX_DOTS) { var d = taskDots(c.cx, c.cy, g.S, ts.length)[i]; return { kind: "dot", x: d.x, y: d.y }; }
    }
    return { kind: "cell", x: c.cx, y: c.cy };
  }

  // ── 停留计时：同一处（同一个 key，指针没挪出 slop 像素）停满 ms 才触发一次；挪了 / 换了 key 重新数 ──
  function Dwell(o) {
    var key = null, ax = 0, ay = 0, t = null, self = {};
    function arm() { o.clear(t); t = o.set(function () { t = null; o.fire(key); }, o.ms); }
    self.at = function (k, x, y) {
      if (k == null) { self.stop(); return; }
      if (k !== key || Math.hypot(x - ax, y - ay) > o.slop) { key = k; ax = x; ay = y; arm(); }
    };
    self.stop = function () { o.clear(t); t = null; key = null; };
    return self;
  }

  var api = { resolveSlot: resolveSlot, markType: markType, plan: plan, slotFromDataset: slotFromDataset, geom: geom, hexPoints: hexPoints, taskDots: taskDots,
    pointOf: pointOf, freeSlot: freeSlot, Dwell: Dwell, MAX_DOTS: MAX_DOTS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined" || typeof window === "undefined") return;
  window.NexusTableHexMini = api;

  // ── DOM ───────────────────────────────────────────────────────────
  var NS = "http://www.w3.org/2000/svg", SIZE = 50, PX = 88, HOVER_MS = 150;
  function app() { return window.NexusTableHexApp; }
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function sv(tag, attrs, cls) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (cls) n.setAttribute("class", cls);
    return n;
  }
  function reduced() { return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); }

  // 底图（所有格子 + 任务点）按「蜂巢对象 + 树对象」缓存：同一批数据下几十张小卡共用一份，只克隆
  // 底图作为 <symbol> 放在文档级隐藏 <svg><defs> 里，每张小卡只 <use> 它，自己只画标记
  var base = null, symbol = null;
  function baseLayer(st) {
    if (base && base.hive === st.hive && base.tree === st.tree) return base;
    var g = geom(st.hive.cells, SIZE, PX), layer = sv("g");
    if (!symbol) {
      var host = sv("svg", { width: "0", height: "0", "aria-hidden": "true", style: "position:absolute" }), defs = sv("defs");
      symbol = sv("symbol", { id: "hm-base" }); defs.appendChild(symbol); host.appendChild(defs); document.body.appendChild(host);
    }
    g.cells.forEach(function (c) {
      var poly = sv("polygon", { points: hexPoints(c.cx, c.cy, SIZE) }, "hm-cell" + (c.empty ? " is-empty" : ""));
      if (c.color) poly.style.setProperty("--zone-raw", c.color);
      poly.style.setProperty("--zone-mix", (c.mix == null ? 40 : c.mix) + "%");
      layer.appendChild(poly);
      if (g.cellPx >= 14 && c.projectId) {
        var ts = liveTasks(st.tree, c.projectId);
        taskDots(c.cx, c.cy, SIZE, ts.length).forEach(function (d) { layer.appendChild(sv("circle", { cx: d.x.toFixed(2), cy: d.y.toFixed(2), r: (SIZE * 0.13).toFixed(2) }, "hm-dot")); });
      }
    });
    symbol.textContent = ""; symbol.setAttribute("viewBox", g.vb.map(function (v) { return v.toFixed(1); }).join(" ")); symbol.appendChild(layer);
    base = { hive: st.hive, tree: st.tree, g: g };
    return base;
  }
  function mark(pt, style, g) {
    if (!pt) return null;
    var cls = "hm-m is-" + style, n;
    if (pt.kind === "zone") {
      n = sv("g", {}, "hm-m-zone");
      pt.cells.forEach(function (c) { n.appendChild(sv("polygon", { points: hexPoints(c.cx, c.cy, SIZE) }, cls)); });
    } else if (pt.kind === "dot") n = sv("circle", { cx: pt.x.toFixed(2), cy: pt.y.toFixed(2), r: (SIZE * 0.24).toFixed(2) }, cls);
    else n = sv("polygon", { points: hexPoints(pt.x, pt.y, SIZE * 1.06) }, cls + (pt.kind === "slot" ? " is-slot" : ""));
    return n;
  }
  // 画一张小蜂巢；→ resolveSlot 的结果（调用方据此标红 / 禁用批准）
  function paint(div) {
    var A = app(), st = A && A.getState(), slot = slotFromDataset(div.dataset);
    div.textContent = "";
    if (!st || !st.hive || !st.tree) return { missing: false };
    var res = resolveSlot(st.tree, slot), b = baseLayer(st), svg = sv("svg", { viewBox: b.g.vb.map(function (v) { return v.toFixed(1); }).join(" "), "aria-hidden": "true" }, "hm-svg");
    svg.appendChild(sv("use", { href: "#hm-base", x: b.g.vb[0].toFixed(1), y: b.g.vb[1].toFixed(1), width: b.g.vb[2].toFixed(1), height: b.g.vb[3].toFixed(1) }));
    var pl = plan(slot, res);
    if (pl) {
      var dp = pointOf(b.g, st.tree, pl.dest, st.hive.cells), op = pointOf(b.g, st.tree, pl.origin, st.hive.cells);
      if (dp && op && (dp.x !== op.x || dp.y !== op.y)) {
        svg.appendChild(sv("line", { x1: op.x.toFixed(1), y1: op.y.toFixed(1), x2: dp.x.toFixed(1), y2: dp.y.toFixed(1) }, "hm-line"));
        svg.appendChild(sv("circle", { cx: op.x.toFixed(1), cy: op.y.toFixed(1), r: (SIZE * 0.2).toFixed(1) }, "hm-origin"));
      }
      var mk = mark(dp, pl.style, b.g);
      if (mk) svg.appendChild(mk);
    }
    div.appendChild(svg);
    return res;
  }
  var OFF = '[data-hd="approve"],[data-ht="approve"],[data-ht="rule-ok"]';
  function setMissing(card, miss) {
    if (!card) return;
    card.classList.toggle("is-missing", miss);
    var host = card.querySelector(".hb-main") || card.querySelector(".hd-face"), msg = card.querySelector(".hm-gone");
    if (miss && !msg && host) host.appendChild(el("div", "hm-gone", "目标已不存在"));
    if (!miss && msg) msg.remove();
    [].forEach.call(card.querySelectorAll(OFF), function (b) {
      if (miss) { b.disabled = true; b.dataset.hmOff = "1"; } else if (b.dataset.hmOff) { b.disabled = false; delete b.dataset.hmOff; }
    });
  }
  // 只画看得见的（折起的大卡 / 分组里的跳过，展开时整栏重画会补上）
  api.paintAll = function (root) {
    [].forEach.call((root || document).querySelectorAll(".hd-minihive"), function (d) { if (d.offsetParent !== null) setMissing(d.closest(".hd-card"), paint(d).missing); });
  };
  // 批准那一刻按现在的树重新算（不信卡上的标记）
  api.isMissing = function (card) {
    var d = card && card.querySelector(".hd-minihive"), A = app(), st = A && A.getState();
    return !!(d && st && st.tree && resolveSlot(st.tree, slotFromDataset(d.dataset)).missing);
  };

  // ── 悬停：主蜂巢推近目标并打同样的标记 ───────────────────────────
  function cellEl(st, pid) { var it = pid && st.byId[pid]; return it ? it.el : null; }
  function labelEl(zid) { return zid ? document.querySelector('.hex-zone-label[data-zone-id="' + (window.CSS && CSS.escape ? CSS.escape(zid) : zid) + '"]') : null; }
  // 没展开的格子里也有同 id 的待办行（藏着的）：只认看得见的；展开的格子里被折起来的行（.hex-all[hidden]）另算，由 reveal 摊开
  function liEl(tid) {
    if (!tid) return null;
    var all = document.querySelectorAll('.hex-cell li[data-task-id="' + (window.CSS && CSS.escape ? CSS.escape(tid) : tid) + '"]');
    return [].filter.call(all, function (n) { return n.closest(".hex-cell.is-expanded") && (n.offsetWidth || n.closest(".hex-all[hidden]")); })[0] || null;
  }
  function levelEl(st, lv) {
    if (!lv) return null;
    if (lv.level === "zone") return labelEl(lv.zoneId);
    if (lv.level === "task") return liEl(lv.taskId) || cellEl(st, lv.projectId);
    return lv.level === "newProject" ? null : cellEl(st, lv.projectId);
  }
  function onScreen(r) { return !!r && r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < window.innerWidth && r.top < window.innerHeight; }

  // X = { focusCard(c), modelCard(key) }（hex-tiers 传来：新项目要借 hex-decide 的幽灵格）
  api.bindHover = function (panel, X) {
    var cur = null, want = null, tm = null, view = null, marks = [], ghosts = [], opened = [], lineTm = null, layer = null;
    function cardOf(t) { var c = t && t.closest && t.closest(".hd-card"); return c && c.querySelector(".hd-minihive") ? c : null; }
    function busy() { return document.body.classList.contains("hd-dragging") || !window.matchMedia || !window.matchMedia("(hover: hover)").matches; }
    function wipe() {
      window.clearTimeout(lineTm); lineTm = null;
      if (layer) { layer.remove(); layer = null; }
      marks.forEach(function (m) { m[0].classList.remove(m[1]); });
      ghosts.forEach(function (g) { g.remove(); });
      opened.forEach(function (o) { if (o.isConnected) o.hidden = true; });
      marks = []; ghosts = []; opened = [];
    }
    function restore() {
      if (!view || !view.changed) return;
      var A = app(), st = A.getState();
      view.changed = false;
      if (view.expanded && st.byId[view.expanded]) { if (st.hive.cells.some(function (c) { return c.__sprout; })) A.collapse(); A.expand(view.expanded); } else A.collapse();
    }
    function add(n, c) { if (n) { n.classList.add(c); marks.push([n, c]); } }
    function reveal(li) { var all = li && li.closest(".hex-all"); if (all && all.hidden) { all.hidden = false; opened.push(all); } }
    function drawLine(from, to) {
      var a = from && from.getBoundingClientRect(), b = to && to.getBoundingClientRect();
      if (!a || !b || !a.width || !b.width) return;
      layer = sv("svg", {}, "hm-line-layer");
      layer.appendChild(sv("line", { x1: a.left + a.width / 2, y1: a.top + a.height / 2, x2: b.left + b.width / 2, y2: b.top + b.height / 2 }));
      layer.appendChild(sv("circle", { cx: a.left + a.width / 2, cy: a.top + a.height / 2, r: 5 }));
      document.body.appendChild(layer);
    }
    function apply(card) {
      var A = app(), st = A.getState(), div = card.querySelector(".hd-minihive"), slot = slotFromDataset(div.dataset);
      var pl = st.tree && plan(slot, resolveSlot(st.tree, slot));
      wipe();
      if (!pl) { restore(); return; }
      if (!view) view = { expanded: st.expandedId, changed: false };
      var zoomed = false, mc = pl.ghost && X.modelCard(card.dataset.key);
      if (pl.ghost) {
        if (mc && mc.verb === "project") { restore(); view.changed = zoomed = true; X.focusCard(mc); } else restore();
      } else if (pl.zoom && st.byId[pl.zoom]) {
        if (st.hive.cells.some(function (c) { return c.__sprout; })) { A.collapse(); view.changed = true; }
        if (st.expandedId !== pl.zoom) { view.changed = zoomed = true; A.expand(pl.zoom); }
      } else restore();
      var cls = "hm-" + pl.style, d = pl.dest, target = null;
      if (d.level === "newProject") target = document.querySelector(".hex-cell.is-ghost");
      else if (d.level === "newTask") {
        var cell = cellEl(st, d.projectId), ul = cell && cell.querySelector(".hex-next-list");
        if (!ul && cell && cell.querySelector(".hex-group")) { ul = el("ul", "hex-list hex-next-list"); cell.querySelector(".hex-group").appendChild(ul); ghosts.push(ul); }
        if (ul) {
          var li = el("li", "hex-todo-card is-ghost hm-mark");
          li.appendChild(el("span", "hex-todo-dot")); li.appendChild(el("span", "hex-task-name", d.name || ""));
          ul.insertBefore(li, ul.firstChild); ghosts.push(li); target = li;
        } else target = cell;
      } else if (d.level === "task") { target = liEl(d.taskId); reveal(target); target = target || cellEl(st, d.projectId); } else target = levelEl(st, d);
      add(target, cls);
      if (target && target.scrollIntoView && (d.level === "task" || d.level === "newTask")) target.scrollIntoView({ block: "nearest" });
      var from = pl.origin && levelEl(st, pl.origin);
      if (from && target) lineTm = window.setTimeout(function () { lineTm = null; drawLine(from, target); }, zoomed ? 520 : 0);
    }
    function show(card) {
      if (card === cur) return;
      if (card && (!card.isConnected || busy())) card = null;
      cur = card;
      if (card) { apply(card); return; }
      wipe(); restore(); view = null;
    }
    function schedule(c) {
      if (c === want) return;
      want = c; window.clearTimeout(tm);
      tm = window.setTimeout(function () { tm = null; show(want); }, HOVER_MS);
    }
    panel.addEventListener("mouseover", function (e) { schedule(cardOf(e.target)); });
    panel.addEventListener("mouseleave", function () { schedule(null); });
    // 当前被指着的元素（批准时飞过去的落点）：标记优先，其次按计划找
    api.cancelHover = function () { window.clearTimeout(tm); tm = null; want = null; show(null); };
    api.checkHover = function () { if (cur && !cur.isConnected) show(null); };
    api.current = function () { return cur && marks.length ? marks[0][0] : null; };
    api.hovering = function (card) { return card === cur; };
  };

  // ── 批准：卡片缩小飞进主蜂巢的目标格；目标不在屏幕里就飞进自己的小蜂巢；减少动效 = 只淡出 ──
  api.fly = function (card) {
    try {
      var A = app(), mh = card.querySelector(".hd-minihive"), face = card.querySelector(".hd-face");
      if (!A || !mh || !face) return;
      var st = A.getState(), slot = slotFromDataset(mh.dataset), pl = st.tree && plan(slot, resolveSlot(st.tree, slot));
      var fr = face.getBoundingClientRect();
      if (!fr.width || !fr.height) return;
      var tgt = api.hovering && api.hovering(card) ? api.current() : null;
      tgt = tgt || (pl && levelEl(st, pl.dest));
      var r = tgt && tgt.getBoundingClientRect();
      if (!onScreen(r)) r = (mh.querySelector(".hm-m") || mh).getBoundingClientRect();
      var g = face.cloneNode(true);
      g.classList.add("hd-ghost", "hm-fly");
      ["--zone-raw", "--zone-mix"].forEach(function (k) { g.style.setProperty(k, card.style.getPropertyValue(k)); });
      g.style.left = fr.left + "px"; g.style.top = fr.top + "px"; g.style.width = fr.width + "px"; g.style.height = fr.height + "px";
      document.body.appendChild(g);
      var s = Math.max(0.04, Math.min(r.width / fr.width, r.height / fr.height));
      var tx = r.left + r.width / 2 - fr.width * s / 2 - fr.left, ty = r.top + r.height / 2 - fr.height * s / 2 - fr.top;
      var frames = reduced() ? [{ opacity: 1 }, { opacity: 0 }] : [{ transform: "translate(0,0) scale(1)", opacity: 1 }, { transform: "translate(" + tx + "px," + ty + "px) scale(" + s + ")", opacity: 0.15 }];
      if (!g.animate) { g.remove(); return; }
      var an = g.animate(frames, { duration: 400, easing: "cubic-bezier(.32,.06,.13,1)", fill: "forwards" });
      an.onfinish = function () { g.remove(); };
      window.setTimeout(function () { g.remove(); }, 900);
    } catch (e) { /* 动效出错不能挡住批准 */ }
  };

  // 树重拉完：小蜂巢原地重画（不等整栏重画）
  window.addEventListener("hive:refreshed", function () { api.paintAll(document.querySelector(".hd-panel")); });
})();
