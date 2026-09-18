/* ring/frontend · ring-instrument.js —— cockpit-v1 改造（2026-08-08 任务单，
 * 含后补的「今天」数据口径落地一轮）。
 *
 * 表盘层：轮询、圆环三段式渲染、表芯（运行态秒跳自增 / 空闲态「今天 · N 分」
 * 大按钮）、图例。控件层（两级选择器、dependsOn 提示、URL 预选、开始/停止/
 * 取消按钮）在 ring-controls.js——两份逻辑本就该共享同一份状态（当前是否
 * 在跑、跑的是哪个任务），但单文件写到 585 行、顶穿了单文件 500 行上限，
 * 拆分点选在「选择你要计的时」与「画表盘 + 判今天数据」的自然边界上。
 * 跨文件调用一律走显式 `window.*` 挂载（三个 IIFE 各自的词法作用域互不
 * 可见，纯静态多 `<script src>` 顺序加载，不引入模块系统，保持 R9 零构建）：
 *   本文件 → window.fetchAndRender、window.refreshIdleTodayDisplay、
 *           window.renderContributionRing、window.ringCurrentState
 *   ring-controls.js → window.onStartClicked、window.populateTaskOptions、
 *                      window.startTimer、window.stopTimer
 *
 * **2026-08-09 倒计时任务单**：`window.ringCurrentState` 是本文件唯一为它
 * 新加的东西——每次轮询拿到的最新 `views/current` payload（拿不到时为
 * `null`），供 ring-countdown.js 判断"当前在跑的会话是不是我本地记的那个
 * 倒计时"（比对 `taskId`）。除这一行赋值外本文件其余渲染逻辑不改一个字——
 * 倒计时的显示切换/进度弧/到点自动停全在 ring-countdown.js 里自成一套，
 * 不侵入这里已经调好的今日切片/首载动画/重绘节流逻辑。
 *
 * 数据源（module_docs/contract.md consumes）：
 *   GET  /api/core/views/current  计时状态（是否在跑/跑哪个/开始时刻），7 秒轮询
 *   GET  /api/core/views/gantt    「今天」范围的任务级逐日事实数据源，同一 7 秒轮询
 *        （v1.1 新增 tasks[].actual，人类裁决不为 ring 开新端点，直接复用这条
 *        既有读端——ring 不消费它的计划/依赖层，只取 actual 与 today）
 *
 * **「今天」判据只认服务端 `views/gantt.today` 字符串，绝不用客户端 `new Date()`
 * 拼日期**——时区归日错一小时就是错一天，本项目已经在别处踩过这个类的坑
 * （见 nexus-core 契约「日界与时区」）。`views/gantt` 不可达或形状不对（缺
 * `today`/`projects`/某项目缺 `tasks`）时静默退回终身累计两段展示，不报错、
 * 不打断 `views/current` 那一半的正常渲染——两条数据源故意做成互不阻塞。
 */

(function () {
  "use strict";

  const POLL_INTERVAL_MS = 7000; // 5–10 秒一次，取中间值，沿用既有值

  // ── DOM 引用 ──────────────────────────────────────────────────────
  const ringWrapEl = document.getElementById("ring-wrap");
  const statusEl = document.getElementById("status-message");
  const chronoCenterEl = document.getElementById("chrono-center");
  const chronoSvgEl = document.getElementById("chrono-svg");
  const segCurrentEl = document.getElementById("seg-current");
  const segSecondEl = document.getElementById("seg-second");
  const segThirdEl = document.getElementById("seg-third");
  const projectSelectEl = document.getElementById("project-select");
  const taskSelectEl = document.getElementById("task-select");
  const dependsHintEl = document.getElementById("depends-hint");
  const controlsRowEl = document.getElementById("controls-row");
  const legendEl = document.getElementById("legend");
  const legendTitleEl = document.getElementById("legend-title");
  const legendCurrentNameEl = document.getElementById("legend-current-name");
  const legendCurrentValueEl = document.getElementById("legend-current-value");
  const legendSecondRowEl = document.getElementById("legend-second-row");
  const legendSecondNameEl = document.getElementById("legend-second-name");
  const legendSecondValueEl = document.getElementById("legend-second-value");
  const legendThirdRowEl = document.getElementById("legend-third-row");
  const legendThirdValueEl = document.getElementById("legend-third-value");

  // ── 小工具 ────────────────────────────────────────────────────────
  function formatDuration(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    const h = Math.floor(value / 3600).toString().padStart(2, "0");
    const m = Math.floor((value % 3600) / 60).toString().padStart(2, "0");
    const s = (value % 60).toString().padStart(2, "0");
    return `${h}:${m}:${s}`;
  }
  function formatMinutes(seconds) {
    return `${Math.round((Number(seconds) || 0) / 60)} 分`;
  }
  function showStatus(message, isError) {
    ringWrapEl.hidden = true;
    statusEl.textContent = message;
    statusEl.classList.toggle("is-error", Boolean(isError));
    statusEl.hidden = false;
  }
  function showRing() {
    ringWrapEl.hidden = false;
    statusEl.hidden = true;
    statusEl.classList.remove("is-error");
  }

  // ── 「今天」数据（GET /api/core/views/gantt，后补的一轮）───────────
  // 人类裁决：不为 ring 开新端点，直接复用 nexus-core v1.1 已经上线的
  // views/gantt——它的 tasks[].actual 本来是给甘特任务行用的，形状恰好也是
  // ring 需要的「任务 × 日 → 秒」。ring 完全不碰它的 plan/dependsOn 层。
  //
  // **「今天」只认服务端 gantt.today 字符串**，绝不用 `new Date()` 在客户端
  // 拼日期——时区/时钟不准会让「今天」在用户眼里变成「昨天」或「明天」，
  // 这类错本项目已经在别处（nexus-core 的日界与时区）踩过一次。
  let todayByProject = null; // projectId -> {tasks:{taskId:{name,seconds}}, totalSeconds} | null=不可用

  // 防御式：整个响应缺 today/projects 就整体判不可用；单个项目缺 tasks 数组
  // 只让那一个项目判不可用，不拖累其它项目（nexus-core 灰度上线时可能出现
  // 新旧数据混杂的项目）。
  function computeTodaySnapshot(gantt) {
    if (!gantt || typeof gantt.today !== "string" || !Array.isArray(gantt.projects)) return null;
    const byProject = {};
    gantt.projects.forEach(project => {
      if (!project || typeof project.id !== "string" || !Array.isArray(project.tasks)) return;
      const tasks = {};
      let totalSeconds = 0;
      project.tasks.forEach(task => {
        if (!task || typeof task.id !== "string") return;
        const entries = Array.isArray(task.actual) ? task.actual : [];
        const todayEntry = entries.find(a => a && a.date === gantt.today);
        const seconds = todayEntry ? Math.max(0, Number(todayEntry.seconds) || 0) : 0;
        tasks[task.id] = { name: String(task.name || ""), seconds };
        totalSeconds += seconds;
      });
      byProject[project.id] = { tasks, totalSeconds };
    });
    return byProject;
  }

  async function loadGanttToday() {
    try {
      const res = await fetch("/api/core/views/gantt");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      todayByProject = computeTodaySnapshot(await res.json());
    } catch (err) {
      // 静默退回：不弹错误提示，不影响 views/current 那一半的渲染（见文件头）。
      todayByProject = null;
    }
  }

  // 项目今日合计秒数。返回 null=该项目的今日数据不可用（整体拿不到 gantt，
  // 或该项目本身缺 tasks 字段）——调用方据此决定要不要退回终身累计展示，
  // **不把 null 当成 0**（0 是「今天真的是 0 分」的真实值）。
  function projectTodaySeconds(projectId) {
    if (!todayByProject) return null;
    const entry = todayByProject[projectId];
    return entry ? entry.totalSeconds : null;
  }
  function globalTodaySeconds() {
    if (!todayByProject) return null;
    return Object.values(todayByProject).reduce((sum, p) => sum + p.totalSeconds, 0);
  }

  // 三档拆分：当前任务（深）/ 次高任务（中）/ 其余任务合计（浅）。项目任务数
  // 不足 2 或 3 时对应档位标记 null，调用方据此决定要不要露出那一行图例——
  // 契约的 --fact-s3 本来就是「装饰档」，任务够多才有意义，硬凑一档「其他
  // 任务 0 分」是噪音，不是诚实。
  function computeTierBreakdown(projectId, currentTaskId) {
    const entry = todayByProject && todayByProject[projectId];
    if (!entry) return null;
    const others = Object.entries(entry.tasks)
      .filter(([taskId]) => taskId !== currentTaskId)
      .map(([taskId, t]) => ({ id: taskId, name: t.name, seconds: t.seconds }))
      .sort((a, b) => b.seconds - a.seconds);
    return {
      totalSeconds: entry.totalSeconds,
      currentSeconds: (entry.tasks[currentTaskId] && entry.tasks[currentTaskId].seconds) || 0,
      second: others[0] || null,
      thirdSeconds: others.length > 1 ? others.slice(1).reduce((s, t) => s + t.seconds, 0) : null,
    };
  }

  // ── 圆环渲染：三段式（当前任务=深/次高任务=中/其余任务合计=浅），今天
  //    范围数据不可用时退回终身累计两段式（当前任务/项目其余）。
  //    pathLength="100" 让百分数直接当 dasharray 单位用，不用算周长（旧版
  //    renderContributionRing 的 circumference 换算这版整段退役）。
  function setSeg(el, startPercent, lengthPercent) {
    el.setAttribute("stroke-dasharray", `${lengthPercent} ${100 - lengthPercent}`);
    el.setAttribute("stroke-dashoffset", String(-startPercent));
  }

  // F-RING-3：分段动画仅首载 350ms，此后（包括真实数据变化）一律直接就位——
  // 与旧版「每次真变化都播 900ms」的行为不同，是本轮按新设计文案的刻意简化，
  // 副作用是彻底避开旧版在动画计数上反复踩的 flaky 坑（见 handoff.md 避坑段）。
  let ringMounted = false;
  function drawSegments(currentPercent, secondPercent, thirdPercent) {
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const paint = (cur, sec, third) => {
      setSeg(segCurrentEl, 0, cur);
      setSeg(segSecondEl, cur, sec);
      setSeg(segThirdEl, cur + sec, third);
    };
    if (!ringMounted && !reduceMotion) {
      [segCurrentEl, segSecondEl, segThirdEl].forEach(el => el.classList.add("is-first-load"));
      paint(0, 0, 0);
      void segCurrentEl.getBoundingClientRect(); // 强制提交 0 态，否则下一帧的赋值不会触发过渡
      requestAnimationFrame(() => paint(currentPercent, secondPercent, thirdPercent));
      setTimeout(() => {
        [segCurrentEl, segSecondEl, segThirdEl].forEach(el => el.classList.remove("is-first-load"));
      }, 400);
    } else {
      paint(currentPercent, secondPercent, thirdPercent);
    }
    ringMounted = true;
  }

  // ── 表芯：运行态秒跳本地自增（F-RING-3），空闲态「今天 · N 分」大按钮 ──
  let centerMode = null; // "idle" | "running"
  let elapsedTimerHandle = null;
  let sessionStartMs = null;

  function stopElapsedTicker() {
    if (elapsedTimerHandle) { clearInterval(elapsedTimerHandle); elapsedTimerHandle = null; }
    sessionStartMs = null;
  }
  function tickElapsedDisplay() {
    const el = document.getElementById("elapsed-display");
    if (!el || sessionStartMs == null) return;
    // 「继续」出来的那一段要接着之前的时间数（ring-pause.js 记累计；只是显示，入账仍按段）。
    const carry = window.ringCarrySeconds ? window.ringCarrySeconds() : 0;
    el.textContent = formatDuration((Date.now() - sessionStartMs) / 1000 + carry);
  }
  function startElapsedTicker(sessionStartAt) {
    const nextStartMs = new Date(sessionStartAt).getTime();
    if (sessionStartMs === nextStartMs && elapsedTimerHandle) return; // 同一段会话，别重开定时器
    sessionStartMs = nextStartMs;
    if (!elapsedTimerHandle) elapsedTimerHandle = setInterval(tickElapsedDisplay, 1000);
    tickElapsedDisplay();
  }

  // 空闲态表芯：有「今天」数据就显示「今天 · N 分」（N=选中项目今日合计，
  // 未选项目退回全部项目今日合计；0 也如实显示，不是隐藏——明文要求）；
  // views/gantt 不可用时退回「当前没有进行中的计时」的纯文案，不编数字。
  function idleTodayMinutesLabel() {
    const projectId = projectSelectEl.value;
    const seconds = projectId ? projectTodaySeconds(projectId) : globalTodaySeconds();
    if (seconds === null) return null; // 今天数据不可用（gantt 不可达/形状不对）
    return `今天 · ${Math.round(seconds / 60)} 分`;
  }

  function renderIdleCenter() {
    if (centerMode === "idle") return;
    centerMode = "idle";
    stopElapsedTicker();
    chronoCenterEl.innerHTML =
      '<p class="idle-total" id="idle-total" hidden></p>' +
      '<p class="idle-caption" id="idle-caption">当前没有进行中的计时</p>' +
      '<button type="button" class="start-big" id="start-big-btn" disabled>开始计时</button>';
    document.getElementById("start-big-btn").addEventListener("click", window.onStartClicked);
    document.getElementById("start-big-btn").disabled = !taskSelectEl.value;
    refreshIdleTodayDisplay();
  }

  // 单独拆出来是因为它要在多处触发：进入空闲态时、项目选择器改变时（不等
  // 7 秒轮询，ring-controls.js 的 change 监听器会调它）、每次轮询拿到新
  // gantt 数据时——共享同一份判断逻辑，故挂到 window 供另一个文件调用。
  function refreshIdleTodayDisplay() {
    if (centerMode !== "idle") return;
    const totalEl = document.getElementById("idle-total");
    if (!totalEl) return;
    const label = idleTodayMinutesLabel();
    totalEl.hidden = label === null;
    if (label !== null) totalEl.textContent = label;
    chronoSvgEl.setAttribute(
      "aria-label",
      label === null ? "计时圆环，当前没有进行中的计时" : `计时圆环，${label}`
    );
  }

  function renderRunningCenter(current) {
    if (centerMode !== "running") {
      centerMode = "running";
      chronoCenterEl.innerHTML =
        '<span class="elapsed" id="elapsed-display">00:00:00</span>' +
        '<span class="task-name" id="running-task-name"></span>';
    }
    document.getElementById("running-task-name").textContent =
      `${current.task.name} · ${current.project.name}`;
    startElapsedTicker(current.sessionStartAt);
    chronoSvgEl.setAttribute(
      "aria-label",
      `计时圆环，正在为『${current.task.name}』计时，占项目累计 ${current.task.shareOfProject}%`
    );
  }

  // ── 分段重绘节流：仅在渲染依赖的值真变化时才重绘（保持 2026-08-03 已修
  //    行为，F-RING-3）。刻意不对整个响应体深比较——sessionStartAt 之类的
  //    字段每次轮询都在动，深比较会让这条护栏形同虚设。key 里带上「今天
  //    数据是否可用」（切换展示模式本身就是渲染依赖的值），可用时再带上
  //    三档的具体秒数。
  let lastRingKey = null;
  function ringKey(current, tiers) {
    if (tiers) {
      return JSON.stringify([
        "today", current.project.id, current.task.id,
        tiers.currentSeconds, tiers.second && tiers.second.id, tiers.second && tiers.second.seconds,
        tiers.thirdSeconds,
      ]);
    }
    return JSON.stringify(["lifetime", current.project.id, current.task.id, current.task.shareOfProject]);
  }

  function applyState(current) {
    const running = Boolean(current && current.running && current.project && current.task);
    if (!running) {
      lastRingKey = null;
      renderIdleCenter();
      refreshIdleTodayDisplay(); // 每次轮询都刷新一遍数字（renderIdleCenter 已挂载时会早退）
      controlsRowEl.hidden = true;
      legendEl.hidden = true;
      projectSelectEl.disabled = false;
      taskSelectEl.disabled = !projectSelectEl.value;
      return;
    }

    renderRunningCenter(current);
    controlsRowEl.hidden = false;
    dependsHintEl.hidden = true; // 录制中锁定选择器，前置提示不再是「下一步该干什么」的信息
    projectSelectEl.disabled = true;
    taskSelectEl.disabled = true;
    if (projectSelectEl.value !== current.project.id) {
      projectSelectEl.value = current.project.id;
      window.populateTaskOptions(current.project.id);
    }
    taskSelectEl.value = current.task.id;
    taskSelectEl.disabled = true; // populateTaskOptions 会顺手把它启用，运行态要再锁一次

    const tiers = computeTierBreakdown(current.project.id, current.task.id);
    const key = ringKey(current, tiers);
    if (key !== lastRingKey) {
      if (tiers) {
        // 今天数据可用：三段式，百分数以「项目今日合计」为分母；合计为 0
        // （今天还没有任何完成的会话）时三段全 0，环只剩空轨道，不强行画满。
        const base = tiers.totalSeconds > 0 ? tiers.totalSeconds : 0;
        const pct = seconds => (base > 0 ? Math.max(0, Math.min(100, (seconds / base) * 100)) : 0);
        drawSegments(pct(tiers.currentSeconds), pct(tiers.second ? tiers.second.seconds : 0),
                     pct(tiers.thirdSeconds || 0));
        legendTitleEl.textContent = "今日贡献";
        legendCurrentNameEl.textContent = current.task.name;
        legendCurrentValueEl.textContent = formatMinutes(tiers.currentSeconds);
        if (tiers.second) {
          legendSecondNameEl.textContent = tiers.second.name;
          legendSecondValueEl.textContent = formatMinutes(tiers.second.seconds);
          legendSecondRowEl.hidden = false;
        } else {
          legendSecondRowEl.hidden = true;
        }
        if (tiers.thirdSeconds !== null) {
          legendThirdValueEl.textContent = formatMinutes(tiers.thirdSeconds);
          legendThirdRowEl.hidden = false;
        } else {
          legendThirdRowEl.hidden = true;
        }
      } else {
        // 今天数据不可用（views/gantt 不可达/形状不对）：退回终身累计两段式，
        // 与上一轮行为完全一致，不报错、不打断这一半的渲染。
        const currentPercent = Math.max(0, Math.min(100, Number(current.task.shareOfProject) || 0));
        const restPercent = Math.max(0, 100 - currentPercent);
        drawSegments(currentPercent, restPercent, 0);
        legendTitleEl.textContent = "项目累计贡献";
        legendCurrentNameEl.textContent = current.task.name;
        legendCurrentValueEl.textContent = formatMinutes(current.task.totalSeconds);
        const restSeconds = Math.max(0, (current.project.totalSeconds || 0) - (current.task.totalSeconds || 0));
        legendSecondNameEl.textContent = "项目其余任务";
        legendSecondValueEl.textContent = formatMinutes(restSeconds);
        legendSecondRowEl.hidden = false;
        legendThirdRowEl.hidden = true;
      }
      lastRingKey = key;
    }
    legendEl.hidden = false;
  }

  // ── 轮询：GET /api/core/views/current（计时状态，必需）+
  //    GET /api/core/views/gantt（今天数据，可选，失败静默退回，见上）。
  //    两条互不阻塞：gantt 请求失败绝不能让 current 那一半也显示「连接失败」。
  async function fetchAndRender() {
    let current;
    try {
      const res = await fetch("/api/core/views/current");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      current = await res.json();
      window.ringCurrentState = current; // ring-countdown.js 的唯一数据入口
    } catch (err) {
      window.ringCurrentState = null; // 拿不到时别让倒计时模块拿着上一轮的陈旧数据瞎判断
      showStatus("连接失败：无法获取当前任务数据，请确认后端服务已启动。", true);
      return;
    }
    await loadGanttToday(); // 内部已吞掉所有失败，不会抛
    try {
      if (!current || current.running === false) {
        showRing();
        applyState(current);
        return;
      }
      if (!current.project || !current.task) {
        showStatus("数据格式错误：接口返回的形状与契约不一致（running=true 但缺 project/task）。", true);
        return;
      }
      showRing();
      applyState(current);
    } catch (err) {
      showStatus("数据格式错误：" + err.message, true);
    }
  }

  // ── 入口。渲染入口沿用契约里的旧名（module_docs/contract.md「渲染入口
  //    window.renderContributionRing(json)」只约束入口存在，未约束入参形状——
  //    F-RING-1 起 json 直接是 CurrentOut 本身，不再是上游中文键映射）。 ──
  window.renderContributionRing = applyState;
  window.fetchAndRender = fetchAndRender;
  window.refreshIdleTodayDisplay = refreshIdleTodayDisplay;

  fetchAndRender();
  setInterval(fetchAndRender, POLL_INTERVAL_MS);
})();
