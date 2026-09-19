/* ring/frontend · ring-pause.js —— 「暂停 / 继续」（2026-09-12）。
 *
 * 人类：「新增一个本次任务暂停按钮（纯前端活）。显示在中心计时圆环和计时面板中。
 * 后端接口保持不变。」
 *
 * 后端没有"暂停"这个状态，也不加。暂停 = 在暂停那一刻调**现有的**
 * `POST /api/core/timer/stop`（经 window.stopTimer，和「停止并记录」同一条路径），
 * 这一段按真实起止时间入账；继续 = 调**现有的** `POST /api/core/timer/start`
 * 对同一个任务新开一段。两段中间的空白不属于任何一段，所以不记账。
 *
 * ⚠️ 为什么不能只在前端把走秒停住：后端一段计时 = start 到 stop。前端停表、后端
 * 不停，暂停的那段照样被记成干活，之后也没有任何接口能把它扣掉。
 *
 * 「继续」接着之前的时间数（人类 2026-09-12：「暂停后继续需要继续之前时间」）：
 * 暂停时把"之前累计 + 这一段"存进暂停记忆的 carriedSeconds，继续成功后转存 CARRY_KEY，
 * 表芯走秒 = 累计 + 本段（ring-instrument.js 的 tickElapsedDisplay 读 window.ringCarrySeconds）。
 * 只是显示，入账仍是一段一段真实起止。
 *
 * 暂停态的「取消不记录」（产品决定：暂停即入账，取消只作废当前段）：暂停时没有当前段，
 * 它只放下"还要继续"这件事；暂停前的时间已入账，撤不回，界面把这句话说出来。
 *
 * "暂停的是哪个任务"只存在本机 localStorage，键与形状见
 * contracts/timer-ring-visual-v1.md「暂停（纯前端）」。hive 蜂巢中心格
 * （modules/hive/code/frontend/hex-center-ctl.js）读写**同一个键** —— 两页同源，
 * 在蜂巢暂停、到计时台继续（或反过来）都成立。
 *
 * 与其他文件的分工：只读 window.ringCurrentState（ring-instrument.js 每轮轮询写），
 * 只调 window.stopTimer / window.startTimer（ring-controls.js）。不碰 #controls-row
 * 的显隐（那是 ring-instrument.js 的既有逻辑，暂停按钮住在里面，白蹭"只在计时中出现"）。
 */
(function () {
  "use strict";

  const PAUSE_KEY = "nexus.timer.paused.v1";
  const CARRY_KEY = "nexus.timer.carry.v1";

  const pauseBtnEl = document.getElementById("pause-btn");
  const pausedRowEl = document.getElementById("paused-row");
  const pausedLabelEl = document.getElementById("paused-label");
  const resumeBtnEl = document.getElementById("resume-btn");
  const pausedDoneBtnEl = document.getElementById("paused-done-btn");
  const pausedDropBtnEl = document.getElementById("paused-drop-btn");
  const pausedNoteEl = document.getElementById("paused-note");

  // 隐私模式 / 禁用站点数据时 localStorage 会直接抛；拿不到就当没暂停过。
  function readKey(key) {
    try {
      const v = JSON.parse(window.localStorage.getItem(key) || "null");
      return (v && v.taskId) ? v : null;
    } catch (err) { return null; }
  }
  function writeKey(key, v) {
    try {
      if (v) window.localStorage.setItem(key, JSON.stringify(v));
      else window.localStorage.removeItem(key);
    } catch (err) { /* 存不下就算了 */ }
  }
  const readPause = () => readKey(PAUSE_KEY);
  const writePause = (v) => writeKey(PAUSE_KEY, v);

  // 计时中这一段之前已累计的秒数（只对"继续"出来的那个任务有值）。
  function carrySeconds(current) {
    const c = readKey(CARRY_KEY);
    return (c && current && current.running && current.task && c.taskId === current.task.id)
      ? (c.carriedSeconds || 0) : 0;
  }
  function firstStart(current) {
    const c = readKey(CARRY_KEY);
    if (c && c.startedAt && current && current.task && c.taskId === current.task.id) return c.startedAt;
    return current && current.sessionStartAt;
  }
  function sessionSeconds(current) {
    const t = current && Date.parse(current.sessionStartAt);
    return t ? Math.max(0, Math.round((Date.now() - t) / 1000)) : 0;
  }
  function clock(seconds) {
    const s = Math.max(0, Math.floor(seconds));
    const pad = (n) => String(n).padStart(2, "0");
    return pad(Math.floor(s / 3600)) + ":" + pad(Math.floor(s / 60) % 60) + ":" + pad(s % 60);
  }

  function render() {
    const current = window.ringCurrentState;
    const running = Boolean(current && current.running && current.task);
    let paused = readPause();
    // 在别处（蜂巢、另一个标签页）已经继续了同一个任务：暂停记忆作废。
    if (running && paused && current.task.id === paused.taskId) {
      writePause(null);
      paused = null;
    }
    // 累计只跟着"继续出来的那个任务"：换了任务、或停下且没在暂停 → 作废。
    const carry = readKey(CARRY_KEY);
    if (carry && (running ? current.task.id !== carry.taskId : !paused)) writeKey(CARRY_KEY, null);
    pausedRowEl.hidden = running || !paused;
    if (paused) {
      pausedLabelEl.textContent = "已暂停：" + paused.taskName +
        (paused.projectName ? " · " + paused.projectName : "") +
        " · " + clock(paused.carriedSeconds || 0);
    }
  }

  pauseBtnEl.addEventListener("click", async () => {
    const current = window.ringCurrentState;
    const memo = (current && current.running && current.task) ? {
      taskId: current.task.id,
      taskName: current.task.name,
      projectName: (current.project && current.project.name) || "",
      pausedAt: new Date().toISOString(),
      carriedSeconds: carrySeconds(current) + sessionSeconds(current),
      // 这件事最初的开始时刻（契约可选字段）：蜂巢中心格的「开始 hh:mm」沿用它
      startedAt: firstStart(current),
      zoneId: (current.zone && current.zone.id) || null, // 契约可选字段：蜂巢中心格暂停中仍用分区色
    } : null;
    pauseBtnEl.disabled = true;
    pauseBtnEl.textContent = "暂停中…";
    const result = await window.stopTimer();
    // 停成功了才记：否则「继续」会指向一段根本没停下来的计时。
    if (result && result.ok && memo) {
      writePause(memo);
      writeKey(CARRY_KEY, null); // 累计已经转进暂停记忆
    }
    pauseBtnEl.textContent = "暂停";
    pauseBtnEl.disabled = false;
    render();
  });

  resumeBtnEl.addEventListener("click", async () => {
    const paused = readPause();
    if (!paused) { render(); return; }
    resumeBtnEl.disabled = true;
    const result = await window.startTimer(paused.taskId);
    resumeBtnEl.disabled = false;
    if (result && result.ok) {
      writeKey(CARRY_KEY, { taskId: paused.taskId, carriedSeconds: paused.carriedSeconds || 0,
                            startedAt: paused.startedAt || null });
      writePause(null);
    }
    render();
  });

  // 暂停态下的「完成」：暂停前的每一段早已入账，这里只是把"还等着继续"这件事放下，
  // 不发任何请求。
  pausedDoneBtnEl.addEventListener("click", () => {
    pausedNoteEl.hidden = true;
    writePause(null);
    render();
  });
  // 暂停态「取消不记录」：没有当前段可作废，只放下"继续"；说清楚已入账的撤不回。
  pausedDropBtnEl.addEventListener("click", () => {
    const paused = readPause();
    writePause(null);
    pausedNoteEl.textContent = "已放弃继续" + (paused ? "「" + paused.taskName + "」" : "") +
      "。暂停前的时间已经入账，撤不回（取消只作废当前段，暂停时没有当前段）。";
    pausedNoteEl.hidden = false;
    render();
  });

  window.addEventListener("storage", (ev) => {
    if (ev.key === PAUSE_KEY || ev.key === CARRY_KEY) render();
  });
  // ring-instrument.js 的表芯走秒读这个：显示 = 累计 + 本段。
  window.ringCarrySeconds = () => carrySeconds(window.ringCurrentState);
  // ringCurrentState 每轮轮询才更新；1 秒看一眼足够跟上，读的只是内存和 localStorage。
  setInterval(render, 1000);
  render();
})();
