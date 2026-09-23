/* ring/frontend · ring-backfill.js —— 补登入口（2026-08-19）。
 *
 * 「完成了但没计时」的事后补录：POST /api/core/timer/backfill。契约唯一事实源
 * `../nexus-core/module_docs/contract.md`「## 补登（规范性 · v1.8，backfill）」，
 * schema 见 `contract-schemas.md` 的 TimerBackfillIn/TimerBackfillOut——本文件
 * 严格照 schema 发字段，不发明、不宽松处理。
 *
 * 拆成独立文件：ring-controls.js 已经 243 行，塞进去容易顶穿单文件 500 行
 * 上限，照 ring-countdown.js 的先例拆分。纯静态多 <script src> 顺序加载共享
 * 全局作用域，不引入模块系统（保持 D18 零构建）；跨文件调用一律显式挂
 * window.*，不猜别的文件内部实现：
 *   本文件依赖 → window.postCore（ring-controls.js 导出——同一份
 *               「从不 throw、detail 原样透传」请求封装，不为补登另写一份）
 *   本文件不对外挂名字（纯监听者，不被别的文件调用，同 ring-countdown.js）
 *
 * 任务下拉走独立的 GET /api/core/views/tree 请求——与 ring-controls.js 消费
 * 同一个端点（"复用现有取任务列表的路径"指的是同一条契约端点，不是共享
 * 内部变量：三个 IIFE 的词法作用域互不可见，treeData 是 ring-controls.js
 * 的私有闭包变量，取不到也不该取）。
 *
 * **与活状态计时完全独立**：不读、不判断 `window.ringCurrentState.running`，
 * 入口按钮永远可点——契约明写补登不碰 `timer_state`，正在计时时也允许补登，
 * 前端不许自己加「必须先停表」的伪约束。入口按钮是 controls 区里独立于
 * #mode-tabs / #controls-row 的兄弟节点，两者的 hidden 切换（ring-countdown.js
 * / ring-instrument.js 按运行态互斥显隐）都碰不到它。
 *
 * **startAt 必须带时区偏移**（契约：不带偏移一律 400，不许猜/静默套用后端
 * 时区）。用浏览器 `Date.getTimezoneOffset()` 拼出本地偏移，绝不发裸的
 * `2026-08-18T14:30:00`。
 *
 * **三条显示规则**（契约要害，规格点名不许简化）：
 *   1. `duplicate:true` 必须显示「这段已经补过了」——绝不能显示成「已记录」，
 *      那是骗用户：落库的是**上一次**那条，这一次什么都没发生。
 *   2. 提交成功要回显服务端返回的 `date`（NEXUS_TZ 归日结果）——前端不自己
 *      算时区，两处算时区迟早不一致（同「日界与时区」节的既有教训）。
 *   3. 失败显示服务端 `detail` 原文——后端 7 条拒绝理由各不相同，吞成笼统
 *      的「失败了」等于让用户猜。
 */

(function () {
  "use strict";
  // 站点前缀（gateway.v1）：网关往页面注入 HONEYCOMB_BASE，整站挂在子路径下时
  // 所有请求与跳转都从它拼。没注入（单测、直接打开文件）就是 "/"。
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";

  const openBtnEl = document.getElementById("backfill-open-btn");
  const dialogEl = document.getElementById("backfill-dialog");
  const taskSelectEl = document.getElementById("backfill-task-select");
  const dateInputEl = document.getElementById("backfill-date");
  const timeInputEl = document.getElementById("backfill-time");
  const minutesInputEl = document.getElementById("backfill-minutes");
  const messageEl = document.getElementById("backfill-message");
  const submitBtnEl = document.getElementById("backfill-submit-btn");
  const cancelBtnEl = document.getElementById("backfill-cancel-btn");

  let treeLoaded = false; // 只在弹层第一次打开时拉一次，同一页面会话内不重复请求

  function showMessage(text, kind) {
    // kind: "error" | "duplicate" | "success" —— 三种视觉都要能一眼分清，
    // 但规则只锁死文案本身，不锁配色，配色走 CSS 类。
    messageEl.textContent = text;
    messageEl.hidden = false;
    messageEl.classList.toggle("is-error", kind === "error");
    messageEl.classList.toggle("is-duplicate", kind === "duplicate");
  }
  function clearMessage() {
    messageEl.hidden = true;
    messageEl.textContent = "";
    messageEl.classList.remove("is-error", "is-duplicate");
  }

  function populateTaskSelect(tree) {
    taskSelectEl.textContent = "";
    taskSelectEl.appendChild(new Option("选择任务…", ""));
    (tree.projects || []).forEach(project => {
      const tasks = project.tasks || [];
      if (tasks.length === 0) return; // 零任务项目在这个下拉里没有意义：补登必须挂具体任务
      const group = document.createElement("optgroup");
      group.label = project.name;
      tasks.forEach(task => group.appendChild(new Option(task.name, task.id)));
      taskSelectEl.appendChild(group);
    });
  }

  async function ensureTreeLoaded() {
    if (treeLoaded) return;
    taskSelectEl.textContent = "";
    taskSelectEl.appendChild(new Option("加载任务列表…", ""));
    try {
      const res = await fetch(BASE + "api/core/views/tree");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const tree = await res.json();
      populateTaskSelect(tree);
      treeLoaded = true;
    } catch (err) {
      taskSelectEl.textContent = "";
      taskSelectEl.appendChild(new Option("任务列表加载失败，请重试", ""));
    }
  }

  function pad2(n) { return String(n).padStart(2, "0"); }

  function defaultDateTime() {
    const now = new Date();
    return {
      date: `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`,
      time: `${pad2(now.getHours())}:${pad2(now.getMinutes())}`,
    };
  }

  function resetForm() {
    clearMessage();
    taskSelectEl.value = "";
    const dt = defaultDateTime();
    dateInputEl.value = dt.date;
    timeInputEl.value = dt.time;
    minutesInputEl.value = "";
    submitBtnEl.disabled = false;
    submitBtnEl.textContent = "补登";
  }

  // 日期 + 开始时刻 → 带浏览器本地时区偏移的 ISO 字符串。
  // getTimezoneOffset() 返回"UTC 减本地"的分钟数、且东区为负（东八区 = -480），
  // 与 ISO 偏移符号相反，必须先取负号再判正负——这是本函数唯一容易写反的地方。
  function toIsoWithLocalOffset(dateStr, timeStr) {
    const [y, m, d] = (dateStr || "").split("-").map(Number);
    const [hh, mm] = (timeStr || "").split(":").map(Number);
    if (!y || !m || !d || Number.isNaN(hh) || Number.isNaN(mm)) return null;
    const local = new Date(y, m - 1, d, hh, mm, 0, 0);
    if (Number.isNaN(local.getTime())) return null;
    const offsetMin = -local.getTimezoneOffset();
    const sign = offsetMin >= 0 ? "+" : "-";
    const abs = Math.abs(offsetMin);
    const offH = pad2(Math.floor(abs / 60));
    const offM = pad2(abs % 60);
    return `${dateStr}T${pad2(hh)}:${pad2(mm)}:00${sign}${offH}:${offM}`;
  }

  openBtnEl.addEventListener("click", () => {
    resetForm();
    dialogEl.showModal();
    ensureTreeLoaded();
  });
  cancelBtnEl.addEventListener("click", () => dialogEl.close());
  dialogEl.addEventListener("cancel", () => {}); // Escape/backdrop 走浏览器默认关闭，不发任何请求

  submitBtnEl.addEventListener("click", async () => {
    clearMessage();
    const taskId = taskSelectEl.value;
    const dateStr = dateInputEl.value;
    const timeStr = timeInputEl.value;
    const minutes = Number(minutesInputEl.value);

    if (!taskId) { showMessage("请选择任务——补登必须挂具体任务，不能只挂项目。", "error"); return; }
    if (!dateStr || !timeStr) { showMessage("请填写日期与开始时刻。", "error"); return; }
    if (!Number.isFinite(minutes) || minutes <= 0) { showMessage("时长必须是正数分钟。", "error"); return; }

    const startAt = toIsoWithLocalOffset(dateStr, timeStr);
    if (!startAt) { showMessage("日期/时刻格式不对，请重新选择。", "error"); return; }

    submitBtnEl.disabled = true;
    submitBtnEl.textContent = "补登中…";
    const result = await window.postCore(BASE + "api/core/timer/backfill", {
      taskId: taskId,
      startAt: startAt,
      durationSeconds: Math.round(minutes * 60),
    });
    submitBtnEl.disabled = false;
    submitBtnEl.textContent = "补登";

    if (!result.ok) {
      // 规则 3：服务端 detail 原文，7 条拒绝理由各不相同，不吞成笼统的「失败了」。
      showMessage(result.message, "error");
      return;
    }
    if (result.data && result.data.duplicate === true) {
      // 规则 1：绝不能写成「已记录」——落库的是上一次那条，这次什么都没发生。
      showMessage("这段已经补过了。", "duplicate");
      return;
    }
    // 规则 2：回显服务端算好的归日结果，前端不自己算时区。
    const date = result.data && result.data.date;
    showMessage(date ? `已记到 ${date}。` : "补登成功。", "success");
  });
})();
