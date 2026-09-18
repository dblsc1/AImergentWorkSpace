// table · 蜂巢行动分区 · 「编辑目录」的写操作编排（2026-09-07）
//
// **不新写一套写入逻辑**：全部走 data.js（window.NexusTableData）已有的函数，
// 也就是 nexus-core 的统一 CRUD 入口 `/api/core/planner/{type}`（契约 planner.crud.v1，
// index.html 的 crud.js / gtd-crud.js 用的是同一批函数）。本文件只做
// 「哪个按钮 → 调哪个已有函数 → 写完刷新」的编排，一个新的请求面都不开。
//
// 为什么不直接复用 crud.js 本身：那个文件是 index.html 三个 <dialog> 的状态机，
// 与那一页的 DOM 死死绑在一起（打开/填充/关闭 #zoneDialog 等）。蜂巢是另一页、
// 另一套 DOM，能复用的是**写路径**，不是那套弹窗编排——同 backfill.js 当初
// 「自己挂一个独立 click 监听、不改 crud.js 的事件委托」的既有判断。
//
// 事件委托挂在 #hive 上，只认 data-hex-action，与 hex-app.js 的展开/收起点击
// 互不干扰（hex-app.js 里显式 return 掉带 data-hex-action 的目标）。
(function () {
  "use strict";

  var D = window.NexusTableData;
  var App = window.NexusTableHexApp;

  function showError(node, message) {
    var box = node.closest(".hex-detail");
    var slot = box && box.querySelector("[data-hex-error]");
    if (slot) slot.textContent = message || "";
  }

  // 写成功后重新拉全量并保持当前展开的项目 —— 与 index.html 侧
  // 「写完 refresh 整棵树、不做本地乐观更新」的既有口径一致（data.js C2）。
  function afterWrite(node, result) {
    if (!result.ok) { showError(node, result.message || "写入失败"); return; }
    showError(node, "");
    App.reloadExpanded();
  }

  function taskIdOf(node) {
    var li = node.closest("[data-task-id]");
    return li && li.dataset.taskId;
  }
  function findTask(id) {
    var tree = App.getState().tree;
    var hit = null;
    ((tree && tree.projects) || []).forEach(function (p) {
      (p.tasks || []).forEach(function (t) { if (t.id === id) hit = t; });
    });
    return hit;
  }

  function startRename(btn) {
    var li = btn.closest("[data-task-id]");
    var span = li.querySelector(".hex-task-name");
    if (!span || li.querySelector("input")) return;
    var id = li.dataset.taskId;
    var old = span.textContent;
    var input = document.createElement("input");
    input.type = "text";
    input.value = old;
    input.className = "hex-rename";
    span.replaceWith(input);
    input.focus();
    input.select();
    var settled = false;
    function cancel() {
      if (settled) return; settled = true;
      input.replaceWith(span);
    }
    function commit() {
      if (settled) return; settled = true;
      var name = input.value.trim();
      input.replaceWith(span);
      if (!name || name === old) return;
      D.renameTask(id, name).then(function (r) { afterWrite(li, r); });
    }
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") { ev.preventDefault(); commit(); }
      if (ev.key === "Escape") { ev.preventDefault(); cancel(); }
    });
    input.addEventListener("blur", commit);
  }

  // 展开卡的标题就是改名入口（人类 2026-09-14）。和任务改名同一套手感：
  // 原地变输入框，Enter 提交、Esc 取消、失焦取消。**失焦是取消不是提交** ——
  // 标题在卡片最上面，点卡片里任何别的东西都会失焦，按提交处理等于误改。
  // 错误没处可挂（.hex-detail 在标题下面，不是标题的祖先），走顶部状态条。
  function startProjectRename(h3) {
    var cell = h3.closest(".hex-cell");
    var pid = cell && cell.dataset.projectId;
    if (!pid || h3.querySelector("input")) return;
    var old = h3.textContent;
    var input = document.createElement("input");
    input.type = "text"; input.value = old; input.className = "hex-rename"; input.maxLength = 120;
    h3.textContent = ""; h3.appendChild(input);
    input.focus(); input.select();
    var settled = false;
    function restore() { settled = true; h3.textContent = old; }
    input.addEventListener("keydown", function (ev) {
      ev.stopPropagation();
      if (ev.key === "Enter") {
        ev.preventDefault();
        if (settled) return;
        var name = input.value.trim();
        restore();
        if (!name || name === old) return;
        D.renameProject(pid, name).then(function (r) {
          if (!r.ok) { App.setStatus("改名失败：" + (r.message || "")); return; }
          App.setStatus("");
          App.reloadExpanded();
        });
      } else if (ev.key === "Escape") { ev.preventDefault(); if (!settled) restore(); }
    });
    input.addEventListener("blur", function () { if (!settled) restore(); });
  }

  function onClick(ev) {
    // ⚠️ 展开那一下**不能顺便进改名**：点一个收起格子的标题时，hex-app 先把
    // 这一格展开（并把这次 click 记进 state.handledClickAt），fillCell 紧接着
    // 给标题挂上 data-hex-action="rename-project" —— 于是**同一个 click** 冒泡
    // 到这里，正好撞上刚挂上去的属性，一点就直接变输入框。人类要的是"大视角下
    // 再点一次标题"，不是"点开就开始改名"。（真机测出来的，纸面审不出来。）
    var st = App.getState && App.getState();
    if (st && st.handledClickAt === ev.timeStamp) return;
    var btn = ev.target.closest("[data-hex-action]");
    if (!btn || btn.tagName === "FORM") return;
    var action = btn.dataset.hexAction;
    if (action === "toggle-task") {
      ev.preventDefault();
      var id = taskIdOf(btn);
      var task = findTask(id);
      if (!task) return;
      D.toggleTaskDone(id, !task.done).then(function (r) { afterWrite(btn, r); });
    } else if (action === "rename-project") {
      ev.preventDefault();
      startProjectRename(btn);
    } else if (action === "rename-task") {
      ev.preventDefault();
      startRename(btn);
    } else if (action === "delete-task") {
      ev.preventDefault();
      var did = taskIdOf(btn);
      var dtask = findTask(did);
      if (!dtask) return;
      if (!window.confirm("删除任务「" + dtask.name + "」？")) return;
      D.deleteTask(did).then(function (r) { afterWrite(btn, r); });
    }
  }

  function onSubmit(ev) {
    var form = ev.target.closest("form[data-hex-action]");
    if (!form) return;
    var action = form.dataset.hexAction;
    if (action !== "create-task" && action !== "create-project") return;
    ev.preventDefault();
    var cell = form.closest(".hex-cell");
    var input = form.querySelector('input[name="name"]');
    var name = (input.value || "").trim();

    // 建项目：长按分区空位展开的那张表单（2026-09-08）。zoneId 读格子的
    // dataset，不在表单里再存一份——两份 zoneId 迟早对不上。
    if (action === "create-project") {
      var zoneId = cell && cell.dataset.zoneId;
      if (!zoneId) return;
      if (!name) { showError(form, "项目名不能为空。"); return; }
      D.createProject(zoneId, name).then(function (r) {
        if (!r.ok) { showError(form, r.message || "写入失败"); return; }
        input.value = "";
        // 建完必须**收起**再重拉：这一格的身份从"占位格"变成了"某个项目"，
        // 展开态挂在坐标键上，重建之后那个键指向的可能已经是别的格子。
        App.collapseAndReload();
      });
      return;
    }

    var projectId = cell && cell.dataset.projectId;
    if (!projectId) return;
    if (!name) { showError(form, "任务标题不能为空。"); return; }
    D.createTask(projectId, name).then(function (r) {
      input.value = "";
      afterWrite(form, r);
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var hive = document.querySelector("#hive");
    if (!hive) return;
    hive.addEventListener("click", onClick);
    hive.addEventListener("submit", onSubmit);
  });
})();
