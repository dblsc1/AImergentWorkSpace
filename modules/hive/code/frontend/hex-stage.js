// hive · hex-stage.js —— 蜂巢新布局的选择与计算（hive.stage.v1「一」「二」）。纯逻辑，无 DOM。
// 中心环选哪条运行、眼睛停哪、桶文案、抽屉行、拖放请求组装、白条、手动计时状态。
// 请求的 path 相对 api/core/，由接线层拼站点前缀。
(function () {
  "use strict";

  var NOW_SECONDS = 15;     // attend 最后一条的 until ≥ 此刻 − 15 秒算此刻在看
  var NOTICE_MS = 3000;
  var MAX_STRIPS = 5;
  var GREY_NAMES = { browser: "浏览器（其他网站）", terminal: "终端（未认出）", other: "其他" };
  var GREY_ORDER = ["browser", "terminal", "other"];

  function ms(s) { var t = s ? Date.parse(s) : NaN; return isNaN(t) ? 0 : t; }

  // attend = views/lanes 的 interactions；dayStartMs 缺省 = 传进来的都算今天
  function pickRing(runs, attend, now, dayStartMs) {
    var list = (attend || []).filter(function (a) {
      return a.kind === "attend" && (dayStartMs == null || ms(a.until) >= dayStartMs);
    });
    if (!list.length) return null;
    var last = list.reduce(function (m, a) { return ms(a.until) >= ms(m.until) ? a : m; });
    var hit = (runs || []).filter(function (r) { return r.runId === last.runId; })[0];
    return hit || { runId: last.runId };
  }

  // 眼睛 = 人此刻的聚焦（hive.stage.v1「十」）。停哪：
  // 手动计时记到了项目 → 那个项目；计时中还没记到项目 → 时钟角上（睁眼）；
  // 没在计时：窗口认出项目 → 那个项目；在场但没认出（收件箱不算项目）→ 左下托盘旁；离开 / 没有心跳 → 时钟角上、闭眼
  var INBOX = "p_inbox", HIDDEN_TITLE = /^(窗口名|路径)\d+$/;
  function eyeHost(focus, running) {
    if (running) return running.projectId && running.projectId !== INBOX ? { at: "project", project: running.projectId, by: "timer" } : { at: "clock", by: "timer" };
    if (!focus || focus.state === "afk") return { at: "clock", closed: true };
    if (focus.projectId && focus.projectId !== INBOX) return { at: "project", project: focus.projectId, by: "window" };
    return { at: "tray", by: "window" };
  }
  // 窗口名：`程序 · 标题`；标题是代号（被隐去）的写「标题已隐去」
  function focusLabel(focus) {
    var app = (focus && focus.app) || "窗口", title = (focus && focus.title) || "";
    // 此刻读不到标题（浏览器没装扩展、标签页对不上等）：用这个窗口所在块的名字（`程序 · 归一化标题`）的标题部分；也没有就只写程序名
    if (!title) { var dn = (focus && focus.displayName) || "", cut = dn.indexOf(" · "); title = cut >= 0 ? dn.slice(cut + 3) : dn.toLowerCase() === app.toLowerCase() ? "" : dn; }
    return !title ? app : app + " · " + (HIDDEN_TITLE.test(title) ? "标题已隐去" : title);
  }

  // 未分类桶的过滤器（「二十四」）：今天累计满 minMinutes 分钟的窗口块才进桶；不满的不显示、不计入桶上的时间
  var BUCKET_MIN_MINUTES = 2;
  function bucketMinutes(raw) { var n = parseInt(raw, 10); return isFinite(n) && n >= 0 && n <= 120 ? n : BUCKET_MIN_MINUTES; }
  function bucketFilter(items, minMinutes) { return (items || []).filter(function (x) { return (x.seconds || 0) >= minMinutes * 60; }); }

  function bucketText(seconds) {
    var m = Math.floor(Math.max(0, seconds) / 60), h = Math.floor(m / 60);
    return "未分类 · 今天 " + (h ? h + " 小时 " + (m % 60) + " 分" : m + " 分");
  }

  // 可拖行按秒降序；看不懂的按 kind 合成灰行
  function drawerRows(blocks) {
    var legible = (blocks || []).filter(function (b) { return b.legible; })
      .sort(function (a, b) { return b.seconds - a.seconds; })
      .map(function (b) { return { name: b.displayName, seconds: b.seconds, draggable: true, block: b }; });
    var grey = [];
    GREY_ORDER.forEach(function (k) {
      var of = (blocks || []).filter(function (b) { return !b.legible && (GREY_NAMES[b.kind] ? b.kind : "other") === k; });
      if (of.length) grey.push({ name: GREY_NAMES[k], kind: k, seconds: of.reduce(function (s, b) { return s + b.seconds; }, 0), draggable: false, block: null, members: greyGroups(of) });
    });
    return legible.concat(grey);
  }

  // 灰行点开：块已是「程序 · 归一化标题」，一块 = 一组同名窗口；按秒降序，块带上标题当 displayName 以便拖放 / 提示沿用
  function greyGroups(of) {
    return of.map(function (b) {
      var app = (b.apps || []).filter(function (a) { return b.blockKey.indexOf(a + " · ") === 0; })[0] || b.blockKey.split(" · ")[0];
      var title = b.blockKey.indexOf(app + " · ") === 0 ? b.blockKey.slice(app.length + 3) : "";
      return { title: title || app, hidden: HIDDEN_TITLE.test(title), app: app, seconds: b.seconds, block: Object.assign({}, b, { displayName: title ? app + " · " + title : app }) };
    }).sort(function (a, b) { return b.seconds - a.seconds; });
  }

  function stripList(items) {
    var sorted = (items || []).slice().sort(function (a, b) { return ms(a.startAt) - ms(b.startAt); });
    return { items: sorted.slice(0, MAX_STRIPS), plus: Math.max(0, sorted.length - MAX_STRIPS) };
  }

  // 记到任务 = 只给 taskId，否则只给 projectId（nexus-core 要求恰好一个）
  function aim(projectId, taskId) { return taskId ? { taskId: taskId } : { projectId: projectId }; }
  // 拖放要发的请求；放到空白处（projectId 为空）= null，什么都不发。块与宠物默认 remember:false（只这一次），气泡里勾上才传 true（2026-11-08）
  function dropRequest(item, projectId, remember, timeFrom, taskId) {
    if (!projectId) return null;
    var enc = encodeURIComponent;
    switch (item.kind) {
      case "block": return { method: "POST", path: "activity/blocks/assign", body: Object.assign({ date: item.block.date, blockKey: item.block.blockKey, ifSeq: item.block.seq, remember: !!remember }, aim(projectId, taskId)) };
      case "pet": return { method: "POST", path: "agents/" + enc(item.runId) + "/place", body: Object.assign({ remember: !!remember }, aim(projectId, taskId), timeFrom === "now" ? { timeFrom: "now" } : {}) };
      case "strip": return { method: "POST", path: "timer/unplaced/" + enc(item.id) + "/place", body: aim(projectId, taskId) };
      case "clock": return { method: "POST", path: "timer/place", body: aim(projectId, taskId) };
    }
    return null;
  }

  function reason(res, err) {
    if (err) return "网络失败";
    if (res.status === 409) return "数据已变化，请刷新后重试";
    if (res.status === 422) return "不能记到这里";
    if (res.status === 404) return "目标不存在";
    if (res.status === 403) return "没有权限";
    return "失败（" + res.status + "）";
  }

  // deps: {send(req) → Promise<{status, body}>, now() → ms, projectName(id)}
  function createStage(deps) {
    var bucket = 0, blocks = [], note = null, docks = {}, unplaced = [], timer = null;

    function say(text) { note = { text: text, until: deps.now() + NOTICE_MS }; }
    async function call(req) {
      try {
        var res = await deps.send(req);
        if (res.status >= 200 && res.status < 300) return { ok: true, body: res.body || {} };
        return { ok: false, why: reason(res) };
      } catch (e) { return { ok: false, why: reason(null, e) }; }
    }
    function fail(out) { say("失败：" + out.why); return { ok: false, reverted: true, why: out.why }; }

    async function drop(item, projectId, opts) {   // opts: {remember, timeFrom, taskId}
      var req = dropRequest(item, projectId, opts && opts.remember, opts && opts.timeFrom, opts && opts.taskId);
      if (!req) return { ok: false, reverted: true };
      if (item.kind === "clock" && !timer) return { ok: false, reverted: true };
      var out = await call(req);
      if (!out.ok) return fail(out);
      if (item.kind === "block") {
        blocks = blocks.filter(function (b) { return b.blockKey !== item.block.blockKey; });
        bucket = Math.max(0, bucket - item.block.seconds);
      } else if (item.kind === "pet") {
        docks[item.runId] = projectId;
      } else if (item.kind === "strip") {
        unplaced = unplaced.filter(function (u) { return u.id !== item.id; });
      } else timer.projectId = projectId;
      return { ok: true, seq: out.body.seq };
    }

    // 气泡里勾上「以后都算」：同一目标再发一次 remember:true。块的 ifSeq 用上一次 assign 回的 seq（缺省 +1）
    async function remember(item, projectId, seq) {
      var req = dropRequest(item, projectId, true);
      if (!req) return { ok: false };
      if (item.kind === "block") req.body.ifSeq = typeof seq === "number" ? seq : item.block.seq + 1;
      var out = await call(req);
      return out.ok ? { ok: true } : fail(out);
    }

    // 开始计时（不选项目）；countdownSeconds = 倒计时（hive.stage.v1「九」，nexus-core v2.51），缺省 = 正计时
    async function startClock(countdownSeconds) {
      var s = await call({ method: "POST", path: "timer/start-unplaced", body: countdownSeconds ? { countdownSeconds: countdownSeconds } : undefined });
      if (!s.ok) return fail(s);
      timer = { projectId: null, startAt: deps.now(), deadlineAt: countdownSeconds ? deps.now() + countdownSeconds * 1000 : null };
      return { ok: true };
    }
    // 停（或没在计时 = 开始，老行为；「九」起时钟的短按不再走这里）
    async function clickClock() {
      if (!timer) return startClock();
      var out = await call({ method: "POST", path: "timer/stop" });
      if (!out.ok) return fail(out);
      if (out.body.unplacedId) unplaced.push({ id: out.body.unplacedId, startAt: new Date(timer.startAt).toISOString(), endAt: new Date(deps.now()).toISOString() });
      timer = null;
      return { ok: true };
    }

    return {
      setBucket: function (seconds, list) { bucket = seconds; blocks = list.slice(); },
      bucketSeconds: function () { return bucket; },
      bucketKeys: function () { return blocks.map(function (b) { return b.blockKey; }); },
      drawer: function () { return drawerRows(blocks); },
      notice: function () { return note && deps.now() < note.until ? note.text : null; },
      petDock: function (runId) { return docks[runId] || null; },
      setStrips: function (list) { unplaced = list.slice(); },
      strips: function () { return stripList(unplaced); },
      timerRunning: function () { return !!timer; },
      timerState: function () { return timer; },
      setTimer: function (t) { timer = t; },
      drop: drop,
      remember: remember,
      clickClock: clickClock,
      startClock: startClock,
      say: say
    };
  }

  // 宠物名牌：人改的名字 > 「任务或项目名·对话名」 > 对话名 > label || agent（2026-11-08）
  function petTitle(run, targetName) {
    if (run.humanName) return run.humanName;
    if (run.conversation) return targetName ? targetName + "·" + run.conversation : run.conversation;
    return run.label || run.agent;
  }

  var api = { petTitle: petTitle, pickRing: pickRing, eyeHost: eyeHost, focusLabel: focusLabel, bucketMinutes: bucketMinutes, bucketFilter: bucketFilter, bucketText: bucketText, drawerRows: drawerRows, greyGroups: greyGroups, stripList: stripList, dropRequest: dropRequest, createStage: createStage };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexStage = api;
})();
