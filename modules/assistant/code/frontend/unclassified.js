/**
 * unclassified.js —— 「待分类」面板（nexus-core 契约 v2.11「改挂未分类时间」）
 *
 * 仓主 2026-10-08：时间记进项目的「未分类」之后，要能追加一条「归入 xxx 任务」，且不破坏可追溯性。
 * 后端的做法是只追加：原来那条计时事实不动，每次归入往台账加一条 session.reassigned。本页只管挑任务、发请求。
 *
 * - 读：任务树里每个有「未分类」时间桶的项目（project.unclassifiedTaskId）各拉一次
 *   GET api/core/events?type=session.completed&taskId=<桶>&limit=200 —— 后端只回**此刻还在桶上**的段，归走一段就少一段。
 * - 按项目分组，时间多的在前。每组一个任务下拉：缺省只列本项目的任务，末尾「其他项目…」换成全部项目的。
 * - 写：行上的「归入」、组上的「全部归入所选任务」= 对每一段 POST api/core/sessions/{eventId}/reassign {taskId}，
 *   一个接一个发。成功（含 duplicate）的行立刻拿掉；部分失败按组报「N / M 段没归入：第一条 detail」，成功的不回滚。
 * - 撤回：刚归入成功的那一批可以「撤回」= 逐段 POST …/reassign {projectId: 原项目}（放回原项目的桶；台账里是又一条改挂）。
 *   只管最近一次操作。
 * - 从第一个请求到列表重拉完，面板里的按钮与下拉全部禁用。
 * - 一段都没有、events 404 / 读取失败 → 整块不出现。**归入不是计时**：不发 honeycomb:timer-changed。
 * - 名字（项目、任务、source）一律 textContent。不轮询：打开页面、每次操作后、标签页重新可见时拉。
 * 对外只挂 window.assistantUnclassified（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var CORE = BASE + "api/core/";
  var OTHER = "__other", OWN = "__own", PFX = "p:"; // PFX + 项目 id = 归入那个项目的未分类
  var SOURCES = { "timer-backend": "计时", "manual-backfill": "补登", "activity-confirmed": "电脑检测" };

  // ── 纯函数 ─────────────────────────────────────────────────────────
  function pad2(n) { return String(n).padStart(2, "0"); }

  function formatMinutes(seconds) {
    var m = Math.round((Number(seconds) || 0) / 60);
    return m < 1 ? "不到 1 分" : m + " 分";
  }

  // 「临时任务 MM-DD HH:MM · N 分」：时间 = 这一段的开始（本机时区，同待确认建议）
  function rowLabel(event) {
    var data = event.data || {};
    var d = new Date(data.startAt || event.time);
    var stamp = isNaN(d.getTime()) ? "" :
      " " + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    return "临时任务" + stamp + " · " + formatMinutes(data.durationSeconds);
  }

  function sourceLabel(source) { return SOURCES[source] || String(source || "未知来源"); }

  function projectPath(tree, project) {
    var zone = ((tree && tree.zones) || []).filter(function (z) { return z.id === project.zoneId; })[0];
    return [zone && zone.name, project.name].filter(Boolean).join(" / ");
  }

  // lists: {项目 id: {total, items}} → 有段的组，时间多的在前
  function buildGroups(tree, lists) {
    return ((tree && tree.projects) || []).filter(function (p) {
      return lists[p.id] && lists[p.id].items.length;
    }).map(function (p) {
      // 段也是长的在前，一样长的新的在前（稳定排序：再一样保持后端给的顺序）
      var at = function (e) { return new Date((e.data || {}).startAt || e.time).getTime() || 0; };
      var dur = function (e) { return Number((e.data || {}).durationSeconds) || 0; };
      var items = lists[p.id].items.slice().sort(function (a, b) { return dur(b) - dur(a) || at(b) - at(a); });
      return {
        project: p, path: projectPath(tree, p), items: items, total: Math.max(lists[p.id].total || 0, items.length),
        seconds: items.reduce(function (sum, e) { return sum + (Number((e.data || {}).durationSeconds) || 0); }, 0)
      };
    }).sort(function (a, b) { return b.seconds - a.seconds; });
  }

  function taskIn(tree, taskId) {
    return ((tree && tree.projects) || []).some(function (p) {
      return (p.tasks || []).some(function (t) { return t.id === taskId; });
    });
  }

  if (typeof window !== "undefined") {
    window.assistantUnclassified = { rowLabel: rowLabel, sourceLabel: sourceLabel, buildGroups: buildGroups,
                                     formatMinutes: formatMinutes };
  }
  if (typeof document === "undefined") return;

  // ── 页面 ───────────────────────────────────────────────────────────
  var panel = document.getElementById("unclassified-panel");
  if (!panel) return;
  var countEl = document.getElementById("unc-count");
  var statusEl = document.getElementById("unc-status");
  var messageEl = document.getElementById("unc-message");
  var undoBtn = document.getElementById("unc-undo");
  var groupsEl = document.getElementById("unc-groups");

  var tree = null, groups = [];
  var choice = {};   // 项目 id → 选中的任务 id
  var wide = {};     // 项目 id → 下拉是否展开到全部项目
  var busy = false;
  var undo = null;   // {projectId, ids[]}：最近一次归入成功的那一批

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function say(text, isError) {
    messageEl.textContent = text || "";
    messageEl.classList.toggle("is-error", Boolean(isError));
  }

  async function getJson(url) {
    try {
      var res = await fetch(url);
      return res.ok ? await res.json() : null;
    } catch (err) { return null; }
  }

  async function reassign(eventId, body) {
    var res, data = null;
    try {
      res = await fetch(CORE + "sessions/" + encodeURIComponent(eventId) + "/reassign", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
      });
      try { data = await res.json(); } catch (err) { data = null; }
    } catch (err) { return { ok: false, detail: "连不上服务" }; }
    if (res.ok) return { ok: true };
    var detail = data && data.detail;
    return { ok: false, detail: typeof detail === "string" ? detail : "请求失败（" + res.status + "）" };
  }

  // 拉任务树 + 每个桶上的段。任何一步不行 → 当没有（面板不出现），不报错。
  async function load() {
    var t = await getJson(CORE + "views/tree");
    var lists = {};
    var withBucket = ((t && t.projects) || []).filter(function (p) { return p.unclassifiedTaskId; });
    var results = await Promise.all(withBucket.map(function (p) {
      return getJson(CORE + "events?type=session.completed&limit=200&taskId=" + encodeURIComponent(p.unclassifiedTaskId));
    }));
    withBucket.forEach(function (p, i) {
      var r = results[i];
      if (r && Array.isArray(r.items)) lists[p.id] = { total: r.total, items: r.items };
    });
    tree = t;
    groups = buildGroups(tree, lists);
    Object.keys(choice).forEach(function (pid) {
      var c = choice[pid];
      if (c.indexOf(PFX) === 0 ? !((tree.projects || []).some(function (p) { return p.id === c.slice(2); })) : !taskIn(tree, c)) delete choice[pid];
    });
  }

  function taskSelect(g) {
    var pid = g.project.id;
    var sel = el("select", "field unc-task");
    sel.setAttribute("aria-label", "把「" + g.path + "」的未分类时间归到哪个任务");
    function option(parent, value, text) {
      var o = el("option", null, text);
      o.value = value;
      parent.appendChild(o);
    }
    function tasksOf(parent, project) {
      (project.tasks || []).forEach(function (task) {
        option(parent, task.id, task.name + (task.done ? "（已完成）" : ""));
      });
    }
    option(sel, "", "选任务…");
    if (wide[pid]) {
      option(sel, OWN, "← 只看本项目");
      ((tree && tree.projects) || []).forEach(function (p) {
        var own = p.id === pid; // 本项目的未分类就是这些段现在的位置：没有「未分类」可选
        if (own && !(p.tasks || []).length) return;
        var og = document.createElement("optgroup");
        og.label = projectPath(tree, p);
        if (!own) option(og, PFX + p.id, "未分类（只记到这个项目）"); // 没有任务的项目也在这里
        tasksOf(og, p);
        sel.appendChild(og);
      });
    } else {
      tasksOf(sel, g.project);
      option(sel, OTHER, "其他项目…");
    }
    sel.value = choice[pid] || "";
    if (sel.value !== (choice[pid] || "")) sel.value = "";  // 选的任务不在这份下拉里（比如换回了只看本项目）
    sel.disabled = busy;
    sel.addEventListener("change", function () {
      if (sel.value === OTHER || sel.value === OWN) {
        wide[pid] = sel.value === OTHER;
        delete choice[pid];
      } else {
        choice[pid] = sel.value;
      }
      render();
    });
    return sel;
  }

  function render() {
    var segments = groups.reduce(function (n, g) { return n + g.total; }, 0);
    panel.hidden = !groups.length && !messageEl.textContent;
    countEl.textContent = segments ? String(segments) : "";
    statusEl.hidden = !messageEl.textContent;
    undoBtn.hidden = !undo;
    undoBtn.disabled = busy;
    groupsEl.textContent = "";
    groups.forEach(function (g) {
      var pid = g.project.id;
      var box = el("section", "unc-group");
      box.dataset.project = pid;
      var head = el("div", "unc-head");
      head.appendChild(el("h3", "unc-project", g.path));
      head.appendChild(el("span", "unc-total mono", g.items.length + " 段 · 共 " + formatMinutes(g.seconds)));
      box.appendChild(head);
      if (g.total > g.items.length) {
        box.appendChild(el("p", "suggest-more", "还有 " + (g.total - g.items.length) + " 段没列出来，归完这些再看。"));
      }
      var sel = taskSelect(g);
      var picked = Boolean(choice[pid]) && sel.value === choice[pid];
      var bar = el("div", "unc-bar");
      bar.appendChild(sel);
      var all = el("button", "btn btn-fact unc-all", "全部归入所选任务");
      all.type = "button";
      all.disabled = busy || !picked;
      all.addEventListener("click", function () { move(g, g.items.slice()); });
      bar.appendChild(all);
      box.appendChild(bar);

      var list = el("ul", "unc-list");
      g.items.forEach(function (event) {
        var li = el("li", "unc-row");
        li.dataset.id = event.id;
        li.appendChild(el("span", "unc-what mono", rowLabel(event)));
        li.appendChild(el("span", "suggest-badge unc-source", sourceLabel(event.source)));
        var one = el("button", "btn btn-ghost unc-one", "归入");
        one.type = "button";
        one.disabled = busy || !picked;
        one.addEventListener("click", function () { move(g, [event]); });
        li.appendChild(one);
        list.appendChild(li);
      });
      box.appendChild(list);
      groupsEl.appendChild(box);
    });
  }

  function pathOf(taskId) {
    var out = taskId;
    ((tree && tree.projects) || []).forEach(function (p) {
      (p.tasks || []).forEach(function (t) { if (t.id === taskId) out = projectPath(tree, p) + " / " + t.name; });
    });
    return out;
  }

  // 一批请求一个接一个发；each(成功的那一项) 让调用方即时改页面。返回 {done, failed, detail}
  async function sendAll(ids, body, each) {
    var done = [], failed = 0, detail = "";
    for (var i = 0; i < ids.length; i++) {
      var r = await reassign(ids[i], body);
      if (r.ok) { done.push(ids[i]); if (each) each(ids[i]); } else { failed++; detail = detail || r.detail; }
    }
    return { done: done, failed: failed, detail: detail };
  }

  async function finish() {
    await load();
    busy = false;
    render();
  }

  async function move(g, events) {
    var taskId = choice[g.project.id];
    if (busy || !taskId || !events.length) return;
    busy = true;
    undo = null;
    say("");
    render();
    var toProject = taskId.indexOf(PFX) === 0 ? taskId.slice(2) : "";
    var label = toProject ? projectPath(tree, (tree.projects || []).filter(function (p) { return p.id === toProject; })[0]) + " · 未分类"
      : pathOf(taskId);
    var result = await sendAll(events.map(function (e) { return e.id; }), toProject ? { projectId: toProject } : { taskId: taskId }, function (id) {
      g.items = g.items.filter(function (e) { return e.id !== id; });  // 成功的行立刻拿掉
      g.total = Math.max(g.total - 1, g.items.length);
      render();
    });
    if (result.done.length) undo = { projectId: g.project.id, ids: result.done };
    say(result.failed
      ? result.failed + " / " + events.length + " 段没归入：" + result.detail
      : "已归入 " + result.done.length + " 段 → " + label, result.failed > 0);
    await finish();
  }

  undoBtn.addEventListener("click", async function () {
    if (busy || !undo) return;
    var batch = undo;
    busy = true;
    undo = null;
    render();
    var result = await sendAll(batch.ids, { projectId: batch.projectId });
    say(result.failed
      ? result.failed + " / " + batch.ids.length + " 段没撤回：" + result.detail
      : "已撤回 " + result.done.length + " 段，放回未分类", result.failed > 0);
    await finish();
  });

  async function refresh() {
    if (busy) return;
    busy = true;
    await finish();
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refresh();
  });
  refresh();
})();
