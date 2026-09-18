/**
 * ring-rename.js —— 计时台改名（2026-09-08 任务单）
 *
 * 干的事：正在计时的任务名（#running-task-name）点一下就能改，
 * 改完 PATCH /api/core/planner/tasks/{id}，再让 ring-instrument.js 重渲染。
 *
 * 为什么是**第五个独立 IIFE**、为什么用浮层而不是就地改：
 *   · ring 四文件各自闭包互不可见，跨文件只走 window.*（见 ring-controls.js
 *     顶部的分工表）。本文件依赖三个已导出的东西，不新增耦合：
 *       window.patchCore        ← ring-controls.js（2026-09-08 新增导出）
 *       window.ringCurrentState ← ring-instrument.js（当前计时对象，带 task.id）
 *       window.fetchAndRender   ← ring-instrument.js（改完刷新的唯一入口）
 *   · #running-task-name 的 textContent **每一轮轮询都被 renderRunningCenter
 *     覆写**，所以编辑框不能替换那个节点，只能盖在它上面。这里照抄
 *     #countdown-overlay 已经立过的规矩：同为 .center 的兄弟层，靠 hidden
 *     互斥，不进 #chrono-center 的 innerHTML 重建逻辑（那是 instrument 的活）。
 *
 * 只写 planner，绝不碰 events（events 是只追加的事实账本）。
 */
(function () {
  "use strict";

  const overlayEl = document.getElementById("rename-overlay");
  const inputEl = document.getElementById("rename-input");
  const saveEl = document.getElementById("rename-save");
  const cancelEl = document.getElementById("rename-cancel");
  const errorEl = document.getElementById("rename-error");
  const centerEl = document.getElementById("chrono-center");
  const openBtnEl = document.getElementById("rename-open-btn");
  if (!overlayEl || !inputEl || !saveEl || !cancelEl || !errorEl || !centerEl) return;

  let editingTaskId = null;

  function showError(message) { errorEl.textContent = message; errorEl.hidden = false; }
  function clearError() { errorEl.hidden = true; errorEl.textContent = ""; }

  function close() {
    editingTaskId = null;
    overlayEl.hidden = true;
    centerEl.hidden = false;
    clearError();
    saveEl.disabled = false;
    inputEl.disabled = false;
  }

  function open() {
    const current = window.ringCurrentState;
    // 没在计时就没有可改的任务——按钮本身也只在运行态才存在，这里是第二道闸。
    if (!current || !current.running || !current.task || !current.task.id) return;
    editingTaskId = current.task.id;
    inputEl.value = current.task.name || "";
    clearError();
    centerEl.hidden = true;   // 与 #countdown-overlay 同一套互斥规矩：任一时刻只一个可见
    overlayEl.hidden = false;
    inputEl.focus();
    inputEl.select();
  }

  async function save() {
    const name = inputEl.value.trim();
    if (!name) { showError("任务名不能为空"); inputEl.focus(); return; }
    const current = window.ringCurrentState;
    // 编辑期间计时被别处停掉/换了任务：这时候提交会改到错误的任务上，宁可作废。
    if (!editingTaskId || !current || !current.task || current.task.id !== editingTaskId) {
      showError("正在计时的任务已经变了，改名作废，请重开");
      return;
    }
    if (name === (current.task.name || "")) { close(); return; }
    saveEl.disabled = true;
    inputEl.disabled = true;
    const result = await window.patchCore(
      "/api/core/planner/tasks/" + encodeURIComponent(editingTaskId), { name: name });
    if (!result.ok) {
      saveEl.disabled = false;
      inputEl.disabled = false;
      showError(result.message);
      return;
    }
    close();
    await window.fetchAndRender();
  }

  // 主入口：控件区那个「改任务名」按钮（人类 2026-09-08：「做一个显眼的改名
  // 按钮」——只能点圆环里那行小字太隐蔽）。它长在 #controls-row 里，那个容器的
  // hidden 由 ring-instrument.js 按运行态切，所以按钮天然只在计时中出现。
  if (openBtnEl) openBtnEl.addEventListener("click", open);

  // 次入口：直接点圆环里的任务名。保留是因为它是"所见即所改"，比先找按钮快。
  // 事件委托到 #chrono-center：#running-task-name 是 renderRunningCenter 用
  // innerHTML 现建的，直接给它挂监听会在每次中心态切换后失效，而在它身上留
  // 钩子又要改 ring-instrument.js。委托两头都不碰。
  centerEl.addEventListener("click", function (ev) {
    if (!ev.target.closest("#running-task-name")) return;
    open();
  });
  saveEl.addEventListener("click", save);
  cancelEl.addEventListener("click", close);
  inputEl.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter") { ev.preventDefault(); save(); }
    else if (ev.key === "Escape") { ev.preventDefault(); close(); }
  });
})();
