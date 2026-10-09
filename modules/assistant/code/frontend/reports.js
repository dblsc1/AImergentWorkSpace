/**
 * reports.js —— 「AI 报告」面板（nexus-core 契约 v2.20「AI 报告」）
 *
 * 仓主 2026-10-09：「AI 一次提交一份报告，我可以一键批准全部。当然单条编辑还是保留的」。AI（经 MCP 的 propose_report）
 * 交上来的是一份**待批准的报告**：很多条动作（记到某个项目 / 任务、提议新任务并记进去、当噪声忽略）。这里只管看和点：
 * 批准、改目标再批准、不要都由本页发请求，AI 发不了；入账走的是和「待确认建议」手点一样的 confirm（后端同一个函数）。
 *
 * - 读：GET api/core/activity/reports?status=pending&limit=1（最新一份待批准的）→ GET …/{id}（条目解析到当前状态）
 *   + 任务树（名字）。没有待批准的报告 / 端点 404 / 读取失败 → 整块不出现（刚批准完的结果行除外）。
 * - 条目按目标项目分组，组与组内都是时间长的在前（同「待分类」「待确认建议」）；忽略的条归「忽略」一组。
 * - 行：做什么 → 去哪、时长、理由；「改」（下拉，与「自动记录」同一种分项目任务下拉：选了就是先改目标再批准）、
 *   「批准」「不要」。头部「全部批准」（一次点击）与「整份不要」。全部批准后留一行「已批准 N 条，M 条已过期」。
 * - 不改既有各块的流程；批准 / 不要做完发 assistant:reports-changed，让待确认建议 / 待分类 / 自动记录重拉。
 * - **AI 给的文字（概述、理由、作者）、窗口标题、项目 / 任务名一律 textContent / Option 文本**，不解释。
 * - 操作进行中全部控件禁用。不轮询：打开页面、每次操作后、标签页重新可见时拉。**不是计时**：不发 honeycomb:timer-changed。
 * 对外只挂 window.assistantReports（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var CORE = BASE + "api/core/";
  var OPEN = { pending: true, failed: true };
  var STATUS = { applied: "已入账", stale: "已过期（你已处理过）", failed: "没成", rejected: "已不要" };
  var KINDS = { assign: "记到项目 / 任务", newTask: "新建任务", dismiss: "忽略" };

  // ── 纯函数 ─────────────────────────────────────────────────────────
  function pad2(n) { return String(n).padStart(2, "0"); }

  function formatMinutes(seconds) {
    var m = Math.round((Number(seconds) || 0) / 60);
    if (m < 1) return "不到 1 分";
    return m < 60 ? m + " 分" : Math.floor(m / 60) + " 小时" + (m % 60 ? " " + (m % 60) + " 分" : "");
  }

  function stamp(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? "" : pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function projectOf(tree, id) {
    var zones = {}, found = null;
    ((tree && tree.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
    ((tree && tree.projects) || []).forEach(function (p) {
      if (p.id === id) found = { id: p.id, path: [zones[p.zoneId], p.name].filter(Boolean).join(" / ") };
    });
    return found;
  }

  // 任务 id → {projectId, label}；项目的未分类桶（id 由项目 id 派生，nexus-core v2.9）也认得
  function taskInfo(tree, taskId) {
    var out = null;
    ((tree && tree.projects) || []).forEach(function (p) {
      var proj = projectOf(tree, p.id);
      if (taskId === p.unclassifiedTaskId || taskId === "t_unc_" + p.id) out = { projectId: p.id, label: proj.path + " · 未分类" };
      (p.tasks || []).forEach(function (t) { if (t.id === taskId) out = { projectId: p.id, label: proj.path + " / " + t.name }; });
    });
    return out;
  }

  // 一条去哪：{projectId（分组用，没有为 null）, label}
  function target(tree, item) {
    if (item.kind === "dismiss") return { projectId: null, label: "忽略（当噪声）" };
    if (item.kind === "newTask" && item.newTask) {
      var np = projectOf(tree, item.newTask.projectId);
      return { projectId: item.newTask.projectId, label: "新建任务「" + item.newTask.name + "」（" + (np ? np.path : item.newTask.projectId) + "）" };
    }
    if (item.taskId) {
      var t = taskInfo(tree, item.taskId);
      return { projectId: t ? t.projectId : null, label: t ? t.label : String(item.taskId) };
    }
    var p = projectOf(tree, item.projectId);
    return { projectId: item.projectId || null, label: (p ? p.path : String(item.projectId || "")) + " · 未分类" };
  }

  // 「app · 标题（等 N 段）」：窗口标题是屏幕来的字，只当文本
  function what(item) {
    var s = item.suggestions || [], first = s[0];
    if (!first) return "（建议已过期）";
    var label = [first.app, first.title].filter(Boolean).join(" · ");
    return label + (s.length > 1 ? " 等 " + s.length + " 段" : "");
  }

  // 按目标项目分组，组间、组内都是时间长的在前（稳定：一样长的保持报告里的顺序）；忽略的一组放最后
  function buildGroups(tree, items) {
    var groups = {}, order = [];
    items.forEach(function (item) {
      var t = target(tree, item);
      var key = t.projectId || (item.kind === "dismiss" ? "__dismiss" : "__other");
      if (!groups[key]) {
        var p = t.projectId ? projectOf(tree, t.projectId) : null;
        groups[key] = { key: key, title: key === "__dismiss" ? "忽略" : key === "__other" ? "其他" : (p ? p.path : t.projectId), items: [], seconds: 0 };
        order.push(key);
      }
      groups[key].items.push({ item: item, target: t });
      groups[key].seconds += Number(item.seconds) || 0;
    });
    order.forEach(function (k) {
      groups[k].items.sort(function (a, b) { return (Number(b.item.seconds) || 0) - (Number(a.item.seconds) || 0); });
    });
    return order.map(function (k) { return groups[k]; }).sort(function (a, b) {
      return (a.key === "__dismiss") - (b.key === "__dismiss") || b.seconds - a.seconds;
    });
  }

  function summaryLine(report) {
    var c = report.counts || {}, kinds = c.byKind || {};
    var parts = Object.keys(KINDS).filter(function (k) { return kinds[k]; }).map(function (k) { return KINDS[k] + " " + kinds[k] + " 条"; });
    return [report.author ? "作者 " + report.author : "", stamp(report.createdAt), "共 " + (c.items || 0) + " 条",
      parts.join("，"), "覆盖约 " + formatMinutes(c.seconds)].filter(Boolean).join(" · ");
  }

  if (typeof window !== "undefined") {
    window.assistantReports = { formatMinutes: formatMinutes, target: target, buildGroups: buildGroups, summaryLine: summaryLine };
  }
  if (typeof document === "undefined") return;

  // ── 页面 ───────────────────────────────────────────────────────────
  var panel = document.getElementById("report-panel");
  if (!panel) return;
  var countEl = document.getElementById("report-count");
  var metaEl = document.getElementById("report-meta");
  var summaryEl = document.getElementById("report-summary");
  var messageEl = document.getElementById("report-message");
  var approveAllBtn = document.getElementById("report-approve-all");
  var rejectAllBtn = document.getElementById("report-reject-all");
  var groupsEl = document.getElementById("report-groups");
  var tree = null, report = null, others = 0, busy = false;

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
  async function getJson(url) {
    try {
      var res = await fetch(url);
      return res.ok ? await res.json() : null;
    } catch (err) { return null; }
  }
  // → {ok, data}；失败带 detail 文本
  async function post(path, body) {
    var res, data = null;
    try {
      res = await fetch(CORE + "activity/reports/" + path, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {})
      });
      data = await res.json().catch(function () { return null; });
    } catch (err) { return { ok: false, detail: "连不上服务" }; }
    if (res.ok) return { ok: true, data: data };
    return { ok: false, detail: (data && typeof data.detail === "string") ? data.detail : "请求失败（" + res.status + "）" };
  }

  async function load() {
    var got = await Promise.all([getJson(CORE + "activity/reports?status=pending&limit=5"), getJson(CORE + "views/tree?includeEphemeral=true")]);
    var list = (got[0] && Array.isArray(got[0].items)) ? got[0].items : [];
    tree = got[1];
    report = list.length ? await getJson(CORE + "activity/reports/" + encodeURIComponent(list[0].id)) : null;
    others = report ? Math.max(list.length - 1, 0) : 0;
  }

  // 改目标的下拉：每个项目一组——「未分类」+ 它的任务（与「自动记录」同一种）
  function picker(item) {
    var sel = el("select", "field report-pick");
    sel.setAttribute("aria-label", "改记到：" + what(item));
    sel.appendChild(new Option("改…", ""));
    var zones = {};
    ((tree && tree.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
    ((tree && tree.projects) || []).forEach(function (p) {
      var og = document.createElement("optgroup");
      og.label = [zones[p.zoneId], p.name].filter(Boolean).join(" / ");
      og.appendChild(new Option("未分类", "p:" + p.id));
      (p.tasks || []).forEach(function (t) { og.appendChild(new Option(t.name + (t.done ? "（已完成）" : ""), "t:" + t.id)); });
      sel.appendChild(og);
    });
    sel.disabled = busy;
    sel.addEventListener("change", function () {
      if (sel.value) approveItem(item, sel.value.indexOf("p:") === 0 ? { projectId: sel.value.slice(2) } : { taskId: sel.value.slice(2) });
    });
    return sel;
  }

  function button(label, cls, aria, onClick, disabled) {
    var b = el("button", cls, label);
    b.type = "button";
    b.setAttribute("aria-label", aria);
    b.disabled = disabled;
    b.addEventListener("click", onClick);
    return b;
  }

  function row(entry) {
    var item = entry.item, open = Boolean(OPEN[item.status]);
    var li = el("li", "unc-row report-row is-" + item.status);
    li.dataset.id = item.id;
    var main = el("div", "report-main");
    main.appendChild(el("span", "unc-what", what(item)));
    main.appendChild(el("span", "auto-target", "→ " + entry.target.label));
    main.appendChild(el("span", "report-dur mono", formatMinutes(item.seconds)));
    if (item.reason) main.appendChild(el("span", "report-reason", "理由：" + item.reason));
    if (!open || item.staleNow || item.status === "failed") {
      var note = item.status !== "pending" ? (STATUS[item.status] || item.status) : item.staleNow + " 段已被你处理过，批准时会跳过";
      if (item.failure && item.status === "failed") note += "：" + item.failure;
      main.appendChild(el("span", "suggest-badge report-status", note));
    }
    li.appendChild(main);
    var acts = el("div", "report-acts");
    if (open && item.kind !== "dismiss") acts.appendChild(picker(item));
    acts.appendChild(button("批准", "btn btn-fact report-ok", "批准：" + what(item), function () { approveItem(item, null); }, busy || !open));
    acts.appendChild(button("不要", "btn btn-ghost report-no", "不要：" + what(item), function () { rejectItem(item); }, busy || !open));
    li.appendChild(acts);
    return li;
  }

  function render() {
    panel.hidden = !report && !messageEl.textContent;
    if (!report) { groupsEl.textContent = ""; metaEl.textContent = ""; summaryEl.textContent = ""; countEl.textContent = ""; approveAllBtn.hidden = rejectAllBtn.hidden = true; return; }
    countEl.textContent = String((report.counts || {}).pending || "");
    metaEl.textContent = summaryLine(report) + (others ? " · 另有 " + others + " 份待批准，处理完这份后显示" : "");
    summaryEl.textContent = report.summary || "";
    summaryEl.hidden = !report.summary;
    var anyOpen = report.items.some(function (i) { return OPEN[i.status]; });
    approveAllBtn.hidden = rejectAllBtn.hidden = false;
    approveAllBtn.disabled = busy || !anyOpen;
    rejectAllBtn.disabled = busy || !anyOpen;
    groupsEl.textContent = "";
    buildGroups(tree, report.items).forEach(function (g) {
      var box = el("section", "unc-group");
      box.dataset.group = g.key;
      var head = el("div", "unc-head");
      head.appendChild(el("h3", "unc-project", g.title));
      head.appendChild(el("span", "unc-total mono", g.items.length + " 条 · 共 " + formatMinutes(g.seconds)));
      box.appendChild(head);
      var list = el("ul", "unc-list");
      g.items.forEach(function (entry) { list.appendChild(row(entry)); });
      box.appendChild(list);
      groupsEl.appendChild(box);
    });
  }

  function changed() { document.dispatchEvent(new Event("assistant:reports-changed")); }

  async function run(request, okText) {
    if (busy) return;
    busy = true;
    say("");
    render();
    var r = await request();
    await load();
    busy = false;
    if (r.ok) say(okText(r.data), false);
    else say("没成：" + r.detail, true);
    render();
    changed();
  }

  function approveAll() {
    run(function () { return post(encodeURIComponent(report.id) + "/approve"); }, function (d) {
      return "已批准 " + d.applied + " 条，" + d.stale + " 条已过期" + (d.failed ? "，" + d.failed + " 条没成（见下，可重试）" : "");
    });
  }
  function rejectAll() {
    run(function () { return post(encodeURIComponent(report.id) + "/reject"); }, function () { return "已整份不要，什么都没记。"; });
  }
  function approveItem(item, override) {
    run(function () { return post(encodeURIComponent(report.id) + "/items/" + encodeURIComponent(item.id) + "/approve", override); }, function (d) {
      return d.status === "applied" ? "已批准：" + what(item) : d.status === "stale" ? "已过期（你已处理过），没有重复记。" : "没成：" + (d.failure || d.status);
    });
  }
  function rejectItem(item) {
    run(function () { return post(encodeURIComponent(report.id) + "/items/" + encodeURIComponent(item.id) + "/reject"); }, function () { return "已不要这一条。"; });
  }

  approveAllBtn.addEventListener("click", approveAll);
  rejectAllBtn.addEventListener("click", rejectAll);

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
