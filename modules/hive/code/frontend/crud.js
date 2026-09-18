// hive · CRUD 弹窗与表单编排（对接 nexus-core.planner.crud.v1）
//
// 只做交互编排：打开/关闭三个 <dialog>、填充下拉框、提交时按 C6 拆分成
// 「只改一个字段」的 PATCH、原样展示后端错误（C3/C4）、成功后调用 app.js
// 的 refresh() 重新拉取 views/tree（C2）。渲染逻辑仍在 app.js，本文件
// 不碰 render()/state.tree——两个文件的关注点分开也是为了单文件不超
// 500 行。
//
// 关键设计决定（避免下一个人重踩）：
//   1. 编辑表单打开时把「原始值」记进 dialog.dataset，提交时逐字段 diff，
//      没变的字段完全不进 PATCH body（C6：改名只改 name、换归属只改
//      zoneId/projectId，不在一次 PATCH 里顺手带别的字段）。
//      定义为「不碰这个字段」，而不是伪造一个看起来正确的默认值。
//   3. data.js 的写函数从不 reject（网络错误也走 {ok:false,...} 分支），
//      本文件因此不需要 .catch。
//   4. 任务完成态是弹窗外的一次性动作（checkbox 直接绑定），失败时用
//      #actionToast 提示，并且总会调用一次 App.refresh()——即使 PATCH
//      失败，重渲染也会把 checkbox 纠正回服务端真值，不需要手动回滚
//      本地状态（本文件全程没有自己的一份 state，只订阅 app.js 广播）。
(function () {
  "use strict";

  var D = window.NexusTableData;
  var App = window.NexusTableApp;
  var $ = function (selector) { return document.querySelector(selector); };
  var currentTree = null;

  App.onTreeChange(function (tree) { currentTree = tree; });

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char];
    });
  }

  // ── 小工具：弹窗状态 / toast ─────────────────────────────────────
  var toastTimer = null;
  function showToast(message) {
    var el = $("#actionToast");
    el.textContent = message;
    el.classList.add("show");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(function () { el.classList.remove("show"); }, 4200);
  }

  function showDialogError(dialog, message) {
    var el = dialog.querySelector(".dialog-error");
    el.textContent = message;
    el.hidden = false;
  }

  function clearDialogError(dialog) {
    var el = dialog.querySelector(".dialog-error");
    el.hidden = true;
    el.textContent = "";
  }

  function setDialogBusy(dialog, busy) {
    Array.prototype.forEach.call(dialog.querySelectorAll("button, input, select"), function (el) {
      el.disabled = busy;
    });
  }

  function openDialog(dialog) { clearDialogError(dialog); dialog.showModal(); }

  // 单请求提交：成功关窗+刷新（C2）；失败原样展示 message，不关窗（C3/C4）。
  function finishSubmit(dialog, promise) {
    setDialogBusy(dialog, true);
    return promise.then(function (result) {
      setDialogBusy(dialog, false);
      if (result.ok) { dialog.close(); return App.refresh(); }
      showDialogError(dialog, result.message);
    });
  }

  // 编辑表单专用：按顺序跑一串「只改一个字段」的 PATCH，第一个失败就停；
  // 没有任何字段变化时 patches 为空，什么请求都不发，直接关窗（C6）。
  function finishSequential(dialog, patches) {
    if (!patches.length) { dialog.close(); return Promise.resolve(); }
    setDialogBusy(dialog, true);
    var chain = Promise.resolve({ ok: true });
    patches.forEach(function (step) {
      chain = chain.then(function (prev) { return prev.ok ? step() : prev; });
    });
    return chain.then(function (result) {
      setDialogBusy(dialog, false);
      if (result.ok) { dialog.close(); return App.refresh(); }
      showDialogError(dialog, result.message);
      return App.refresh(); // 前面的字段可能已经改成功，如实刷新展示（C2）
    });
  }

  function optionHtml(value, label, selected) {
    return '<option value="' + escapeHtml(value) + '"' + (selected ? " selected" : "") + ">" +
      escapeHtml(label) + "</option>";
  }

  function fillZoneSelect(select, selectedId) {
    var zones = (currentTree && currentTree.zones) || [];
    select.innerHTML = zones.map(function (zone) {
      return optionHtml(zone.id, zone.name, zone.id === selectedId);
    }).join("");
  }

  function fillProjectSelect(select, selectedId) {
    var zones = (currentTree && currentTree.zones) || [];
    var zonesById = {};
    zones.forEach(function (zone) { zonesById[zone.id] = zone; });
    var projects = (currentTree && currentTree.projects) || [];
    select.innerHTML = projects.map(function (project) {
      var zoneName = (zonesById[project.zoneId] && zonesById[project.zoneId].name) || "?";
      return optionHtml(project.id, zoneName + " / " + project.name, project.id === selectedId);
    }).join("");
  }

  // ── 分区弹窗：新建 / 改名 / 删除（C1，无归属可换） ────────────────
  function openZoneDialog(zoneId) {
    var dialog = $("#zoneDialog");
    var zone = zoneId ? (currentTree.zones || []).filter(function (z) { return z.id === zoneId; })[0] : null;
    dialog.dataset.zoneId = zone ? zone.id : "";
    dialog.dataset.originalName = zone ? zone.name : "";
    $("#zoneModalTitle").textContent = zone ? "编辑分区" : "新建分区";
    $("#zoneNameInput").value = zone ? zone.name : "";
    $("#deleteZoneBtn").hidden = !zone;
    openDialog(dialog);
  }

  function submitZoneForm(event) {
    event.preventDefault();
    var dialog = $("#zoneDialog");
    var name = $("#zoneNameInput").value;
    var zoneId = dialog.dataset.zoneId;
    if (!zoneId) { finishSubmit(dialog, D.createZone(name)); return; }
    var patches = [];
    if (name !== dialog.dataset.originalName) {
      patches.push(function () { return D.renameZone(zoneId, name); });
    }
    finishSequential(dialog, patches);
  }

  function deleteCurrentZone() {
    var dialog = $("#zoneDialog");
    if (!dialog.dataset.zoneId) return;
    finishSubmit(dialog, D.deleteZone(dialog.dataset.zoneId));
  }

  // ── 项目弹窗：新建 / 改名 / 换分区 / 改计划权重 / 删除（C1） ───────
  function openProjectDialog(projectId, presetZoneId) {
    var dialog = $("#projectDialog");
    var project = projectId
      ? (currentTree.projects || []).filter(function (p) { return p.id === projectId; })[0] : null;
    dialog.dataset.projectId = project ? project.id : "";
    dialog.dataset.originalName = project ? project.name : "";
    dialog.dataset.originalZoneId = project ? project.zoneId : "";
    $("#projectModalTitle").textContent = project ? "编辑项目" : "新建项目";
    $("#projectNameInput").value = project ? project.name : "";
    fillZoneSelect($("#projectZoneSelect"), project ? project.zoneId : presetZoneId);
    $("#deleteProjectBtn").hidden = !project;
    openDialog(dialog);
  }

  function submitProjectForm(event) {
    event.preventDefault();
    var dialog = $("#projectDialog");
    var name = $("#projectNameInput").value;
    var zoneId = $("#projectZoneSelect").value;
    var projectId = dialog.dataset.projectId;

    if (!projectId) {
      finishSubmit(dialog, D.createProject(zoneId, name, {}));
      return;
    }

    var patches = [];
    if (name !== dialog.dataset.originalName) {
      patches.push(function () { return D.renameProject(projectId, name); });
    }
    if (zoneId !== dialog.dataset.originalZoneId) {
      patches.push(function () { return D.moveProject(projectId, zoneId); });
    }
    finishSequential(dialog, patches);
  }

  function deleteCurrentProject() {
    var dialog = $("#projectDialog");
    if (!dialog.dataset.projectId) return;
    finishSubmit(dialog, D.deleteProject(dialog.dataset.projectId));
  }

  // ── 任务弹窗：新建 / 改名 / 换项目 / 删除（C1；切换完成态见下方 toggleTask） ──
  // TreeOut 里 tasks[] 靠嵌套隐含归属，任务对象本身**不带** projectId 字段
  // （见 nexus-core contract.md TreeOut 形状）——这里从外层项目补上，
  // 供预填下拉框选中项、以及编辑时的改前改后 diff 用（C6）。不修改原 tree 对象，
  // 只返回一份浅拷贝。（2026-07-31 Playwright 真机冒烟测出：漏补这个字段会让
  // dialog.dataset.originalProjectId 变成字符串 "undefined"，下拉框选错项，
  // 且每次编辑任务都会被误判成"换了项目"、多发一次不必要的 PATCH。）
  function findTaskById(taskId) {
    var projects = (currentTree && currentTree.projects) || [];
    for (var i = 0; i < projects.length; i += 1) {
      var tasks = projects[i].tasks || [];
      for (var j = 0; j < tasks.length; j += 1) {
        if (tasks[j].id === taskId) {
          var found = {};
          for (var key in tasks[j]) { found[key] = tasks[j][key]; }
          found.projectId = projects[i].id;
          return found;
        }
      }
    }
    return null;
  }

  // ── 排期 + 前置任务（F-TABLE-3，防御式）────────────────────────────
  // feature-detect：只认"当前已加载的 views/tree 里，这个任务对象有没有
  // dependsOn 键"（D.hasScheduleFields），不是猜、不是查配置——今天(2026-08-08)
  // nexus-core 还没实现这两个字段（读源码 planner/schemas.py 的 TaskUpdate
  // 确认，extra="forbid"），键不存在，控件整块隐藏，页面其余功能不受影响；
  // 后端一旦上线，下次打开弹窗就自动显示，hive 这边不需要再改代码。
  function fillDependsSelect(select, task) {
    var others = D.listOtherTasks(currentTree, task.id);
    var selected = (task.dependsOn || []);
    select.innerHTML = others.map(function (t) {
      return optionHtml(t.id, t.label, selected.indexOf(t.id) !== -1);
    }).join("");
  }

  function openTaskDialog(taskId, presetProjectId) {
    var dialog = $("#taskDialog");
    var task = taskId ? findTaskById(taskId) : null;
    dialog.dataset.taskId = task ? task.id : "";
    dialog.dataset.originalName = task ? task.name : "";
    dialog.dataset.originalProjectId = task ? task.projectId : "";
    $("#taskModalTitle").textContent = task ? "编辑任务" : "新建任务";
    $("#taskNameInput").value = task ? task.name : "";
    fillProjectSelect($("#taskProjectSelect"), task ? task.projectId : presetProjectId);
    $("#deleteTaskBtn").hidden = !task;

    // 排期只在"编辑既有任务，且后端已支持"时出现——新建任务这里从不显示
    // （新建时还没有 id，"前置任务"这种依赖别的已存在任务的概念也无从谈起）。
    var supportsSchedule = !!task && D.hasScheduleFields(task);
    var scheduleSection = $("#taskScheduleSection");
    scheduleSection.hidden = !supportsSchedule;
    if (supportsSchedule) {
      var plan = task.plan || {};
      dialog.dataset.originalPlanStart = plan.start || "";
      dialog.dataset.originalPlanEnd = plan.end || "";
      dialog.dataset.originalDependsOn = JSON.stringify((task.dependsOn || []).slice().sort());
      $("#taskPlanStartInput").value = plan.start || "";
      $("#taskPlanEndInput").value = plan.end || "";
      fillDependsSelect($("#taskDependsSelect"), task);
    }
    openDialog(dialog);
  }

  // 排期两个日期框 → 决定要不要发 plan 补丁，以及发什么值。
  // 返回 null = 不发（没变）；{invalid:true} = 只填了一侧，本地拦截不发请求；
  // {plan: {...}|null} = 要发的值（都留空 = 清空排期）。
  function readPlanPatch(dialog) {
    var startVal = $("#taskPlanStartInput").value;
    var endVal = $("#taskPlanEndInput").value;
    if ((startVal && !endVal) || (!startVal && endVal)) return { invalid: true };
    var nextPlan = (startVal && endVal) ? { start: startVal, end: endVal } : null;
    var originalStart = dialog.dataset.originalPlanStart || "";
    var originalEnd = dialog.dataset.originalPlanEnd || "";
    var unchanged = nextPlan
      ? (startVal === originalStart && endVal === originalEnd)
      : (!originalStart && !originalEnd);
    return unchanged ? null : { plan: nextPlan };
  }

  function readSelectedDependsOn() {
    return Array.prototype.map.call(
      $("#taskDependsSelect").selectedOptions || [], function (opt) { return opt.value; }
    );
  }

  function submitTaskForm(event) {
    event.preventDefault();
    var dialog = $("#taskDialog");
    var name = $("#taskNameInput").value;
    var projectId = $("#taskProjectSelect").value;
    var taskId = dialog.dataset.taskId;

    if (!taskId) {
      finishSubmit(dialog, D.createTask(projectId, name, {}));
      return;
    }

    var scheduleVisible = !$("#taskScheduleSection").hidden;
    var planPatch = scheduleVisible ? readPlanPatch(dialog) : null;
    if (planPatch && planPatch.invalid) {
      showDialogError(dialog, "计划的开始和结束日期要么都填，要么都留空。");
      return;
    }

    var patches = [];
    if (name !== dialog.dataset.originalName) {
      patches.push(function () { return D.renameTask(taskId, name); });
    }
    if (projectId !== dialog.dataset.originalProjectId) {
      patches.push(function () { return D.moveTask(taskId, projectId); });
    }
    if (planPatch) {
      patches.push(function () { return D.updateTaskPlan(taskId, planPatch.plan); });
    }
    if (scheduleVisible) {
      var selectedDependsOn = readSelectedDependsOn().slice().sort();
      if (JSON.stringify(selectedDependsOn) !== dialog.dataset.originalDependsOn) {
        // F-API-3 并发注记：PATCH dependsOn 前先重读一次该任务，缩小"多窗同时改
        // 同一个任务"的竞态窗口（后端仍是 last-write-wins，前端能做的只有缩窗口，
        // 不是消灭它）。任务在这期间被删了就报错，不当空操作悄悄放过。
        patches.push(function () {
          return D.fetchTree().then(function (freshResult) {
            var freshTask = freshResult.tree && D.findTaskById(freshResult.tree, taskId);
            if (!freshTask) {
              return { ok: false, status: 404, message: "这个任务可能已被删除，前置任务未更新，请刷新重试。" };
            }
            return D.updateTaskDependsOn(taskId, selectedDependsOn);
          });
        });
      }
    }
    finishSequential(dialog, patches);
  }

  function deleteCurrentTask() {
    var dialog = $("#taskDialog");
    if (!dialog.dataset.taskId) return;
    finishSubmit(dialog, D.deleteTask(dialog.dataset.taskId));
  }

  // 2026-09-14：#zoneGrid 的事件委托（handleGridClick/handleGridChange）连同
  // toggleTask / goToRing 一起删掉 —— 旧的「经典列表」整个去掉了，那几个
  // data-action 现在没有任何 DOM 会打出来。三个 <dialog> 留着：回顾清单
  // 「跳到对应对象」就是切回主视图再打开这两个既有弹窗（见文件尾的导出），
  // 顶栏「＋新建分区」也还走 #zoneDialog。
  document.addEventListener("DOMContentLoaded", function () {
    $("#newZone").addEventListener("click", function () { openZoneDialog(null); });

    $("#zoneForm").addEventListener("submit", submitZoneForm);
    $("#projectForm").addEventListener("submit", submitProjectForm);
    $("#taskForm").addEventListener("submit", submitTaskForm);

    $("#deleteZoneBtn").addEventListener("click", deleteCurrentZone);
    $("#deleteProjectBtn").addEventListener("click", deleteCurrentProject);
    $("#deleteTaskBtn").addEventListener("click", deleteCurrentTask);

    [$("#zoneDialog"), $("#projectDialog"), $("#taskDialog")].forEach(function (dialog) {
      dialog.addEventListener("click", function (event) {
        if (event.target === dialog) dialog.close(); // 点击 backdrop 关闭
      });
      Array.prototype.forEach.call(dialog.querySelectorAll("[data-close-dialog]"), function (btn) {
        btn.addEventListener("click", function () { dialog.close(); });
      });
    });
  });

  // 扩展 app.js 已创建的 window.NexusTableApp：回顾视图（F-REVIEW-2
  // 「跳到对应对象处理」）复用这两个既有的编辑弹窗打开函数，不新开一套"只读详情"
  // UI——回顾清单点一条项目/任务，就是切到控制台 tab 再打开它平常就有的编辑弹窗，
  // 用户在同一个弹窗里就能直接处理（改期/搬移/删除），不是另造一个信息展示层。
  window.NexusTableApp.openProjectDialog = openProjectDialog;
  window.NexusTableApp.openTaskDialog = function (taskId) { openTaskDialog(taskId); };
}());
