// hive · hex-decide.js —— 右上角「AI 决策」面板（取代 2026-10-22 的 hex-pending.js 待批准浮层）。
// 折叠成一个角标；展开两栏：「分类历史」（activity/auto：规则 / AI 自动记下的段，可拖到蜂巢里改归属）
// 与「待批准」（proposals + 报告条：左滑批准、右滑拒绝，5 秒内可撤销，同名窗口合并成一张）。
// 数据端点：activity/proposals、activity/reports（同旧面板）、activity/auto、sessions/{id}/reassign、planner/tasks（经 data.js 的 createTask）。
// 上半是纯函数（给 hex-decide.test.js 用），下半是 DOM（文字一律 textContent）。
(function () {
  "use strict";

  function clip(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n) + "…" : s; }
  function minutes(sec) { return Math.round((Number(sec) || 0) / 60); }

  // 与 assistant/suggestions.js 的 normTitle、nexus-core history.norm_title 同一个算法：
  // 去掉标题开头的状态符号（✳ ⠂ ● * 之类）、计数「(3)」「[2]」，空白并成一个；去完是空的就用原样的。
  function normTitle(title) {
    var t = String(title || "").replace(/\s+/g, " ").trim();
    return t.replace(/^(?:[\p{So}*•·]|\(\d+\)|\[\d+\]|\s)+/u, "") || t;
  }

  function lookup(tree, projectId, taskId) {
    var zones = {}, out = { projectName: "", taskName: "", projectId: null, zoneId: null };
    ((tree && tree.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
    ((tree && tree.projects) || []).forEach(function (p) {
      var hit = p.id === projectId;
      (p.tasks || []).forEach(function (t) { if (taskId && t.id === taskId) { hit = true; out.taskName = t.name; } });
      if (taskId && (taskId === p.unclassifiedTaskId || taskId === "t_unc_" + p.id)) { hit = true; out.taskName = "未分类"; }
      if (hit) { out.projectId = p.id; out.projectName = p.name; out.zoneId = p.zoneId; out.zoneName = zones[p.zoneId] || ""; }
    });
    return out;
  }
  // 「↓ 项目 - 任务」；找不到任务 = 项目的未分类
  function pathText(tg) { return "↓ " + (tg.projectName || "？") + " - " + (tg.taskName || "未分类"); }

  // 报告条里的窗口：最长的那段的标准化标题（没标题用程序名）；不止一种窗口时带「等 N 个窗口」
  function windowName(sugs) {
    if (!sugs.length) return "";
    var best = sugs.reduce(function (a, s) { return (Number(s.durationSeconds) || 0) > (Number(a.durationSeconds) || 0) ? s : a; });
    var seen = {};
    sugs.forEach(function (s) { seen[normTitle(s.title) || s.app || ""] = 1; });
    var k = Object.keys(seen).length, nm = normTitle(best.title) || best.app || "";
    return k > 1 ? nm + "（等 " + k + " 个窗口）" : nm;
  }
  function mainKey(sugs) {
    if (!sugs.length) return null;
    var best = sugs.reduce(function (a, s) { return (Number(s.durationSeconds) || 0) > (Number(a.durationSeconds) || 0) ? s : a; });
    return normTitle(best.title) || best.app || null;
  }

  // proposals: GET activity/proposals 的 body；reports: 报告数组（items=true，已带明细）；tree: views/tree
  // → { cards:[{key, verb, tone, name, obj, to, reason, n, min, projectId, taskId, zoneId, items:[{path, newItem}], ...}], reports:[{id,label,n,path}] }
  // 报告条按「种类 + 去向 + 标准化窗口标题」合并成一张卡，items 里每条底层项各有自己的批准 / 拒绝路径。
  function zoneName(tree, id) { var z = ((tree && tree.zones) || []).filter(function (x) { return x.id === id; })[0]; return z ? z.name : ""; }
  function conf(p) { var v = p && p.confidencePercent; return typeof v === "number" && isFinite(v) ? Math.max(0, Math.min(100, Math.round(v))) : null; }
  function build(proposals, reports, tree) {
    var cards = [], taskProps = {}, repList = [], merged = {};
    var P = proposals || {};
    function one(p) { return [{ path: "proposals/" + encodeURIComponent(p.id), newItem: false }]; }
    (P.projects || []).forEach(function (p) {
      cards.push({ key: p.id, verb: "project", name: p.name, obj: p.name, reason: p.reason || "", n: 1, min: 0,
        to: "↓ 新建项目" + (p.zoneName ? "（分区：" + p.zoneName + "）" : "") + ((p.tasks || []).length ? " · " + p.tasks.length + " 个任务" : ""),
        projectId: null, zoneId: p.zoneId || null, tasks: (p.tasks || []).map(function (t) { return t.name; }), conf: conf(p),
        meta: { op: "create", kind: "project", name: p.name, zoneName: p.zoneName || zoneName(tree, p.zoneId), why: p.reason || "" },
        slot: { op: "create", kind: "project", tZone: p.zoneId || null },
        items: [{ path: "proposals/" + encodeURIComponent(p.id), newItem: true }] });
    });
    (P.tasks || []).forEach(function (t) {
      taskProps[t.id] = 1;
      var pn = t.projectName || lookup(tree, t.projectId).projectName || "";
      cards.push({ key: t.id, verb: "task", name: t.name, obj: t.name, reason: t.reason || "", n: 1, min: 0,
        to: "↓ " + pn + " - ＋ " + t.name + "（新任务）", projectId: t.projectId, tasks: [], conf: conf(t),
        meta: { op: "create", kind: "task", name: t.name, projectName: pn, why: t.reason || "" },
        slot: { op: "create", kind: "task", tProject: t.projectId, tZone: lookup(tree, t.projectId).zoneId },
        items: [{ path: "proposals/" + encodeURIComponent(t.id), newItem: true }] });
    });
    function changeMeta(c, from, isTask, tree) {
    var m = { op: c.op === "move" ? "change" : c.op, kind: isTask ? "task" : "project", name: from, why: c.reason || "" };
    if (isTask) m.projectName = lookup(tree, c.projectId).projectName;
    if (c.op === "rename") m.newName = c.name;
    if (c.op === "move") { m.toName = c.toName == null ? "（已不存在）" : c.toName; m.toKind = isTask ? "project" : "zone"; }
    return m;
  }
  // 小蜂巢的位置数据：origin = 现在在哪（改 / 删 / 改名），target = 去哪（新建 / 移到）；改名、删除的目标就是它自己
  function changeSlot(c, isTask, tree) {
    var lk = lookup(tree, isTask ? c.projectId : c.targetId), s = { op: c.op === "move" ? "change" : c.op, kind: isTask ? "task" : "project" };
    var here = { oZone: lk.zoneId, oProject: lk.projectId || (isTask ? c.projectId : c.targetId), oTask: isTask ? c.targetId : null };
    if (c.op !== "move") return Object.assign(s, { tZone: here.oZone, tProject: here.oProject, tTask: here.oTask });
    Object.assign(s, here);
    if (isTask) { s.tProject = c.toId; s.tZone = lookup(tree, c.toId).zoneId; } else s.tZone = c.toId;
    return s;
  }
  // changes: 改名 / 移动 / 删除现有项目或任务；target=project → 目标格就是 targetId，task → 目标在 projectId 那个项目里
    (P.changes || []).forEach(function (c) {
      if (c.op !== "rename" && c.op !== "move" && c.op !== "delete") return;   // 新服务端的未知操作：不画，也不能抛
      var from = c.targetName == null ? "（已不存在）" : c.targetName, isTask = c.target === "task";
      cards.push({ key: c.id, verb: c.op === "move" ? "cmove" : c.op, tone: c.op === "delete" ? "del" : null, reason: c.reason || "", n: 1, min: 0, tasks: [],
        name: from, items: one(c),
        to: c.op === "rename" ? "↓ 改名为 " + (c.name == null ? "？" : c.name) : c.op === "move" ? "↓ 移到 " + (c.toName == null ? "（已不存在）" : c.toName) : "↓ 删除",
        projectId: isTask ? c.projectId : c.targetId, taskId: isTask ? c.targetId : null, hlProject: !isTask,
        destProjectId: c.op === "move" && isTask ? c.toId : null, conf: conf(c), meta: changeMeta(c, from, isTask, tree), slot: changeSlot(c, isTask, tree) });
    });
    // sessionChanges: 提议把一段已记的时间改挂到别处（from → to · 开始时刻 · 时长）
    (P.sessionChanges || []).forEach(function (s) {
      if (!s || !s.id || (s.op && s.op !== "reassign") || (s.status != null && s.status !== "pending")) return;
      var fr = lookup(tree, s.fromProjectId, s.fromTaskId), to = lookup(tree, s.projectId, s.taskId), d = new Date(s.startAt);
      var when = isNaN(d.getTime()) ? "" : ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
      var mm = minutes(s.durationSeconds), len = mm < 1 ? "不到 1 分钟" : mm + " 分钟";
      cards.push({ key: s.id, verb: "session", reason: [when, len, s.reason ? "AI：" + s.reason : ""].filter(Boolean).join(" · "), n: 1, min: 0, tasks: [],
        name: fr.projectName ? fr.projectName + " - " + (fr.taskName || "未分类") : "（原归属）", items: one(s), to: pathText(to), projectId: to.projectId, zoneId: to.zoneId, conf: conf(s),
        meta: { op: "session", kind: "task", startAt: s.startAt, durationSeconds: s.durationSeconds, toTaskName: to.taskName === "未分类" ? "" : to.taskName, toProjectName: to.projectName, why: s.reason || "" },
        slot: { op: "session", kind: "task", tProject: to.projectId, tZone: to.zoneId, tTask: s.taskId || null, oProject: fr.projectId, oZone: fr.zoneId, oTask: s.fromTaskId || null } });
    });
    (P.ignores || []).forEach(function (g) {
      var app = g.app == null ? "？" : g.app;
      cards.push({ key: g.id, verb: "ignore", tone: "ignore", name: g.titleContains ? app + " · " + g.titleContains : app, reason: g.reason || "",
        n: 1, min: 0, tasks: [], projectId: null, items: one(g),
        to: "↓ 以后都忽略这个窗口" + (!g.titleContains && g.hasTitleFilter ? "（带标题条件）" : "") });
    });
    (reports || []).forEach(function (r) {
      var cnt = 0;
      (r.items || []).forEach(function (it) {
        if (it.status !== "pending" && it.status !== "failed") return;   // 后端把 failed 也当未办完（可重试）
        var base = "reports/" + encodeURIComponent(r.id) + "/items/" + encodeURIComponent(it.id);
        var sugs = it.suggestions || [], sec = sugs.reduce(function (a, s) { return a + (Number(s.durationSeconds) || 0); }, 0);
        var reason = it.reason || r.summary || "", win = windowName(sugs), mk = mainKey(sugs), card;
        if (it.kind === "newTask" && it.newTask) {
          cnt++;
          if (taskProps[it.newTask.proposalId]) return;   // 同一条任务提议已经有卡了
          var tg = lookup(tree, it.newTask.projectId);
          card = { mk: ["task", it.newTask.projectId, it.newTask.name, mk].join("|"), verb: "task", name: win || it.newTask.name, obj: it.newTask.name, reason: reason,
            to: "↓ " + tg.projectName + " - ＋ " + it.newTask.name + "（新任务）", projectId: it.newTask.projectId, tasks: [], conf: null,
            meta: { op: "create", kind: "task", name: it.newTask.name, projectName: tg.projectName, why: reason },
            slot: { op: "create", kind: "task", tProject: it.newTask.projectId, tZone: tg.zoneId } };
        } else if (it.kind === "dismiss") {
          cnt++;
          card = { mk: ["noise", mk].join("|"), verb: "noise", tone: "ignore", name: win || "这些窗口", reason: reason, to: "↓ 当噪声，不记录", projectId: null, tasks: [] };
        } else if (it.kind === "assign") {
          cnt++;
          var t2 = lookup(tree, it.projectId, it.taskId);
          card = { mk: ["move", it.projectId, it.taskId, mk].join("|"), verb: "move", name: win || t2.taskName || t2.projectName || String(it.taskId || it.projectId || ""),
            reason: reason, to: pathText(t2), projectId: t2.projectId, taskId: it.taskId || null, tasks: [] };
        } else return;
        var item = { path: base, newItem: false }, segs = Math.max(sugs.length, 1);
        var key = mk == null ? r.id + "/" + it.id : card.mk;
        if (merged[key]) {
          var m = merged[key]; m.items.push(item); m.n += segs; m.min += minutes(sec); m.sec += sec;
          if (!m.reason && reason) m.reason = reason;
          return;
        }
        card.key = key; card.items = [item]; card.n = segs; card.sec = sec; card.min = minutes(sec);
        merged[key] = card; cards.push(card);
      });
      if (cnt) repList.push({ id: r.id, label: (r.author ? r.author + "：" : "") + clip(r.summary, 40), n: cnt, path: "reports/" + encodeURIComponent(r.id) });
    });
    // merged 里 min 是各条取整后相加；改成按总秒数取整
    cards.forEach(function (c) { if (c.sec != null) { c.min = minutes(c.sec); delete c.sec; } delete c.mk; });
    return { cards: cards, reports: repList };
  }

  // 撤销计时器：commit 之后 ms 毫秒才真发；期间 undo 取消；flush（转后台 / pagehide）立刻发出所有。时钟 / 发送都注入，好测。
  function Commits(o) {
    var list = [], inflight = 0, seq = 0, self = {};
    function fire(e, opts) {
      var i = list.indexOf(e); if (i < 0) return Promise.resolve();
      list.splice(i, 1); o.clearTimer(e.timer);
      inflight++;
      return Promise.resolve(o.send(e, opts)).then(function (res) { inflight--; o.onDone(e, res); }, function () { inflight--; o.onDone(e, null); });
    }
    self.add = function (card, decision) {
      var e = { id: ++seq, card: card, decision: decision };
      e.timer = o.setTimer(function () { fire(e); }, o.ms);
      list.push(e); return e;
    };
    self.undo = function (id) {
      for (var i = 0; i < list.length; i++) if (list[i].id === id) { o.clearTimer(list[i].timer); return list.splice(i, 1)[0]; }
      return null;
    };
    // 立刻发出所有待发的。新项目提议排在它的任务提议前面（先批项目，任务才批得动）。
    // parallel = 页面正在卸载（pagehide）：等不起上一条，全部并行 keepalive；否则（标签页转后台）一条接一条。
    self.flush = function (parallel) {
      var opts = { keepalive: true, parallel: !!parallel }, p = null;
      if (!parallel) list.forEach(function (e) { o.clearTimer(e.timer); });   // 顺序发：别让排在后面的原计时器抢跑
      list.slice().sort(function (a, b) { return (a.card.verb === "project" ? 0 : 1) - (b.card.verb === "project" ? 0 : 1); }).forEach(function (e) {
        if (parallel) fire(e, opts); else p = p ? p.then(function () { return fire(e, opts); }) : fire(e, opts);
      });
      return p || Promise.resolve();
    };
    self.pending = function () { return list.length + inflight; };
    self.has = function (key) { return list.some(function (e) { return e.card.key === key; }); };
    return self;
  }

  var api = { build: build, normTitle: normTitle, Commits: Commits, lookup: lookup, pathText: pathText };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined" || typeof window === "undefined") return;
  window.NexusTableHexDecide = api;

  // ── DOM ───────────────────────────────────────────────────────────
  var App = window.NexusTableHexApp, H = window.NexusTableHexData, D = window.NexusTableData;
  var BASE = window.HONEYCOMB_BASE || "/", CORE = BASE + "api/core/", ACT = CORE + "activity/";
  var LS_KEY = "hive.decide.collapsed", UNDO_MS = 5000, HIST_MAX = 60;
  var shell = document.querySelector(".board-shell");
  if (!App || !shell) return;

  var panel = document.createElement("aside");
  panel.className = "hd-panel"; panel.hidden = true;
  panel.setAttribute("aria-label", "AI 决策");
  shell.appendChild(panel);
  panel.addEventListener("click", function (ev) { ev.stopPropagation(); });   // 点浮层不算「点空白收起」

  var toasts = document.createElement("div");
  toasts.className = "hd-toasts"; toasts.setAttribute("role", "status"); toasts.setAttribute("aria-live", "polite");
  document.body.appendChild(toasts);

  var hooks = {};   // 三级分类打开时由 hex-tiers.js 填：render / load / busy 换成一栏决策栏的
  var retry = null, model = { cards: [], reports: [] }, raw = { p: null, r: [] }, hist = [], errors = {}, held = {};
  var collapsed = true, tab = "pend", busyAct = false, swiping = 0, dragging = false, dirtyLoad = false;
  try { collapsed = window.localStorage.getItem(LS_KEY) !== "0"; } catch (e) { /* 存不下就每次折叠 */ }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]; }); }
  function getJson(url) { return fetch(url).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }); }
  // 同 getJson，但留下状态码（404 = 端点不存在，别的失败 = 保留旧值）：→ {status, body}
  function getRaw(url) {
    return fetch(url).then(function (r) { return r.json().catch(function () { return null; }).then(function (b) { return { status: r.status, body: r.ok ? b : null }; }); })
      .catch(function () { return { status: 0, body: null }; });
  }
  function visible() { return model.cards.filter(function (c) { return !held[c.key]; }); }
  // 这份报告里有没有条目正在撤销窗口里（有 → 「全部批准」先别点：它会越过那张卡的决定）
  function reportHeld(r) {
    return Object.keys(held).some(function (k) {
      return held[k].card.items.some(function (i) { return i.path.indexOf(r.path + "/items/") === 0; });
    });
  }
  function syncApproveAll() {
    [].forEach.call(panel.querySelectorAll(".hd-report"), function (row) {
      var r = model.reports.filter(function (x) { return x.id === row.dataset.report; })[0], b = row.querySelector("[data-hd=approve-all]");
      if (!r || !b) return;
      var h = reportHeld(r);
      b.disabled = busyAct || h; b.title = h ? "等撤销倒计时结束" : "";
    });
  }
  function editing() { return !!document.querySelector(".hd-newtask input"); }
  function busy() { return dragging || swiping > 0 || commits.pending() > 0 || editing() || !!(hooks.busy && hooks.busy()); }

  // 卡片 / 历史段的底色：跟蜂巢格子完全同一份（格子的 --zone-raw / --zone-mix）
  function cellFor(projectId, zoneId) {
    var s = App.getState(), it = projectId && s.byId && s.byId[projectId];
    if (it && it.cell) return it.cell;
    return (s.hive ? s.hive.cells : []).filter(function (c) { return zoneId && c.zoneId === zoneId && !c.__sprout; })[0] || null;
  }
  function tint(node, projectId, zoneId) {
    var c = cellFor(projectId, zoneId);
    if (!c) return;
    node.style.setProperty("--zone-raw", c.color);
    node.style.setProperty("--zone-mix", (c.mix == null ? H.DEPTH_MIX.max : c.mix) + "%");
  }

  // ── 提示条 ────────────────────────────────────────────────────────
  function toast(text, opts) {
    opts = opts || {};
    var t = el("div", "hd-toast" + (opts.error ? " is-error" : ""));
    t.appendChild(el("span", null, text));
    if (opts.undo) {
      var b = el("button", "hd-toast-undo", "撤销"); b.type = "button";
      b.addEventListener("click", function () { opts.undo(); });
      t.appendChild(b);
    }
    toasts.appendChild(t);
    if (!opts.sticky) window.setTimeout(function () { t.remove(); }, opts.ms || 3500);
    return t;
  }

  // ── 批准 / 拒绝：先撤销窗口，再逐条发 ───────────────────────────
  function postOne(path, newItem, keepalive) {
    return fetch(ACT + path, { method: "POST", keepalive: !!keepalive }).then(function (res) {
      if (res.ok) return null;
      return res.json().catch(function () { return null; }).then(function (b) {
        return (newItem && res.status === 409 && /approve$/.test(path)) ? "先批准它的项目" : ((b && typeof b.detail === "string") ? b.detail : "请求失败（" + res.status + "）");
      });
    }).catch(function () { return "连不上服务"; });
  }
  // → [{item, err}] 失败的那些。pagehide 时并行 + keepalive（来不及等上一条）
  function sendEntry(e, opts) {
    var verb = e.decision, items = e.card.items, fails = [];
    if (opts && opts.parallel) { items.forEach(function (it) { postOne(it.path + "/" + verb, it.newItem, true); }); return Promise.resolve(fails); }
    return items.reduce(function (p, it) {
      return p.then(function () {
        return postOne(it.path + "/" + verb, it.newItem, opts && opts.keepalive).then(function (err) { if (err) fails.push({ item: it, err: err }); });
      });
    }, Promise.resolve()).then(function () { return fails; });
  }
  var commits = Commits({
    ms: UNDO_MS,
    setTimer: function (f, ms) { return window.setTimeout(f, ms); },
    clearTimer: function (t) { window.clearTimeout(t); },
    send: sendEntry,
    onDone: function (e, fails) {
      var key = e.card.key;
      delete held[key]; syncApproveAll();
      if (e.toast) e.toast.remove();
      if (fails && fails.length) {
        errors[key] = fails[0].err;
        e.card.items = fails.map(function (f) { return f.item; });   // 没发成的留在卡上，刷新前也能再点
        toast("没办成：" + e.card.name + "：" + fails[0].err, { error: true, ms: 6000 });
      } else if (fails) delete errors[key];
      if (commits.pending() === 0) refreshAll();
    }
  });
  window.addEventListener("pagehide", function () { commits.flush(true); });
  document.addEventListener("visibilitychange", function () { if (document.visibilityState === "hidden") commits.flush(false); });

  function refreshAll() { return Promise.resolve(App.refresh()); }

  function restoreEl(c) {
    if (!c || !c.isConnected) return false;
    c.classList.remove("is-gone"); c.style.height = "";
    var f = c.querySelector(".hd-face");
    if (f) { f.classList.remove("anim"); f.style.transform = ""; }
    [].forEach.call(c.querySelectorAll(".hd-under"), function (u) { u.hidden = true; });
    return true;
  }
  function commit(card, decision, cardEl, quiet) {
    if (held[card.key]) return;
    held[card.key] = { card: card, el: cardEl };
    if (decision === "approve" && window.NexusTableHexBar) window.NexusTableHexBar.bumpApprovals();   // 「跳一跳」的概率跟着批准次数降
    if (quiet) return commits.add(card, decision);
    var word = decision === "approve" ? "已批准：" : decision === "dismiss" ? "已撤下：" : "已拒绝：";
    var entry = commits.add(card, decision), t;
    t = toast(word + card.name, { sticky: true, undo: function () {
      if (!commits.undo(entry.id)) return;
      delete held[card.key]; t.remove();
      restoreEl(cardEl);   // 直接把那张卡放回原位（拖拽 / 滑动中 render 会被推迟）
      if (dragging || swiping) dirtyLoad = true; else render();
      updateBadge(); syncApproveAll();
    } });
    entry.toast = t;
    window.setTimeout(function () { t.remove(); }, UNDO_MS + 200);
    updateBadge(); syncApproveAll();
  }
  // 按钮 / 键盘 / 滑动共用：焦点先挪到下一张卡，再收起
  function decide(c, decision, cardEl) {
    if (held[c.key]) return;
    if (cardEl.contains(document.activeElement)) {
      var n = cardEl.nextElementSibling, ok = function (x) { return x && !(x.classList.contains("hd-card") && !x.classList.contains("is-gone")); };
      while (ok(n)) n = n.nextElementSibling;
      if (!n) { n = cardEl.previousElementSibling; while (ok(n)) n = n.previousElementSibling; }
      if (n && n.focus) n.focus({ preventScroll: true });
    }
    collapseEl(cardEl);
    commit(c, decision, cardEl);
  }
  // 一批卡一起批准 / 拒绝：每张照走 commit（各自的撤销计时、held），只出一条汇总提示；撤销 = 整批撤回。→ 实际处理的张数
  function decideMany(list, decision) {
    var todo = list.filter(function (x) { return x.el && !held[x.c.key]; }), done = [];
    if (!todo.length) return 0;
    todo.forEach(function (x) { collapseEl(x.el); done.push({ el: x.el, entry: commit(x.c, decision, x.el, true) }); });
    var t = toast((decision === "approve" ? "已批准 " : "已拒绝 ") + done.length + " 项", { sticky: true, undo: function () {
      done.forEach(function (d) { if (commits.undo(d.entry.id)) { delete held[d.entry.card.key]; restoreEl(d.el); } });
      t.remove();
      if (dragging || swiping) dirtyLoad = true; else render();
      updateBadge(); syncApproveAll();
    } });
    done.forEach(function (d) { d.entry.toast = t; });
    window.setTimeout(function () { t.remove(); }, UNDO_MS + 200);
    updateBadge(); syncApproveAll();
    return done.length;
  }
  // 动画：卡片收起（高度归零），下面的卡片自然上移
  function collapseEl(c) {
    c.style.height = c.offsetHeight + "px";
    void c.offsetHeight;
    c.classList.add("is-gone");
    c.style.height = "0px";
  }

  // ── 渲染 ──────────────────────────────────────────────────────────
  var badgeEl = null;
  function updateBadge() {
    if (hooks.recount) hooks.recount();
    if (badgeEl) badgeEl.textContent = String(visible().length);
  }
  function btn(cls, text, act) {
    var b = el("button", cls, text); b.type = "button"; b.dataset.hd = act; return b;
  }

  function pendCard(c) {
    var n = el("div", "hd-card" + (c.tone ? " is-" + c.tone : ""));
    n.dataset.key = c.key; n.tabIndex = 0;
    n.setAttribute("aria-label", c.name + "，按 A 批准，按 R 拒绝");
    var ok = el("div", "hd-under is-ok", "批准"), no = el("div", "hd-under is-no", "拒绝");
    ok.hidden = no.hidden = true;
    var f = el("div", "hd-face");
    f.appendChild(el("div", "hd-win", clip(c.name, 60)));
    if (c.to) f.appendChild(el("div", "hd-to", clip(c.to, 80)));
    if (c.reason) f.appendChild(el("div", "hd-why", clip(c.reason, 120)));
    if (c.n > 1 || c.items.length > 1) f.appendChild(el("div", "hd-meta", c.n + " 段合并" + (c.min ? " · " + c.min + " 分钟" : "")));
    if (errors[c.key]) { var er = el("div", "hd-err", errors[c.key]); er.setAttribute("role", "alert"); f.appendChild(er); }
    var acts = el("div", "hd-acts");
    acts.appendChild(btn("hd-btn", "批准", "approve")); acts.appendChild(btn("hd-btn", "不要", "reject"));
    f.appendChild(acts);
    n.appendChild(ok); n.appendChild(no); n.appendChild(f);
    if (!c.tone) tint(n, c.projectId, c.zoneId);
    bindSwipe(n, f, ok, no, c);
    return n;
  }

  function bindSwipe(card, face, ok, no, c, onDecide) {
    var sx = null, sy = 0, dx = 0, on = false;
    face.addEventListener("pointerdown", function (ev) {
      card.__swiped = false;   // 每次按下都清：被取消的滑动不留标记吞掉下一次点按钮
      if ((ev.button !== undefined && ev.button !== 0) || ev.target.closest("button")) return;
      sx = ev.clientX; sy = ev.clientY; dx = 0; on = false;
    });
    face.addEventListener("pointermove", function (ev) {
      if (sx == null) return;
      dx = ev.clientX - sx;
      if (!on) {
        if (Math.abs(dx) < 6 || Math.abs(ev.clientY - sy) > Math.abs(dx)) return;
        on = true; swiping++; face.classList.remove("anim");
        try { face.setPointerCapture(ev.pointerId); } catch (e) { /* 合成事件没有活动指针 */ }
      }
      face.style.transform = "translateX(" + dx + "px)";
      ok.hidden = dx >= 0; no.hidden = dx <= 0;
    });
    function end(ev) {
      if (sx == null) return;
      sx = null;
      if (!on) return;
      on = false; swiping--; card.__swiped = ev.type !== "pointercancel";   // 紧跟着的那次 click 由 panel 的 click 处理器吞掉并清标记
      face.classList.add("anim");
      if (Math.abs(dx) > card.offsetWidth * 0.3 && ev.type !== "pointercancel") {
        var yes = dx < 0;
        face.style.transform = "translateX(" + (yes ? -120 : 120) + "%)";
        if ((onDecide || decide)(c, yes ? "approve" : "reject", card) === false) { face.style.transform = ""; ok.hidden = no.hidden = true; }   // 处理器说不行：弹回
      } else {
        face.style.transform = ""; ok.hidden = no.hidden = true;
        if (dirtyLoad && !busy()) { dirtyLoad = false; load(); }
      }
    }
    face.addEventListener("pointerup", end); face.addEventListener("pointercancel", end);
  }

  function histCard(it) {
    var tree = App.getState().tree, tg = lookup(tree, it.projectId, it.taskId);
    var n = el("div", "hd-card hd-hcard"); n.dataset.event = it.eventId;
    var f = el("div", "hd-face");
    f.appendChild(el("div", "hd-win", clip(it.title || it.app || "（无标题）", 60)));
    f.appendChild(el("div", "hd-to", tg.projectName ? pathText(tg) : "↓ " + (it.taskId || it.projectId || "？")));
    var m = el("div", "hd-meta");
    m.appendChild(el("span", "hd-tag", it.reassigned ? "已改" : it.ai ? "AI" : "规则"));
    var d = new Date(it.startAt), mm = minutes(it.durationSeconds);
    m.appendChild(el("span", null, (isNaN(d.getTime()) ? "" : ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2) + " · ") + (mm < 1 ? "不到 1 分钟" : mm + " 分钟")));
    f.appendChild(m); n.appendChild(f);
    tint(n, tg.projectId, tg.zoneId);
    bindDrag(f, it);
    return n;
  }

  function render() {
    if (dragging || swiping) { dirtyLoad = true; return; }
    if (hooks.render) return hooks.render();
    var vis = visible(), pc = panel.querySelector(".hd-col-pend"), hc = panel.querySelector(".hd-col-hist");
    var scroll = [pc ? pc.scrollTop : 0, hc ? hc.scrollTop : 0];
    var fc = document.activeElement && panel.contains(document.activeElement) && document.activeElement.closest(".hd-col-pend .hd-card"),
        fkey = fc && fc.dataset.key;
    panel.hidden = !vis.length && !hist.length && !commits.pending();
    panel.textContent = "";
    panel.dataset.tab = tab; panel.dataset.collapsed = collapsed ? "1" : "0";
    var head = btn("hd-head", "", "toggle");
    head.setAttribute("aria-expanded", String(!collapsed));
    head.appendChild(el("span", "hd-title", "AI 决策"));
    badgeEl = el("span", "hd-badge", String(vis.length)); head.appendChild(badgeEl);
    head.appendChild(el("span", "hd-chev", collapsed ? "▾" : "▴"));
    panel.appendChild(head);
    if (collapsed) return;
    var tabs = el("div", "hd-tabs"); tabs.setAttribute("role", "group");
    [["pend", "待批准"], ["hist", "分类历史"]].forEach(function (t) {
      var b = btn("hd-tab", t[1], "tab"); b.dataset.tab = t[0]; b.setAttribute("aria-pressed", String(tab === t[0])); tabs.appendChild(b);
    });
    panel.appendChild(tabs);
    var cols = el("div", "hd-cols");
    var hcol = el("div", "hd-col hd-col-hist"), pcol = el("div", "hd-col hd-col-pend");
    hcol.appendChild(el("h2", null, "分类历史 · 规则 + AI"));
    if (hist.length) hist.forEach(function (it) { hcol.appendChild(histCard(it)); });
    else hcol.appendChild(el("div", "hd-empty", "今天还没有自动记下的段"));
    pcol.appendChild(el("h2", null, "待批准 · 左滑批准 右滑拒绝"));
    vis.forEach(function (c) { pcol.appendChild(pendCard(c)); });
    if (!vis.length) pcol.appendChild(el("div", "hd-empty", "没有待批准的"));
    model.reports.forEach(function (r) {
      var row = el("div", "hd-report"); row.dataset.report = r.id;
      row.appendChild(el("span", "hd-why", r.label + " · " + r.n + " 条"));
      if (errors[r.id]) row.appendChild(el("span", "hd-err", errors[r.id]));
      var b = btn("hd-btn", "全部批准", "approve-all"); b.disabled = busyAct; row.appendChild(b);
      pcol.appendChild(row);
    });
    var foot = el("a", "hd-foot", "去待批准页"); foot.href = BASE + "assistant/"; pcol.appendChild(foot);
    cols.appendChild(hcol); cols.appendChild(pcol); panel.appendChild(cols);
    hcol.scrollTop = scroll[1]; pcol.scrollTop = scroll[0];
    if (fkey) [].some.call(pcol.querySelectorAll(".hd-card"), function (n) { if (n.dataset.key === fkey) { n.focus({ preventScroll: true }); return true; } });
    syncApproveAll();
  }

  // ── 取数 ──────────────────────────────────────────────────────────
  function ymd(d) { return d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2); }
  function dayBefore(str) {   // YYYY-MM-DD 的前一天（纯日期运算，不碰时区）
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(str || ""), d = m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] - 1)) : null;
    return d ? d.toISOString().slice(0, 10) : null;
  }
  // 先读今天（不带参数，响应的 date 是服务端的「今天」），再按它推昨天读 date=（v2.37）；
  // 响应没有 date（老服务端）就退回浏览器本地日期。每次响应本来就只含那一天，不再另按浏览器零点截；老服务端忽略 date 又回今天 → 按 id 去重。
  function loadHist() {
    if (hooks.render) return Promise.resolve();   // 三级分类：历史来自窗口块，不再读 activity/auto
    return getJson(ACT + "auto").then(function (t) {
      var y = (t && dayBefore(t.date)) || ymd(new Date(Date.now() - 86400000));
      return getJson(ACT + "auto?date=" + y).then(function (yy) {
        var seen = {};
        if (!t && !yy) return;   // 两次都没取到：留着旧的
        hist = [].concat((t && t.items) || [], (yy && yy.items) || []).filter(function (x) {
          if (!x.eventId || seen[x.id || x.eventId]) return false;
          return (seen[x.id || x.eventId] = true);
        }).sort(function (p, q) { return new Date(q.startAt).getTime() - new Date(p.startAt).getTime(); }).slice(0, HIST_MAX);
      });
    });
  }
  function load() {
    if (busy()) { dirtyLoad = true; return Promise.resolve(); }
    return hooks.load ? Promise.all([hooks.load(), loadOld()]) : loadOld();
  }
  function loadOld() {
    return Promise.all([getJson(ACT + "proposals?status=pending&limit=100"), getJson(ACT + "reports?status=pending&items=true&limit=20"), loadHist()]).then(function (r) {
      var list = (r[1] && Array.isArray(r[1].items)) ? r[1].items : [];
      if (r[0]) raw.p = r[0];   // 取不到（失败）就留着上一份，别把卡片清空
      // 列表里的条不带窗口明细（suggestions / seconds），逐份读详情（GET reports/{id}，已解析）；失败就退回列表那份
      if (!r[1]) return Promise.resolve().then(function () { if (!busy()) recompute(); else dirtyLoad = true; });
      return Promise.all(list.slice(0, 5).map(function (x) {
        return getJson(ACT + "reports/" + encodeURIComponent(x.id)).then(function (d) { return d || x; });
      })).then(function (full) { if (busy()) { dirtyLoad = true; return; } raw.r = full; recompute(); });
    });
  }
  function recompute() {
    if (busy()) { dirtyLoad = true; return; }
    model = build(raw.p, raw.r, App.getState().tree);
    render();
    window.clearTimeout(retry);
    if (!App.getState().tree) retry = window.setTimeout(recompute, 1500);   // 蜂巢还在拉树：名字解析等它
  }
  function idle() { if (!busy() && dirtyLoad) { dirtyLoad = false; load(); } }

  // ── 分类历史：拖到蜂巢里改归属 ─────────────────────────────────
  function reassign(it, body) {
    if (it.send) return it.send(body);   // 三级分类的历史卡：改挂 = blocks/assign
    return fetch(CORE + "sessions/" + encodeURIComponent(it.eventId) + "/reassign", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (b) {
        return res.ok ? null : ((b && typeof b.detail === "string") ? b.detail : "请求失败（" + res.status + "）");
      });
    }).catch(function () { return "连不上服务"; });
  }
  function flash(taskId) {
    var q = taskId && document.querySelector('.hex-cell li[data-task-id="' + (window.CSS && CSS.escape ? CSS.escape(taskId) : taskId) + '"]');
    if (!q) return;
    var all = q.closest(".hex-all"); if (all) all.hidden = false;
    q.classList.add("hd-flash");
    window.setTimeout(function () { q.classList.remove("hd-flash"); }, 1400);
  }
  function moved(it, body) {
    if (it.onMoved) { it.onMoved(body); return; }
    var tree = App.getState().tree, tg = lookup(tree, body.projectId, body.taskId);
    it.reassigned = true; it.taskId = body.taskId || "t_unc_" + body.projectId; it.projectId = tg.projectId || body.projectId;
    toast("已改归属 → " + (tg.projectName ? pathText(tg).slice(2) : "所选的目标"));
    render(); flash(body.taskId);
    loadHist().then(function () { if (!busy()) render(); });
  }
  function doReassign(it, body) {
    return reassign(it, body).then(function (err) {
      if (err) { toast("没改成：" + err, { error: true, ms: 6000 }); return false; }
      moved(it, body); return true;
    });
  }

  // 新建任务行：只在拖拽时出现在每个展开的格子里
  function ensureRows(cell) {
    var all = cell.querySelector(".hex-all");
    if (all && all.hidden) { all.hidden = false; all.dataset.hdOpened = "1"; }
    if (cell.querySelector(".hd-newtask")) return;
    var ul = cell.querySelector(".hex-next-list");
    if (!ul) { var g = cell.querySelector(".hex-group"); if (!g) return; ul = el("ul", "hex-list hex-next-list"); g.appendChild(ul); }
    var li = el("li", "hex-todo-card hd-newtask", "＋ 新建任务");
    ul.insertBefore(li, ul.firstChild);   // 放最上面：长列表滚动时也总看得见
  }
  function cleanRows() {
    [].forEach.call(document.querySelectorAll(".hd-newtask"), function (n) { if (!n.querySelector("input")) n.remove(); });
    [].forEach.call(document.querySelectorAll(".hex-all[data-hd-opened]"), function (n) { n.hidden = true; delete n.dataset.hdOpened; });
    [].forEach.call(document.querySelectorAll(".hd-over"), function (n) { n.classList.remove("hd-over"); });
  }

  function newTaskInput(row, cell, it) {
    var pid = cell.dataset.projectId, input = document.createElement("input"), inflight = false;
    input.type = "text"; input.maxLength = 64; input.value = clip(it.title || it.app || "", 64).replace(/…$/, "");
    input.setAttribute("aria-label", "新任务名");
    row.textContent = ""; row.classList.remove("hd-over"); row.appendChild(input);
    ["pointerdown", "click"].forEach(function (t) { input.addEventListener(t, function (e) { e.stopPropagation(); }); });
    var closed = false;
    function cancel() { if (inflight || closed) return; closed = true; row.remove(); idle(); }
    input.addEventListener("blur", cancel);   // 点到别处 / 失焦 = 取消（请求在路上时不取消）
    input.addEventListener("keydown", function (ev) {
      ev.stopPropagation();
      if (ev.isComposing || ev.keyCode === 229) return;   // 输入法选字的回车 / Esc 不算
      if (ev.key === "Escape") { ev.preventDefault(); cancel(); }
      else if (ev.key === "Enter") {
        ev.preventDefault();
        var name = input.value.trim().slice(0, 64);
        if (!name || inflight) return;
        inflight = true; input.readOnly = true; input.setAttribute("aria-busy", "true");   // readOnly 不丢焦点：disabled 会让 Esc 落到蜂巢的全局 Esc 上把格子收起
        D.createTask(pid, name).then(function (r) {
          if (!r.ok || !r.data || !r.data.id) {
            inflight = false; input.readOnly = false; input.removeAttribute("aria-busy"); input.focus();
            toast("没建成：" + (r.message || "写入失败"), { error: true, ms: 6000 }); return;
          }
          var tid = r.data.id;
          return reassign(it, { taskId: tid }).then(function (err) {
            closed = true; row.remove(); inflight = false;
            // 任务已经建好了：失败只说改归属，重试 = 把卡片再拖到那条新任务上（不会再建一条）
            if (err) toast("任务已建好（" + name + "），但改归属没成：" + err, { error: true, ms: 8000 });
            else moved(it, { taskId: tid });
            return App.reloadExpanded().then(function () { flash(tid); });
          });
        }).then(function () { inflight = false; idle(); }, function () { inflight = false; idle(); });
      }
    });
    input.focus(); input.select();
  }

  var swallow = false;
  document.addEventListener("click", function (ev) { if (swallow) { swallow = false; ev.stopPropagation(); ev.preventDefault(); } }, true);

  var LONG_PRESS_MS = 300, DWELL_MS = 180;
  function bindDrag(face, it) {
    face.addEventListener("pointerdown", function (ev) {
      if ((ev.button !== undefined && ev.button !== 0) || dragging) return;
      var touch = ev.pointerType === "touch", armed = !touch, pressTimer = null;
      var x0 = ev.clientX, y0 = ev.clientY, ghost = null, cell = null, row = null, newRow = null, over = false;
      var px = x0, py = y0, raf = 0, dwell = 0, lastHit = null, target = null;
      // 触摸：按住 ~300ms 才开始拖（之前一动就是滚动列表）；武装之后吞掉 touchmove，免得页面跟着滚
      if (touch) pressTimer = window.setTimeout(function () { pressTimer = null; armed = true; face.classList.add("is-armed"); }, LONG_PRESS_MS);
      function noSel(e) { e.preventDefault(); }   // 代替 body 上的 user-select:none（继承属性，一挂整页重算样式）
      function tm(e) { if (armed && e.cancelable) e.preventDefault(); }
      function mv(e) {
        if (!ghost) {
          if (Math.abs(e.clientX - x0) < 6 && Math.abs(e.clientY - y0) < 6) return;
          if (!armed) { finish(false); return; }   // 触摸还没按够就动了 = 在滚动列表，放弃
          ghost = face.cloneNode(true); ghost.classList.add("hd-ghost");
          ghost.style.setProperty("--zone-raw", face.parentNode.style.getPropertyValue("--zone-raw"));
          ghost.style.setProperty("--zone-mix", face.parentNode.style.getPropertyValue("--zone-mix"));
          ghost.style.transform = "translate3d(" + (x0 - 40) + "px," + (y0 - 20) + "px,0)";
          document.body.appendChild(ghost); dragging = true; document.body.classList.add("hd-dragging");
        }
        px = e.clientX; py = e.clientY;
        if (!raf) raf = window.requestAnimationFrame(tick);   // 一帧只干一次：pointermove 在高回报率鼠标上一帧好几个
      }
      function schedule() { if (!raf && !over) raf = window.requestAnimationFrame(tick); }
      function clearOver() { [].forEach.call(document.querySelectorAll(".hd-over"), function (n) { n.classList.remove("hd-over"); }); target = null; }
      // 指针停在格子上 DWELL_MS 才展开（路过的格子不展开）；展开后补一次命中测试，把「新建任务」行 / 任务行点亮
      // 主线程卡住时定时器会晚到，而 pointermove 已经排在前面：用最新的 px/py 复核一次，指针早走了就不展开
      function settle(c) {
        dwell = 0;
        var h = document.elementFromPoint(px, py);
        if (!over && cell === c && h && c.contains(h)) enter(c);
      }
      function enter(c) {
        dwell = 0;
        if (over || cell !== c) return;
        if (App.getState().expandedId !== c.dataset.projectId) {
          App.expand(c.dataset.projectId);
          cell = App.getState().byId[c.dataset.projectId].el;
        }
        ensureRows(cell);
        cell.classList.add("hd-over");
        lastHit = null; schedule();
      }
      function tick() {
        raf = 0;
        if (over || !ghost) return;
        ghost.style.transform = "translate3d(" + (px - 40) + "px," + (py - 20) + "px,0)";
        var hit = document.elementFromPoint(px, py);
        if (hit === lastHit) return;   // 指针下还是同一个节点：什么都没变，不碰 DOM
        lastHit = hit;
        var c = hit && !hit.closest(".hd-panel") && hit.closest(".hex-cell[data-project-id]");
        if (c && c.classList.contains("is-ghost")) c = null;
        c = c || null;
        if (c !== cell) {
          window.clearTimeout(dwell); dwell = 0;
          clearOver(); cell = c; row = newRow = null;
          if (!cell) return;
          cell.classList.add("hd-over");
          if (App.getState().expandedId === cell.dataset.projectId) enter(cell);
          else dwell = window.setTimeout(settle, DWELL_MS, cell);
          return;
        }
        if (!cell || !cell.classList.contains("is-expanded")) return;
        newRow = hit.closest(".hd-newtask");
        row = !newRow && hit.closest("li[data-task-id]");
        var t = newRow || row;
        if (t !== target) { if (target) target.classList.remove("hd-over"); target = t; if (t) t.classList.add("hd-over"); }
      }
      function cleanRowsExcept(keep) {
        [].forEach.call(document.querySelectorAll(".hd-newtask"), function (n) { if (n !== keep) n.remove(); });
        [].forEach.call(document.querySelectorAll(".hex-all[data-hd-opened]"), function (n) { n.hidden = true; delete n.dataset.hdOpened; });
      }
      // 唯一的收尾口：松手（ok）/ Esc / 失焦 / 标签页转后台 / 指针丢失（cancel）都走这里，面板 / 影子 / 标记一律复位
      function finish(ok) {
        if (over) return;
        if (raf && ok) { window.cancelAnimationFrame(raf); tick(); }   // 松手前补上最后一次命中，放下的位置 = 最后一个指针位置
        over = true;
        window.clearTimeout(pressTimer);
        window.removeEventListener("pointermove", mv); window.removeEventListener("selectstart", noSel); window.removeEventListener("pointerup", upH); window.removeEventListener("pointercancel", cancelH);
        window.removeEventListener("touchmove", tm); window.removeEventListener("keydown", keyH, true); window.removeEventListener("blur", cancelH);
        document.removeEventListener("visibilitychange", visH); window.removeEventListener("lostpointercapture", cancelH, true);
        face.classList.remove("is-armed");
        window.cancelAnimationFrame(raf); window.clearTimeout(dwell);
        if (!ghost) return;
        swallow = true; window.setTimeout(function () { swallow = false; }, 50);
        ghost.remove(); document.body.classList.remove("hd-dragging");
        var pid = cell && cell.dataset.projectId, nr = newRow, tr = row;
        clearOver();
        dragging = false;
        if (ok && pid) {
          if (nr) { cleanRowsExcept(nr); newTaskInput(nr, cell, it); return; }
          cleanRows();
          doReassign(it, tr ? { taskId: tr.dataset.taskId } : { projectId: pid }).then(idle);
        } else { cleanRows(); idle(); }
      }
      function upH(e) { finish(e.type === "pointerup"); }
      function cancelH() { finish(false); }
      function keyH(e) { if (e.key === "Escape" && ghost) { e.stopPropagation(); e.preventDefault(); finish(false); } }
      function visH() { if (document.visibilityState === "hidden") finish(false); }
      window.addEventListener("pointermove", mv); window.addEventListener("selectstart", noSel); window.addEventListener("pointerup", upH); window.addEventListener("pointercancel", cancelH);
      window.addEventListener("touchmove", tm, { passive: false }); window.addEventListener("keydown", keyH, true); window.addEventListener("blur", cancelH);
      document.addEventListener("visibilitychange", visH); window.addEventListener("lostpointercapture", cancelH, true);
    });
  }

  // ── 点卡片：在蜂巢里指给人看 ──────────────────────────────────
  function removeGhosts() {
    var s = App.getState();
    if (s.hive && s.hive.cells.some(function (c) { return c.__sprout; })) {
      s.hive.cells = s.hive.cells.filter(function (c) { return !c.__sprout; });
      App.mount(true);
    }
  }
  function ghostLi(text) { return '<li class="hex-todo-card is-ghost"><span class="hex-todo-dot"></span><span class="hex-task-name">' + esc(text) + '</span><span class="hex-flag">AI 建议</span></li>'; }

  // 新项目：在它的分区里找一个空位，插一格幽灵（__sprout：collapse / refresh 已经会把它清掉）
  function ghostProject(c) {
    var s = App.getState(), cells = s.hive.cells, zc = cells.filter(function (x) { return x.zoneId === c.zoneId; })[0];
    if (!zc) return false;
    var taken = {}; cells.forEach(function (x) { taken[x.q + "," + x.r] = 1; }); taken["0,0"] = 1;
    var best = null;
    cells.filter(function (x) { return x.zoneId === c.zoneId; }).forEach(function (x) {
      [[1, 0], [0, 1], [-1, 1], [-1, 0], [0, -1], [1, -1]].forEach(function (d) {
        var q = x.q + d[0], r = x.r + d[1], ring = (Math.abs(q) + Math.abs(r) + Math.abs(q + r)) / 2;
        if (!taken[q + "," + r] && (!best || ring < best.ring)) best = { q: q, r: r, ring: ring };
      });
    });
    if (!best) return false;
    var id = "ghost_" + c.key;
    s.hive.cells.push({ q: best.q, r: best.r, ring: best.ring, angleDeg: H.cellAngle(best), zoneId: zc.zoneId, zoneName: zc.zoneName,
      color: zc.color, colorSlot: zc.colorSlot, colorSource: zc.colorSource, relaxed: false, placeholder: false,
      project: { id: id, name: c.obj || c.name, zoneId: c.zoneId, tasks: [] }, boundaryDirs: [], mix: H.DEPTH_MIX.min, __sprout: true });
    App.mount(true);
    App.expand("c" + best.q + "_" + best.r);
    var item = s.byId[id];
    item.el.classList.add("is-ghost");
    item.el.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });   // 幽灵格不参与长按计时
    var t = item.el.querySelector(".hex-title");
    ["data-hex-action", "role", "tabindex", "title"].forEach(function (a) { t.removeAttribute(a); });
    item.el.querySelector(".hex-body").innerHTML = '<div class="hex-detail"><div class="hex-sub">' + esc(zc.zoneName) + " · AI 建议的新项目</div>" +
      '<div class="hex-group"><div class="hex-group-title">将建的任务</div><ul class="hex-list">' +
      (c.tasks.length ? c.tasks.map(ghostLi).join("") : '<li class="hex-line is-empty">（只建项目，没有任务）</li>') + "</ul></div></div>";
    return true;
  }

  function focusCard(c) {
    var s = App.getState();
    removeGhosts();
    // expand() 对已展开的项目直接返回，上一次点出来的幽灵行 / 描边要自己清
    [].forEach.call(document.querySelectorAll(".hex-todo-card.is-ghost, .is-pending-hl, .is-pending-del"), function (n) {
      if (n.classList.contains("is-ghost")) n.parentNode.removeChild(n); else n.classList.remove("is-pending-hl", "is-pending-del");
    });
    if (c.verb === "project") { ghostProject(c); return; }
    if (!c.projectId || !s.byId[c.projectId]) return;
    App.expand(c.projectId);
    if (c.destProjectId && s.byId[c.destProjectId]) s.byId[c.destProjectId].el.classList.add("is-pending-hl");   // 任务移到的项目格（展开之后再点亮，免得被调灰）
    var el = s.byId[c.projectId].el, del = c.verb === "delete" ? " is-pending-del" : "";
    if (c.hlProject) { el.classList.add("is-pending-hl"); if (del) el.classList.add("is-pending-del"); return; }
    if (c.verb === "task") {
      var ul = el.querySelector(".hex-next-list");
      if (!ul) { ul = document.createElement("ul"); ul.className = "hex-list hex-next-list"; var g = el.querySelector(".hex-group"); if (g) g.appendChild(ul); }
      ul.insertAdjacentHTML("afterbegin", ghostLi(c.obj || c.name));
      ul.firstChild.scrollIntoView({ block: "nearest" });
    } else if (c.taskId) {
      var li = el.querySelector('[data-task-id="' + (window.CSS && CSS.escape ? CSS.escape(c.taskId) : c.taskId) + '"]');
      if (!li) return;
      var all = li.closest(".hex-all"); if (all) all.hidden = false;
      li.classList.add("is-pending-hl");
      if (del) li.classList.add("is-pending-del");
      li.scrollIntoView({ block: "nearest" });
    }
  }


  // ── 事件 ──────────────────────────────────────────────────────────
  function cardOf(node) {
    var card = node.closest(".hd-card:not(.hd-hcard)"), key = card && card.dataset.key;
    return { card: card, c: key && model.cards.filter(function (x) { return x.key === key; })[0] };
  }
  panel.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-hd]"), k = cardOf(ev.target), kind = b && b.dataset.hd;
    if (k.card && k.card.__swiped) { k.card.__swiped = false; return; }
    if (kind === "toggle") {
      collapsed = !collapsed;
      try { window.localStorage.setItem(LS_KEY, collapsed ? "1" : "0"); } catch (e) { /* 同上 */ }
      render();
    } else if (kind === "tab") { tab = b.dataset.tab; render(); }
    else if (kind === "approve" || kind === "reject") { if (k.c) decide(k.c, kind, k.card); }
    else if (kind === "approve-all") {
      var r = model.reports.filter(function (x) { return x.id === b.closest(".hd-report").dataset.report; })[0];
      if (r && !reportHeld(r)) {
        busyAct = true; render();
        postOne(r.path + "/approve", false).then(function (err) {
          busyAct = false;
          if (err) { errors[r.id] = err; render(); return; }
          delete errors[r.id]; refreshAll();
        });
      }
    } else if (k.c) focusCard(k.c);
  });
  panel.addEventListener("keydown", function (ev) {
    var k = cardOf(ev.target);
    if (!k.c || ev.target !== k.card || ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (ev.key === "Enter") focusCard(k.c);
    else if (ev.key === "a" || ev.key === "A") decide(k.c, "approve", k.card);
    else if (ev.key === "r" || ev.key === "R") decide(k.c, "reject", k.card);
  });

  // 刷新：启动、蜂巢重拉树之后（onTreeChange / App.refresh）、每 60 秒（标签页可见时）
  var orig = App.refresh;
  App.refresh = function () { return Promise.resolve(orig.apply(App, arguments)).then(function (r) { return load().then(function () { return r; }); }); };
  var A = window.NexusTableApp;
  if (A && A.onTreeChange) A.onTreeChange(function () { window.setTimeout(recompute, 300); });
  window.setInterval(function () { if (!document.hidden) load(); }, 60000);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) load(); });   // 切回来立刻刷新，不等下一个 60 秒
  // 旧面板立即照常读；账号设置 tiers 开着 → 再换成一栏决策栏（hex-tiers.js）并重载。
  // 关着 / 取不到 / 一栏的 start 抛错 → 旧面板一个字节不变（抛错时撤掉已挂的钩子再画一遍旧的）。
  load();
  getJson(ACT + "settings").then(function (st) {
    if (!st || st.tiers !== true) return;
    return new Promise(function (ok) {   // hex-tiers.js 还没解析完（settings 先回来了）：等到文档解析完再查
      if (window.NexusTableHexTiers || document.readyState !== "loading") ok(); else document.addEventListener("DOMContentLoaded", function () { ok(); });
    }).then(function () {
      var T = window.NexusTableHexTiers;
      if (!T || !T.start) return;
      try {
        T.start({ hooks: hooks, panel: panel, el: el, btn: btn, toast: toast, tint: tint, cellFor: cellFor, bindDrag: bindDrag, bindSwipe: bindSwipe,
          pendCard: pendCard, decide: decide, decideMany: decideMany, settings: st, collapseEl: collapseEl, held: held, errors: errors, model: function () { return model; },
          raw: function () { return raw; }, collapsed: function () { return collapsed; }, render: render, App: App, H: H,
          ACT: ACT, UNDO_MS: UNDO_MS, getJson: getJson, getRaw: getRaw, focusCard: focusCard, refreshAll: refreshAll });
      } catch (e) {
        ["render", "load", "busy", "recount"].forEach(function (k) { delete hooks[k]; });
        delete panel.dataset.tiers;
        if (window.console) console.warn("hex-tiers 启动失败，退回旧面板", e);
      }
      render();
      return load();
    });
  });
})();
