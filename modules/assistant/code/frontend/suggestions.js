/**
 * suggestions.js —— 「待确认建议」面板（nexus-core 契约 v2.2「活动建议」，v2.5 的 idle 段）
 *
 * 桌面检测程序（ai-detector）看见「11:05–12:07 在 VS Code 里」就上传一条建议，但**建议不是事实**：
 * 它不进任何统计，直到人在这里点「确认」。确认 = 后端经补登同一条路径写一条 session.completed。
 * （2026-09-30 从计时页搬来「AI助理」页；计时页只留一个「N 条待确认 → AI助理」的链接。）
 *
 * - idle 段（v2.5：前台没换、但无操作）单独标「无操作·可能在阅读」，**永远不进「全部确认」**——
 *   契约说由人决定算不算；它的把握本来就 ≤ 0.3。
 * - AI 匹配（v2.7，仓主 2026-10-02「最简单的匹配 + y/n」）：「让 AI 匹配」借 chat.js 发一轮固定的话，助理经 MCP 给
 *   没有任务的条目配任务（classifier=assistant）。这样的条目只出一行「AI 建议：路径 · 把握 · 理由」和两个按钮：
 *   「是 ✓」= 既有的 confirm（带那个任务）；「否 ✗」= unmatch（清掉、记住，条目留在待确认里，换成手动挑任务）。
 *   聊天后端没装 / 没配模型时按钮不出现。reason 是模型写的，同样只进 textContent。
 * - 按窗口分组（仓主 2026-10-03：检测程序按标签页分段后，一个窗口的零碎几秒都成了一条，挑不过来）：
 *   同一 (app, title, idle) 的几段并成一行，一个下拉、一次确认 / 忽略 / 是 / 否，逐段发。有建议的段指向不同任务
 *   →「建议不一致」让人挑。勾「以后这个窗口都记到这个任务」→ 确认后经 rules.js 往分类规则最前面加一条精确匹配的规则。
 * - 提议新任务（nexus-core v2.8，仓主 2026-10-03「AI 能自动加新任务」，草稿 + 一键确认）：助理找不到合适的现成任务时
 *   可以提议「新建任务：项目 / 名称」。行上名字可改；「是 ✓」= confirm {name}（后端建任务，同一提议只建一个），
 *   组里其余段用建好的 taskId；「否 ✗」= unmatch {proposalId}。下拉仍可改选现成任务。**「全部确认」永远不建任务**。
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

  // 助理配的、任务还在树里的 → 出「是 / 否」；任务已删的退回手动挑。
  function aiMatch(item, tree) {
    var s = item.suggestion || {};
    return s.classifier === "assistant" && taskPath(tree, s.taskId) !== null;
  }

  // 「分区 / 项目」；查不到（项目已删）返回 null。
  function projectPath(tree, projectId) {
    var zones = {};
    ((tree && tree.zones) || []).forEach(function (z) { zones[z.id] = z.name; });
    var p = ((tree && tree.projects) || []).filter(function (x) { return x.id === projectId; })[0];
    return p ? [zones[p.zoneId], p.name].filter(Boolean).join(" / ") : null;
  }

  // 助理提议的新任务（v2.8），项目还在树里才算；项目没了退回手动挑。
  function newTaskOf(s, tree) {
    var nt = s && s.newTask;
    return s && s.classifier === "assistant" && nt && projectPath(tree, nt.projectId) !== null ? nt : null;
  }

  // 一条建议指向什么：任务 id，或「new:提议 id」，或 null（没有建议）
  function sugKey(s) { return (s && (s.taskId || (s.newTask && "new:" + s.newTask.proposalId))) || null; }

  // 按窗口分组（仓主 2026-10-03「一个窗口对应一个任务」）：同一 (app, title) 的几段并成一行，一次挑任务、一次确认。
  // idle 段单独成组（键里带 idle）：它们照旧不进「全部确认」。组按第一段出现的顺序排。
  function groupItems(list) {
    var out = [], by = {};
    (list || []).forEach(function (it) {
      var k = JSON.stringify([it.app, it.title, Boolean(it.idle)]);
      if (!by[k]) out.push(by[k] = { key: k, app: it.app, title: it.title, idle: Boolean(it.idle), items: [] });
      by[k].items.push(it);
    });
    return out;
  }

  // 组的建议：**每一段**都建议同一个任务才算（把握取最小）。有的段指向别的任务、有的没建议（比如「否」只成功了一半，
  // 那几段的建议已清掉）→ mixed，让人挑——否则「是」会把已否掉的那段也记到同一个任务上。
  // v2.8：每一段都提议同一个新任务也算（taskId 为 null，带 newTask）。
  function groupSuggestion(g) {
    var withTask = g.items.filter(function (it) { return sugKey(it.suggestion); });
    if (!withTask.length) return { taskId: null };
    var s0 = withTask[0].suggestion, k0 = sugKey(s0);
    if (withTask.length < g.items.length || withTask.some(function (it) { return sugKey(it.suggestion) !== k0; })) {
      return { taskId: null, mixed: true };
    }
    var out = {
      taskId: s0.taskId || null, reason: s0.reason,
      confidence: Math.min.apply(null, withTask.map(function (it) {
        return typeof it.suggestion.confidence === "number" ? it.suggestion.confidence : 0;
      })),
      classifier: withTask.every(function (it) { return it.suggestion.classifier === s0.classifier; }) ? s0.classifier : null,
    };
    if (!s0.taskId) out.newTask = s0.newTask;
    return out;
  }

  // 「全部确认」按组：组的建议当一条看，同 eligible 的条件（提议新任务的组 taskId 为 null，天然不进：建任务必须人点）
  function eligibleGroups(groups, threshold, tree) {
    return groups.filter(function (g) {
      return eligible([{ idle: g.idle, suggestion: groupSuggestion(g) }], threshold, tree).length > 0;
    });
  }

  // 「以后这个窗口都记到这个任务」→ 一条只认这个窗口的规则：程序名、标题都整串匹配（规则不分大小写）。
  // 规则匹配的是「隐私处理后、换代号前」的标题（detector.rules.v1「一」、ai-detector segment.go）：
  // [IP]、[主机] 这类占位符上传的与匹配的一样，转义后照样认；「窗口名3」「路径2」这种代号不是，加了也永远不中。
  var PSEUDONYM = /^(窗口名|路径)\d+$/; // ai-detector privacy.go 的 token()
  function reEscape(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function windowRule(app, title, taskId) {
    if (PSEUDONYM.test(title || "")) return { why: "标题是代号，规则认的是换代号之前的标题，没法从这里加" };
    var r = { app: "^" + reEscape(app || "") + "$", title: "^" + reEscape(title || "") + "$", taskId: taskId,
      confidence: 0.9, note: ("待确认里勾的：" + app + (title ? " · " + title : "")).slice(0, 120), enabled: true };
    if (r.app.length > 200 || r.title.length > 200) return { why: "标题太长，规则放不下" };
    return { rule: r };
  }

  window.assistantSuggestions = { formatRange: formatRange, taskPath: taskPath, eligible: eligible, aiMatch: aiMatch,
    groupItems: groupItems, groupSuggestion: groupSuggestion, eligibleGroups: eligibleGroups, windowRule: windowRule,
    projectPath: projectPath, newTaskOf: newTaskOf };

  var AI_PROMPT = "请匹配待确认的活动：读取待确认的活动记录和我的项目、任务，" +
    "给能判断的每条活动配一个最合适的任务；现成任务都不合适、又明显属于某个项目的，可以提议一个新任务；拿不准的跳过。";

  // ── DOM ─────────────────────────────────────────────────────────────
  var panelEl = document.getElementById("suggest-panel");
  var listEl = document.getElementById("suggest-list");
  var countEl = document.getElementById("suggest-count");
  var emptyEl = document.getElementById("suggest-empty");
  var moreEl = document.getElementById("suggest-more");
  var msgEl = document.getElementById("suggest-message");
  var thresholdEl = document.getElementById("suggest-threshold");
  var allBtnEl = document.getElementById("suggest-confirm-all");
  var aiBtnEl = document.getElementById("suggest-ai-match");
  if (!panelEl || !listEl) return;

  var items = [];
  var groups = []; // items 按窗口分的组（render 时重算）
  var total = 0; // 服务端的待确认总数；一次只拉 200 条，多出来的要让人知道还有
  var tree = null;
  var chosen = {}; // 组键 → 人在下拉里改过的 taskId（重绘时保留）
  var remember = {}; // 组键 → 勾了「以后这个窗口都记到这个任务」（重绘时保留）
  var names = {}; // 下拉选择键 → 人改过的新任务名字（v2.8，重绘时保留）
  var busy = false;
  var chat = { configured: false, generating: false }; // chat.js 报的状态（没装聊天后端就一直是这个）
  var aiAsked = false; // 这一轮是「让 AI 匹配」发起的：答完后报一句结果

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

  // 一组此刻显示的样子。下拉的选择按「组 + 组此刻的建议」记：重拉后建议变了（比如助理刚配了任务），旧的选择自然作废，
  // 行上的按钮与「全部确认」发的都是 taskId（= 下拉里看得到的那个任务；不在当前任务树里的一律当没选）。
  function view(g) {
    var s = groupSuggestion(g), ai = aiMatch({ suggestion: s }, tree), nt = newTaskOf(s, tree);
    var ck = JSON.stringify([g.key, s.taskId, Boolean(s.mixed), ai, nt ? nt.proposalId : null]);
    if (chosen[ck] && taskPath(tree, chosen[ck]) === null) delete chosen[ck]; // 选过的任务刷新后被删了
    var t = ai || chosen[ck] === undefined ? s.taskId : chosen[ck];
    return { s: s, ai: ai, nt: nt, ck: ck, taskId: t && taskPath(tree, t) !== null ? t : "" };
  }

  function taskSelect(v, placeholder) {
    var sel = document.createElement("select");
    sel.className = "field suggest-task";
    sel.setAttribute("aria-label", "确认到哪个任务");
    sel.appendChild(new Option(placeholder || "选择任务…", ""));
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
    var want = v.taskId || "";
    sel.value = want;
    if (sel.value !== want) sel.value = ""; // 建议的任务已不在树里：让人重挑
    sel.addEventListener("change", function () { chosen[v.ck] = sel.value; syncButtons(); });
    return sel;
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function label(g) { return g.app + (g.title ? " · " + g.title : ""); }

  // 一行 = 一个窗口（同 app、同 title 的几段）。只有一段时与原来的一条一样。
  function renderGroup(g) {
    var n = g.items.length;
    var li = el("li", "suggest-item" + (g.idle ? " is-idle" : ""));
    li.dataset.id = g.items[0].id;
    li.dataset.count = String(n);
    var start = g.items[0].startAt, end = g.items[0].endAt, secs = 0;
    g.items.forEach(function (it) {
      if (new Date(it.startAt) < new Date(start)) start = it.startAt;
      if (new Date(it.endAt) > new Date(end)) end = it.endAt;
      secs += it.durationSeconds || 0;
    });
    var head = el("div", "suggest-head");
    head.appendChild(el("span", "suggest-range mono", formatRange(start, end)));
    if (g.idle) head.appendChild(el("span", "suggest-badge", "无操作·可能在阅读"));
    head.appendChild(el("span", "suggest-dur mono", (n > 1 ? n + " 段 · 共 " : "") + formatMinutes(secs)));
    li.appendChild(head);
    li.appendChild(el("p", "suggest-what", label(g)));
    if (n > 1) {
      var det = el("details", "suggest-segs");
      det.appendChild(el("summary", null, "看这 " + n + " 段"));
      g.items.forEach(function (it) {
        det.appendChild(el("p", "suggest-seg mono", formatRange(it.startAt, it.endAt) + " · " + formatMinutes(it.durationSeconds)));
      });
      li.appendChild(det);
    }

    var v = view(g), s = v.s, ai = v.ai, nt = v.nt;
    var path = taskPath(tree, s.taskId);
    var pct = " · 把握 " + Math.round((s.confidence || 0) * 100) + "%";
    var sel = null, nameEl = null;
    var row = el("div", "row");
    var ok = el("button", "btn btn-fact suggest-confirm", ai || nt ? "是 ✓" : "确认");
    ok.type = "button";
    var no = el("button", "btn btn-ghost " + (ai || nt ? "suggest-no" : "suggest-dismiss"), ai || nt ? "否 ✗" : "忽略");
    no.type = "button";
    if (ai) {
      // 助理配的：一行建议 + 是 / 否，不出下拉（否了之后重绘成手动挑）
      li.classList.add("is-ai");
      li.appendChild(el("p", "suggest-hint", "AI 建议：" + path + pct + (s.reason ? " · " + s.reason : "")));
      ok.setAttribute("aria-label", "是，记到 " + path);
      no.setAttribute("aria-label", "否，不是 " + path);
    } else if (nt) {
      // 助理提议的新任务：名字可改，「是」才建；也可以在下拉里改选现成任务（那就是普通确认，不建）
      li.classList.add("is-ai", "is-new");
      li.appendChild(el("p", "suggest-hint", "AI 建议新建任务" + pct + (s.reason ? " · " + s.reason : "")));
      var pill = el("label", "suggest-newtask");
      pill.appendChild(el("span", "suggest-newtask-path", "新建任务：" + projectPath(tree, nt.projectId) + " /"));
      nameEl = el("input", "field suggest-newname");
      nameEl.maxLength = 64;
      nameEl.value = names[v.ck] !== undefined ? names[v.ck] : nt.name;
      nameEl.setAttribute("aria-label", "新任务的名字（可改）");
      nameEl.addEventListener("input", function () { names[v.ck] = nameEl.value; syncButtons(); });
      pill.appendChild(nameEl);
      li.appendChild(pill);
      li.appendChild(sel = taskSelect(v, "不选现成任务（新建上面这个）"));
      ok.setAttribute("aria-label", "是，新建任务并记进去");
      no.setAttribute("aria-label", "否，不建这个任务");
    } else {
      var rejected = g.items.some(function (it) { return (it.rejectedTaskIds || []).length; });
      var hint = s.taskId ? "建议：" + (path || "（任务已不存在）") + pct
        : s.mixed ? "建议不一致，请自己选任务"
        : rejected ? "AI 的建议已否掉，请自己选任务，或忽略" : "没有建议，请选任务";
      li.appendChild(el("p", "suggest-hint", hint));
      li.appendChild(sel = taskSelect(v));
    }
    // 勾上 = 确认成功后加一条规则，检测程序以后自动给这个窗口建议同一个任务。rules.js 没加载就不出现。
    var cb = null;
    if (window.assistantRules && window.assistantRules.prepend) {
      var why = windowRule(g.app, g.title, "").why;
      var lab = el("label", "suggest-rule");
      cb = el("input", "suggest-rule-cb");
      cb.type = "checkbox";
      cb.disabled = Boolean(why);
      cb.checked = !why && Boolean(remember[g.key]);
      cb.addEventListener("change", function () { remember[g.key] = cb.checked; });
      lab.appendChild(cb);
      lab.appendChild(document.createTextNode(" 以后这个窗口都记到这个任务"));
      if (why) lab.appendChild(el("span", "suggest-rule-why", "（" + why + "）"));
      li.appendChild(lab);
    }
    ok.addEventListener("click", function () {
      var pick = sel ? sel.value : s.taskId;
      act([{ g: g, ck: v.ck, taskId: pick, remember: Boolean(cb && cb.checked),
        newName: nt && !pick ? nameEl.value.trim() : "" }], "confirm");
    });
    no.addEventListener("click", function () {
      act([{ g: g, ck: v.ck, taskId: s.taskId, proposalId: nt ? nt.proposalId : "" }], ai || nt ? "unmatch" : "dismiss");
    });
    row.appendChild(ok);
    row.appendChild(no);
    li.appendChild(row);
    return li;
  }

  // 下拉被人清空的组（看得到的任务是空）不进「全部确认」
  function bulkTargets() {
    return eligibleGroups(groups, threshold(), tree).filter(function (g) { return view(g).taskId; });
  }

  function syncButtons() {
    var n = bulkTargets().reduce(function (sum, g) { return sum + g.items.length; }, 0);
    allBtnEl.textContent = "全部确认（把握 ≥ " + Math.round(threshold() * 100) + "%）" + (n ? " · " + n : "");
    allBtnEl.disabled = busy || n === 0;
    listEl.querySelectorAll("li").forEach(function (li) {
      var sel = li.querySelector("select"); // 助理配的条目没有下拉：「是」总可点
      var name = li.querySelector(".suggest-newname"); // 提议新任务：没选现成任务时名字不能空
      li.querySelector(".suggest-confirm").disabled = busy || Boolean(sel && !sel.value && !(name && name.value.trim()));
      li.querySelector(".suggest-dismiss, .suggest-no").disabled = busy;
    });
    if (aiBtnEl) {
      aiBtnEl.hidden = !chat.configured; // 没装聊天后端 / 没配模型：不出现
      // aiAsked 在点击那一下就置上：建会话还没完（generating 还没变）时连点不会发出第二轮
      aiBtnEl.disabled = busy || chat.generating || aiAsked || items.length === 0;
      aiBtnEl.textContent = aiAsked ? "AI 正在匹配…" : "让 AI 匹配";
    }
  }

  function render() {
    listEl.textContent = "";
    groups = groupItems(items);
    groups.forEach(function (g) { listEl.appendChild(renderGroup(g)); });
    countEl.textContent = items.length ? String(items.length) : "";
    emptyEl.hidden = items.length > 0;
    var more = total - items.length;
    moreEl.textContent = more > 0 ? "还有 " + more + " 条更早的待确认，先处理上面的" : "";
    moreEl.hidden = more <= 0;
    syncButtons();
  }

  var loadSeq = 0; // 只认最后一次 load 的结果：早发晚到的旧快照会把刚确认的条目又放回来
  // 返回 false = 没拉到新列表（页面上还是旧的）；被更新的一次 load 顶掉的算 true（那一次会重绘）
  async function load() {
    var mine = ++loadSeq;
    var res;
    try {
      res = await fetch(API + "?status=pending&limit=200");
    } catch (err) {
      return false; // 网络抖一下不值得在页面上报错，下次可见时再拉
    }
    if (mine !== loadSeq) return true;
    if (res.status === 404) { panelEl.hidden = true; return true; } // 后端早于 v2.2：整块不出现
    if (!res.ok) { panelEl.hidden = false; showMessage("待确认列表加载失败（HTTP " + res.status + "）", true); return false; }
    var body = await res.json().catch(function () { return null; });
    // 每次都重拉树：人可能刚在「任务」页加了任务，要能马上选到
    try {
      var t = await fetch(BASE + "api/core/views/tree");
      if (t.ok) tree = await t.json();
    } catch (err) { /* 拉不到就沿用上一份；从没拉到过则下拉为空，确认按钮保持禁用 */ }
    if (mine !== loadSeq) return true;
    items = (body && body.items) || [];
    total = (body && typeof body.total === "number") ? body.total : items.length;
    panelEl.hidden = false;
    render();
    return true;
  }

  // jobs = [{g, ck, taskId, remember, newName?, proposalId?}]：一组里逐段发，失败的留在列表里，按组报后端 detail 原文（同补登规则 3）；
  // 同组已成功的段就是确认了，不回滚。unmatch 只发带着那个建议的段。
  // busy 从第一个请求一直到列表重拉完：期间阈值、聊天状态等触发的 syncButtons 不会把旧列表上的按钮放开。
  async function act(jobs, action, bulk) {
    if (busy) return;
    busy = true;
    syncButtons();
    showMessage("", false);
    var done = 0, errors = [], notes = [], handled = {};
    try {
      for (var j = 0; j < jobs.length; j++) {
        var g = jobs[j].g, taskId = jobs[j].taskId, pid = jobs[j].proposalId, newName = jobs[j].newName;
        var targets = action !== "unmatch" ? g.items : g.items.filter(function (it) {
          return pid ? sugKey(it.suggestion) === "new:" + pid : (it.suggestion || {}).taskId === taskId;
        });
        var ok = 0, firstErr = "", created = ""; // created：新建任务那一下后端回的 taskId，其余段直接记到它
        for (var i = 0; i < targets.length; i++) {
          // 人对这段否掉过的任务，确认时绝不发（组里别的段可能还建议着它）
          if (action === "confirm" && (targets[i].rejectedTaskIds || []).indexOf(taskId) >= 0) {
            if (!firstErr) firstErr = "有的段你已经否掉过这个任务，请换一个";
            continue;
          }
          // unmatch 带上页面上看到的任务 / 提议：助理刚换过的话后端 409，不会否错
          var body = action === "dismiss" ? undefined
            : action === "unmatch" ? (pid ? { proposalId: pid } : { taskId: taskId })
            : newName && !created ? { name: newName } : { taskId: created || taskId };
          var r = await post(API + "/" + encodeURIComponent(targets[i].id) + "/" + action, body);
          if (r.ok) {
            ok += 1;
            handled[targets[i].id] = action === "unmatch" ? (pid ? "new:" + pid : taskId) : true;
            if (newName && !created) created = (r.data && r.data.taskId) || "";
          }
          else if (!firstErr) firstErr = r.message;
        }
        done += ok;
        if (firstErr) {
          errors.push(label(g) + "：" + (targets.length > 1 ? (targets.length - ok) + " / " + targets.length + " 段失败，" : "") + firstErr);
        } else {
          delete chosen[jobs[j].ck];
        }
        if (created) notes.push("已新建任务「" + newName + "」，" + ok + " 段记进去了。");
        var rule = windowRule(g.app, g.title, created || taskId).rule;
        if (action === "confirm" && jobs[j].remember && ok > 0 && rule) {
          var rr = await window.assistantRules.prepend(rule);
          if (!rr.ok) errors.push(label(g) + "：已确认，但规则没加上：" + rr.detail);
          else {
            delete remember[g.key];
            notes.push(rr.skipped ? "这个窗口的规则已经有了。" : "已加规则：以后这个窗口建议记到这个任务（检测程序下一轮起）。");
          }
        }
      }
      // 按钮保持禁用，等列表重拉完再按新列表放开（旧列表上再点就是重复提交）
      if (!(await load())) {
        // 没拉到新列表：确认 / 忽略成功的段已不在待确认里，从旧列表拿掉；否掉的段仍待确认，
        // 在本地清掉建议、记进 rejectedTaskIds（同服务端），留着手动挑。下次可见时再拉全。
        var before = items.length;
        items = items.filter(function (it) { return handled[it.id] !== true; }).map(function (it) {
          var no = handled[it.id];
          if (!no) return it;
          if (no.indexOf("new:") === 0) { // 否掉的是提议的新任务：去掉提议（后端记进 rejectedProposalIds，不回出）
            var sg = Object.assign({}, it.suggestion, { confidence: 0, reason: "" });
            delete sg.newTask;
            return Object.assign({}, it, { suggestion: sg });
          }
          return Object.assign({}, it, {
            suggestion: Object.assign({}, it.suggestion, { taskId: null, confidence: 0, reason: "" }),
            rejectedTaskIds: (it.rejectedTaskIds || []).concat([no]),
          });
        });
        // 拿掉的段也不再算进服务端总数（否掉的仍待确认，不减）；「还有 N 条更早的」才不会多报
        total = Math.max(total - (before - items.length), items.length);
        render();
      }
    } finally {
      busy = false;
    }
    syncButtons();
    if (errors.length) showMessage(errors[0] + (errors.length > 1 ? "（另有 " + (errors.length - 1) + " 处失败）" : ""), true);
    else if (bulk || notes.length) showMessage((bulk ? "已确认 " + done + " 条。" : "") + notes.join(""), false);
  }

  // ── 让 AI 匹配：借「AI 对话」发一轮；答完（assistant:turn-done）重拉列表 ──
  if (aiBtnEl) {
    aiBtnEl.addEventListener("click", function () {
      if (!window.assistantChat || !window.assistantChat.ask || !window.assistantChat.ask(AI_PROMPT)) return;
      aiAsked = true;
      showMessage("AI 正在看活动记录和你的项目，进度见上面的「AI 对话」…", false);
      syncButtons();
    });
  }
  document.addEventListener("assistant:chat-state", function (e) {
    chat = e.detail || chat;
    syncButtons();
  });
  // 一轮答完：助理可能配了任务（不管是点按钮还是用户自己在对话里说的）。正在确认时不抢，确认完自己会重拉。
  document.addEventListener("assistant:turn-done", function () {
    var asked = aiAsked;
    aiAsked = false;
    if (busy) return;
    load().then(function () {
      syncButtons();
      if (!asked) return;
      var n = items.filter(function (it) { return aiMatch(it, tree) || newTaskOf(it.suggestion, tree); }).length;
      showMessage(n ? "AI 给 " + n + " 条配了任务，逐条点「是」或「否」。"
        : "AI 这次没配上任何一条，原因见上面的对话。", false);
    });
  });

  thresholdEl.addEventListener("input", syncButtons);
  allBtnEl.addEventListener("click", function () {
    act(bulkTargets().map(function (g) {
      var v = view(g);
      return { g: g, ck: v.ck, taskId: v.taskId, remember: Boolean(remember[g.key]) };
    }), "confirm", true);
  });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && !busy) load();
  });

  load();
})();
