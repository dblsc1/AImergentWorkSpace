// gantt/code/frontend · 数据层（纯逻辑，无 DOM 依赖）
//
// 职责：拉取双图层数据源（GET /api/core/views/gantt）、发起计划改期写请求
// （PATCH /api/core/planner/projects/{id}，不为甘特开专用端点——contract.md
// 「契约索引声明」consumes 段明文：拖拽改期走统一入口），以及把后端形状
// 整理成 vis-timeline 的 groups/items 数据结构。不碰 DOM，可在浏览器
// （挂 window.GanttData）与 Node（gantt-data.test.js 用 require()）里跑，
// 与 hive/data.js 同一套约定（写请求 ok/status/message 归一化、写完调用方
// 重新拉取、不做乐观更新）。
//
// 关键约束（不要在这个文件里破坏，编号 D5/D7/D8 见下）：
//   - 红线基准只用后端给的 today 字符串（"YYYY-MM-DD"）。本文件不调用
//     不带参数的 `new Date()` 取"现在"——nexus-core 契约明文过这条：客户端
//     时钟/时区飘会让"该干的干了没"这个基准各人一个答案，这个项目今天刚
//     因为日界算错（UTC vs 本地）修过一轮，前端不能重犯同一类错。
//   - 日期一律走"本地日历日"构造（localDateFromISO：new Date(y, m-1, d)），
//     不用 `new Date(isoString)`——后者按 UTC 解析再转本地显示，在非 UTC
//     时区会和 vis-timeline 用浏览器本地时区画的坐标轴错位一天，正是后端
//     那次修的同一类 bug，前端换个地方不能重犯。
//   - 事实层（actual）item 一律 editable:false，三个视觉通道里"填充/线型"
//     两个通道靠 className 挂到 style.css；"颜色"通道同样在 style.css。
//   - 写操作（patchProjectPlan）只管发请求、归一化 ok/status/message，
//     不做本地乐观更新——刷新展示是调用方（gantt-view.js）在写成功后
//     重新拉取的责任（D8）。
//
// L2（2026-08-08）新增任务层：projects[].tasks[] 建模成 vis nestedGroups 的
// 子行——buildGroupModels() 返回扁平数组，用 kind 区分项目行/任务行；项目行
// 带 taskIds 供调用方拼 nestedGroups+showNested:false（默认折叠）。任务计划条
// 走同一套 buildXxxItem 命名（buildTaskPlanItem/buildTaskActualItems），写入
// 端点是既有的 PATCH /api/core/planner/tasks/{id}（patchTaskPlan，D6 同款
// 纪律：不为甘特开专用端点）。isTaskOutOfBounds() 是本轮唯一的新判定逻辑：
// 任务计划期超出项目计划期、或项目本身没有计划期，都算越界（PRD F-GANTT-2
// 用户裁决：允许，只提示不阻止），怎么画（渐变/className）交给 style.css。
//
// L3（2026-08-08）新增依赖箭头：buildDependencyArrows() 把 tasks[].dependsOn
// 摊平成 timeline-arrows 的 ArrowSpec 数组，每条箭头挂 state（normal/
// conflict/unscheduled，dependencyArrowState() 判定）供 DOM 层上色。箭头
// 只能连真实存在的 vis item（vendor 限制），taskAnchorItemId() 找锚点：
// 有计划锚计划条，没计划但有过实际记录锚第一条事实块，两者都没有就画不出，
// 跳过并记入 handoff.md 技术债。computeConflictProjectIds() 按全量数据算
// 折叠态冲突聚合章要挂在哪些项目行上，不依赖任何显示开关（PRD 明文「折叠态
// 冲突不许隐身」）。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.GanttData = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var GANTT_VIEW_PATH = "/api/core/views/gantt";
  var PLANNER_PROJECTS_PREFIX = "/api/core/planner/projects/";
  var PLANNER_TASKS_PREFIX = "/api/core/planner/tasks/";

  // ── 本地日历日期（不经 UTC 解析，避免与 vis-timeline 的本地时区坐标轴错位）──
  function localDateFromISO(dateStr) {
    var parts = String(dateStr || "").split("-");
    var y = Number(parts[0]), m = Number(parts[1]), d = Number(parts[2]);
    if (!y || !m || !d) return null;
    return new Date(y, m - 1, d);
  }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function formatLocalISO(date) {
    return date.getFullYear() + "-" + pad2(date.getMonth() + 1) + "-" + pad2(date.getDate());
  }

  function addLocalDays(date, n) {
    var next = new Date(date.getTime());
    next.setDate(next.getDate() + n);
    return next;
  }

  // 纯日历算术（今天+N天这类相对计算），入参出参都是给定的日期字符串，
  // 不读客户端时钟——和上面 localDateFromISO 一样只是「构造/搬运」不是「取现在」。
  function addDaysISO(dateStr, n) {
    var d = localDateFromISO(dateStr);
    return d ? formatLocalISO(addLocalDays(d, n)) : dateStr;
  }

  // ── 时长展示（tooltip 用，"32分钟"/"<1分钟"，与 hive 的 archive 展示同约定）──
  function formatMinutes(seconds) {
    var n = Number(seconds);
    if (!isFinite(n) || n < 0) n = 0;
    var minutes = Math.round(n / 60);
    if (minutes < 1) return n > 0 ? "<1分钟" : "0分钟";
    return minutes + "分钟";
  }

  // ── 请求层：统一 {ok:true,data} / {ok:false,status,message}，从不 reject ──
  // 与 hive/data.js 的 request() 同一套约定：4xx 响应体 {detail:"..."} 原样
  // 透传成 message，本层不改写、不包装后端给的文案（D10：detail 原样展示）。
  function request(fetchImpl, method, path, body) {
    if (!fetchImpl) {
      return Promise.resolve({ ok: false, status: 0, message: "fetch 不可用" });
    }
    var init = { method: method };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    return fetchImpl(path, init).then(function (res) {
      if (res.status === 204) return { ok: true, data: null };
      return res.json().catch(function () { return null; }).then(function (payload) {
        if (res.ok) return { ok: true, data: payload };
        var message = (payload && payload.detail) ? payload.detail : ("请求失败（HTTP " + res.status + "）");
        return { ok: false, status: res.status, message: message };
      });
    }).catch(function (err) {
      var message = (err && err.message) || String(err);
      return { ok: false, status: 0, message: "网络请求失败：" + message };
    });
  }

  function resolveFetch(options) {
    options = options || {};
    return options.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  }

  function fetchGanttView(options) {
    return request(resolveFetch(options), "GET", GANTT_VIEW_PATH);
  }

  function projectPlanPath(id) {
    return PLANNER_PROJECTS_PREFIX + encodeURIComponent(id);
  }

  function taskPlanPath(id) {
    return PLANNER_TASKS_PREFIX + encodeURIComponent(id);
  }

  // plan 传 {start,end}（排期/改期）；不为甘特开专用端点，统一走 planner
  // 入口，与 hive 的 CRUD 写请求是同一条路（contract.md consumes 段原话）。
  function patchProjectPlan(id, plan, options) {
    return request(resolveFetch(options), "PATCH", projectPlanPath(id), { plan: plan });
  }

  // 任务计划条的拖拽保存（L2 新增）：同一 D6/D8 模式，只是端点换成既有的
  // PATCH /api/core/planner/tasks/{id}（nexus-core 契约 v1.1 起就有，本模块
  // 只是第一次消费它——不新开专用端点，与项目层同一条纪律）。
  function patchTaskPlan(id, plan, options) {
    return request(resolveFetch(options), "PATCH", taskPlanPath(id), { plan: plan });
  }

  // ── 派生判定 ──────────────────────────────────────────────────────
  function isUnscheduled(project) {
    return !(project && project.plan);
  }

  // ── vis-timeline groups：项目行 + 任务子行（L2：nestedGroups）───────────
  // 返回纯数据模型，不拼 HTML 字符串——理由同旧版注释（vis-timeline 的
  // group.content 是字符串会被转义，DOM 节点交给调用方现造）。L2 起这个
  // 返回值是**扁平数组**，用 kind 区分项目行/任务行，任务行紧跟在其所属
  // 项目行之后（调用方按 kind 分流：项目行要带 taskIds 给 nestedGroups+
  // showNested:false 默认折叠；任务行不再嵌套）。
  // D9 的"未排期徽标+可排期"只保留在项目层——任务层未排期本轮只给徽标
  // （F-GANTT-7 任务空态链接任务页是下一轮的活，不在本轮范围内，
  // 不顺手加排期按钮/弹窗，明确不做）。
  //
  // L6（2026-08-09）新增 `plan` 字段：已排期为 `{start,end}`，未排期为
  // `null`——供 DOM 层给"改期"编辑入口预填当前值，不用反查 state.view
  // （模型本身已经带够信息，与 unscheduled/done/hasConflict 同一设计：DOM
  // 层只画，不判断，见文件头注释）。项目与任务两条分支各自独立取自
  // project.plan / task.plan，互不影响。
  function planSnapshot(entity) {
    return entity && entity.plan ? { start: entity.plan.start, end: entity.plan.end } : null;
  }

  function buildGroupModels(projects) {
    var conflictProjectIds = computeConflictProjectIds(projects); // L3：折叠态冲突不许隐身，按全量数据算，不依赖任何显示开关
    var groups = [];
    (projects || []).forEach(function (project) {
      var tasks = project.tasks || [];
      groups.push({
        kind: "project",
        id: project.id,
        name: project.name,
        unscheduled: isUnscheduled(project),
        plan: planSnapshot(project),
        taskIds: tasks.map(function (t) { return t.id; }),
        hasConflict: conflictProjectIds.indexOf(project.id) !== -1
      });
      tasks.forEach(function (task) {
        groups.push({
          kind: "task",
          id: task.id,
          name: task.name,
          unscheduled: isUnscheduled(task),
          plan: planSnapshot(task),
          done: !!task.done,
          projectId: project.id
        });
      });
    });
    return groups;
  }

  // ── vis-timeline items：计划层（可拖）+ 事实层（锁死，逐日）─────────────
  // stack:false（选项在 gantt-view.js 里配）让同一 group 里的两层叠在同一行；
  // className 挂三个通道里的"线型/填充"两个（颜色也在 CSS，靠 className 选中），
  // editable 在这里按 D6/D7 显式给值，不依赖全局 Timeline 配置隐式继承。
  function buildPlanItem(project) {
    return {
      id: project.id + ":plan",
      group: project.id,
      start: localDateFromISO(project.plan.start),
      end: localDateFromISO(project.plan.end),
      type: "range",
      className: "gantt-item gantt-plan",
      content: "",
      title: "计划 · " + project.plan.start + " → " + project.plan.end,
      editable: { updateTime: true, updateGroup: false, remove: false },
      selectable: true
    };
  }

  // ── 事实层相邻分段"视觉连成一组"：只合并真连续（0 天间隔）
  // 的天，恰好隔 1 天不合并（凭空画过渡段会跟 D7"流水账一比一映射"冲突，
  // 做不到就明说不做）。items 按 actual[] 原序构建，天然升序；
  // 用 item.start 反算日期串做相邻判定，不额外挂字段（item 原样喂 DataSet）。
  // 具体怎么去掉交界线交给 style.css 的 --join-l/--join-r 规则。
  function appendActualJoinClasses(items, classPrefix) {
    for (var i = 0; i < items.length; i++) {
      if (i > 0 && addDaysISO(formatLocalISO(items[i - 1].start), 1) === formatLocalISO(items[i].start)) {
        items[i].className += " " + classPrefix + "--join-l";
        items[i - 1].className += " " + classPrefix + "--join-r";
      }
    }
    return items;
  }

  function buildActualItems(project) {
    var out = [];
    var actual = (project && project.actual) || [];
    actual.forEach(function (day, idx) {
      var seconds = Number(day.seconds);
      if (!(seconds > 0)) return; // 有记录但 0 秒：没什么好画的，不占一个 item
      out.push({
        id: project.id + ":actual:" + (day.date || idx),
        group: project.id,
        start: localDateFromISO(day.date),
        end: localDateFromISO(addDaysISO(day.date, 1)),
        type: "range",
        className: "gantt-item gantt-actual",
        content: "",
        title: "事实（锁死） · " + day.date + " · " + formatMinutes(seconds),
        editable: false, // D7：append-only 流水账派生，拖它=改历史，硬锁
        selectable: true
      });
    });
    return appendActualJoinClasses(out, "gantt-actual");
  }

  // ── 越界排期判定（L2，PRD F-GANTT-2 用户裁决：允许，只提示不阻止）───────
  // 任务计划期超出项目计划期，或项目本身没有计划期（无窗口可比较，视为
  // 越界，因为此时任务计划漂在没有基准的地方）——两种情形都成立就算越界。
  // 字符串比较对 "YYYY-MM-DD" 定长格式等价于日期序比较，不需要先转 Date。
  function isTaskOutOfBounds(task, project) {
    if (!task || !task.plan) return false; // 未排期任务没有条可画，不算越界
    if (!project || !project.plan) return true; // 项目无计划期：没有窗口可比较
    return task.plan.start < project.plan.start || task.plan.end > project.plan.end;
  }

  // ── vis-timeline items：任务层计划条（细，可拖）+ 事实块（细，锁死）───────
  // className 比项目层多挂一段 gantt-task-*（细版视觉 + 独立的拖拽分发依据，
  // gantt-view.js 的 onItemMove 靠 className 决定 PATCH 打 tasks/{id} 还是
  // projects/{id}）；越界再叠一个修饰类，颜色处理交给 style.css 的渐变规则。
  function buildTaskPlanItem(task, project) {
    var oob = isTaskOutOfBounds(task, project);
    var className = "gantt-item gantt-task-plan" + (oob ? " gantt-task-plan--oob" : "");
    return {
      id: task.id + ":plan",
      group: task.id,
      start: localDateFromISO(task.plan.start),
      end: localDateFromISO(task.plan.end),
      type: "range",
      className: className,
      content: "",
      title: "任务计划 · " + task.plan.start + " → " + task.plan.end +
        (oob ? "（越出项目计划窗口）" : ""),
      editable: { updateTime: true, updateGroup: false, remove: false },
      selectable: true
    };
  }

  function buildTaskActualItems(task) {
    var out = [];
    var actual = (task && task.actual) || [];
    actual.forEach(function (day, idx) {
      var seconds = Number(day.seconds);
      if (!(seconds > 0)) return; // 与项目层同一约定：0 秒不占 item
      out.push({
        id: task.id + ":actual:" + (day.date || idx),
        group: task.id,
        start: localDateFromISO(day.date),
        end: localDateFromISO(addDaysISO(day.date, 1)),
        type: "range",
        className: "gantt-item gantt-task-actual",
        content: "",
        title: "任务事实（锁死） · " + day.date + " · " + formatMinutes(seconds),
        editable: false, // D7 双保险同款：任务事实一样是 append-only 派生，拖它=改历史
        selectable: true
      });
    });
    return appendActualJoinClasses(out, "gantt-task-actual");
  }

  function buildItems(projects) {
    var items = [];
    (projects || []).forEach(function (project) {
      if (!isUnscheduled(project)) items.push(buildPlanItem(project));
      items = items.concat(buildActualItems(project));
      (project.tasks || []).forEach(function (task) {
        if (!isUnscheduled(task)) items.push(buildTaskPlanItem(task, project));
        items = items.concat(buildTaskActualItems(task));
      });
    });
    return items;
  }

  // ── L3：依赖箭头（tasks[].dependsOn，PRD F-GANTT-3）───────────────────
  // 任务分散在各自 project.tasks[] 里，dependsOn 跨项目也允许（PRD 明文），
  // 所以先摊平建一份全量索引，供箭头构造与折叠态冲突聚合共用，不重复扫两遍。
  function indexTasksByProject(projects) {
    var taskById = {};
    var projectIdByTaskId = {};
    (projects || []).forEach(function (project) {
      (project.tasks || []).forEach(function (task) {
        taskById[task.id] = task;
        projectIdByTaskId[task.id] = project.id;
      });
    });
    return { taskById: taskById, projectIdByTaskId: projectIdByTaskId };
  }

  // 冲突判定：depTask 是被依赖的前置任务，task 是声明 dependsOn 的后置任务。
  // 冲突＝后置任务开始得比前置任务结束还早（违反"前置先完成"的顺序）——
  // 对应 PRD「A.plan.start < B.plan.end」，这里 A=task（后置）、B=depTask（前置），
  // 用变量名直接表意，不用 A/B 这种要对照文档才看得懂的字母。
  // 任一端未排期时不判冲突（另有 unscheduled 态单独表达，见 dependencyArrowState）。
  function dependencyConflict(depTask, task) {
    if (!depTask || !depTask.plan || !task || !task.plan) return false;
    return task.plan.start < depTask.plan.end;
  }

  // 三态：unscheduled（任一端没有计划期）> conflict（都排期但顺序冲突）> normal。
  function dependencyArrowState(depTask, task) {
    if (!depTask || !depTask.plan || !task || !task.plan) return "unscheduled";
    return dependencyConflict(depTask, task) ? "conflict" : "normal";
  }

  // timeline-arrows 只能连接真实存在的 vis item（读源码确认过：_drawArrows
  // 靠 itemsData.get(id) 判存在，addArrow 本身不校验但画不出没有锚点的箭头），
  // 所以要给每个任务找一个"锚点 item id"：有计划就锚在计划条上（位置最准确、
  // 且计划条本身可拖，箭头会跟着走，符合直觉）；没有计划但有过实际记录，
  // 锚在第一条有时长的事实块上（好过完全画不出来）；两者都没有——纯未排期
  // 且从未工作过的任务——画不出锚点，这条箭头只能跳过（vendor 物理限制，
  // 记入 handoff.md 技术债，不是本文件的判断错误）。
  function taskAnchorItemId(task) {
    if (task && task.plan) return task.id + ":plan";
    var actual = (task && task.actual) || [];
    var firstWithSeconds = actual.filter(function (d) { return Number(d.seconds) > 0; })[0];
    if (firstWithSeconds) return task.id + ":actual:" + firstWithSeconds.date;
    return null;
  }

  // 返回 timeline-arrows 的 ArrowSpec 数组（{id, id_item_1, id_item_2, ...}），
  // 额外挂 state/title 两个字段——timeline-arrows 只读它认的那几个键，多余字段
  // 原样保留在 dep 对象上，供 DOM 层按 state 上色/加虚线（三个视觉通道见 D2
  // 同款分工：这里只判定状态，怎么画交给 gantt-view.js + style.css）。
  function buildDependencyArrows(projects) {
    var idx = indexTasksByProject(projects);
    var arrows = [];
    Object.keys(idx.taskById).forEach(function (taskId) {
      var task = idx.taskById[taskId];
      (task.dependsOn || []).forEach(function (depId) {
        var depTask = idx.taskById[depId];
        if (!depTask) return; // 依赖指向的任务不在当前视图数据里，跳过不崩
        var fromAnchor = taskAnchorItemId(depTask);
        var toAnchor = taskAnchorItemId(task);
        if (!fromAnchor || !toAnchor) return; // 两端都没有可锚定 item，画不出，见上方注释
        var state = dependencyArrowState(depTask, task);
        arrows.push({
          id: depId + "->" + taskId,
          id_item_1: fromAnchor,
          id_item_2: toAnchor,
          state: state,
          title: (state === "conflict" ? "冲突 · " : "") + depTask.name + " → " + task.name
        });
      });
    });
    return arrows;
  }

  // 折叠态冲突聚合章（PRD F-GANTT-3「折叠态冲突不许隐身」）：只要项目里任一
  // 任务牵涉到一条冲突依赖（不管它是前置还是后置），这个项目就该挂章——
  // 按全量数据算，不受任何折叠/图层开关影响，所以放在 buildGroupModels()
  // 里直接调用，不给 DOM 层留"忘了传显示状态进来"的空子。
  function computeConflictProjectIds(projects) {
    var idx = indexTasksByProject(projects);
    var conflictProjectIds = {};
    Object.keys(idx.taskById).forEach(function (taskId) {
      var task = idx.taskById[taskId];
      (task.dependsOn || []).forEach(function (depId) {
        var depTask = idx.taskById[depId];
        if (!depTask || !dependencyConflict(depTask, task)) return;
        conflictProjectIds[idx.projectIdByTaskId[taskId]] = true;
        if (idx.projectIdByTaskId[depId]) conflictProjectIds[idx.projectIdByTaskId[depId]] = true;
      });
    });
    return Object.keys(conflictProjectIds);
  }

  // ── 排期默认区间（今天 → 今天+7天）：今天来自服务端 todayStr 参数，不猜 ──
  function defaultScheduleRange(todayStr) {
    return { start: todayStr, end: addDaysISO(todayStr, 7) };
  }

  // ── L6.5（UI 重做）左侧信息列：起 / 止 / 工期（对照 Bryntum 数据表列，
  // 信息密度差距最大的一项）─────────────────────────────────────
  // 复用 localDateFromISO 而不是新开一条日期解析路径——避免和 D5/D7 已经
  // 踩过的"UTC 解析错位"同一类坑。
  function planDurationDays(plan) {
    if (!plan || !plan.start || !plan.end) return null;
    var start = localDateFromISO(plan.start);
    var end = localDateFromISO(plan.end);
    if (!start || !end) return null;
    var MS_PER_DAY = 24 * 60 * 60 * 1000;
    var days = Math.round((end.getTime() - start.getTime()) / MS_PER_DAY);
    return days >= 0 ? days + 1 : null; // 含首尾两天，跟排期弹窗"开始=结束"记 1 天一致
  }

  // "MM-DD → MM-DD" 短格式（配合行标签有限宽度）；跨年才落回完整 ISO，
  // 这种情形罕见，不值得为它单独分支复杂度。
  function formatPlanRange(plan) {
    if (!plan || !plan.start || !plan.end) return "";
    var sameYear = plan.start.slice(0, 4) === plan.end.slice(0, 4);
    var startLabel = sameYear ? plan.start.slice(5) : plan.start;
    var endLabel = sameYear ? plan.end.slice(5) : plan.end;
    return startLabel + " → " + endLabel;
  }

  // ── 缩放粒度预设（F-GANTT-5「日/周/月/季」按钮那一半；滚轮连续缩放的
  // 上下限在 gantt-view.js 的 Timeline options 里配，不在这个纯逻辑文件）。
  // 每档给一个以"今天"为中心的窗口宽度——具体天数是产品判断不是算出来的，
  // 写常量表方便下一轮改数字，不用去 DOM 层里翻。today 只经参数传入，
  // 不读客户端时钟（D5 同款纪律）。
  var GRANULARITY_SPANS = {
    day: { before: 3, after: 4 },       // ~1 周，看得清单日格子
    week: { before: 10, after: 18 },    // ~4 周
    month: { before: 30, after: 60 },   // ~3 个月
    quarter: { before: 60, after: 200 } // ~9 个月，跨季度全貌
  };

  function zoomWindowForGranularity(todayStr, granularity) {
    var span = GRANULARITY_SPANS[granularity];
    if (!span || !todayStr) return null;
    return {
      start: addDaysISO(todayStr, -span.before),
      end: addDaysISO(todayStr, span.after)
    };
  }

  return {
    GANTT_VIEW_PATH: GANTT_VIEW_PATH,
    localDateFromISO: localDateFromISO,
    formatLocalISO: formatLocalISO,
    addDaysISO: addDaysISO,
    formatMinutes: formatMinutes,
    request: request,
    fetchGanttView: fetchGanttView,
    projectPlanPath: projectPlanPath,
    patchProjectPlan: patchProjectPlan,
    taskPlanPath: taskPlanPath,
    patchTaskPlan: patchTaskPlan,
    isUnscheduled: isUnscheduled,
    isTaskOutOfBounds: isTaskOutOfBounds,
    buildGroupModels: buildGroupModels,
    buildPlanItem: buildPlanItem,
    buildActualItems: buildActualItems,
    buildTaskPlanItem: buildTaskPlanItem,
    buildTaskActualItems: buildTaskActualItems,
    buildItems: buildItems,
    defaultScheduleRange: defaultScheduleRange,
    planDurationDays: planDurationDays,
    formatPlanRange: formatPlanRange,
    zoomWindowForGranularity: zoomWindowForGranularity,
    dependencyConflict: dependencyConflict,
    dependencyArrowState: dependencyArrowState,
    taskAnchorItemId: taskAnchorItemId,
    buildDependencyArrows: buildDependencyArrows,
    computeConflictProjectIds: computeConflictProjectIds
  };
});
