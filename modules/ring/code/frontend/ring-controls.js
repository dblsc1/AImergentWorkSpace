/* ring/frontend · ring-controls.js —— cockpit-v1 改造（2026-08-08）。
 *
 * 控件层：两级选择器（项目→任务）、F-RING-6 前置提示、F-RING-4 URL 预选、
 * 开始/停止/取消三个写操作按钮。拆出这一半是因为整份仪器逻辑合并后单文件
 * 冲到 585 行、超了单文件 500 行上限——拆分点选在「选择你要计的时」与
 * 「画表盘 + 判断今天数据」这两件事的自然边界上，不是硬切。
 *
 * 与 ring-instrument.js / ring-countdown.js 的分工（纯静态多 <script src>
 * 顺序加载共享全局作用域，不引入模块系统，保持 R9 零构建；跨文件调用一律走
 * 显式 `window.*` 挂载，不依赖闭包共享——三个 IIFE 各自的词法作用域互不可见）：
 *   本文件 → window.onStartClicked、window.populateTaskOptions、
 *           window.startTimer、window.stopTimer、window.postCore、window.patchCore
 *           （postCore 是 2026-08-19 补登新增导出，见下方 ring-backfill.js 分工；
 *             patchCore 是 2026-09-08 计时台改名新增导出，见 ring-rename.js）
 *   ring-instrument.js → window.fetchAndRender、window.refreshIdleTodayDisplay、
 *                        window.renderContributionRing、window.ringCurrentState
 *   ring-countdown.js  → 不对外挂名字（纯监听者，不被别的文件调用）
 * 两边互相只读对方暴露的这几个名字，不猜对方内部实现。
 *
 * **2026-08-09 倒计时的最小重构**：`startTimer(taskId)` / `stopTimer()`
 * 从原来揉在按钮 click 处理器里的一次性代码抽成独立函数并挂 window——
 * 倒计时模块「开始倒计时」调用的是与「开始计时」完全相同的
 * `POST /api/core/timer/start`（后端不区分正/倒计时，零新增接口），
 * 「到点自动停」调用的是与「停止并记录」完全相同的 `POST /api/core/timer/stop`。
 * 按钮自身的 disabled/文案切换仍留在各自的 click 处理器里，不进这两个函数
 * ——倒计时模块没有这两个按钮，不该被强迫接受它们的 UI 副作用。
 *
 * 数据源：GET /api/core/views/tree（两级选择器 + dependsOn 前置提示）、
 * POST /api/core/timer/start|stop|cancel（写入面）。
 */

(function () {
  "use strict";
  // 站点前缀（gateway.v1）：网关往页面注入 HONEYCOMB_BASE，整站挂在子路径下时
  // 所有请求与跳转都从它拼。没注入（单测、直接打开文件）就是 "/"。
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";

  // ── DOM 引用 ──────────────────────────────────────────────────────
  const projectSelectEl = document.getElementById("project-select");
  const taskSelectEl = document.getElementById("task-select");
  const dependsHintEl = document.getElementById("depends-hint");
  const stopBtnEl = document.getElementById("stop-btn");
  const cancelOpenBtnEl = document.getElementById("cancel-open-btn");
  const timerErrorEl = document.getElementById("timer-error");
  const cancelDialogEl = document.getElementById("cancel-dialog");
  const cancelDialogBackEl = document.getElementById("cancel-dialog-back");
  const cancelDialogConfirmEl = document.getElementById("cancel-dialog-confirm");

  function showTimerError(message) {
    timerErrorEl.textContent = message;
    timerErrorEl.hidden = false;
  }
  function clearTimerError() {
    timerErrorEl.hidden = true;
    timerErrorEl.textContent = "";
  }

  // 统一请求封装：从不 throw，detail 原样透传（R8 沿用）。
  // 2026-09-08：方法提成参数，好让改名走 PATCH。postCore/patchCore 是它的两层
  // 皮，语义和返回结构逐字节相同 —— 不要为 PATCH 再写第二个 fetch 包装。
  async function requestCore(method, path, body) {
    let res;
    try {
      const init = { method: method };
      if (body !== undefined) {
        init.headers = { "Content-Type": "application/json" };
        init.body = JSON.stringify(body);
      }
      res = await fetch(path, init);
    } catch (err) {
      return { ok: false, message: "网络请求失败：" + ((err && err.message) || String(err)) };
    }
    let payload = null;
    try { payload = await res.json(); } catch (err) { payload = null; }
    if (res.ok) {
      // 计时写成功 → 告诉顶栏立刻重拉（见 navbar.js 的 honeycomb:timer-changed）
      if (path.indexOf("api/core/timer/") !== -1) window.dispatchEvent(new Event("honeycomb:timer-changed"));
      return { ok: true, data: payload };
    }
    const message = (payload && payload.detail) ? payload.detail : ("请求失败（HTTP " + res.status + "）");
    return { ok: false, message: message };
  }
  function postCore(path, body) { return requestCore("POST", path, body); }
  function patchCore(path, body) { return requestCore("PATCH", path, body); }

  // ── 任务树：两级选择器 + dependsOn 前置提示的唯一数据源 ─────────────
  let treeData = { zones: [], projects: [] };
  let taskIndex = {}; // taskId -> {task, project}

  function buildTaskIndex(tree) {
    const idx = {};
    (tree.projects || []).forEach(project => {
      (project.tasks || []).forEach(task => { idx[task.id] = { task, project }; });
    });
    return idx;
  }

  function populateProjectOptions() {
    projectSelectEl.textContent = "";
    projectSelectEl.appendChild(new Option("选择项目…", ""));
    (treeData.projects || []).forEach(project => {
      projectSelectEl.appendChild(new Option(project.name, project.id));
    });
  }

  // 零任务项目也要出现在任务选择器里，作为不可选提示行（2026-08-03 U3/U4
  // 的既有行为，两级选择改造后原样保留，只是从「跨项目 optgroup」收窄成
  // 「当前已选项目」范围内的判断）。ring-instrument.js 的 applyState() 在
  // 同步运行态选中值时也要调它，所以挂到 window 上（见文件头分工说明）。
  function populateTaskOptions(projectId) {
    taskSelectEl.textContent = "";
    taskSelectEl.appendChild(new Option("选择任务…", ""));
    const project = (treeData.projects || []).find(p => p.id === projectId);
    taskSelectEl.disabled = !project;
    if (!project) { syncStartEnabled(); updateDependsHint(); return; }
    const tasks = project.tasks || [];
    if (tasks.length === 0) {
      const hint = new Option("（还没有任务 —— 去「任务」页给它加一个）", "");
      hint.disabled = true;
      hint.className = "task-option-empty";
      taskSelectEl.appendChild(hint);
    } else {
      tasks.forEach(task => taskSelectEl.appendChild(new Option(task.name, task.id)));
    }
    syncStartEnabled();
    updateDependsHint();
  }

  function syncStartEnabled() {
    const hasTask = Boolean(taskSelectEl.value);
    const startBig = document.getElementById("start-big-btn");
    if (startBig) startBig.disabled = !hasTask;
  }

  // F-RING-6：前置提示。**防御式**——dependsOn/done 任一字段缺失都只表现为
  // 「不显示」，绝不抛错（nexus-core 该字段并行开发中，F-API-1 落地前后都不能炸）。
  function isUnmetDependency(depTask) {
    if (!depTask) return false; // 依赖的任务查不到（被删/引用坏）：静默当没有这条依赖
    if (typeof depTask.done !== "boolean") return false; // done 字段缺失：不知道，不敢断言未完成
    return depTask.done === false;
  }
  function updateDependsHint() {
    const entry = taskIndex[taskSelectEl.value];
    const dependsOn = entry && Array.isArray(entry.task.dependsOn) ? entry.task.dependsOn : null;
    if (!dependsOn || dependsOn.length === 0) { dependsHintEl.hidden = true; return; }
    const unmetNames = dependsOn
      .map(depId => taskIndex[depId] && taskIndex[depId].task)
      .filter(isUnmetDependency)
      .map(task => task.name);
    if (unmetNames.length === 0) { dependsHintEl.hidden = true; return; }
    dependsHintEl.textContent = `前置『${unmetNames.join("、")}』未完成`;
    dependsHintEl.hidden = false;
  }

  async function loadTree() {
    try {
      const res = await fetch(BASE + "api/core/views/tree");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      treeData = await res.json();
    } catch (err) {
      showTimerError("任务列表加载失败：无法获取分区/项目/任务数据，请确认后端服务已启动。");
      return;
    }
    taskIndex = buildTaskIndex(treeData);
    populateProjectOptions();
    applyUrlPreselect();
  }

  // F-RING-4：?task=<id> 预选项目与任务；id 无效（查不到、或选完之后 select
  // 没接受这个 value）一律静默忽略，落回空闲态的默认展示——不报错、不提示。
  function applyUrlPreselect() {
    let taskId = "";
    try { taskId = new URL(window.location.href).searchParams.get("task") || ""; }
    catch (err) { return; }
    if (!taskId) return;
    const entry = taskIndex[taskId];
    if (!entry) return;
    projectSelectEl.value = entry.project.id;
    if (projectSelectEl.value !== entry.project.id) return; // select 没接受这个项目 id
    populateTaskOptions(entry.project.id);
    taskSelectEl.value = taskId;
    if (taskSelectEl.value !== taskId) return; // select 没接受（例如任务被过滤掉）
    updateDependsHint();
    window.refreshIdleTodayDisplay(); // 预选没走 change 事件（程序设值不触发），手动同步一次
  }

  projectSelectEl.addEventListener("change", () => {
    populateTaskOptions(projectSelectEl.value);
    window.refreshIdleTodayDisplay(); // 空闲态表芯「今天 · N 分」联动，不等下一次轮询
  });
  taskSelectEl.addEventListener("change", () => {
    syncStartEnabled();
    updateDependsHint();
  });

  // ── 按钮：开始 / 停止并记录 / 取消，不记录（+确认弹层，F-RING-2） ──
  // startTimer/stopTimer 挂 window 是因为 ring-countdown.js（开始倒计时 /
  // 到点自动停）要复用同一条网络路径——后端不区分正/倒计时，两边必须打
  // 同一个接口，不能各自维护一份 fetch 逻辑（迟早漂移）。
  async function startTimer(taskId) {
    clearTimerError();
    const result = await postCore(BASE + "api/core/timer/start", { taskId: taskId });
    if (!result.ok) { showTimerError(result.message); return result; }
    await window.fetchAndRender();
    return result;
  }
  async function stopTimer() {
    clearTimerError();
    const result = await postCore(BASE + "api/core/timer/stop", undefined);
    if (!result.ok) { showTimerError(result.message); return result; }
    await window.fetchAndRender();
    return result;
  }

  // onStartClicked 挂 window 是因为它绑定在 ring-instrument.js 用 innerHTML
  // 动态生成的 #start-big-btn 上（那段 DOM 属于表芯，天然是 instrument 的活）。
  async function onStartClicked() {
    const taskId = taskSelectEl.value;
    if (!taskId) return;
    const btn = document.getElementById("start-big-btn");
    if (btn) { btn.disabled = true; btn.textContent = "开始中…"; }
    const result = await startTimer(taskId);
    if (!result.ok && btn) { btn.disabled = false; btn.textContent = "开始计时"; }
  }

  stopBtnEl.addEventListener("click", async () => {
    stopBtnEl.disabled = true;
    stopBtnEl.textContent = "停止中…";
    await stopTimer();
    stopBtnEl.textContent = "停止并记录";
    stopBtnEl.disabled = false;
  });

  cancelOpenBtnEl.addEventListener("click", () => {
    clearTimerError();
    cancelDialogEl.showModal();
  });
  cancelDialogBackEl.addEventListener("click", () => cancelDialogEl.close());
  cancelDialogEl.addEventListener("cancel", () => {}); // Escape/backdrop 走浏览器默认关闭，不发任何请求

  cancelDialogConfirmEl.addEventListener("click", async () => {
    cancelDialogConfirmEl.disabled = true;
    const result = await postCore(BASE + "api/core/timer/cancel", undefined);
    cancelDialogConfirmEl.disabled = false;
    cancelDialogEl.close();
    if (!result.ok) { showTimerError(result.message); return; }
    await window.fetchAndRender();
  });

  window.onStartClicked = onStartClicked;
  window.populateTaskOptions = populateTaskOptions;
  window.startTimer = startTimer;
  window.stopTimer = stopTimer;
  window.postCore = postCore; // 2026-08-19 补登新增导出——ring-backfill.js
  window.patchCore = patchCore; // 2026-09-08 计时台改名新增导出——ring-rename.js
                               // 复用同一份「从不 throw、detail 原样透传」的请求封装，
                               // 不为补登另写一份 fetch 逻辑（迟早漂移）。

  loadTree();
})();
