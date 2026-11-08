// hive · hex-stage.js —— 蜂巢新布局的选择与计算（hive.stage.v1「一」「二」）。纯逻辑，无 DOM。
// 中心环选哪条运行、气泡停哪、桶文案、抽屉行、拖放请求组装、白条、手动计时状态。
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

  function pickBubbleHost(focus) {
    if (focus && focus.state !== "afk" && focus.projectId) return { project: focus.projectId };
    return { clock: true };
  }

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
      if (of.length) grey.push({ name: GREY_NAMES[k], seconds: of.reduce(function (s, b) { return s + b.seconds; }, 0), draggable: false, block: null });
    });
    return legible.concat(grey);
  }

  function stripList(items) {
    var sorted = (items || []).slice().sort(function (a, b) { return ms(a.startAt) - ms(b.startAt); });
    return { items: sorted.slice(0, MAX_STRIPS), plus: Math.max(0, sorted.length - MAX_STRIPS) };
  }

  // 拖放要发的请求；放到空白处（projectId 为空）= null，什么都不发
  function dropRequest(item, projectId) {
    if (!projectId) return null;
    var enc = encodeURIComponent;
    switch (item.kind) {
      case "block": return { method: "POST", path: "activity/blocks/assign", body: { date: item.block.date, blockKey: item.block.blockKey, ifSeq: item.block.seq, projectId: projectId, remember: true } };
      case "pet": return { method: "POST", path: "agents/" + enc(item.runId) + "/place", body: { projectId: projectId, remember: true } };
      case "strip": return { method: "POST", path: "timer/unplaced/" + enc(item.id) + "/place", body: { projectId: projectId } };
      case "clock": return { method: "POST", path: "timer/place", body: { projectId: projectId } };
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

    async function drop(item, projectId) {
      var req = dropRequest(item, projectId);
      if (!req) return { ok: false, reverted: true };
      if (item.kind === "clock" && !timer) return { ok: false, reverted: true };
      var out = await call(req), name = deps.projectName(projectId);
      if (!out.ok) return fail(out);
      if (item.kind === "block") {
        blocks = blocks.filter(function (b) { return b.blockKey !== item.block.blockKey; });
        bucket = Math.max(0, bucket - item.block.seconds);
        say("已记下：以后 " + item.block.displayName + " 都算 " + name);
      } else if (item.kind === "pet") {
        docks[item.runId] = projectId;
        if (out.body.rule) say("已记下：以后 " + item.agent + " 都算 " + name);
      } else if (item.kind === "strip") {
        unplaced = unplaced.filter(function (u) { return u.id !== item.id; });
      } else timer.projectId = projectId;
      return { ok: true };
    }

    // 点时钟：没在计时 = 开始（不选项目）；在计时 = 停
    async function clickClock() {
      if (!timer) {
        var s = await call({ method: "POST", path: "timer/start-unplaced" });
        if (!s.ok) return fail(s);
        timer = { projectId: null, startAt: deps.now() };
        return { ok: true };
      }
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
      setTimer: function (t) { timer = t; },
      drop: drop,
      clickClock: clickClock
    };
  }

  var api = { pickRing: pickRing, pickBubbleHost: pickBubbleHost, bucketText: bucketText, drawerRows: drawerRows, stripList: stripList, dropRequest: dropRequest, createStage: createStage };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexStage = api;
})();
