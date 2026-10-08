/**
 * auto.js —— 「自动记录」面板（nexus-core 契约 v2.14「自动跟踪进行中的任务」）
 *
 * 「允许 AI 管理进行中的任务」开着时，分类规则把握够高的段上传后直接记成时间，不再逐条确认。这里列出今天这样
 * 记下的每一段：哪个窗口、记到了哪、多少分钟；记错了用「改归属」换到别的任务 / 别的项目的未分类。
 *
 * - 读：GET api/core/activity/auto（今天的，新的在前）+ 任务树（名字）。
 * - 改归属：行上的下拉，选了就发 POST api/core/sessions/{eventId}/reassign {taskId} 或 {projectId}
 *   （同「待分类」：台账只追加一条改挂，原来的记录不动，可以再改）。发完重拉。
 * - 一段都没有、端点 404 / 读取失败 → 整块不出现。**改归属不是计时**：不发 honeycomb:timer-changed。
 * - 窗口标题、项目名、任务名一律 textContent / Option 文本。不轮询：打开页面、每次操作后、标签页重新可见时拉。
 * 对外只挂 window.assistantAuto（纯函数，给单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var CORE = BASE + "api/core/";

  // ── 纯函数 ─────────────────────────────────────────────────────────
  function pad2(n) { return String(n).padStart(2, "0"); }

  // 「HH:MM · N 分 · 程序 · 标题」（时间 = 这一段的开始，本机时区）
  function rowLabel(item) {
    var d = new Date(item.startAt), m = Math.round((Number(item.durationSeconds) || 0) / 60);
    var at = isNaN(d.getTime()) ? "" : pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    return [at, m < 1 ? "不到 1 分" : m + " 分", item.app, item.title].filter(Boolean).join(" · ");
  }

  // 现在记在哪：「分区 / 项目 / 任务」；项目的未分类桶 → 「分区 / 项目 · 未分类」；查不到 → id
  function targetLabel(tree, item) {
    var zones = {}, out = null;
    ((tree && tree.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
    ((tree && tree.projects) || []).forEach(function (p) {
      var path = [zones[p.zoneId], p.name].filter(Boolean).join(" / ");
      // 桶的 id 由项目 id 派生（nexus-core v2.9）：树里还没带出 unclassifiedTaskId 也认得
      if (item.taskId === p.unclassifiedTaskId || item.taskId === "t_unc_" + p.id) out = path + " · 未分类";
      (p.tasks || []).forEach(function (t) { if (t.id === item.taskId) out = path + " / " + t.name; });
    });
    return out || String(item.taskId || item.projectId || "");
  }

  if (typeof window !== "undefined") window.assistantAuto = { rowLabel: rowLabel, targetLabel: targetLabel };
  if (typeof document === "undefined") return;

  // ── 页面 ───────────────────────────────────────────────────────────
  var panel = document.getElementById("auto-panel");
  if (!panel) return;
  var countEl = document.getElementById("auto-count");
  var messageEl = document.getElementById("auto-message");
  var listEl = document.getElementById("auto-list");
  var tree = null, items = [], busy = false;

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

  // 改归属的下拉：每个项目一组——「未分类」（放到这个项目的桶）+ 它的任务
  function picker(item) {
    var sel = el("select", "field auto-pick");
    sel.setAttribute("aria-label", "把「" + rowLabel(item) + "」改记到");
    sel.appendChild(new Option("改归属…", ""));
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
    sel.addEventListener("change", function () { if (sel.value) move(item, sel.value); });
    return sel;
  }

  function render() {
    panel.hidden = !items.length && !messageEl.textContent;
    countEl.textContent = items.length ? String(items.length) : "";
    listEl.textContent = "";
    items.forEach(function (item) {
      var li = el("li", "unc-row auto-row");
      li.dataset.id = item.eventId;
      li.appendChild(el("span", "unc-what mono", rowLabel(item)));
      li.appendChild(el("span", "auto-target", "→ " + targetLabel(tree, item)));
      if (item.ai) li.appendChild(el("span", "suggest-badge auto-ai", "AI 认的"));   // v2.15：按 AI 认的那条规则记下的
      if (item.reassigned) li.appendChild(el("span", "suggest-badge", "已改"));
      li.appendChild(picker(item));
      listEl.appendChild(li);
    });
  }

  async function load() {
    var got = await Promise.all([getJson(CORE + "activity/auto"), getJson(CORE + "views/tree?includeEphemeral=true")]);
    items = (got[0] && Array.isArray(got[0].items)) ? got[0].items : [];
    tree = got[1];
  }

  async function move(item, value) {
    if (busy) return;
    busy = true;
    say("");
    render();
    var body = value.indexOf("p:") === 0 ? { projectId: value.slice(2) } : { taskId: value.slice(2) };
    var res = null, data = null;
    try {
      res = await fetch(CORE + "sessions/" + encodeURIComponent(item.eventId) + "/reassign", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
      });
      data = await res.json().catch(function () { return null; });
    } catch (err) { res = null; }
    await load();
    busy = false;
    if (res && res.ok) {
      var now = items.filter(function (x) { return x.eventId === item.eventId; })[0];
      say("已改归属 → " + (now ? targetLabel(tree, now) : "所选的目标"), false);
    } else {
      say("没改成：" + (res ? ((data && typeof data.detail === "string") ? data.detail : "请求失败（" + res.status + "）")
        : "连不上服务"), true);
    }
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
