/**
 * suggestions.js —— 「待确认建议」面板（nexus-core 契约 v2.2「活动建议」，v2.5 的 idle 段）
 *
 * 桌面检测程序（ai-detector）看见「11:05–12:07 在 VS Code 里」就上传一条建议，但**建议不是事实**：
 * 它不进任何统计，直到人在这里点「确认」。确认 = 后端经补登同一条路径写一条 session.completed。
 * （2026-09-30 从计时页搬来「AI助理」页；计时页只留一个「N 条待确认 → AI助理」的链接。）
 *
 * - idle 段（v2.5：前台没换、但无操作）单独标「无操作·可能在阅读」，**永远不进「全部确认」**——
 *   契约说由人决定算不算；它的把握本来就 ≤ 0.3。
 * 对外只挂 window.assistantSuggestions（纯函数，给单测用）。
 *
 * - 端点 404（后端早于 v2.2）→ 整块不出现，不报错。
 * - **不发 honeycomb:timer-changed**：确认不是开始/停止计时，顶栏芯片只关心在跑的那段
 *   （postCore 只对 api/core/timer/ 路径发这个事件，这里的路径天然不触发）。
 * - app/title 来自别的机器，一律 textContent，不进 innerHTML。
 * - 不轮询：只在打开页面、每次操作后、标签页重新可见时拉一次——轮询重绘会冲掉人正在改的下拉。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/core/activity/suggestions";

  // ── 纯函数（window.ringSuggestions，单测直接调） ─────────────────────
  function pad2(n) { return String(n).padStart(2, "0"); }

  // 本机时区显示（同补登：人看的是自己的墙钟）。跨天时结束也带日期。
  function formatRange(startAt, endAt) {
    var s = new Date(startAt), e = new Date(endAt);
    var day = function (d) { return pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()); };
    var hm = function (d) { return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); };
    return day(s) + " " + hm(s) + "–" + (day(s) === day(e) ? "" : day(e) + " ") + hm(e);
  }

  function formatMinutes(seconds) {
    var m = Math.round(seconds / 60);
    return m < 1 ? "不到 1 分" : m + " 分";
  }

  // taskId → 「分区 / 项目 / 任务」；查不到（任务已删）返回 null。
  function taskPath(tree, taskId) {
    if (!tree || !taskId) return null;
    var zones = {};
    (tree.zones || []).forEach(function (z) { zones[z.id] = z.name; });
    var projects = tree.projects || [];
    for (var i = 0; i < projects.length; i++) {
      var tasks = projects[i].tasks || [];
      for (var j = 0; j < tasks.length; j++) {
        if (tasks[j].id === taskId) {
          return [zones[projects[i].zoneId], projects[i].name, tasks[j].name].filter(Boolean).join(" / ");
        }
      }
    }
    return null;
  }

  // 「全部确认」只动有建议任务、且把握够的（契约：没有 taskId 的必须人挑）。
  // 给了 tree 时建议的任务还得在树里——被删的任务确认必 404，该由人重挑。
  // idle 段（无操作）一律人挑，不管阈值调多低。
  function eligible(items, threshold, tree) {
    return (items || []).filter(function (it) {
      var s = it.suggestion || {};
      return !it.idle && Boolean(s.taskId) && typeof s.confidence === "number" && s.confidence >= threshold &&
        (!tree || taskPath(tree, s.taskId) !== null);
    });
  }

  window.assistantSuggestions = { formatRange: formatRange, taskPath: taskPath, eligible: eligible };

  // ── DOM ─────────────────────────────────────────────────────────────
  var panelEl = document.getElementById("suggest-panel");
  var listEl = document.getElementById("suggest-list");
  var countEl = document.getElementById("suggest-count");
  var emptyEl = document.getElementById("suggest-empty");
  var moreEl = document.getElementById("suggest-more");
  var msgEl = document.getElementById("suggest-message");
  var thresholdEl = document.getElementById("suggest-threshold");
  var allBtnEl = document.getElementById("suggest-confirm-all");
  if (!panelEl || !listEl) return;

  var items = [];
  var total = 0; // 服务端的待确认总数；一次只拉 200 条，多出来的要让人知道还有
  var tree = null;
  var chosen = {}; // id → 人在下拉里改过的 taskId（重绘时保留）
  var busy = false;

  // 同计时页的 postCore：从不 throw，失败时 detail 原样透传。
  async function post(url, body) {
    var init = { method: "POST" };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    var res;
    try { res = await fetch(url, init); } catch (err) {
      return { ok: false, message: "网络请求失败：" + ((err && err.message) || String(err)) };
    }
    var payload = await res.json().catch(function () { return null; });
    if (res.ok) return { ok: true, data: payload };
    return { ok: false, message: (payload && payload.detail) ? payload.detail : "请求失败（HTTP " + res.status + "）" };
  }

  function showMessage(text, isError) {
    msgEl.textContent = text;
    msgEl.hidden = !text;
    msgEl.classList.toggle("is-error", Boolean(isError));
  }

  // 输入框按百分数（人读 80 比读 0.8 顺），接口的 confidence 是 0–1
  function threshold() {
    var v = Number(thresholdEl.value);
    return Number.isFinite(v) && thresholdEl.value !== "" ? Math.min(Math.max(v, 0), 100) / 100 : 0.8;
  }

  function taskSelect(item) {
    var sel = document.createElement("select");
    sel.className = "field suggest-task";
    sel.setAttribute("aria-label", "确认到哪个任务");
    sel.appendChild(new Option("选择任务…", ""));
    var zones = {};
    ((tree && tree.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
    ((tree && tree.projects) || []).forEach(function (p) {
      var tasks = p.tasks || [];
      if (!tasks.length) return;
      var group = document.createElement("optgroup");
      group.label = [zones[p.zoneId], p.name].filter(Boolean).join(" / ");
      tasks.forEach(function (t) { group.appendChild(new Option(t.name, t.id)); });
      sel.appendChild(group);
    });
    var want = chosen[item.id] !== undefined ? chosen[item.id] : (item.suggestion && item.suggestion.taskId) || "";
    sel.value = want;
    if (sel.value !== want) sel.value = ""; // 建议的任务已不在树里：让人重挑
    sel.addEventListener("change", function () { chosen[item.id] = sel.value; syncButtons(); });
    return sel;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function renderItem(item) {
    var li = el("li", "suggest-item" + (item.idle ? " is-idle" : ""));
    li.dataset.id = item.id;
    var head = el("div", "suggest-head");
    head.appendChild(el("span", "suggest-range mono", formatRange(item.startAt, item.endAt)));
    if (item.idle) head.appendChild(el("span", "suggest-badge", "无操作·可能在阅读"));
    head.appendChild(el("span", "suggest-dur mono", formatMinutes(item.durationSeconds)));
    li.appendChild(head);
    li.appendChild(el("p", "suggest-what", item.app + (item.title ? " · " + item.title : "")));

    var s = item.suggestion || {};
    var path = taskPath(tree, s.taskId);
    var hint = s.taskId
      ? "建议：" + (path || "（任务已不存在）") + " · 把握 " + Math.round((s.confidence || 0) * 100) + "%"
      : "没有建议，请选任务";
    li.appendChild(el("p", "suggest-hint", hint));

    li.appendChild(taskSelect(item));
    var row = el("div", "row");
    var ok = el("button", "btn btn-fact suggest-confirm", "确认");
    ok.type = "button";
    var no = el("button", "btn btn-ghost suggest-dismiss", "忽略");
    no.type = "button";
    ok.addEventListener("click", function () { act([item], "confirm"); });
    no.addEventListener("click", function () { act([item], "dismiss"); });
    row.appendChild(ok);
    row.appendChild(no);
    li.appendChild(row);
    return li;
  }

  function selectedTask(id) {
    var sel = listEl.querySelector('li[data-id="' + CSS.escape(id) + '"] select');
    return sel ? sel.value : "";
  }

  function syncButtons() {
    var n = eligible(items, threshold(), tree).length;
    allBtnEl.textContent = "全部确认（把握 ≥ " + Math.round(threshold() * 100) + "%）" + (n ? " · " + n : "");
    allBtnEl.disabled = busy || n === 0;
    listEl.querySelectorAll("li").forEach(function (li) {
      li.querySelector(".suggest-confirm").disabled = busy || !li.querySelector("select").value;
      li.querySelector(".suggest-dismiss").disabled = busy;
    });
  }

  function render() {
    listEl.textContent = "";
    items.forEach(function (it) { listEl.appendChild(renderItem(it)); });
    countEl.textContent = items.length ? String(items.length) : "";
    emptyEl.hidden = items.length > 0;
    var more = total - items.length;
    moreEl.textContent = more > 0 ? "还有 " + more + " 条更早的待确认，先处理上面的" : "";
    moreEl.hidden = more <= 0;
    syncButtons();
  }

  var loadSeq = 0; // 只认最后一次 load 的结果：早发晚到的旧快照会把刚确认的条目又放回来
  async function load() {
    var mine = ++loadSeq;
    var res;
    try {
      res = await fetch(API + "?status=pending&limit=200");
    } catch (err) {
      return; // 网络抖一下不值得在页面上报错，下次可见时再拉
    }
    if (mine !== loadSeq) return;
    if (res.status === 404) { panelEl.hidden = true; return; } // 后端早于 v2.2：整块不出现
    if (!res.ok) { panelEl.hidden = false; showMessage("待确认列表加载失败（HTTP " + res.status + "）", true); return; }
    var body = await res.json().catch(function () { return null; });
    // 每次都重拉树：人可能刚在「任务」页加了任务，要能马上选到
    try {
      var t = await fetch(BASE + "api/core/views/tree");
      if (t.ok) tree = await t.json();
    } catch (err) { /* 拉不到就沿用上一份；从没拉到过则下拉为空，确认按钮保持禁用 */ }
    if (mine !== loadSeq) return;
    items = (body && body.items) || [];
    total = (body && typeof body.total === "number") ? body.total : items.length;
    panelEl.hidden = false;
    render();
  }

  // 逐条发，失败的留在列表里并显示后端 detail 原文（同补登规则 3）。
  async function act(targets, action, bulk) {
    busy = true;
    syncButtons();
    showMessage("", false);
    var done = 0, errors = [];
    try {
      for (var i = 0; i < targets.length; i++) {
        var it = targets[i];
        var suggested = (it.suggestion || {}).taskId;
        var body = action === "confirm" ? { taskId: selectedTask(it.id) || suggested } : undefined;
        var r = await post(API + "/" + encodeURIComponent(it.id) + "/" + action, body);
        if (r.ok) {
          done += 1;
          delete chosen[it.id];
        } else {
          errors.push(r.message);
        }
      }
    } finally {
      busy = false; // 按钮先保持禁用，等列表重拉完再按新列表放开（旧列表上再点就是重复提交）
    }
    await load();
    syncButtons(); // load() 失败时不重绘，按钮不能停在禁用
    if (errors.length) showMessage(errors[0] + (errors.length > 1 ? "（另有 " + (errors.length - 1) + " 条失败）" : ""), true);
    else if (bulk) showMessage("已确认 " + done + " 条。", false);
  }

  thresholdEl.addEventListener("input", syncButtons);
  allBtnEl.addEventListener("click", function () { act(eligible(items, threshold(), tree), "confirm", true); });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && !busy) load();
  });

  load();
})();
