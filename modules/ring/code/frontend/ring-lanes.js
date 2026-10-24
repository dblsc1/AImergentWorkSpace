/**
 * ring-lanes.js —— 计时页的「泳道」主视图（nexus-core v2.4 `views.lanes.v1`，ring 契约同名条）
 *
 * 圆环下方整宽、默认展开：人一条线在最上，下面每个代理运行一条线，连线表示人回话 / 人在看。
 * 2026-10-03 起画成卡片（ring 契约同日条）：人一张卡钉在最前（卡头写此刻在电脑前 / 离开 / 不在线），
 * 代理按「在等你的在前 → 视窗内活跃秒数 → 最近转入」排，前 5 张展开，其余收进「还有 N 个」。
 * 缺省「最近 3 小时」实时窗口（读今天，跨零点读昨天 + 今天，本地按响应的 now 裁），可切「今天」。
 * 画图交给共享的 <前缀>__cockpit/lanes.js（顶栏芯片的精简预览用的是同一份，配色、段的推法一致）。
 *
 * - 泳道偏好（nexus-core v2.22，ring 契约同名条）：每张代理卡有 ⋯ 菜单（置顶 / 不再显示 / 上移 / 下移）、在跑的卡能长按拖动换位，
 *   区尾有一个折叠菜单「已折叠 n 个」（v2.25：出错 / 空闲 / 已结束 / 手动隐藏，隐藏的可恢复，折叠的可置顶显示）。偏好在服务端（PUT api/core/lanes/prefs/…），排序也是服务端算的（agents[].rank）；
 *   这里先乐观地画（lanes.js 的 optimistic()），请求失败就退回原来那份并说一声。菜单开着 / 正在拖时轮询只攒着、收尾再画。
 *   后端早于 v2.22（响应没有 hiddenAgents）就没有这些入口。
 * - 约 15 秒轮询，页面不可见时不拉；404（后端早于 v2.4）或共享渲染件加载失败 → 整块不出现。
 * - 画的是标记，不是时长：不出现任何合计。label/agent/detail/app/title 一律 textContent（lanes.js 保证）。
 * - 面板标题的红绿灯读的是同一份响应里在跑运行的当前相位（与 views/current 的 agents[].phase 同源）。
 * - 自动跟踪（nexus-core v2.14）：human.auto → 人那张卡上的「自动 · 项目 / 任务」+ 走秒（lanes.js 画）；
 *   human.needsChoice → 泳道最上面一张「你在 X，记到哪？」（ring-choice.js 做卡，经 opts.lead 交给 lanes.js 摆）。
 *   卡出现后一直留到人答 / 说这次不选——人得切到浏览器来答，期间服务端换了别的窗口也不换卡；手动开始计时就收。
 * - 让 AI 认窗口（nexus-core v2.15）：human.aiThinking → 人那张卡上一行小字「AI 正在认这个窗口…」（这期间不出选卡）；
 *   human.auto.source 是 "ai" → 「自动 · …（AI 认的）」后面一个「不对」：POST activity/choice/reject {key}
 *   （服务端撤掉 AI 写的规则与临时选择），随即把那张「你在 X，记到哪？」摆出来让人自己选。都由 lanes.js 画，这里只接动作。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/core/views/lanes";
  var PREFS = BASE + "api/core/lanes/prefs";
  var POLL_MS = 15000;
  var NOTICE_MS = 4000;
  var HOUR = 3600000, DAY = 86400000;

  var panel = document.getElementById("lanes-panel");
  var view = document.getElementById("lanes-view");
  var stateEl = document.getElementById("lanes-state");
  if (!panel || !view) return;
  var rangeBtns = panel.querySelectorAll("[data-hours]");

  var hours = 3;       // 0 = 今天
  var last = null;     // 最近一次成功的响应
  var lastQ = "";      // 它的查询串（?date= / ?from=&to=）
  var shownQ = "";     // 此刻**画在页面上**的那份数据的查询串（draw() 画之前记下；拖拽排位要发的是它，不是最近一次拉到的 lastQ）
  var gone = false;    // 404：后端没有这个端点，不再拉
  var seq = 0;
  var lead = null;     // 「你在 X，记到哪？」那张卡（没有为 null）
  var notice = "";     // 偏好动作失败时在标题旁说一声（NOTICE_MS 后消失）
  var dirty = false;   // 菜单 / 拖动期间攒下的轮询结果，收尾时再画

  function closeLead() {
    lead = null;
    if (last && last.human) last.human.needsChoice = null;   // 手里这份响应还写着它：别马上又问一遍
    draw();
    load();
    window.dispatchEvent(new Event("honeycomb:timer-changed"));   // 顶栏芯片上的小点跟着收
  }

  // 「不对」：撤掉 AI 认的，换人来选。失败（网络 / 已经撤过）就重拉一次，按服务端此刻的说法画。
  async function autoWrong(auto) {
    var r = window.RingChoice ? await window.RingChoice.reject(auto.key) : { ok: false };
    if (r.ok && last && last.human) {
      last.human.auto = null;
      lead = window.RingChoice.card({ key: auto.key, app: auto.app, title: auto.title }, closeLead);
      draw();
    }
    load();
  }

  // 偏好动作：先画乐观的，再发请求；失败退回原来那份。成功后重拉一次拿服务端算的 rank。
  async function pref(op, method, path, body) {
    var before = last, guess = window.HoneycombLanes.optimistic(last, op);
    last = guess;
    seq += 1;                                                  // 还在路上的旧轮询结果作废，别把乐观的画面冲回去
    draw();
    var ok = false;
    try {
      var res = await fetch(PREFS + path, {
        method: method, credentials: "same-origin",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined
      });
      ok = res.ok;
    } catch (e) { ok = false; }
    if (!ok) {
      if (last === guess) last = before;                       // 期间没被新结果换掉才退回
      notice = "没有成功，已恢复";
      setTimeout(function () { notice = ""; draw(); }, NOTICE_MS);
      draw();
    }
    load();
  }
  var prefs = {
    onPin: function (r, on) { pref({ type: "pin", agent: r.agent, label: r.label, unverified: r.unverified, on: on }, "PUT", "/agent", { agent: r.agent || "", label: r.label || "", pinned: on }); },
    onHide: function (r) { pref({ type: "hide", agent: r.agent, label: r.label, unverified: r.unverified }, "PUT", "/agent", { agent: r.agent || "", label: r.label || "", hidden: true, unverified: !!r.unverified }); },
    onRestore: function (h) { pref({ type: "restore", agent: h.agent, label: h.label, unverified: h.unverified }, "PUT", "/agent", { agent: h.agent, label: h.label || "", hidden: false, unverified: !!h.unverified }); },
    onMove: function (r, index) {
      view.hclReveal = r.runId;
      var win = {};   // 拖拽的下标按页面**已画出**的数据的窗口数（凌晨读「昨天 + 今天」），带上同样的 date / from / to
      new URLSearchParams(shownQ).forEach(function (v, k) { win[k] = v; });
      pref({ type: "move", runId: r.runId, index: index }, "PUT", "/order", Object.assign({ runId: r.runId, index: index }, win));
    }
  };
  view.addEventListener("hcl-idle", function () { if (dirty) { dirty = false; draw(); } });

  // nexus-core v2.23：封顶折叠掉的较早运行按身份汇在 dropped 里；有数就说几段，没有（老后端 / 只是会话超限）退回原话
  function moreHint(d) {
    if (!d.truncated) return "";
    var n = (Array.isArray(d.dropped) ? d.dropped : []).reduce(function (a, x) { return a + (x.runs || 0); }, 0);
    return n ? "较早的 " + n + " 段已折叠" : "还有更多（只列出了最新的一部分）";
  }

  function draw() {
    var L = window.HoneycombLanes;
    if (!last || !L) return;
    if (view.hclBusy) { dirty = true; return; }               // 菜单开着 / 正在拖：别把手里的东西画没了
    var human = last.human || {};
    if (human.running) lead = null;                         // 手动计时永远优先
    else if (!lead && human.needsChoice && window.RingChoice) lead = window.RingChoice.card(human.needsChoice, closeLead);
    shownQ = lastQ;                                          // 下面就要画 last 了：它对应的窗口就是页面上的窗口
    var now = Date.parse(last.now);
    var v0, v1;
    if (hours) {
      v0 = now - hours * HOUR;
      v1 = now + hours * HOUR * 0.03;    // 右边留一点，「现在」那根线和在跑的末端看得见
    } else {
      v1 = Date.parse(last.windowEnd);
      v0 = v1 - DAY;
    }
    var infos = L.render(view, last, {
      viewStart: v0, viewEnd: v1, presence: true, cards: true, top: 5, lead: lead, onAutoWrong: autoWrong,
      prefs: Array.isArray(last.hiddenAgents) ? prefs : null,   // 老后端（早于 v2.22）没有这个键：不给入口
      focusFallback: document.getElementById("lanes-title"),   // 「还有 N 个」重画后没了时焦点落这里
      more: moreHint(last)
    });
    // 标题红绿灯：复用 render 排好的 runInfo（在跑的运行一定与视窗重叠，都在里面），不再逐个排 phases
    var live = { working: 0, waiting: 0, error: 0 };
    infos.forEach(function (k) {
      if (k.r.endAt || k.lost) return;   // 失联的（nexus-core v2.18）不算在干活 / 在等你
      if (k.ph === "working") live.working += 1;
      else if (k.ph === "error") live.error += 1;
      else if (k.ph !== "idle") live.waiting += 1;
    });
    // v2.24：出错的泳道已挪进 inactiveAgents（不画成泳道），但出错仍要看得见：今天里出过错的照样亮「N 个出错」
    var dayStart = Date.parse(last.windowEnd) - DAY;
    (Array.isArray(last.inactiveAgents) ? last.inactiveAgents : []).forEach(function (x) {
      if (x.reason === "error" && Date.parse(x.lastWorkAt) >= dayStart) live.error += 1;
    });
    var bits = [];
    if (live.waiting) bits.push(live.waiting + " 个在等你");
    if (live.working) bits.push(live.working + " 个在干活");
    if (live.error) bits.push(live.error + " 个出错");
    if (notice) bits.push(notice);
    stateEl.textContent = bits.join(" · ");
  }

  async function load() {
    if (gone || document.visibilityState === "hidden" || !window.HoneycombLanes) return;
    var mine = ++seq;
    var q = hours ? window.HoneycombLanes.query(last, hours) : (last ? "?date=" + last.today : "");
    var body;
    try {
      var res = await fetch(API + q, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (res.status === 404) { gone = true; panel.hidden = true; return; }
      if (!res.ok) return;                 // 暂时拉不到：沿用上一张图
      body = await res.json();
    } catch (e) { return; }
    if (mine !== seq || !body || !body.now) return;
    last = body;
    lastQ = q;
    // 第一次不带参只拿到今天；最近 3 小时跨了零点就马上补拉两天的
    var next = hours ? window.HoneycombLanes.query(body, hours) : "";
    if (next && next !== q && next.indexOf("from=") !== -1) { load(); return; }
    panel.hidden = false;
    draw();
  }

  Array.prototype.forEach.call(rangeBtns, function (btn) {
    btn.addEventListener("click", function () {
      hours = Number(btn.getAttribute("data-hours"));
      Array.prototype.forEach.call(rangeBtns, function (b) {
        var on = b === btn;
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-pressed", String(on));
      });
      draw();
      load();
    });
  });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") load();
  });
  // 主题切换不用重画：颜色全是 var(--…)。

  function start() {
    load();
    setInterval(load, POLL_MS);
  }
  if (window.HoneycombLanes) { start(); return; }
  var s = document.createElement("script");
  s.src = BASE + "__cockpit/lanes.js";
  s.onload = start;                         // 加载失败：面板保持 hidden
  document.head.appendChild(s);
})();
