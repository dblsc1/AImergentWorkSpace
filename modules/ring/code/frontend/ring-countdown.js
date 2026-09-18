/* ring/frontend · ring-countdown.js —— 倒计时（番茄钟），2026-08-09 任务单。
 *
 * **最终口径（开工中段更正过一次，本文件按最终口径写）**：倒计时＝预设了
 * 目标时长的正计时。「开始倒计时」调用与「开始计时」完全相同的
 * `POST /api/core/timer/start`（后端不区分正/倒计时，零新增后端接口，见
 * `window.startTimer`）；到点自动调用与「停止并记录」完全相同的
 * `POST /api/core/timer/stop`（见 `window.stopTimer`），落 session，和正计时
 * 手动停止完全一样进档案/投影/圆环。本文件**不发起任何新的写请求语义**，
 * 只是多了「设定目标时长 + 倒数显示 + 到点自动停 + 到点提醒」这一层前端逻辑。
 *
 * 与另外两个文件的分工（三个 IIFE 词法作用域互不可见，跨文件调用一律走
 * 显式 `window.*`，保持 R9 零构建）：
 *   本文件不挂任何名字给别人调——纯监听者，只读 `window.ringCurrentState`
 *   （ring-instrument.js 挂的最新 `views/current`）、调用
 *   `window.startTimer`/`window.stopTimer`（ring-controls.js 挂的）。
 *
 * ── 与正计时的共存/互斥方案 ──────────────────────────────────────────
 * 空闲态用「计时方式」两个 tab（#mode-tab-stopwatch/#mode-tab-countdown）
 * 切换：默认「正计时」＝既有行为零变化（`#start-big-btn` 照常显示）；切到
 * 「倒计时」则隐藏 `#start-big-btn`、露出时长面板（预设 25/45/60 + 自定义 +
 * 「开始倒计时」按钮）——任一时刻只有一个「开始」入口可点，不给「两种方式
 * 都能点」的机会。一旦有任何计时在跑（不论哪种方式开始的，只认
 * `window.ringCurrentState.running`），tab 与时长面板整体隐藏，只留现有的
 * 停止/取消两个按钮——这一步不需要额外判断"是不是我的倒计时"，因为后端
 * 本来就只允许一个进行中的会话（`timer/start` 会自动关闭上一个未结束的，
 * 现有行为，本文件不重复判断）。倒计时运行中的表芯显示（剩余 MM:SS + 进度弧）
 * 与正计时运行中的表芯显示（已用时长）互斥展示，由 `determineActiveRecord()`
 * 判断"当前在跑的会话是不是我本地记的那个倒计时"（比对 taskId），一旦不
 * 匹配（例如从另一个标签页/设备用正计时启动了别的任务）就判定为非倒计时
 * 展示，并清理本地这条失效记录，不留垃圾状态。
 *
 * ── 本地续算 ──────────────────────────────────────────────────────
 * localStorage 只存 `{taskId, targetSeconds, endAt}`（`endAt` = 开始时
 * `Date.now() + targetSeconds*1000`），纯粹用于刷新后算「剩余还有多少」——
 * **不落库、不参与「今天」归日判断**，与契约里「今天只认服务端字符串，不用
 * 客户端 new Date() 拼日期」那条规则并不冲突（那条管的是事实的日期归属，
 * 这里只是本地倒计时用的相对时长）。刷新时若已经过期（`endAt` 已过），
 * 下一次 tick 就会立刻判定「到点」并走自动停止流程，不需要特殊分支。
 *
 * ── 到点提醒的降级纪律 ────────────────────────────────────────────
 * beep（Web Audio）与 Web Notification 都是锦上添花：拿不到权限/浏览器不
 * 支持/策略拦截时必须纯视觉降级（标题闪烁 + 表芯变色闪烁），**绝不允许把
 * 异常抛到 console**——每个可能失败的调用都包在 try/catch 里，异步分支
 * 额外挂 `.catch(() => {})`，两层都不放过。
 */

(function () {
  "use strict";

  const STORAGE_KEY = "ring.countdown.v1";
  const TICK_MS = 1000; // 与既有 elapsed-display 的自增节奏一致
  const RETRY_COOLDOWN_MS = 5000; // 到点后 stopTimer 失败时的重试冷却，避免每秒瞎打请求

  // ── DOM 引用 ──────────────────────────────────────────────────────
  const modeTabsEl = document.getElementById("mode-tabs");
  const modeTabStopwatchEl = document.getElementById("mode-tab-stopwatch");
  const modeTabCountdownEl = document.getElementById("mode-tab-countdown");
  const countdownPickerEl = document.getElementById("countdown-picker");
  const durationChips = Array.from(document.querySelectorAll(".duration-chip"));
  const durationCustomEl = document.getElementById("duration-custom");
  const startCountdownBtnEl = document.getElementById("start-countdown-btn");
  const taskSelectEl = document.getElementById("task-select");
  const chronoCenterEl = document.getElementById("chrono-center");
  const countdownOverlayEl = document.getElementById("countdown-overlay");
  const countdownRemainingEl = document.getElementById("countdown-remaining");
  const countdownTaskNameEl = document.getElementById("countdown-task-name");
  const countdownArcEl = document.getElementById("countdown-arc");
  const timerErrorEl = document.getElementById("timer-error");

  // ── localStorage：只读写这一条 key，任何存取失败都静默降级 ───────────
  function readRecord() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const rec = JSON.parse(raw);
      if (!rec || typeof rec.taskId !== "string" ||
          typeof rec.targetSeconds !== "number" || typeof rec.endAt !== "number") {
        return null; // 形状不对（旧版本/被篡改）：当没有，不抛错
      }
      return rec;
    } catch (err) {
      return null; // 隐私模式/存储被禁：倒计时仍可用，只是刷新不能续算
    }
  }
  function writeRecord(rec) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(rec)); } catch (err) { /* 见上 */ }
  }
  function clearRecord() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (err) { /* 见上 */ }
  }

  function showTimerError(message) {
    timerErrorEl.textContent = message;
    timerErrorEl.hidden = false;
  }
  function clearTimerError() {
    timerErrorEl.hidden = true;
    timerErrorEl.textContent = "";
  }

  function formatRemaining(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(value / 3600).toString().padStart(2, "0");
    const m = Math.floor((value % 3600) / 60).toString().padStart(2, "0");
    const s = (value % 60).toString().padStart(2, "0");
    return `${h}:${m}:${s}`;
  }

  // ── 空闲态：计时方式 tab + 时长面板 ──────────────────────────────────
  let activeMode = "stopwatch"; // "stopwatch" | "countdown"，纯 UI 选择，不影响已在跑的会话
  let selectedMinutes = null;

  function setMode(mode) {
    activeMode = mode;
    modeTabStopwatchEl.classList.toggle("is-active", mode === "stopwatch");
    modeTabStopwatchEl.setAttribute("aria-selected", String(mode === "stopwatch"));
    modeTabCountdownEl.classList.toggle("is-active", mode === "countdown");
    modeTabCountdownEl.setAttribute("aria-selected", String(mode === "countdown"));
    countdownPickerEl.hidden = mode !== "countdown";
    syncStopwatchStartVisibility();
  }
  modeTabStopwatchEl.addEventListener("click", () => setMode("stopwatch"));
  modeTabCountdownEl.addEventListener("click", () => setMode("countdown"));

  // #start-big-btn 由 ring-instrument.js 的 renderIdleCenter() 动态创建，每次
  // 从运行态回到空闲态都会重建一次（默认可见）——这里每 tick 都重新按当前
  // tab 校正一次，不是一次性挂载就够。
  function syncStopwatchStartVisibility() {
    const btn = document.getElementById("start-big-btn");
    if (btn) btn.hidden = activeMode === "countdown";
  }

  function selectPreset(minutes) {
    selectedMinutes = minutes;
    durationCustomEl.value = "";
    durationChips.forEach(chip => {
      const isActive = Number(chip.dataset.minutes) === minutes;
      chip.classList.toggle("is-active", isActive);
      chip.setAttribute("aria-pressed", String(isActive));
    });
    syncStartCountdownEnabled();
  }
  durationChips.forEach(chip => {
    chip.setAttribute("aria-pressed", "false");
    chip.addEventListener("click", () => selectPreset(Number(chip.dataset.minutes)));
  });

  durationCustomEl.addEventListener("input", () => {
    const raw = durationCustomEl.value;
    const n = Number(raw);
    selectedMinutes = (raw !== "" && Number.isInteger(n) && n > 0) ? n : null;
    durationChips.forEach(chip => {
      chip.classList.remove("is-active");
      chip.setAttribute("aria-pressed", "false");
    });
    syncStartCountdownEnabled();
  });

  function syncStartCountdownEnabled() {
    startCountdownBtnEl.disabled = !(taskSelectEl.value && selectedMinutes);
  }
  taskSelectEl.addEventListener("change", syncStartCountdownEnabled);

  startCountdownBtnEl.addEventListener("click", async () => {
    const taskId = taskSelectEl.value;
    if (!taskId || !selectedMinutes) return;
    clearTimerError();
    startCountdownBtnEl.disabled = true;
    startCountdownBtnEl.textContent = "开始中…";
    const targetSeconds = selectedMinutes * 60;
    const result = await window.startTimer(taskId); // 与「开始计时」同一条网络路径
    startCountdownBtnEl.textContent = "开始倒计时";
    if (!result || !result.ok) {
      syncStartCountdownEnabled();
      return; // 出错文案已由 startTimer 内部的 showTimerError 显示
    }
    timeUpHandled = false;
    lastStopAttemptAt = 0;
    writeRecord({ taskId, targetSeconds, endAt: Date.now() + targetSeconds * 1000 });
  });

  // ── 运行态：判断"当前在跑的是不是我本地记的那个倒计时" ─────────────────
  // window.ringCurrentState 三种状态都要区分：undefined=还没拿到第一次轮询
  // 结果、null=上一次轮询失败（不确定真实状态）——这两种都保守地"不清理、
  // 不展示"，只有明确拿到 running:false 或 taskId 不匹配时才清理本地记录，
  // 避免一次网络抖动就把刷新续算的凭证冲掉。
  function determineActiveRecord() {
    const rec = readRecord();
    if (!rec) return null;
    const current = window.ringCurrentState;
    if (current === undefined || current === null) return null;
    if (!current.running) { clearRecord(); return null; }
    if (!current.task || current.task.id !== rec.taskId) { clearRecord(); return null; }
    return rec;
  }

  function setArc(remainingPercent) {
    const pct = Math.max(0, Math.min(100, remainingPercent));
    countdownArcEl.setAttribute("stroke-dasharray", `${pct} ${100 - pct}`);
    countdownArcEl.setAttribute("stroke-dashoffset", "0");
  }

  // ── 到点提醒：标题闪烁（视觉，必现）+ beep/Notification（锦上添花，
  //    任一降级都不许报 console 错误） ──────────────────────────────────
  let flashHandle = null;
  let savedTitle = null;
  let effectsStarted = false;

  function startTimeUpEffects() {
    countdownOverlayEl.classList.add("is-time-up");
    if (!flashHandle) {
      savedTitle = document.title;
      let toggled = false;
      flashHandle = setInterval(() => {
        toggled = !toggled;
        document.title = toggled ? "⏰ 时间到！" : savedTitle;
      }, 900);
    }
    if (!effectsStarted) {
      effectsStarted = true;
      playBeep();
      sendNotification();
    }
  }
  function stopTimeUpEffects() {
    countdownOverlayEl.classList.remove("is-time-up");
    if (flashHandle) {
      clearInterval(flashHandle);
      flashHandle = null;
      if (savedTitle !== null) document.title = savedTitle;
      savedTitle = null;
    }
    effectsStarted = false;
  }

  // Web Audio beep：**绝不因授权/策略失败报 console 错误**——同步构造与异步
  // resume() 两层都包了 try/catch 与 .catch()，任何一层失败都直接吞掉，
  // 纯视觉提示（标题闪烁+表芯变色）已经在 startTimeUpEffects() 里独立生效。
  function playBeep() {
    try {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      if (!AudioCtx) return;
      const ctx = new AudioCtx();
      const emit = () => {
        try {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.frequency.value = 880;
          gain.gain.value = 0.15;
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.start();
          osc.stop(ctx.currentTime + 0.35);
          osc.addEventListener("ended", () => { try { ctx.close(); } catch (err) { /* 忽略 */ } });
        } catch (err) { /* 合成失败：忽略，不影响视觉提示 */ }
      };
      if (ctx.state === "suspended") {
        ctx.resume().then(emit).catch(() => { /* 没有用户手势授权：静默降级 */ });
      } else {
        emit();
      }
    } catch (err) {
      // AudioContext 构造失败（浏览器不支持/策略拦截）：静默降级，不报错。
    }
  }

  // Web Notification：只在**已经**拿到权限时发，不在到点这一刻代为
  // requestPermission()（那需要用户手势上下文，自动请求既不合规也常被拒）。
  function sendNotification() {
    try {
      if (!("Notification" in window)) return;
      if (Notification.permission !== "granted") return;
      const current = window.ringCurrentState;
      const taskName = (current && current.task && current.task.name) || "";
      new Notification("时间到", { body: taskName ? `『${taskName}』倒计时结束` : "倒计时结束" });
    } catch (err) {
      // 通知构造失败：静默降级，不报错。
    }
  }

  // ── 到点自动停：调用与「停止并记录」完全相同的 window.stopTimer() ─────
  let timeUpHandled = false;
  let lastStopAttemptAt = 0;
  function handleTimeUp() {
    startTimeUpEffects();
    if (timeUpHandled) return;
    const now = Date.now();
    if (now - lastStopAttemptAt < RETRY_COOLDOWN_MS) return;
    lastStopAttemptAt = now;
    timeUpHandled = true;
    window.stopTimer().then(result => {
      if (result && result.ok) {
        clearRecord(); // 成功：下一次 tick determineActiveRecord() 会因 running=false 自然退出倒计时展示
      } else {
        timeUpHandled = false; // 失败：允许过完冷却期后重试，见 RETRY_COOLDOWN_MS
      }
    });
  }

  // ── 主循环：1 秒一次，本地计算，不发任何网络请求 ─────────────────────
  function tick() {
    syncStopwatchStartVisibility();
    syncStartCountdownEnabled();

    const current = window.ringCurrentState;
    const running = Boolean(current && current.running);
    modeTabsEl.hidden = running;
    countdownPickerEl.hidden = running || activeMode !== "countdown";

    const rec = determineActiveRecord();
    if (!rec) {
      if (!countdownOverlayEl.hidden) {
        countdownOverlayEl.hidden = true;
        chronoCenterEl.hidden = false;
      }
      stopTimeUpEffects();
      return;
    }

    chronoCenterEl.hidden = true;
    countdownOverlayEl.hidden = false;
    const remainingMs = rec.endAt - Date.now();
    const remainingSeconds = Math.ceil(Math.max(0, remainingMs) / 1000);
    countdownRemainingEl.textContent = formatRemaining(remainingSeconds);
    countdownTaskNameEl.textContent = (current.task && current.task.name) || "";
    setArc(rec.targetSeconds > 0 ? (remainingMs / (rec.targetSeconds * 1000)) * 100 : 0);

    if (remainingMs <= 0) {
      handleTimeUp();
    } else {
      stopTimeUpEffects();
    }
  }

  tick();
  setInterval(tick, TICK_MS);
})();
