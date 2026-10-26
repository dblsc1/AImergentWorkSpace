// hive · hex-tiers-cards.js —— 一栏决策栏的纯逻辑（置信度分组、卡片文案、跳一跳概率、卡片顺序）+ 日程改动小卡的 DOM。
// 上半是纯函数（给 hex-tiers-cards.test.js 用），下半是 DOM（文字一律 textContent）。hex-tiers.js / hex-decide.js 用它。
(function () {
  "use strict";

  var LS_ORDER = "hive.decide.order", LS_APPROVALS = "hive.decide.approvals";
  var DEF_ORDER = ["q", "s", "r", "b", "h"];
  var WORD = { zone: "分区", project: "项目", task: "任务" };

  function two(n) { return ("0" + n).slice(-2); }
  function hhmm(ms) { var d = new Date(ms); return isNaN(d.getTime()) ? "" : two(d.getHours()) + ":" + two(d.getMinutes()); }
  function nm(x) { return x == null || x === "" ? "？" : String(x); }

  // ── 置信度：缺字段 = null；阈值缺 = 80；>= 阈值 = 高，null / 低于 = 低 ──
  function confOf(v) { return typeof v === "number" && isFinite(v) ? Math.max(0, Math.min(100, Math.round(v))) : null; }
  function thresholdOf(st) { var v = st && st.highConfidenceThresholdPercent; return typeof v === "number" && isFinite(v) ? Math.max(0, Math.min(100, v)) : 80; }
  function isHigh(conf, th) { var c = confOf(conf); return c != null && c >= (th == null ? 80 : th); }
  function groupByConfidence(cards, getConf, th) {
    var out = { high: [], low: [] };
    (cards || []).forEach(function (c) { (isHigh(getConf(c), th) ? out.high : out.low).push(c); });
    return out;
  }

  // ── 卡片文案 ──
  var TAGS = { create: ["新建", "create"], change: ["更改", "change"], rename: ["更改", "change"], session: ["更改", "change"], delete: ["删除", "delete"] };
  function tagOf(meta) { return TAGS[meta && meta.op] || TAGS.change; }
  // 标题 = 名字 + （任务只有）项目名小字 + 「（任务）」/「（项目）」
  function titleParts(meta) {
    return { name: nm(meta.name), proj: meta.kind === "task" && meta.projectName ? meta.projectName : "", suffix: meta.kind === "task" ? "（任务）" : "（项目）" };
  }
  function sessionTitle(meta) {
    var a = Date.parse(meta.startAt), sec = Number(meta.durationSeconds) || 0, m = Math.round(sec / 60);
    var len = m < 1 ? "不到 1 分钟" : m < 60 ? m + " 分钟" : Math.floor(m / 60) + " 小时 " + two(m % 60) + " 分";
    return (isNaN(a) ? "" : hhmm(a) + "–" + hhmm(a + sec * 1000) + " · ") + len;
  }
  function subtitle(meta) {
    var task = meta.kind === "task", to = nm(meta.toName), w = WORD[meta.toKind || (task ? "project" : "zone")];
    switch (meta.op) {
      case "create": return task ? "到 " + nm(meta.projectName) + " 项目 下" : "到 " + nm(meta.zoneName) + " 分区 下";
      case "change": return task ? "更改 " + nm(meta.projectName) + " 的 " + nm(meta.name) + " 任务 到 " + to + " " + w + " 下" : "更改 " + nm(meta.name) + " 项目 到 " + to + " " + w + " 下";
      case "rename": return "改名为 " + nm(meta.newName);
      case "delete": return task ? "删除 " + nm(meta.projectName) + " 的 " + nm(meta.name) + " 任务" : "删除 " + nm(meta.name) + " 项目";
      case "session": return "改挂到 " + (meta.toTaskName ? meta.toTaskName + " 任务" : nm(meta.toProjectName) + " 项目");
    }
    return "";
  }
  // 窗口规则卡的一行：windowRule 收进块；blockRule 记到任务 / 项目
  function ruleLine(r) {
    if (r.op === "delete") return "删除这条规则";
    if (r.kind === "windowRule") return "收进「" + (r.block || "") + "」";
    return r.taskName ? "记到 " + r.taskName + " 任务（" + nm(r.projectName) + "）" : "记到 " + nm(r.projectName) + " 项目";
  }

  // ── 跳一跳：概率 = 1 / (1 + 批准次数 / 20)；方向随机，幅度 10–16px ──
  function hintChance(approvals) { return 1 / (1 + Math.max(0, Number(approvals) || 0) / 20); }
  function shouldHint(approvals, rnd) { return rnd < hintChance(approvals); }
  function jumpDx(r1, r2) { return (r1 < 0.5 ? -1 : 1) * Math.round(10 + r2 * 6); }
  function store(s) { if (s) return s; try { return typeof window !== "undefined" ? window.localStorage : null; } catch (e) { return null; } }
  function readApprovals(s) { try { s = store(s); return Math.max(0, parseInt(s.getItem(LS_APPROVALS), 10) || 0); } catch (e) { return 0; } }
  function bumpApprovals(s) { try { s = store(s); s.setItem(LS_APPROVALS, String(readApprovals(s) + 1)); } catch (e) { /* 存不下就不记 */ } }

  // ── 大卡顺序：存的缺项补上、多的 / 重复的丢掉；把 id 放到目标前面 ──
  function normOrder(saved, def) {
    var out = [];
    (Array.isArray(saved) ? saved : []).concat(def || DEF_ORDER).forEach(function (id) { if ((def || DEF_ORDER).indexOf(id) >= 0 && out.indexOf(id) < 0) out.push(id); });
    return out;
  }
  function moveOnto(order, id, target) {   // id 占 target 的位置（往后拖落在 target 后面，往前拖落在前面）
    var from = order.indexOf(id), to = order.indexOf(target);
    if (from < 0 || to < 0 || from === to) return order.slice();
    var out = order.filter(function (x) { return x !== id; });
    out.splice(to, 0, id);
    return out;
  }
  function loadOrder(s) { try { return normOrder(JSON.parse(store(s).getItem(LS_ORDER))); } catch (e) { return DEF_ORDER.slice(); } }
  function saveOrder(order, s) { try { store(s).setItem(LS_ORDER, JSON.stringify(order)); } catch (e) { /* 同上 */ } }

  var api = { DEF_ORDER: DEF_ORDER, confOf: confOf, thresholdOf: thresholdOf, isHigh: isHigh, groupByConfidence: groupByConfidence, tagOf: tagOf, titleParts: titleParts,
    sessionTitle: sessionTitle, subtitle: subtitle, ruleLine: ruleLine, hintChance: hintChance, shouldHint: shouldHint, jumpDx: jumpDx, readApprovals: readApprovals,
    bumpApprovals: bumpApprovals, normOrder: normOrder, moveOnto: moveOnto, loadOrder: loadOrder, saveOrder: saveOrder };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined" || typeof window === "undefined") return;
  window.NexusTableHexBar = api;

  // ── DOM ───────────────────────────────────────────────────────────
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  var SLOT = { tZone: "targetZoneId", tProject: "targetProjectId", tTask: "targetTaskId", oZone: "originZoneId", oProject: "originProjectId", oTask: "originTaskId", op: "op", kind: "kind", block: "block" };
  // 右侧留给第二部分画小蜂巢的空位：固定大小、空的，只带目标 / 原位置的 id 和操作种类
  api.slotEl = function (slot) {
    var n = el("div", "hd-minihive");
    Object.keys(slot || {}).forEach(function (k) { if (SLOT[k] && slot[k] != null) n.dataset[SLOT[k]] = String(slot[k]); });
    return n;
  };
  api.tagEl = function (meta) { var t = api.tagOf(meta); return el("span", "hb-tag is-" + t[1], t[0]); };
  // 日程改动小卡：X = hex-tiers 传来的上下文（btn / tint / bindSwipe / errors）
  api.crudCard = function (X, c) {
    var m = c.meta, n = el("div", "hd-card hb-card"), tp = titleParts(m), sess = m.op === "session";
    var name = sess ? sessionTitle(m) : tp.name;
    n.dataset.key = c.key; n.tabIndex = 0;
    n.setAttribute("aria-label", name + "，按 A 批准，按 R 拒绝");
    var ok = el("div", "hd-under is-ok", "批准"), no = el("div", "hd-under is-no", "拒绝");
    ok.hidden = no.hidden = true;
    var f = el("div", "hd-face"), row = el("div", "hb-row"), main = el("div", "hb-main");
    main.appendChild(api.tagEl(m));
    var t = el("div", "hd-win", name);
    if (!sess) { if (tp.proj) t.appendChild(el("span", "hb-proj", " " + tp.proj)); t.appendChild(document.createTextNode(tp.suffix)); }
    main.appendChild(t);
    main.appendChild(el("div", "hd-to", subtitle(m)));
    if (m.why) main.appendChild(el("div", "hd-why", m.why));
    if (X.errors[c.key]) { var er = el("div", "hd-err", X.errors[c.key]); er.setAttribute("role", "alert"); main.appendChild(er); }
    row.appendChild(main); row.appendChild(api.slotEl(c.slot)); f.appendChild(row);
    var acts = el("div", "hd-acts");
    acts.appendChild(X.btn("hd-btn", "批准", "approve")); acts.appendChild(X.btn("hd-btn", "不要", "reject"));
    f.appendChild(acts);
    n.appendChild(ok); n.appendChild(no); n.appendChild(f);
    var o = sess && c.slot && (c.slot.oProject || c.slot.oZone) ? { p: c.slot.oProject, z: c.slot.oZone } : { p: c.projectId, z: c.zoneId };   // 更改按原位置着色
    X.tint(n, o.p, o.z);
    X.bindSwipe(n, f, ok, no, c);
    return n;
  };
  // 展开一张大卡 / 一个分组时，里面看得见的可滑小卡各向左或向右跳一下，提示能滑
  api.hint = function (root) {
    if (!root || !api.shouldHint(readApprovals(), Math.random())) return;
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    [].forEach.call(root.querySelectorAll(".hd-card:not(.ht-question):not(.ht-hist):not(.is-gone) > .hd-face"), function (f) {
      if (!f.animate || f.offsetParent === null) return;
      var dx = jumpDx(Math.random(), Math.random());
      f.animate([{ transform: "translateX(0)" }, { transform: "translateX(" + dx + "px)", offset: 0.4 }, { transform: "translateX(0)" }], { duration: 300, easing: "ease-in-out" });
    });
  };
})();
