// hive · 待批准浮层（右上角卡片栏）——把 AI 提的「新增项目 / 新增任务 / 改挂 / 当噪声」画成卡片，
// 点一下在蜂巢里展开对应项目：现有任务脉冲描边，AI 建议的新东西用半透明虚线「幽灵」显示；卡片上直接批准 / 不要。
// 数据只读两个现成端点（nexus-core.activity.proposals.v1 / reports.v1）；proposals 的 changes（改名 / 移动 / 删除）与 ignores（以后都忽略）2026-10-22 起也画成卡。
// 上半是纯函数（build，给 hex-pending.test.js 用），下半是 DOM。
(function () {
  "use strict";

  var VERBS = { project: ["新增项目", "＋"], task: ["新增任务", "＋"], move: ["改挂", "→"], noise: ["当噪声", "×"],
    rename: ["改名", "✎"], cmove: ["移动", "⇅"], delete: ["删除", "✕"], ignore: ["以后都忽略", "⊘"] };

  function clip(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n) + "…" : s; }
  function minutes(sec) { var m = Math.round((Number(sec) || 0) / 60); return m < 1 ? "不到 1 分" : m + " 分"; }

  function lookup(tree, projectId, taskId) {
    var zones = {}, out = { projectName: "", taskName: "", projectId: null };
    ((tree && tree.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
    ((tree && tree.projects) || []).forEach(function (p) {
      var hit = p.id === projectId;
      (p.tasks || []).forEach(function (t) { if (taskId && t.id === taskId) { hit = true; out.taskName = t.name; } });
      if (taskId && (taskId === p.unclassifiedTaskId || taskId === "t_unc_" + p.id)) { hit = true; out.taskName = "未分类"; }
      if (hit) { out.projectId = p.id; out.projectName = p.name; out.zoneName = zones[p.zoneId] || ""; }
    });
    return out;
  }

  // proposals: GET activity/proposals 的 body；reports: 报告数组（items=true）；tree: views/tree
  // → { cards:[{key, verb, name, reason, targetLabel, meta, projectId, taskId, zoneId, tasks, op}], reports:[{id, label, n}] }
  // op = {path, verb} 批准 / 不要共用的相对路径（approve|reject 接在后面）
  function build(proposals, reports, tree) {
    var cards = [], taskProps = {}, repList = [];
    var P = proposals || {};
    (P.projects || []).forEach(function (p) {
      cards.push({ key: p.id, verb: "project", name: p.name, reason: p.reason || "", targetLabel: p.zoneName ? "分区：" + p.zoneName : "",
        meta: (p.tasks || []).length ? (p.tasks.length + " 个任务") : "", projectId: null, zoneId: p.zoneId || null,
        tasks: (p.tasks || []).map(function (t) { return t.name; }), path: "proposals/" + encodeURIComponent(p.id) });
    });
    (P.tasks || []).forEach(function (t) {
      taskProps[t.id] = 1;
      cards.push({ key: t.id, verb: "task", name: t.name, reason: t.reason || "", targetLabel: "→ " + (t.projectName || lookup(tree, t.projectId).projectName || ""),
        meta: "", projectId: t.projectId, tasks: [], path: "proposals/" + encodeURIComponent(t.id) });
    });
    // changes: 改名 / 移动 / 删除现有项目或任务；target=project → 目标格就是 targetId，task → 目标在 projectId 那个项目里
    (P.changes || []).forEach(function (c) {
      if (c.op !== "rename" && c.op !== "move" && c.op !== "delete") return;   // 新服务端的未知操作：不画，也不能抛
      var from = c.targetName == null ? "（已不存在）" : c.targetName, isTask = c.target === "task";
      cards.push({ key: c.id, verb: c.op === "move" ? "cmove" : c.op, reason: c.reason || "", targetLabel: "", meta: "", tasks: [], path: "proposals/" + encodeURIComponent(c.id),
        name: c.op === "rename" ? from + " 改成 " + (c.name == null ? "？" : c.name) : c.op === "move" ? from + " 移到 " + (c.toName == null ? "（已不存在）" : c.toName) : from,
        projectId: isTask ? c.projectId : c.targetId, taskId: isTask ? c.targetId : null, hlProject: !isTask,
        destProjectId: c.op === "move" && isTask ? c.toId : null });
    });
    (P.ignores || []).forEach(function (g) {
      cards.push({ key: g.id, verb: "ignore", name: g.app == null ? "？" : g.app, reason: g.reason || "", meta: "", projectId: null, tasks: [], path: "proposals/" + encodeURIComponent(g.id),
        targetLabel: g.titleContains ? "标题含 “" + g.titleContains + "”" : g.hasTitleFilter ? "带标题条件" : "" });
    });
    (reports || []).forEach(function (r) {
      var n = 0;
      (r.items || []).forEach(function (it) {
        if (it.status !== "pending" && it.status !== "failed") return;   // 后端把 failed 也当未办完（可重试）
        var base = "reports/" + encodeURIComponent(r.id) + "/items/" + encodeURIComponent(it.id);
        var sugs = it.suggestions || [], sec = sugs.reduce(function (a, s) { return a + (Number(s.durationSeconds) || 0); }, 0);
        var meta = sugs.length ? sugs.length + " 个窗口" + (sec ? " · " + minutes(sec) : "") : "";
        if (it.status === "failed") meta = (meta ? meta + " · " : "") + "上次失败";
        var reason = it.reason || r.summary || "";
        if (it.kind === "newTask" && it.newTask) {
          n++;
          // 同一条任务提议也会出现在 proposals.tasks 里 → 已经有卡了就不再画
          if (taskProps[it.newTask.proposalId]) return;
          cards.push({ key: r.id + "/" + it.id, verb: "task", name: it.newTask.name, reason: reason,
            targetLabel: "→ " + lookup(tree, it.newTask.projectId).projectName, meta: meta, projectId: it.newTask.projectId, tasks: [], path: base });
        } else if (it.kind === "dismiss") {
          n++;
          cards.push({ key: r.id + "/" + it.id, verb: "noise", name: "这些窗口当噪声", reason: reason, targetLabel: "", meta: meta, projectId: null, tasks: [], path: base });
        } else if (it.kind === "assign") {
          n++;
          var tg = lookup(tree, it.projectId, it.taskId);
          cards.push({ key: r.id + "/" + it.id, verb: "move", name: tg.taskName || tg.projectName || String(it.taskId || it.projectId || ""),
            reason: reason, targetLabel: tg.taskName ? "→ " + tg.projectName : "→ 项目的未分类", meta: meta,
            projectId: tg.projectId, taskId: it.taskId || null, tasks: [], path: base });
        }
      });
      if (n) repList.push({ id: r.id, label: (r.author ? r.author + "：" : "") + clip(r.summary, 40), n: n, path: "reports/" + encodeURIComponent(r.id) });
    });
    return { cards: cards, reports: repList };
  }

  var api = { build: build, VERBS: VERBS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined" || typeof window === "undefined") return;
  window.NexusTableHexPending = api;

  // ── DOM ───────────────────────────────────────────────────────────
  var App = window.NexusTableHexApp, H = window.NexusTableHexData;
  var BASE = window.HONEYCOMB_BASE || "/", CORE = BASE + "api/core/activity/";
  var LS_KEY = "hive.pending.collapsed";
  var shell = document.querySelector(".board-shell");
  if (!App || !shell) return;

  var panel = document.createElement("aside");
  panel.className = "hp-panel"; panel.hidden = true;
  panel.setAttribute("aria-label", "AI 待批准");
  shell.appendChild(panel);
  panel.addEventListener("click", function (ev) { ev.stopPropagation(); });   // 点浮层不算「点空白收起」

  var retry = null, model = { cards: [], reports: [] }, raw = { p: null, r: [] }, errors = {}, busy = false;
  var collapsed = false;
  try { collapsed = window.localStorage.getItem(LS_KEY) === "1"; } catch (e) { /* 存不下就每次展开 */ }

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]; }); }
  function getJson(url) {
    return fetch(url).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; });
  }

  function render() {
    var n = model.cards.length;
    panel.hidden = !n;
    if (!n) return;
    var html = '<button type="button" class="hp-head" data-hp="toggle" aria-expanded="' + !collapsed + '">' +
      '<span class="hp-title">AI 待批准</span><span class="hp-badge">' + n + '</span><span class="hp-chev" aria-hidden="true">' + (collapsed ? "▾" : "▴") + "</span></button>";
    if (!collapsed) {
      html += '<div class="hp-list">' + model.cards.map(function (c) {
        var v = VERBS[c.verb], err = errors[c.key];
        return '<div class="hp-card is-' + c.verb + '" data-key="' + esc(c.key) + '" tabindex="0" role="button">' +
          '<div class="hp-row"><span class="hp-chip">' + v[1] + " " + v[0] + '</span>' + (c.meta ? '<span class="hp-meta">' + esc(c.meta) + "</span>" : "") + "</div>" +
          '<div class="hp-name">' + esc(clip(c.name, 40)) + "</div>" +
          (c.targetLabel ? '<div class="hp-target">' + esc(clip(c.targetLabel, 50)) + "</div>" : "") +
          (c.reason ? '<div class="hp-reason">' + esc(clip(c.reason, 90)) + "</div>" : "") +
          (err ? '<div class="hp-err" role="alert">' + esc(err) + "</div>" : "") +
          '<div class="hp-acts"><button type="button" class="hp-btn is-ok" data-hp="approve"' + (busy ? " disabled" : "") + ">批准</button>" +
          '<button type="button" class="hp-btn" data-hp="reject"' + (busy ? " disabled" : "") + ">不要</button></div></div>";
      }).join("") + model.reports.map(function (r) {
        return '<div class="hp-report" data-report="' + esc(r.id) + '"><span class="hp-reason">' + esc(r.label) + " · " + r.n + ' 条</span>' +
          (errors[r.id] ? '<span class="hp-err" role="alert">' + esc(errors[r.id]) + "</span>" : "") +
          '<button type="button" class="hp-btn is-ok" data-hp="approve-all"' + (busy ? " disabled" : "") + ">全部批准</button></div>";
      }).join("") + "</div>" +
        '<a class="hp-foot" href="' + esc(BASE + "assistant/") + '">去待批准页</a>';
    }
    panel.innerHTML = html;
  }

  function load() {
    return Promise.all([getJson(CORE + "proposals?status=pending&limit=100"), getJson(CORE + "reports?status=pending&items=true&limit=20")]).then(function (r) {
      var list = (r[1] && Array.isArray(r[1].items)) ? r[1].items : [];
      raw.p = r[0];
      // 列表里的条不带窗口明细（suggestions / seconds），逐份读详情（GET reports/{id}，已解析）；失败就退回列表那份
      return Promise.all(list.slice(0, 5).map(function (x) {
        return getJson(CORE + "reports/" + encodeURIComponent(x.id)).then(function (d) { return d || x; });
      })).then(function (full) { raw.r = full; recompute(); });
    });
  }
  function recompute() {
    model = build(raw.p, raw.r, App.getState().tree);
    render();
    window.clearTimeout(retry);
    if (!App.getState().tree) retry = window.setTimeout(recompute, 1500);   // 蜂巢还在拉树：名字解析等它
  }

  // → 失败时返回错误文案，成功 null
  function post(path, newItem) {
    return fetch(CORE + path, { method: "POST" }).then(function (res) {
      if (res.ok) return null;
      return res.json().catch(function () { return null; }).then(function (b) {
        return (newItem && res.status === 409 && /approve$/.test(path)) ? "先批准它的项目" : ((b && typeof b.detail === "string") ? b.detail : "请求失败（" + res.status + "）");
      });
    }).catch(function () { return "连不上服务"; });
  }
  function act(path, verb, errKey, newItem) {
    busy = true; render();
    return post(path + "/" + verb, newItem).then(function (err) {
      busy = false;
      if (err) { errors[errKey] = err; render(); return; }
      delete errors[errKey];
      return App.refresh();   // 已包了一层：重拉蜂巢后自己 load()
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
      project: { id: id, name: c.name, zoneId: c.zoneId, tasks: [] }, boundaryDirs: [], mix: H.DEPTH_MIX.min, __sprout: true });
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
      ul.insertAdjacentHTML("afterbegin", ghostLi(c.name));
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

  panel.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-hp]"), card = ev.target.closest(".hp-card"), key = card && card.dataset.key;
    var c = key && model.cards.filter(function (x) { return x.key === key; })[0];
    var kind = b && b.dataset.hp;
    if (kind === "toggle") {
      collapsed = !collapsed;
      try { window.localStorage.setItem(LS_KEY, collapsed ? "1" : "0"); } catch (e) { /* 同上 */ }
      render();
    } else if (kind === "approve" || kind === "reject") { if (c) act(c.path, kind, c.key, c.verb === "project" || (c.verb === "task" && c.path.indexOf("proposals/") === 0)); }
    else if (kind === "approve-all") {
      var r = model.reports.filter(function (x) { return x.id === b.closest(".hp-report").dataset.report; })[0];
      if (r) act(r.path, "approve", r.id);
    } else if (c) focusCard(c);
  });
  panel.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && ev.target.classList.contains("hp-card")) ev.target.click();
  });

  // 刷新：启动、蜂巢重拉树之后（onTreeChange / App.refresh）、每 60 秒（标签页可见时）
  var orig = App.refresh;
  App.refresh = function () { return Promise.resolve(orig.apply(App, arguments)).then(function (r) { return load().then(function () { return r; }); }); };
  var A = window.NexusTableApp;
  if (A && A.onTreeChange) A.onTreeChange(function () { window.setTimeout(recompute, 300); });
  window.setInterval(function () { if (!document.hidden) load(); }, 60000);
  load();
})();
