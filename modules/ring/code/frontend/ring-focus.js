/* ring/frontend · ring-focus.js —— 「此刻的焦点」的表芯（2026-10-09，nexus-core v2.16 的 views/current.focus；
 * 契约变更记录同日条）。从 ring-instrument.js 拆出：那边已到单文件 500 行上限，而这一块只认自己的 DOM。
 *
 * 没有手动计时时，表芯不再只说「当前没有进行中的计时」：写出人此刻在哪个窗口 / 项目 / 任务、待了多久。
 * 字与走秒出自共享件 window.HoneycombFocus（页面直接引 ../__cockpit/focus.js；项目 / 任务是服务端认的，这里不认）。
 * **不是计时**：轨道换成虚线并呼吸（ring.css 的 .chrono.is-focus），「开始计时」一下才起真的手动计时。
 * 全部 createElement + textContent，没有 innerHTML。
 *
 * 对外只挂 window.ringFocus：
 *   render(focus)  focus = HoneycombFocus.describe() 的结果；ring-instrument.js 在没在计时且它非 null 时每轮调
 *   leave()        表芯要换成别的（计时中 / 空闲）之前调：停走秒、摘掉虚线轨道
 * 用到 ring-controls.js 挂的 window.startTimer / postCore / onStartClicked / populateTaskOptions（点按钮时才取）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";

  const chronoCenterEl = document.getElementById("chrono-center");
  const chronoSvgEl = document.getElementById("chrono-svg");
  const projectSelectEl = document.getElementById("project-select");
  const taskSelectEl = document.getElementById("task-select");
  const chronoEl = chronoCenterEl.parentElement;
  const UNCLASSIFIED_PATH = projectId =>
    BASE + "api/core/planner/projects/" + encodeURIComponent(projectId) + "/unclassified";
  let focusKey = null;
  let focusSinceMs = null;
  let focusTickerHandle = null;

  function tickFocusDisplay() {
    const el = document.getElementById("focus-elapsed");
    if (el && focusSinceMs != null) el.textContent = window.HoneycombFocus.clock((Date.now() - focusSinceMs) / 1000);
  }
  function leaveFocus() {
    if (focusTickerHandle) { clearInterval(focusTickerHandle); focusTickerHandle = null; }
    focusKey = null;
    focusSinceMs = null;
    chronoEl.classList.remove("is-focus", "is-afk");
    chronoCenterEl.removeAttribute("data-focus");
  }
  function centerNode(tag, className, id, text) {
    const el = document.createElement(tag);
    el.className = className;
    el.id = id;
    el.textContent = text;
    return el;
  }

  // 对焦点认出的任务起手动计时；只到项目 → 先取（没有就建）它的「未分类」时间桶。下拉先预选好。
  async function onFocusStartClicked(focus, btn) {
    btn.disabled = true;
    btn.textContent = "开始中…";
    if (Array.from(projectSelectEl.options).some(o => o.value === focus.projectId)) {
      projectSelectEl.value = focus.projectId;
      window.populateTaskOptions(focus.projectId);
      if (focus.taskId) taskSelectEl.value = focus.taskId;
      taskSelectEl.dispatchEvent(new Event("change"));
    }
    let taskId = focus.taskId;
    let failure = null;
    if (!taskId) {
      const bucket = await window.postCore(UNCLASSIFIED_PATH(focus.projectId), undefined);
      taskId = bucket.ok && bucket.data ? bucket.data.taskId : null;
      if (!taskId) failure = bucket.message || "没拿到这个项目的「未分类」";
    }
    const result = taskId ? await window.startTimer(taskId) : { ok: false };
    if (result.ok) return;
    if (failure) {
      const errorEl = document.getElementById("timer-error");
      errorEl.textContent = failure;
      errorEl.hidden = false;
    }
    btn.disabled = false;
    btn.textContent = "开始计时";
  }

  function renderFocusCenter(focus) {
    const afk = focus.state === "afk";
    const key = JSON.stringify([focus.state, focus.lead, focus.window, focus.hint, focus.projectId, focus.taskId]);
    focusSinceMs = focus.since;
    if (key !== focusKey) { // 字没变就不重建：按钮的「开始中…」、焦点都留着
      focusKey = key;
      chronoCenterEl.textContent = "";
      const lead = centerNode("p", "focus-lead", "focus-lead", focus.lead);
      lead.title = focus.lead;
      const win = centerNode("p", "focus-window", "focus-window", focus.window);
      win.title = focus.window;
      win.hidden = !focus.window;
      const note = afk ? "" : (focus.auto ? "自动跟踪中" : "未计时");
      const hint = centerNode("p", "focus-hint", "focus-hint", [focus.hint, note].filter(Boolean).join(" · "));
      hint.hidden = !hint.textContent;
      const known = !afk && Boolean(focus.projectId);
      const btn = centerNode("button", "start-big", known ? "focus-start-btn" : "start-big-btn", "开始计时");
      btn.type = "button";
      if (known) {
        btn.title = "对「" + focus.target + "」开始计时";
        btn.addEventListener("click", () => onFocusStartClicked(focus, btn));
      } else {
        btn.addEventListener("click", window.onStartClicked);
        btn.disabled = !taskSelectEl.value;
      }
      [lead, win, centerNode("span", "elapsed focus-elapsed", "focus-elapsed", ""), hint, btn]
        .forEach(el => chronoCenterEl.appendChild(el));
      chronoEl.classList.add("is-focus");
      chronoEl.classList.toggle("is-afk", afk);
      chronoCenterEl.setAttribute("data-focus", focus.state);
      if (!focusTickerHandle) focusTickerHandle = setInterval(tickFocusDisplay, 1000);
    }
    tickFocusDisplay();
    chronoSvgEl.setAttribute("aria-label", "计时圆环，" + focus.lead +
      (focus.window ? "，" + focus.window : "") + (afk ? "" : "（没有在计时）"));
  }

  window.ringFocus = { render: renderFocusCenter, leave: leaveFocus };
})();
