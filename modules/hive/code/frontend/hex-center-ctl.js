/* hive · hex-center-ctl.js —— 中心计时格上的三个按钮（2026-09-12）。
 *
 * 人类原话：「加入三个按钮，刚好排列在六边形的下半部分 2 个和最底部。下半两个
 * 一个是完成一个是暂停。最底部是取消不记录」「新增一个本次任务暂停按钮（纯前端活）
 * ……后端接口保持不变」。
 *
 *   完成        = POST /timer/stop     与计时台「停止并记录」同一个接口
 *   暂停        = POST /timer/stop  +  本机记住"停的是哪个任务"
 *   取消不记录  = POST /timer/cancel   与计时台「取消，不记录」同一个接口
 *   「完成」「取消不记录」都要点两下（人类 2026-09-12：「完成按钮也要有个确定」）
 *   继续        = POST /timer/start {taskId}（只在暂停态出现）
 *
 * ⚠️ 为什么暂停**必须**发 stop，而不是前端自己把走秒停住：
 *   后端一段计时 = start 那一刻到 stop 那一刻。前端停表而后端不停，暂停的那段
 *   时间照样被记成干活；等人类回来点「继续」，前端也没有接口能把中间那段扣掉。
 *   所以暂停 = 在暂停那一刻真的 stop（这一段起止时间准确入账），继续 = 新开一段。
 *   两段中间的空白不属于任何一段，自然不记账；任务总时长 = 各段相加，照样准。
 *   "暂停"这个状态本身后端不知道，只存在本机 —— 这就是「纯前端」的那一半。
 *
 * 「继续」要**接着之前的时间**数（人类 2026-09-12：「暂停后继续需要继续之前时间」）。
 * 后端每段都是独立的，所以累计只能前端记：暂停时把"之前累计 + 这一段"存进
 * 暂停记忆的 carriedSeconds；继续成功后转存进 CARRY_KEY，计时中显示 = 累计 + 本段。
 * 这只是**显示**：入账的仍是一段一段真实起止，一秒都不会被重复记。
 *
 * 暂停态的「取消不记录」（定规：暂停即入账，取消只作废当前段）：暂停时已经没有
 * 当前段了，所以它只是放下"还要继续"这件事 —— 暂停前的时间已入账，撤不回，
 * 状态栏把这句话说出来。按钮照常显示（人类：「按钮不用隐藏」）。
 *
 * 暂停记忆放 localStorage（键见 PAUSE_KEY）。蜂巢页和计时台同源，**两边共用这一个键**：
 * 这边暂停，计时台那边看得到「继续」，反之亦然。键名与形状写进了
 * contracts/timer-ring-visual-v1.md「暂停（纯前端）」一节，两个模块都照那份实现。
 */
(function () {
  "use strict";

  var PAUSE_KEY = "nexus.timer.paused.v1";
  var CARRY_KEY = "nexus.timer.carry.v1";
  var TIMER_STOP = "/api/core/timer/stop";
  var TIMER_CANCEL = "/api/core/timer/cancel";
  var TIMER_START = "/api/core/timer/start";
  // 「完成」「取消不记录」要点第二下才生效：一个把这段结掉、一个把这段丢掉，
  // 都没有撤销；而它们就在「暂停」旁边，手一滑就按错。
  // 计时台那边是弹层确认；六边形里塞不下弹层，改成"按钮原地变成确认"，
  // CONFIRM_MS 内不点第二下就自己复原。
  var CONFIRM_MS = 3000;
  var NEEDS_CONFIRM = { done: "再点确认", forget: "再点确认", cancel: "再点一次确认", drop: "再点一次确认" };

  var ctx = null;
  var busy = false;
  var armedAt = 0, armedBtn = null;

  // ── 暂停记忆 ─────────────────────────────────────────────────
  // 读写都包 try：隐私模式 / 禁用站点数据时 localStorage 会直接抛。
  // 拿不到就当"没暂停过"，按钮照样能停能取消，只是没有「继续」。
  function readKey(key) {
    try {
      var v = JSON.parse(window.localStorage.getItem(key) || "null");
      return (v && v.taskId) ? v : null;
    } catch (e) { return null; }
  }
  function writeKey(key, v) {
    try {
      if (v) window.localStorage.setItem(key, JSON.stringify(v));
      else window.localStorage.removeItem(key);
    } catch (e) { /* 存不下就算了，见上 */ }
  }
  function readPause() { return readKey(PAUSE_KEY); }
  function writePause(v) { writeKey(PAUSE_KEY, v); }

  // 计时中这一段之前已经累计了多少秒（只对"继续"出来的那个任务有值）。
  function carried(current) {
    var c = readKey(CARRY_KEY);
    return (c && current && current.running && current.task && c.taskId === current.task.id)
      ? (c.carriedSeconds || 0) : 0;
  }
  // 这件事**最初**是几点开始的：继续出来的那一段要沿用暂停前的起点，
  // 否则「开始 hh:mm」会跳成点继续那一刻（人类要看的是这件事从几点干起）。
  function startedAt(current) {
    var c = readKey(CARRY_KEY);
    if (c && c.startedAt && current && current.task && c.taskId === current.task.id) return c.startedAt;
    return current && current.sessionStartAt;
  }
  function sessionSeconds(current) {
    var t = current && Date.parse(current.sessionStartAt);
    return t ? Math.max(0, Math.round((Date.now() - t) / 1000)) : 0;
  }

  function markup() {
    return '<div class="hex-center-ctl" hidden>' +
      '<button type="button" class="hex-ctl-btn is-done" data-hex-ctl="done">' +
        '<span class="hex-ctl-label">完成</span></button>' +
      '<button type="button" class="hex-ctl-btn is-pause" data-hex-ctl="pause">' +
        '<span class="hex-ctl-label">暂停</span></button>' +
      // 三块的形状全在 hex.css（clip-path 贴着六边形的边切），这里只放字。
      '<button type="button" class="hex-ctl-btn is-cancel" data-hex-ctl="cancel">' +
        '<span class="hex-ctl-label">取消不记录</span></button>' +
      "</div>";
  }

  // 由 paintCenter 每秒调一次。三种态：
  //   计时中         完成 / 暂停 / 取消不记录
  //   暂停中（本机） 完成 / 继续 / 取消（后两者都不发请求：时间早已入账）
  //   空闲           不显示按钮（点中心格去计时台开始计时）
  // 返回暂停记忆，好让 paintCenter 在空闲态把"暂停的是谁"写进标题。
  function paint(centerEl, current) {
    var box = centerEl && centerEl.querySelector(".hex-center-ctl");
    if (!box) return null;
    var running = !!(current && current.running);
    var paused = readPause();
    // 在别处（计时台、另一个标签页）继续了同一个任务：暂停记忆作废。
    if (running && paused && current.task && current.task.id === paused.taskId) {
      writePause(null); paused = null;
    }
    // 累计只跟着"继续出来的那个任务"：换了任务、或者停下且没在暂停 → 作废。
    var carry = readKey(CARRY_KEY);
    if (carry && (running ? !(current.task && current.task.id === carry.taskId) : !paused)) {
      writeKey(CARRY_KEY, null);
    }
    var mode = running ? "running" : (paused ? "paused" : "idle");
    box.hidden = (mode === "idle");
    centerEl.classList.toggle("has-ctl", mode !== "idle");
    centerEl.classList.toggle("is-paused", mode === "paused");
    if (box.dataset.mode !== mode) {
      box.dataset.mode = mode;
      disarm();
      var done = box.querySelector(".is-done"), pause = box.querySelector(".is-pause");
      var cancel = box.querySelector(".is-cancel");
      if (mode === "paused") {
        label(pause, "继续"); pause.dataset.hexCtl = "resume";
        done.dataset.hexCtl = "forget";
        cancel.dataset.hexCtl = "drop";   // 暂停态没有当前段可作废，只能放下"继续"
      } else {
        label(pause, "暂停"); pause.dataset.hexCtl = "pause";
        done.dataset.hexCtl = "done";
        cancel.dataset.hexCtl = "cancel";
      }
    }
    if (armedAt && Date.now() - armedAt > CONFIRM_MS) disarm();
    return mode === "paused" ? paused : null;
  }

  function label(btn, text) { btn.querySelector(".hex-ctl-label").textContent = text; }
  function disarm() {
    if (armedBtn) {
      armedBtn.classList.remove("is-armed");
      label(armedBtn, armedBtn.dataset.label);
    }
    armedAt = 0; armedBtn = null;
  }

  function after(r, okText) {
    busy = false;
    if (!r || !r.ok) { ctx.setStatus("操作失败：" + ((r && r.message) || "未知错误")); return; }
    return ctx.getJson(ctx.currentPath).then(function (c) {
      if (c.ok) ctx.state.current = c.data;
      ctx.paintCenter();
      ctx.setStatus(okText);
    });
  }

  // hex-app 的 click 处理器先问这里；返回 true = 这一下是按钮的，别再当"点中心格进计时台"。
  function onClick(ev) {
    var btn = ev.target.closest && ev.target.closest("[data-hex-ctl]");
    if (!btn || !btn.closest(".hex-center")) return false;
    ev.preventDefault();
    ev.stopPropagation();
    if (busy) return true;
    var act = btn.dataset.hexCtl;
    var cur = ctx.state.current;
    var name = (cur && cur.task && cur.task.name) || "";
    // 第一下只"上膛"；第二下必须还是**同一颗**按钮才算确认 ——
    // 上膛了「完成」再去点「取消」，是换主意，不是确认。
    if (NEEDS_CONFIRM[act] && armedBtn !== btn) {
      disarm();
      armedAt = Date.now(); armedBtn = btn;
      btn.dataset.label = btn.querySelector(".hex-ctl-label").textContent;
      btn.classList.add("is-armed");
      label(btn, NEEDS_CONFIRM[act]);
      return true;
    }
    disarm();
    if (act === "cancel") {
      busy = true;
      ctx.postJson(TIMER_CANCEL, undefined).then(function (r) { after(r, "已取消，这段不记录：" + name); });
    } else if (act === "done") {
      busy = true;
      ctx.postJson(TIMER_STOP, undefined).then(function (r) { after(r, "已完成并记录：" + name); });
    } else if (act === "pause") {
      busy = true;
      var memo = cur && cur.task ? {
        taskId: cur.task.id, taskName: cur.task.name,
        projectName: (cur.project && cur.project.name) || "",
        pausedAt: new Date().toISOString(),
        carriedSeconds: carried(cur) + sessionSeconds(cur),
        startedAt: startedAt(cur),
        zoneId: (cur.zone && cur.zone.id) || null   // 暂停中中心格仍用这个分区的颜色
      } : null;
      ctx.postJson(TIMER_STOP, undefined).then(function (r) {
        if (r && r.ok && memo) {                     // 停成功了才记，否则"继续"会指向一段没停下的计时
          writePause(memo);
          writeKey(CARRY_KEY, null);                 // 累计已经转进暂停记忆
        }
        after(r, "已暂停：" + name + "（这一段已记录，点「继续」接着计）");
      });
    } else if (act === "resume") {
      var p = readPause();
      if (!p) { ctx.paintCenter(); return true; }
      busy = true;
      ctx.postJson(TIMER_START, { taskId: p.taskId }).then(function (r) {
        if (r && r.ok) {
          writeKey(CARRY_KEY, { taskId: p.taskId, carriedSeconds: p.carriedSeconds || 0,
                                startedAt: p.startedAt || null });
          writePause(null);
        }
        after(r, "继续计时：" + p.taskName);
      });
    } else if (act === "forget") {
      var q = readPause();
      writePause(null);
      ctx.paintCenter();
      ctx.setStatus("已完成：" + ((q && q.taskName) || "") + "（暂停前的时间都已记录）");
    } else if (act === "drop") {
      var d = readPause();
      writePause(null);
      ctx.paintCenter();
      ctx.setStatus("已放弃继续：" + ((d && d.taskName) || "") +
        "（暂停前的时间已经入账，撤不回；取消只作废当前段，暂停时没有当前段）");
    }
    return true;
  }

  function init(c) {
    ctx = c;
    // 另一个标签页 / 计时台改了暂停记忆：立刻重画，不等下一秒。
    window.addEventListener("storage", function (ev) {
      if (ev.key === PAUSE_KEY || ev.key === CARRY_KEY) ctx.paintCenter();
    });
  }

  window.NexusTableHexCenterCtl = {
    init: init, markup: markup, paint: paint, onClick: onClick,
    readPause: readPause, carried: carried, startedAt: startedAt, PAUSE_KEY: PAUSE_KEY, CARRY_KEY: CARRY_KEY
  };
})();
