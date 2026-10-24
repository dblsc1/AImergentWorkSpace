/**
 * newitems.js —— 「新项目与任务」面板（nexus-core 契约 v2.30「AI 提议新项目与任务」）
 *
 * 仓主 2026-10-19：给 MCP 加「新增项目」「新增项目下的任务」两个接口，都要人批准。AI（经 MCP 的 propose_project / propose_task）
 * 只能存提议；批准 / 不要只有人在这里点（POST activity/proposals/{id}/approve|reject，后端只许人）。
 *
 * - 读：GET api/core/activity/proposals?status=pending → {projects: [项目提议 + tasks[]], tasks: [挂在现成项目上的任务提议]}。
 *   没有待批准的 / 端点 404 / 读取失败 → 整块不出现（刚处理完的结果行除外）。
 * - 项目卡：分区、理由、它下面的任务提议。「批准项目和任务」= 先批准项目、再逐条批准它的任务（某一步失败就停，写在提示里）；
 *   「只批准项目」；「不要」（项目与它下面待批准的任务一起不要）。任务行单独「批准」「不要」；项目还没批准时「批准」禁用。
 * - **AI 给的文字（名字、理由）、分区 / 项目名一律 textContent**，不解释。操作进行中全部控件禁用。不轮询：打开页面、每次操作后、标签页重新可见时拉。
 * 对外只挂 window.assistantNewItems（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var CORE = BASE + "api/core/";

  // 项目卡里「批准项目和任务」要依次批准的 id：项目自己，再是它还没批准的任务
  function approveChain(project) {
    return [project.id].concat((project.tasks || []).filter(function (t) { return t.status === "pending"; }).map(function (t) { return t.id; }));
  }

  if (typeof window !== "undefined") window.assistantNewItems = { approveChain: approveChain };
  if (typeof document === "undefined") return;

  var panel = document.getElementById("newitems-panel");
  if (!panel) return;
  var countEl = document.getElementById("newitems-count");
  var messageEl = document.getElementById("newitems-message");
  var groupsEl = document.getElementById("newitems-groups");
  var data = { projects: [], tasks: [], changes: [], ignores: [] }, busy = false;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function say(text, isError) {
    messageEl.textContent = text || "";
    messageEl.hidden = !text;
    messageEl.classList.toggle("is-error", Boolean(isError));
  }
  async function load() {
    try {
      var res = await fetch(CORE + "activity/proposals?status=pending&limit=100");
      var body = res.ok ? await res.json() : null;
      data = body && Array.isArray(body.projects) && Array.isArray(body.tasks) ? body : { projects: [], tasks: [] };
      // 老服务端没有这两个键：当空
      data.changes = Array.isArray(data.changes) ? data.changes : [];
      data.ignores = Array.isArray(data.ignores) ? data.ignores : [];
    } catch (err) { data = { projects: [], tasks: [], changes: [], ignores: [] }; }
  }
  // → {ok, detail}
  async function post(id, verb) {
    var res, body = null;
    try {
      res = await fetch(CORE + "activity/proposals/" + encodeURIComponent(id) + "/" + verb, { method: "POST" });
      body = await res.json().catch(function () { return null; });
    } catch (err) { return { ok: false, detail: "连不上服务" }; }
    if (res.ok) return { ok: true };
    return { ok: false, detail: (body && typeof body.detail === "string") ? body.detail : "请求失败（" + res.status + "）" };
  }

  function button(label, cls, aria, onClick, disabled) {
    var b = el("button", cls, label);
    b.type = "button";
    b.setAttribute("aria-label", aria);
    b.disabled = disabled;
    b.addEventListener("click", onClick);
    return b;
  }

  function taskRow(task, projectPending) {
    var li = el("li", "unc-row report-row");
    li.dataset.id = task.id;
    var main = el("div", "report-main");
    main.appendChild(el("span", "unc-what", "新任务「" + task.name + "」"));
    if (task.projectName) main.appendChild(el("span", "auto-target", "→ " + task.projectName));
    if (task.reason) main.appendChild(el("span", "report-reason", "理由：" + task.reason));
    if (projectPending) main.appendChild(el("span", "suggest-badge report-status", "要等项目批准后才能批准"));
    li.appendChild(main);
    var acts = el("div", "report-acts");
    acts.appendChild(button("批准", "btn btn-fact report-ok", "批准任务：" + task.name, function () { run([[task.id, "approve"]], "已批准任务「" + task.name + "」"); }, busy || projectPending));
    acts.appendChild(button("不要", "btn btn-ghost report-no", "不要任务：" + task.name, function () { run([[task.id, "reject"]], "已不要任务「" + task.name + "」"); }, busy));
    li.appendChild(acts);
    return li;
  }

  // AI 提议改名 / 移动 / 删除现有项目或任务（nexus-core 的 changes）；删除不可逆，标成危险色
  var OPS = { rename: "改名", move: "移动", delete: "删除" };
  function changeRow(c) {
    var li = el("li", "unc-row report-row" + (c.op === "delete" ? " is-danger" : ""));
    li.dataset.id = c.id;
    var from = c.targetName == null ? "（已不存在）" : c.targetName;
    var main = el("div", "report-main");
    main.appendChild(el("span", "unc-what", OPS[c.op] || c.op));
    main.appendChild(el("span", "auto-target", c.op === "rename" ? from + " 改成 " + (c.name == null ? "？" : c.name) : c.op === "move" ? from + " 移到 " + (c.toName == null ? "（已不存在）" : c.toName) : from));
    if (c.reason) main.appendChild(el("span", "report-reason", "理由：" + c.reason));
    li.appendChild(main);
    return decide(li, c.id, from);
  }
  function ignoreRow(g) {
    var li = el("li", "unc-row report-row");
    li.dataset.id = g.id;
    var main = el("div", "report-main");
    main.appendChild(el("span", "unc-what", "以后都忽略"));
    main.appendChild(el("span", "auto-target", (g.app == null ? "？" : g.app) + (g.titleContains ? " · 标题含 “" + g.titleContains + "”" : g.hasTitleFilter ? " · 带标题条件" : "")));
    if (g.reason) main.appendChild(el("span", "report-reason", "理由：" + g.reason));
    li.appendChild(main);
    return decide(li, g.id, g.app);
  }
  function decide(li, id, label) {
    var acts = el("div", "report-acts");
    acts.appendChild(button("批准", "btn btn-fact report-ok", "批准：" + label, function () { run([[id, "approve"]], "已批准「" + label + "」"); }, busy));
    acts.appendChild(button("不要", "btn btn-ghost report-no", "不要：" + label, function () { run([[id, "reject"]], "已不要「" + label + "」"); }, busy));
    li.appendChild(acts);
    return li;
  }
  function group(title, rows, make) {
    if (!rows.length) return;
    var box = el("section", "unc-group");
    box.appendChild(el("div", "unc-head")).appendChild(el("h3", "unc-project", title));
    var list = el("ul", "unc-list");
    rows.forEach(function (r) { list.appendChild(make(r)); });
    box.appendChild(list);
    groupsEl.appendChild(box);
  }

  function projectCard(p) {
    var box = el("section", "unc-group");
    box.dataset.id = p.id;
    var head = el("div", "unc-head");
    head.appendChild(el("h3", "unc-project", "新项目「" + p.name + "」"));
    head.appendChild(el("span", "unc-total mono", (p.zoneName ? "分区：" + p.zoneName : "") + " · " + p.tasks.length + " 个任务"));
    box.appendChild(head);
    if (p.reason) box.appendChild(el("p", "report-reason", "理由：" + p.reason));
    var acts = el("div", "suggest-bulk");
    acts.appendChild(button(p.tasks.length ? "批准项目和任务" : "批准项目", "btn btn-fact newitems-all", "批准项目：" + p.name,
      function () { run(approveChain(p).map(function (id) { return [id, "approve"]; }), "已批准项目「" + p.name + "」" + (p.tasks.length ? "和它的任务" : "")); }, busy));
    if (p.tasks.length) {
      acts.appendChild(button("只批准项目", "btn btn-ghost newitems-only", "只批准项目：" + p.name, function () { run([[p.id, "approve"]], "已批准项目「" + p.name + "」，任务还在下面等你"); }, busy));
    }
    acts.appendChild(button("不要", "btn btn-ghost danger newitems-no", "不要项目：" + p.name, function () { run([[p.id, "reject"]], "已不要项目「" + p.name + "」和它下面的任务"); }, busy));
    box.appendChild(acts);
    if (p.tasks.length) {
      var list = el("ul", "unc-list");
      p.tasks.forEach(function (t) { list.appendChild(taskRow(t, true)); });
      box.appendChild(list);
    }
    return box;
  }

  function render() {
    var n = data.projects.length + data.tasks.length + data.changes.length + data.ignores.length;
    panel.hidden = !n && !messageEl.textContent;
    countEl.textContent = n ? String(n) : "";
    groupsEl.textContent = "";
    data.projects.forEach(function (p) { groupsEl.appendChild(projectCard(p)); });
    if (data.tasks.length) {
      var box = el("section", "unc-group");
      box.appendChild(el("div", "unc-head")).appendChild(el("h3", "unc-project", "现有项目下的新任务"));
      var list = el("ul", "unc-list");
      data.tasks.forEach(function (t) { list.appendChild(taskRow(t, false)); });
      box.appendChild(list);
      groupsEl.appendChild(box);
    }
    group("修改与删除", data.changes, changeRow);
    group("以后都忽略", data.ignores, ignoreRow);
  }

  // steps: [[id, "approve"|"reject"], …] 依次做，失败就停
  async function run(steps, okText) {
    if (busy) return;
    busy = true;
    say("");
    render();
    var failed = null;
    for (var i = 0; i < steps.length && !failed; i++) {
      var r = await post(steps[i][0], steps[i][1]);
      if (!r.ok) failed = r.detail;
    }
    await load();
    busy = false;
    say(failed ? "没成：" + failed : okText, Boolean(failed));
    render();
  }

  async function refresh() {
    if (busy) return;
    busy = true;
    await load();
    busy = false;
    render();
  }
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") refresh();
  });
  refresh();
})();
