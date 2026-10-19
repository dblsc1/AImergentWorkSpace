/**
 * reports.js —— 「AI 报告」面板（nexus-core 契约 v2.20「AI 报告」）
 *
 * 仓主 2026-10-09：「AI 一次提交一份报告，我可以一键批准全部。当然单条编辑还是保留的」。AI（经 MCP 的 propose_report）
 * 交上来的是一份**待批准的报告**：很多条动作（记到某个项目 / 任务、提议新任务并记进去、当噪声忽略）。这里只管看和点：
 * 批准、改目标再批准、不要都由本页发请求，AI 发不了；入账走的是和「待确认建议」手点一样的 confirm（后端同一个函数）。
 *
 * - 读：GET api/core/activity/reports?status=pending&limit=1（最新一份待批准的）→ GET …/{id}（条目解析到当前状态）
 *   + 任务树（名字）。没有待批准的报告 / 端点 404 / 读取失败 → 整块不出现（刚批准完的结果行除外）。
 * - 仓主 2026-10-19：「一个 ai 会话理论上只对应一个任务。把界面优化成一个窗口 / agent 对话对应的一个项目的任务，点击展开可以
 *   单独划分，否则直接全部批准。」（nexus-core v2.33）按**窗口**（程序 + 归一化标题，同 detector 的 window_key）收起成一行：总时长、段数、
 *   报告给的去向（同一窗口的段落在不同去向时标「混合」，不藏）。点开列出每一段；整个窗口或单段可「改」（下拉，与「自动记录」同一种
 *   分项目任务下拉）或「当噪声」（= 忽略；不是「不要 AI 这个猜测」）——这些只改报告（POST …/items/{id}/split），不入账。窗口按时长长的在前，全是「忽略」的最后。
 * - 头部「全部批准」（一次点击）与「整份不要」。全部批准后留一行「已批准 N 条，M 条已过期」。
 * - 不改既有各块的流程；批准 / 不要做完发 assistant:reports-changed，让待确认建议 / 待分类重拉（「自动记录」在另一页，不听这个事件，打开或标签页重新可见时才重拉）。
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

  // 窗口的键：程序 + 归一化标题（suggestions.js 的 normTitle，与服务端 history.norm_title 同算法），不分大小写
  function winKey(app, title) {
    var S = typeof window !== "undefined" && window.assistantSuggestions;
    var t = S ? S.normTitle(title || "") : String(title || "").split(/\s+/).join(" ");
    return String(app || "").toLowerCase() + "\n" + t.toLowerCase();
  }

  // 报告的条 → 窗口：每个窗口 {key, app, title, segs[{item, sug, target}], seconds, labels[去向文字], dismissOnly}，
  // 时长长的在前（稳定），全是「忽略」的放最后；段按开始时间。同一窗口的段可以分属不同的条（不同去向）→ labels 不止一个 = 混合
  function buildWindows(tree, items) {
    var wins = {}, order = [];
    items.forEach(function (item) {
      var t = target(tree, item);
      (item.suggestions || []).forEach(function (sug) {
        var key = winKey(sug.app, sug.title);
        if (!wins[key]) { wins[key] = { key: key, app: sug.app, title: sug.title, segs: [], seconds: 0, labels: [] }; order.push(key); }
        var w = wins[key];
        w.segs.push({ item: item, sug: sug, target: t });
        w.seconds += Number(sug.durationSeconds) || 0;
        if (w.labels.indexOf(t.label) < 0) w.labels.push(t.label);
      });
    });
    order.forEach(function (k) {
      wins[k].segs.sort(function (a, b) { return String(a.sug.startAt).localeCompare(String(b.sug.startAt)); });
      wins[k].dismissOnly = wins[k].segs.every(function (g) { return g.item.kind === "dismiss"; });
    });
    return order.map(function (k) { return wins[k]; }).sort(function (a, b) {
      return a.dismissOnly - b.dismissOnly || b.seconds - a.seconds;
    });
  }

  function summaryLine(report) {
    var c = report.counts || {}, kinds = c.byKind || {};
    var parts = Object.keys(KINDS).filter(function (k) { return kinds[k]; }).map(function (k) { return KINDS[k] + " " + kinds[k] + " 条"; });
    return [report.author ? "作者 " + report.author : "", stamp(report.createdAt), "共 " + (c.items || 0) + " 条",
      parts.join("，"), "覆盖约 " + formatMinutes(c.seconds)].filter(Boolean).join(" · ");
  }

  if (typeof window !== "undefined") {
    window.assistantReports = { formatMinutes: formatMinutes, target: target, buildWindows: buildWindows, summaryLine: summaryLine };
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

  // 改目标的下拉：每个项目一组——「未分类」+ 它的任务（与「自动记录」同一种）；选了 → onPick({taskId}|{projectId})
  function picker(label, onPick) {
    var sel = el("select", "field report-pick");
    sel.setAttribute("aria-label", label);
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
      if (sel.value) onPick(sel.value.indexOf("p:") === 0 ? { projectId: sel.value.slice(2) } : { taskId: sel.value.slice(2) });
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

  var expanded = {};  // 窗口键 → 展开着（重绘后保持）
  function isOpen(g) { return Boolean(OPEN[g.item.status]); }
  function whatOf(w) { return [w.app, w.title].filter(Boolean).join(" · "); }

  function statusNote(g) {
    var item = g.item;
    if (item.status === "pending" && !item.staleNow) return "";
    var note = item.status !== "pending" ? (STATUS[item.status] || item.status) : item.staleNow + " 段已被你处理过，批准时会跳过";
    return item.failure && item.status === "failed" ? note + "：" + item.failure : note;
  }

  // 把若干段（还能改的）挪到新去向：按原来所在的条各发一次 split；挪走的整条就地改目标
  function moveSegs(segs, to) {
    var byItem = {}, ids = [];
    segs.filter(isOpen).forEach(function (g) {
      if (to.dismiss && g.item.kind === "dismiss") return;
      if (!byItem[g.item.id]) { byItem[g.item.id] = []; ids.push(g.item.id); }
      byItem[g.item.id].push(g.sug.id);
    });
    if (!ids.length) return;
    run(async function () {
      var r = { ok: true }, i = 0;
      for (; i < ids.length && r.ok; i++) {
        r = await post(encodeURIComponent(report.id) + "/items/" + encodeURIComponent(ids[i]) + "/split",
          Object.assign({ suggestionIds: byItem[ids[i]] }, to));
      }
      if (!r.ok && i > 1) r = { ok: false, detail: "已挪 " + (i - 1) + "/" + ids.length + " 条后失败：" + r.detail };
      return r;
    }, function () { return "已改（还没入账，点「全部批准」才记）。"; });
  }

  function segRow(g) {
    var open = isOpen(g);
    var li = el("li", "unc-row report-row report-seg is-" + g.item.status);
    li.dataset.sid = g.sug.id;
    var main = el("div", "report-main");
    main.appendChild(el("span", "unc-what", stamp(g.sug.startAt) + " · " + formatMinutes(g.sug.durationSeconds)));
    main.appendChild(el("span", "auto-target", "→ " + g.target.label));
    var note = statusNote(g);
    if (note) main.appendChild(el("span", "suggest-badge report-status", note));
    li.appendChild(main);
    if (open) {
      var acts = el("div", "report-acts");
      acts.appendChild(picker("这一段改记到：" + stamp(g.sug.startAt), function (to) { moveSegs([g], to); }));
      acts.appendChild(button("当噪声", "btn btn-ghost report-no", "这一段当噪声：" + stamp(g.sug.startAt), function () { moveSegs([g], { dismiss: true }); }, busy || g.item.kind === "dismiss"));
      li.appendChild(acts);
    }
    return li;
  }

  function winRow(w) {
    var open = w.segs.some(isOpen), isExp = Boolean(expanded[w.key]);
    var li = el("li", "report-win" + (isExp ? " is-expanded" : ""));
    li.dataset.key = w.key;
    var head = el("div", "unc-row report-row");
    var main = el("div", "report-main");
    var tog = button((isExp ? "▾ " : "▸ ") + whatOf(w), "btn btn-ghost report-toggle unc-what", (isExp ? "收起" : "展开") + "窗口：" + whatOf(w), function () {
      expanded[w.key] = !isExp;
      render();
    }, false);
    tog.setAttribute("aria-expanded", String(isExp));
    main.appendChild(tog);
    main.appendChild(el("span", "report-dur mono", w.segs.length + " 段 · " + formatMinutes(w.seconds)));
    main.appendChild(el("span", "auto-target report-wintarget" + (w.labels.length > 1 ? " is-mixed" : ""),
      w.labels.length > 1 ? "→ 混合（" + w.labels.length + " 个去向，展开看）：" + w.labels.join("；") : "→ " + w.labels[0]));
    head.appendChild(main);
    if (open) {
      var acts = el("div", "report-acts");
      acts.appendChild(picker("整个窗口改记到：" + whatOf(w), function (to) { moveSegs(w.segs, to); }));
      acts.appendChild(button("当噪声", "btn btn-ghost report-no", "整个窗口当噪声：" + whatOf(w), function () { moveSegs(w.segs, { dismiss: true }); }, busy || w.dismissOnly));
      head.appendChild(acts);
    }
    li.appendChild(head);
    if (isExp) {
      var list = el("ul", "unc-list report-segs");
      w.segs.forEach(function (g) { list.appendChild(segRow(g)); });
      li.appendChild(list);
    }
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
    var wins = buildWindows(tree, report.items);
    // 一段都看不到的条（建议全过期 / 已处理）：留一行，免得「全部批准」开着列表却是空的
    var orphans = report.items.filter(function (i) { return !(i.suggestions || []).length; });
    if (wins.length || orphans.length) {
      var box = el("section", "unc-group");
      var head = el("div", "unc-head");
      head.appendChild(el("h3", "unc-project", "窗口（一个窗口 = 一个会话 = 一个任务）"));
      head.appendChild(el("span", "unc-total mono", wins.length + " 个窗口" + (orphans.length ? "，另有 " + orphans.length + " 条已无可见的段" : "")));
      box.appendChild(head);
      var list = el("ul", "unc-list");
      wins.forEach(function (w) { list.appendChild(winRow(w)); });
      orphans.forEach(function (i) {
        var li = el("li", "unc-row report-row report-orphan is-" + i.status);
        li.dataset.id = i.id;
        li.appendChild(el("span", "unc-what", (i.staleNow || 1) + " 条已过期或已处理"));
        li.appendChild(el("span", "auto-target", "→ " + target(tree, i).label));
        list.appendChild(li);
      });
      box.appendChild(list);
      groupsEl.appendChild(box);
    }
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
