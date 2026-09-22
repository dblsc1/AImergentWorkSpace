// gantt/code/frontend · 视图层（DOM + vis-timeline 绑定）
//
// 只在浏览器里跑，依赖 window.vis（vendor/vis-timeline-graph2d.min.js 挂的
// 全局，只读——行为要改在这里包一层，不改 vendor 文件）与 window.GanttData
// （gantt-data.js）。职责：拉数据 → 建 groups/items → 起 vis.Timeline →
// 图层开关 / 拖拽保存 / 排期弹窗 / 错误展示。
//
// 本文件遵循的设计要点（D1–D12）：
//   D1  options.stack=false —— 两层叠在同一行，不是上下两轨。
//   D2  三个通道里"线型/填充"靠 className 挂 CSS（style.css），"颜色"同源；
//       本文件不重复决定颜色，只挂类名。
//   D3  图例文字是 index.html 静态内容，不是本文件的活。
//   D4  两个独立大按钮，各自 aria-pressed 明确开关态（不是猜的）。
//   D5  红线只用 timeline.addCustomTime(todayDate,'today')；todayDate 只
//       经 GanttData.localDateFromISO(view.today) 得到，本文件不调用不带
//       参数的 `new Date()`。
//   D6  拖拽结束在 onItemMove 里调 GanttData.patchProjectPlan（PATCH
//       /api/core/planner/projects/{id}），不开专用端点。
//   D7  事实层锁死在数据层已经是 editable:false；这里 onItemMove 里对
//       非 plan 的 item 再挡一次（双保险，不是单点依赖）。
//   D8  onItemMove 无论成败都先 callback(null) 撤销 vis 自己的即时移动，
//       PATCH 成功后整批 refreshAll() 重画——不做本地乐观更新。
//   D9  未排期项目只有事实层 item；行标签里的"未排期"徽标 + "排期"按钮
//       打开 scheduleDialog，提交后走同一条 PATCH。
//       L6（2026-08-09）扩展：已排期的项目/任务改用"改期"按钮（同一个
//       scheduleDialog，openScheduleDialog() 按 kind 决定预填与 PATCH
//       目标），不改未排期项目的创建流程、不给未排期任务新增入口
//       （F-GANTT-7 仍不在本轮范围）。
//   D10 fetch/patch 失败时用 statusBanner / 弹窗内 dialog-error 展示，
//       文案就是 GanttData.request() 归一化出的 message（后端 detail 原样）。
//
// L2（2026-08-08）新增任务行（PRD F-GANTT-1/2）：
//   - buildGroupEntry() 按 model.kind 分流：项目行有子任务时挂
//     nestedGroups+showNested:false（默认折叠），任务行不再嵌套。折叠三角
//     的绘制与点击展开/收起是 vis-timeline 内置行为（.vis-nesting-group），
//     本文件不用手写手势。
//   - layerFilter 改成宽松子串匹配（"plan"/"actual"），让计划/事实两个
//     图层开关同时管住项目层与任务层的 item（两者是同一个语义轴）。
//   - onItemMove 按 className 精确匹配 "gantt-task-plan" 决定 PATCH 打
//     tasks/{id} 还是 projects/{id}，D6/D7/D8 三条纪律原样套用在任务层。
//   - 越界样式（任务计划超出项目计划窗口，或项目本身无计划）走 CSS 修饰类
//     gantt-task-plan--oob，本文件只挂类名不管颜色（同 D2 的分工）。
//
// L3（2026-08-08）新增依赖箭头（timeline-arrows，见 index.html 的 module
// 桥接）：ensureArrows()/createArrowsInstance()/syncArrows() 管理**单例**
// Arrow 实例（不随 render() 重建，理由见 ensureArrows 上方注释——vendor
// 没有 destroy()，重建会泄漏事件监听器）；applyArrowStateStyle() 逐条箭头
// 覆盖颜色/虚线（vendor 只支持整实例一个颜色，这里在自己代码里包一层做到
// 三态）。项目行的"依赖冲突"聚合章由 buildGroupEntry() 按
// model.hasConflict 挂，数据来自 GD.computeConflictProjectIds()（按全量
// 数据算，不受折叠/图层开关影响）。
"use strict";

(function () {
  var GD = window.GanttData;

  var els = {
    timeline: document.getElementById("ganttTimeline"),
    statusBanner: document.getElementById("statusBanner"),
    toast: document.getElementById("actionToast"),
    togglePlan: document.getElementById("togglePlan"),
    toggleActual: document.getElementById("toggleActual"),
    refreshBtn: document.getElementById("refreshGantt"),
    zoomButtons: document.querySelectorAll("[data-zoom-granularity]"),
    fitAllBtn: document.getElementById("fitAllGantt"),
    goTodayBtn: document.getElementById("goTodayGantt"),
    emptyState: document.getElementById("ganttEmpty"),
    scheduleDialog: document.getElementById("scheduleDialog"),
    scheduleForm: document.getElementById("scheduleForm"),
    scheduleModalAction: document.getElementById("scheduleModalAction"),
    scheduleProjectName: document.getElementById("scheduleProjectName"),
    scheduleStart: document.getElementById("scheduleStartInput"),
    scheduleEnd: document.getElementById("scheduleEndInput"),
    scheduleError: document.getElementById("scheduleDialogError")
  };

  var state = {
    view: null,             // 最近一次成功拉取的 {projects, today}
    planVisible: true,
    actualVisible: true,
    schedulingKind: null,   // "project" | "task"——L6 起排期/改期共用一套状态
    schedulingId: null,
    dragging: false         // 拖拽反馈用：是否正在追踪指针以定位落点浮标
  };

  var itemsDataSet = new window.vis.DataSet([]);
  var groupsDataSet = new window.vis.DataSet([]);
  var itemsView = new window.vis.DataView(itemsDataSet, { filter: layerFilter });
  var timeline = null;
  var customTimeAdded = false;
  var arrowsInstance = null; // L3：单例，跨 render() 复用（不重建，见下方 syncArrows 的理由）

  // vis-timeline 的 group.content 若给字符串会经内置 xss 转义再塞进
  // innerHTML（HTML 标签被转义成字面文本显示，量出来的行高也会跟着乱）——
  // 所以行标签在这里用 DOM API 现造 Element 再交给 vis（content 是 Element
  // 时 vis 走 appendChild，不经过转义）。GD.buildGroupModels() 只给数据
  // 模型（是否未排期/kind/taskIds），怎么画是这个文件的活。
  //
  // L2：项目行（kind:"project"）多出 nestedGroups + showNested:false（默认
  // 折叠到项目层，展开见任务条——PRD F-GANTT-1）；折叠/展开的三角图标与点击
  // 交互是 vis-timeline 内置的（.vis-nesting-group 的 ::before，见
  // vendor/README.md），本文件不用自己写手势。
  // 任务行（kind:"task"）本轮只给"未排期"纯文字提示，不接排期按钮/弹窗——
  // F-GANTT-7（任务空态链任务页）是下一轮的活，这里不顺手加交互面（明确不做）。
  //
  // L3：项目行按 model.hasConflict（GD.computeConflictProjectIds() 按全量
  // 数据算，见 gantt-data.js）挂一个琥珀小章——折叠/展开、图层开关都不影响
  // 这个章的有无，因为它反映的是数据事实，不是当前视图状态（PRD「折叠态
  // 冲突不许隐身」）。
  function buildGroupEntry(model) {
    var wrap = document.createElement("div");
    wrap.className = model.kind === "task" ? "gantt-row-label gantt-row-label--task" : "gantt-row-label";

    var name = document.createElement("span");
    name.className = "gantt-row-name";
    name.textContent = model.name;
    wrap.appendChild(name);

    // UI 重做：左侧信息列补起/止/工期（对照 Bryntum 数据表列，信息密度差距
    // 最大的一项）。只在已排期时才有内容——未排期已经有"未排期"徽标
    // 说明状态，两者不重复。数字用等宽字体对齐（style.css .gantt-row-meta）。
    if (model.plan) {
      var meta = document.createElement("span");
      meta.className = "gantt-row-meta";
      var days = GD.planDurationDays(model.plan);
      meta.textContent = GD.formatPlanRange(model.plan) + (days ? " · " + days + "d" : "");
      wrap.appendChild(meta);
    }

    if (model.kind === "task" && model.done) {
      var doneBadge = document.createElement("span");
      doneBadge.className = "gantt-row-badge gantt-row-badge--done";
      doneBadge.textContent = "已完成";
      wrap.appendChild(doneBadge);
    }

    if (model.kind === "project" && model.hasConflict) {
      var conflictBadge = document.createElement("span");
      conflictBadge.className = "gantt-row-badge gantt-row-badge--conflict";
      conflictBadge.title = "本项目内有任务依赖排期冲突（子任务可能被折叠隐藏，但冲突仍存在）";
      conflictBadge.textContent = "依赖冲突";
      wrap.appendChild(conflictBadge);
    }

    if (model.unscheduled) {
      var badge = document.createElement("span");
      badge.className = "gantt-row-badge";
      badge.textContent = "未排期";
      wrap.appendChild(badge);

      if (model.kind !== "task") {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "gantt-row-schedule";
        btn.textContent = "排期";
        btn.setAttribute("data-project-id", model.id);
        btn.setAttribute("data-project-name", model.name);
        wrap.appendChild(btn);
        wrap.classList.add("gantt-row-label--tap"); // 触控 ≥44px（--tap），见 style.css 对应注释
      }
    } else if (model.plan) {
      // 已排期的编辑入口（本轮新增，用户反馈 2）：项目、任务都给，复用同一个
      // scheduleDialog，靠 data-entity-* 系列属性带够「打开时要预填什么、
      // 提交时该打哪条 PATCH」，onTimelineContainerClick 只管读属性不猜。
      // 未排期任务本轮仍不给入口（F-GANTT-7 还没做，「排期」创建流程本身
      // 不存在，谈不上编辑），只有 model.plan 存在（已排期）才出现。
      var editBtn = document.createElement("button");
      editBtn.type = "button";
      editBtn.className = "gantt-row-reschedule";
      editBtn.textContent = "改期";
      editBtn.setAttribute("data-entity-kind", model.kind);
      editBtn.setAttribute("data-entity-id", model.id);
      editBtn.setAttribute("data-entity-name", model.name);
      editBtn.setAttribute("data-plan-start", model.plan.start);
      editBtn.setAttribute("data-plan-end", model.plan.end);
      editBtn.setAttribute("aria-label", "改期 " + model.name);
      wrap.appendChild(editBtn);
      wrap.classList.add("gantt-row-label--tap"); // 触控 ≥44px（--tap），见 style.css 对应注释
    }

    var entry = { id: model.id, content: wrap };
    // 只在真的有任务子行时才给 nestedGroups——防御性写法：不确定 vis 对空数组
    // nestedGroups 的处理是否会画出一个点不动的折叠三角，没必要冒这个险，
    // 没有任务的项目应该看起来和 L2 之前完全一样。
    if (model.kind === "project" && model.taskIds && model.taskIds.length > 0) {
      entry.nestedGroups = model.taskIds;
      entry.showNested = false; // 默认折叠到项目层（PRD F-GANTT-1）
    }
    return entry;
  }

  // "plan"/"actual" 子串同时命中项目层（gantt-plan/gantt-actual）与任务层
  // （gantt-task-plan/gantt-task-actual）——两个图层开关按钮管的是"计划 vs
  // 事实"这个语义轴，不分项目/任务粒度，故意让它们共用同一个开关。
  function layerFilter(item) {
    if (item.className.indexOf("plan") !== -1) return state.planVisible;
    if (item.className.indexOf("actual") !== -1) return state.actualVisible;
    return true;
  }

  // ── 错误 / 提示 ──────────────────────────────────────────────────
  function showError(message) {
    els.statusBanner.textContent = message;
    els.statusBanner.classList.add("show");
  }
  function hideError() {
    els.statusBanner.classList.remove("show");
    els.statusBanner.textContent = "";
  }
  var toastTimer = null;
  function showToast(message) {
    els.toast.textContent = message;
    els.toast.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { els.toast.classList.remove("show"); }, 2600);
  }

  // ── 拉数据 → 建 Timeline（D10：失败给可见提示，detail 原样展示）───────
  function refreshAll() {
    return GD.fetchGanttView().then(function (result) {
      if (!result.ok) {
        showError("加载甘特数据失败：" + result.message);
        return;
      }
      hideError();
      state.view = result.data;
      render();
    });
  }

  function render() {
    var projects = (state.view && state.view.projects) || [];
    var todayStr = state.view && state.view.today;

    groupsDataSet.clear();
    groupsDataSet.add(GD.buildGroupModels(projects).map(buildGroupEntry));
    itemsDataSet.clear();
    itemsDataSet.add(GD.buildItems(projects));

    var empty = projects.length === 0;
    els.emptyState.hidden = !empty;
    els.timeline.hidden = empty;
    if (empty) return;

    if (!timeline) {
      // start/end 必须在构造 Timeline 时就传进 options，不能之后再靠
      // setWindow() 补——vis 在没给 start/end 时会照着已有 item 的时间跨度
      // 异步（下一帧）自己 fit 一次，事后 setWindow() 会被这次内部 fit
      // 竞态覆盖回去（真机验证过：setWindow 后立即 getWindow 是对的，几百
      // 毫秒后又被拽回窄窗口）。今天 todayStr 只用于计算默认窗口两端，
      // 不是"取现在"，跟 D5 的红线约束不是一回事。
      timeline = new window.vis.Timeline(els.timeline, itemsView, groupsDataSet, buildOptions(todayStr));
    }

    if (todayStr) {
      var todayDate = GD.localDateFromISO(todayStr); // 唯一"今天"来源：后端 today，不是客户端时钟
      if (!customTimeAdded) {
        timeline.addCustomTime(todayDate, "today");
        timeline.setCustomTimeMarker("今天", "today");
        customTimeAdded = true;
      } else {
        timeline.setCustomTime(todayDate, "today");
      }
    }

    ensureArrows(projects);
  }

  // ── L3：依赖箭头（timeline-arrows，见 index.html 里的 type="module" 桥接）──
  // Arrow 实例只建一次、跨 render() 复用，不重建——读 vendor 源码确认过：
  // 每个 Arrow 实例会给 timeline 挂一个"changed"事件监听且没有 destroy()，
  // 重建等于每次 refreshAll() 都新增一个不会被回收的监听器 + 一份残留 SVG
  // （内存泄漏）。所以数据变化靠"同步"而不是"重建"：每次 render() 都把当前
  // 目标箭头集合与实例里已有的做一次全量差异（先清空再按目标重加，箭头量
  // 对个人时间管理工具的规模不构成性能问题），用公开的 addArrow/removeArrow/
  // getIdArrows，不碰内部 _dependency 数组本身。
  function ensureArrows(projects) {
    if (arrowsInstance) { syncArrows(projects); return; }
    if (!window.TimelineArrow) {
      window.addEventListener("timeline-arrows:ready", function onReady() {
        window.removeEventListener("timeline-arrows:ready", onReady);
        if (arrowsInstance) return; // 双重保险：等待期间可能已经建过
        createArrowsInstance();
        syncArrows((state.view && state.view.projects) || []);
      });
      return;
    }
    createArrowsInstance();
    syncArrows(projects);
  }

  function createArrowsInstance() {
    // color/strokeWidth 只是这个实例的"默认值"（vendor 只支持整实例一个颜色），
    // 真正的三态上色靠 applyArrowStateStyle() 逐条覆盖，这里给的是保险默认。
    arrowsInstance = new window.TimelineArrow(timeline, [], {
      color: "var(--ink-2)",
      strokeWidth: 1.5,
      hideWhenItemsNotVisible: true
    });
  }

  function syncArrows(projects) {
    if (!arrowsInstance) return;
    var targetArrows = GD.buildDependencyArrows(projects);
    arrowsInstance.getIdArrows().forEach(function (id) { arrowsInstance.removeArrow(id); });
    targetArrows.forEach(function (spec) {
      arrowsInstance.addArrow(spec);
      applyArrowStateStyle(spec.state);
    });
  }

  // timeline-arrows 的公开 API 没有"这一条箭头用什么颜色"的入口（color 是
  // 整个实例共用一份），所以直接找刚 addArrow() 推进去的那条 <path> 现改
  // style——读源码确认 _dependency/_dependencyPath 两个数组永远同步 push，
  // 下标对得上；用 Array.isArray 防御，万一 vendor 未来版本改了内部结构，
  // 优雅退化成"没有专属颜色"而不是抛错（不改 vendor 文件本身，行为包在
  // 这一层，同 D2 的分工原则）。三态：
  //   normal      var(--ink-2) 细线（默认色，这里其实可以不设，写出来是显式）
  //   conflict    var(--warn) 实线（PRD：冲突用琥珀）
  //   unscheduled var(--ink-3) 虚线（任一端未排期，不判冲突，只提示"这条不完整"）
  function applyArrowStateStyle(state) {
    var paths = arrowsInstance && arrowsInstance._dependencyPath;
    if (!Array.isArray(paths) || paths.length === 0) return;
    var path = paths[paths.length - 1];
    if (!path || !path.style) return;
    if (state === "conflict") {
      path.style.stroke = "var(--warn)";
      path.style.strokeDasharray = "";
    } else if (state === "unscheduled") {
      path.style.stroke = "var(--ink-3)";
      path.style.strokeDasharray = "4 3";
    } else {
      path.style.stroke = "var(--ink-2)";
      path.style.strokeDasharray = "";
    }
  }

  // 滚轮连续缩放的上下限（F-GANTT-5 前半句）：近端别缩到看不出"天"这个刻度
  // 有意义（<1 天没有产品语义），远端别缩到十年那种谁都读不出信息的尺度。
  // 单位是毫秒，vis-timeline 原生 zoomMin/zoomMax 选项。
  var ZOOM_MIN_MS = 1000 * 60 * 60 * 18;              // 18 小时：留一点余量，免得刚好卡在 1 天
  var ZOOM_MAX_MS = 1000 * 60 * 60 * 24 * 365 * 4;    // 4 年

  function buildOptions(todayStr) {
    var options = {
      stack: false,                 // D1：两层叠在同一行，不许上下两轨
      showCurrentTime: false,       // 红线只认后端 today；vendor 自带那条走浏览器时钟，关掉
      editable: false,              // 全局默认锁死，计划 item 自己声明 editable（D7 双保险）
      selectable: true,
      zoomable: true,
      zoomMin: ZOOM_MIN_MS,
      zoomMax: ZOOM_MAX_MS,
      moveable: true,
      orientation: "top",
      // groupHeightMode 留默认（auto：取"行标签自然高度"与"行内 item 占用
      // 高度"两者较大值）。试过 "fitItems"——它只按 item 量行高、不管标签，
      // 未排期行的徽标+按钮换行后被下一行盖住，真机截图看出来的，别再切。
      margin: { item: { horizontal: 0, vertical: 10 } }, // UI 重做：行间留白从 4 放宽到 10
      tooltip: { followMouse: true, overflowMethod: "cap" },
      onMoving: onItemMoving,
      onMove: onItemMove
    };
    if (todayStr) {
      // 默认窗口以后端今天为中心，偏向未来（甘特更该露出"要干的"）。
      options.start = GD.localDateFromISO(GD.addDaysISO(todayStr, -7));
      options.end = GD.localDateFromISO(GD.addDaysISO(todayStr, 21));
    }
    return options;
  }

  // ── 缩放工具栏（F-GANTT-5 后半句：日/周/月/季按钮 + 适应全部 + 回到今天）──
  function onSetGranularity(granularity) {
    var todayStr = state.view && state.view.today;
    var win = todayStr ? GD.zoomWindowForGranularity(todayStr, granularity) : null;
    if (!win || !timeline) return;
    timeline.setWindow(GD.localDateFromISO(win.start), GD.localDateFromISO(win.end));
  }

  function onFitAll() {
    if (timeline) timeline.fit();
  }

  function onGoToday() {
    var todayStr = state.view && state.view.today;
    if (!todayStr || !timeline) return;
    timeline.setWindow(
      GD.localDateFromISO(GD.addDaysISO(todayStr, -7)),
      GD.localDateFromISO(GD.addDaysISO(todayStr, 21))
    );
  }

  // ── 拖拽反馈（本轮五条痛点之二）───────────────────────────────────────
  // 落点日期浮标：拖拽过程中跟着鼠标显示"当前松手会落到哪天"。这是纯视觉
  // 预览，不落库——onMoving 的 callback(item) 只是允许 vis 画出这个拖拽中的
  // 临时位置，D8 管的是"最终这一下算不算数"（onMove），两者不是一回事。
  var dragBadge = document.createElement("div");
  dragBadge.className = "gantt-drag-badge";
  dragBadge.hidden = true;
  document.body.appendChild(dragBadge);

  var lastPointer = { x: 0, y: 0 };
  function trackPointer(evt) {
    lastPointer.x = evt.clientX;
    lastPointer.y = evt.clientY;
    positionDragBadge();
  }
  function positionDragBadge() {
    dragBadge.style.left = (lastPointer.x + 16) + "px";
    dragBadge.style.top = (lastPointer.y + 16) + "px";
  }

  function onItemMoving(item, callback) {
    if (!state.dragging) {
      state.dragging = true;
      document.addEventListener("pointermove", trackPointer);
    }
    dragBadge.textContent = GD.formatLocalISO(item.start) + " → " + GD.formatLocalISO(item.end);
    dragBadge.hidden = false;
    positionDragBadge();
    callback(item); // 只是拖拽中的视觉预览，不写库；是否采纳落在 onMove（D8）
  }

  function stopDragFeedback() {
    document.removeEventListener("pointermove", trackPointer);
    state.dragging = false;
    dragBadge.hidden = true;
  }

  // D6+D7+D8：拖拽结束回调。只放行 plan item（项目层或任务层）；成败都不
  // 采纳 vis 自己的即时移动（callback(null)），PATCH 成功后整批重拉重画——
  // 这条纪律本轮不改。本轮新增的是"松手之后"到"PATCH 回来"这段中间态的
  // 视觉表达：不再是「弹回＝不知道成没成」，而是加一个独立的半透明 pending
  // 幽灵 item 顶在原 item 上方，标示"正在提交"；真正的 item 数据（origin
  // dataset）从没被改过（仍然是 D8 说的"不做乐观更新"），pending 幽灵只是
  // 一层视觉指示，PATCH 成功后整批 refreshAll() 自然覆盖掉它，失败则单独
  // 移除 + 报错——失败才是真正的"弹回"，跟"提交中"是两种状态，不再混在一起。
  // L2：className 精确匹配 "gantt-task-plan"（不是宽松的 "plan" 子串）才走
  // 任务端点——任务 item 的 className 同时含 "gantt-task-plan" 与越界修饰类，
  // 用 indexOf 找精确串不受修饰类影响；项目 item 的 className 不含
  // "gantt-task-plan"，两者不会互相误判（indexOf("gantt-task-plan") 在纯项目
  // className "gantt-item gantt-plan" 里必为 -1，反过来也一样，逐字符核对过）。
  function onItemMove(item, callback) {
    stopDragFeedback();

    var isTaskPlan = item.className.indexOf("gantt-task-plan") !== -1;
    var isProjectPlan = item.className.indexOf("gantt-plan") !== -1 && !isTaskPlan;
    if (!isTaskPlan && !isProjectPlan) { callback(null); return; }

    var entityId = item.group; // 任务 item 的 group 是 task.id，项目 item 的 group 是 project.id（同一约定）
    var plan = { start: GD.formatLocalISO(item.start), end: GD.formatLocalISO(item.end) };
    var patchFn = isTaskPlan ? GD.patchTaskPlan : GD.patchProjectPlan;
    var pendingId = item.id + ":pending";

    callback(null); // D8：永远先撤销 vis 自己的即时移动，真相以刷新为准，这条不变

    // 半透明 pending 态：叠加一个不可选中/不可拖的幽灵 item 在原位置（拖拽
    // 落点），而不是碰原 item 的数据——item 原样还在旧位置，pending 幽灵才
    // 是"看起来去了新位置、正在等服务端确认"的那一层。
    itemsDataSet.add({
      id: pendingId,
      group: item.group,
      start: item.start,
      end: item.end,
      type: item.type || "range",
      className: item.className + " gantt-pending",
      content: "",
      editable: false,
      selectable: false
    });

    patchFn(entityId, plan).then(function (result) {
      itemsDataSet.remove(pendingId); // 成功：马上 refreshAll 用真相覆盖；失败：单独摘掉，只留报错
      if (!result.ok) {
        showError("保存改期失败：" + result.message);
        return;
      }
      showToast("已保存改期");
      refreshAll();
    });
  }

  // ── 图层开关（D4：两个独立大按钮，aria-pressed 标开关态）───────────────
  function setLayerButtonState(btn, visible) {
    btn.setAttribute("aria-pressed", visible ? "true" : "false");
    btn.classList.toggle("is-active", visible);
  }

  function onTogglePlan() {
    state.planVisible = !state.planVisible;
    setLayerButtonState(els.togglePlan, state.planVisible);
    itemsView.refresh();
  }

  function onToggleActual() {
    state.actualVisible = !state.actualVisible;
    setLayerButtonState(els.toggleActual, state.actualVisible);
    itemsView.refresh();
  }

  // ── 排期弹窗（D9：未排期项目从这里排期；L6：已排期项目/任务从这里改期）───
  // kind 固定是 "project" 或 "task"；prefillStart/prefillEnd 有值时代表
  // "改期"（预填当前 plan，弹窗标题换成"改期"），留空代表"排期"（默认区间
  // 走 GD.defaultScheduleRange，标题"排期"）——两条路径共用同一份表单/校验/
  // PATCH 分流逻辑，不重复实现一个几乎一样的弹窗。
  function openScheduleDialog(kind, id, name, prefillStart, prefillEnd) {
    state.schedulingKind = kind;
    state.schedulingId = id;
    var isEdit = !!(prefillStart && prefillEnd);
    els.scheduleModalAction.textContent = isEdit ? "改期" : "排期";
    els.scheduleProjectName.textContent = name || "";
    if (isEdit) {
      els.scheduleStart.value = prefillStart;
      els.scheduleEnd.value = prefillEnd;
    } else {
      var todayStr = state.view && state.view.today;
      var range = todayStr ? GD.defaultScheduleRange(todayStr) : { start: "", end: "" };
      els.scheduleStart.value = range.start;
      els.scheduleEnd.value = range.end;
    }
    els.scheduleError.hidden = true;
    els.scheduleError.textContent = "";
    els.scheduleDialog.showModal();
  }

  // 排期/改期按钮渲染在 vis 的行标签 HTML 里（每次 render 都会重建 DOM），
  // 所以用委托监听挂在容器上，不直接绑单个按钮引用。两个按钮各自的 data-*
  // 属性名不同（历史原因：.gantt-row-schedule 是 D9 就有的老属性名，
  // .gantt-row-reschedule 是本轮新增），分两个 closest() 判断，不强行合并
  // 成一套属性名去动老按钮已经在用的标记。
  function onTimelineContainerClick(evt) {
    var createBtn = evt.target.closest && evt.target.closest(".gantt-row-schedule");
    if (createBtn) {
      openScheduleDialog("project", createBtn.getAttribute("data-project-id"), createBtn.getAttribute("data-project-name"));
      return;
    }
    var editBtn = evt.target.closest && evt.target.closest(".gantt-row-reschedule");
    if (!editBtn) return;
    openScheduleDialog(
      editBtn.getAttribute("data-entity-kind"),
      editBtn.getAttribute("data-entity-id"),
      editBtn.getAttribute("data-entity-name"),
      editBtn.getAttribute("data-plan-start"),
      editBtn.getAttribute("data-plan-end")
    );
  }

  function onScheduleSubmit(evt) {
    evt.preventDefault();
    var start = els.scheduleStart.value;
    var end = els.scheduleEnd.value;
    if (!start || !end) {
      els.scheduleError.textContent = "开始和结束日期都要填";
      els.scheduleError.hidden = false;
      return;
    }
    // kind 决定打哪条 PATCH——项目 patchProjectPlan、任务 patchTaskPlan，
    // 两条本来就是既有端点（D6/L2 同款纪律：不为甘特开专用端点）。
    var patch = state.schedulingKind === "task" ? GD.patchTaskPlan : GD.patchProjectPlan;
    patch(state.schedulingId, { start: start, end: end }).then(function (result) {
      if (!result.ok) {
        els.scheduleError.textContent = result.message; // D10：后端 detail 原样展示
        els.scheduleError.hidden = false;
        return;
      }
      els.scheduleDialog.close();
      showToast("已保存排期");
      refreshAll();
    });
  }

  function onDialogChromeClick(evt) {
    var trigger = evt.target.closest && evt.target.closest("[data-close-dialog]");
    if (!trigger) return;
    var dialog = trigger.closest("dialog");
    if (dialog) dialog.close();
  }

  // ── 绑定 ────────────────────────────────────────────────────────
  els.togglePlan.addEventListener("click", onTogglePlan);
  els.toggleActual.addEventListener("click", onToggleActual);
  els.refreshBtn.addEventListener("click", function () { refreshAll(); });
  els.zoomButtons.forEach(function (btn) {
    btn.addEventListener("click", function () { onSetGranularity(btn.getAttribute("data-zoom-granularity")); });
  });
  if (els.fitAllBtn) els.fitAllBtn.addEventListener("click", onFitAll);
  if (els.goTodayBtn) els.goTodayBtn.addEventListener("click", onGoToday);
  els.timeline.addEventListener("click", onTimelineContainerClick);
  els.scheduleForm.addEventListener("submit", onScheduleSubmit);
  els.scheduleDialog.addEventListener("click", onDialogChromeClick);

  setLayerButtonState(els.togglePlan, state.planVisible);
  setLayerButtonState(els.toggleActual, state.actualVisible);

  refreshAll();
})();
