// hive · 蜂巢长按计时（2026-09-08 从 hex-app.js 拆出）
//
// 装的是两件在时间轴上连着、在代码上却各自独立的事：
//   · **长按 → 建任务 → 开始计时 → 飞进中央圆环**（人类 2026-09-08 的要求）
//   · **展开态的聚焦/灰度**（同一批要求里的"不要拆开整个蜂巢"）
// 放一起是因为它们都只**改类名和发请求**，一行布局都不算 —— 布局全在 hex-app.js。
//
// 拆出来的直接原因是 hex-app.js 撞了 1000 行硬线；但这条缝本来就在：
// 除了 init() 收下的那几个回调，这个文件不认识 hex-app 里的任何东西。
(function () {
  "use strict";

  var H = window.NexusTableHexData;
  var D = window.NexusTableData;

  // hex-app.js 在 boot() 里注进来的运行时上下文。**不在模块顶层去抓** ——
  // 那些函数定义在 hex-app 的闭包里，外面本来就够不着。
  var ctx = null;
  function init(c) { ctx = c; }
  function $(sel) { return document.querySelector(sel); }

  // ── 长按开始计时（2026-09-08 人类：「长按某个项目自动创建一个子任务
  // （标题就弄个日期+时间任务）开始计时」「长按子任务也自动开始计时」）──
  //
  // 全部是前端接线，后端一行不改：建任务走 data.js::createTask，
  // 起停走 code/ring 已在用的那两个端点（同一个后端单例 timer_state，
  // 所以这里一按，ring 页和顶栏状态条立刻同步 —— 见 contracts/timer-ring-visual-v1.md
  // 「通用性已经由后端单例保证」）。
  var LONGPRESS_MS = 520;   // 低于 ~450ms 会和"手抖的点击"混淆，高于 ~700ms 手感发木
  var LONGPRESS_SLOP = 10;  // 按下后挪超过这么多像素 = 想拖/想滚，不是想长按
  var FLY_MS = 620;
  var SWELL_MS = 3000;      // 圆环膨胀停留多久（人类定的：3 秒）
  var TIMER_START = "/api/core/timer/start";
  var TIMER_STOP = "/api/core/timer/stop";

  // ── 聚焦：展开时本分区亮，其余分区灰 ─────────────────────────
  //
  // 人类原话：「不要拆开整个六边形蜂巢，而是把这个分区放大的同时，灰度掉别的
  // 分区。营造一种屏幕聚焦在当前分区当前任务的感觉」。
  //
  // 放大在 computeItems 里（绕分区质心 ×FOCUS_ZOOM），压暗在这里，两件事分开：
  // 尺寸是布局、灰度是外观，混在一起会让 FLIP 去动 filter（合成器不便宜）。
  // 中心格**永不灰** —— 计时圆环是全场焦点，正好是这次聚焦要指向的地方。
  function applyFocus() {
    var fz = null;
    if (ctx.state.expandedId) {
      var it = ctx.state.byId[ctx.state.expandedId];
      if (it && it.cell) fz = it.cell.zoneId;
    }
    ctx.state.cells.forEach(function (it) {
      if (it.isCenter) { it.el.classList.remove("is-dimmed"); return; }
      it.el.classList.toggle("is-dimmed", fz !== null && it.cell.zoneId !== fz);
    });
    ctx.state.labels.forEach(function (l) {
      l.el.classList.toggle("is-dimmed", fz !== null && l.zone.id !== fz);
    });
    var hive = $("#hive");
    if (hive) hive.classList.toggle("is-focused", fz !== null);
  }

  // ── 长按 → 开始计时 ────────────────────────────────────────
  //
  // 两个抓手，同一条流水线：
  //   · 长按**项目格** → 先建一条以「日期 时间」命名的子任务，再对它计时；
  //   · 长按**待办卡片**（悬停面板里那三条，或展开卡「下一步」里那三条）
  //     → 那条任务已经存在，直接计时，不再建。
  //
  // 「中央已经有任务就自动停止再计时」不在前端判：/timer/start 之前无条件
  // 先发一次 /timer/stop（仅当 ctx.state.current.running），两步串行不并行 ——
  // 并行发后端有可能先收到 start 再收到 stop，结果把刚起的这段停了。
  // 飞进中央圆环 = 「神灯」动画（hex-genie.js）：被按的那一块按自己的形状被吸进中心格，
  // 离中心近的一头先进、尾巴最后（人类 2026-09-12：「mac 那种变形 - 移动到回收站 -
  // 从头到尾进入」）。只动脱离布局的替身，不碰蜂巢里任何一个真元素 —— 真元素这时候
  // 正被 refresh 重建，动它必然打架。prefers-reduced-motion 直接跳过。
  // 落点、形状、为什么上一版会飞偏，都写在 hex-genie.js 的文件头里。
  function flyToCenter(fromEl) {
    var centerEl = ctx.state.centerItem && ctx.state.centerItem.el;
    if (!centerEl || !fromEl || ctx.reduceMotion() || !window.NexusTableHexGenie) return;
    var hive = document.getElementById("hive");
    window.NexusTableHexGenie.run(fromEl, centerEl, hive && hive.parentNode, FLY_MS, function () {
      swellCenter(centerEl);
    });
  }

  // 落地反馈 = **中央圆环膨胀 3 秒再自动缩回**（人类 2026-09-08 第二轮：
  // 「不应该是看到六边形放大。正在计时的圆环膨胀 3 秒然后自动缩小就行」）。
  // 上一版是中心格 ::before 闪 520ms —— 太轻，而且"六边形变大"这件事
  // 被长按时那一格的悬停态顶上了，看着像放大了错的东西。
  // 计时器存在元素上：3 秒内连按两下，第二下要重新计时而不是被第一下的
  // 定时器提前掐掉（否则第二次膨胀只剩几百毫秒）。
  function swellCenter(centerEl) {
    if (!centerEl) return;
    if (centerEl.__swell) clearTimeout(centerEl.__swell);
    if (centerEl.__swellOut) clearTimeout(centerEl.__swellOut);
    // 人类 2026-09-11：「中间的计时圈干脆做一个自动放大 3 秒效果和鼠标放上去
    // 一样，背景颜色取计划色闪烁 1.5 次。然后缩小回原位。」
    //
    // 放大 = 直接进悬停态（hoverCenter → setHover，和鼠标放上去是同一段代码）。
    // 上一版自己在布局层乘一个 CATCH_MAG=1.5，和悬停档（×1.95）是两套尺寸、
    // 两套样式，看着就不像"鼠标放上去那样"。
    //
    // 闪烁的三个时长**全部从这里注进 CSS**，一个毫秒数都不在 hex.css 里写死：
    //   --flash-ms = SWELL_MS / 1.5   一个"常 → 计划色 → 常"周期，播 1.5 遍
    //                                 正好铺满放大的 3 秒，停在计划色上
    //   --swell-ms = SWELL_MS          第二段动画的起点（= 开始缩小的那一刻）
    //   --anim-ms  = 布局过渡时长       从计划色淡回常态，和缩小同一段时间
    // 这样"闪完、缩小、颜色回落"是同一条时间轴，不会出现"已经缩了颜色还亮着"
    // 或者"颜色先跳回去再缩"。
    var animMs = (ctx.animMs || 460);
    centerEl.style.setProperty("--flash-ms", (SWELL_MS / 1.5) + "ms");
    centerEl.style.setProperty("--swell-ms", SWELL_MS + "ms");
    centerEl.style.setProperty("--anim-ms", animMs + "ms");
    centerEl.classList.remove("is-catch");
    void centerEl.offsetWidth;                 // 重启 CSS 动画，否则同一个类不会再放一次
    centerEl.classList.add("is-catch");
    if (ctx.hoverCenter) ctx.hoverCenter(true);
    centerEl.__swell = window.setTimeout(function () {
      centerEl.__swell = null;
      if (ctx.hoverCenter) ctx.hoverCenter(false);       // 缩小回原位
      // is-catch 留到缩小走完：它身上挂着"从计划色淡回常态"那第二段动画。
      centerEl.__swellOut = window.setTimeout(function () {
        centerEl.__swellOut = null;
        centerEl.classList.remove("is-catch");
      }, animMs);
    }, SWELL_MS);
  }


  function startTimerOn(taskId) {
    var stopFirst = (ctx.state.current && ctx.state.current.running)
      ? ctx.postJson(TIMER_STOP, undefined)
      : Promise.resolve({ ok: true });
    return stopFirst.then(function (r) {
      if (!r.ok) return r;
      return ctx.postJson(TIMER_START, { taskId: taskId });
    });
  }

  // fromEl 只用来当动画起点；即使它随后被 refresh 从文档树上摘掉也没关系，
  // 替身在 body 上、rect 早就量完了。
  function longPressFire(fromEl, opts) {
    var label = opts.label || "";
    flyToCenter(fromEl);
    // 长按开火 = 焦点交给中央圆环，被按的那一格**立刻退出悬停放大**。
    // 人类原话：「不应该是看到六边形放大」。
    // 锁到飞行 + 膨胀走完为止，期间不让任何一格再长大。
    if (ctx.clearHover) ctx.clearHover(FLY_MS + SWELL_MS);
    // reduced-motion 下 flyToCenter 整段跳过，圆环那一下也就不会被触发；
    // 这里补一次，让"开始计时了"这个反馈在任何设置下都存在（CSS 侧会把
    // 膨胀动画降成静态放大）。
    if (ctx.reduceMotion()) swellCenter(ctx.state.centerItem && ctx.state.centerItem.el);
    ctx.setStatus("开始计时：" + label + "…");
    var got = opts.taskId
      ? Promise.resolve({ ok: true, data: { id: opts.taskId } })
      : D.createTask(opts.projectId, label);
    got.then(function (r) {
      if (!r.ok) { ctx.setStatus("建任务失败：" + r.message); return null; }
      var id = r.data && r.data.id;
      if (!id) { ctx.setStatus("建任务失败：后端没有返回任务 id"); return null; }
      return startTimerOn(id);
    }).then(function (r) {
      if (!r) return;
      if (!r.ok) { ctx.setStatus("开始计时失败：" + r.message); return; }
      return ctx.getJson(ctx.currentPath).then(function (c) {
        if (c.ok) { ctx.state.current = c.data; ctx.paintCenter(); }
        ctx.setStatus("正在计时：" + label);
        // 新建的子任务要出现在待办里，得重拉一次树；等飞行动画走完再拉，
        // 否则 mount() 会在动画中途把起点那一格换掉。
        if (!opts.taskId) window.setTimeout(function () { ctx.refresh(true); }, FLY_MS);
      });
    });
  }

  // 按下 → 计时 → 到点开火。任何位移/抬起/取消都撤销。
  // 用 pointer 事件一套覆盖鼠标与触摸（平板：手指按住不动就是长按，
  // 不需要 hover 这个在触摸屏上根本不存在的档）。
  var press = null;
  function pressCancel() {
    if (!press) return;
    if (press.timer) clearTimeout(press.timer);
    if (press.el) {
      press.el.classList.remove("is-pressing");
      press.el.style.removeProperty("--press-ms");
    }
    press = null;
  }
  function onPressDown(ev) {
    pressCancel();
    if (ev.button !== undefined && ev.button !== 0) return;
    if (ev.target.closest("[data-hex-action], input, textarea, button")) return;
    var card = ev.target.closest(".hex-todo-card[data-task-id]");
    var cell = ev.target.closest(".hex-cell");
    if (!cell || cell.classList.contains("hex-center")) return;
    // 占位格（分区里的空位）长按 = 建**项目**，不是建任务、不计时
    // （人类 2026-09-08：「长按分区空白区域可以添加项目」）。
    // 已经展开成表单的那一格排除掉 —— 那时候按住的是输入框所在的卡片。
    var isNew = cell.classList.contains("is-placeholder") &&
                !cell.classList.contains("is-expanded");
    var projectId = cell.dataset.projectId;
    if (!isNew && !projectId && !card) return;
    var opts = isNew
      ? { newProject: true }
      : card
      ? { taskId: card.dataset.taskId,
          label: (card.querySelector(".hex-todo-name, .hex-task-name") || card).textContent.trim() }
      : { projectId: projectId, label: H.stampTaskName() };
    var target = (!isNew && card) || cell;
    press = { el: target, x: ev.clientX, y: ev.clientY, fired: false, timer: null };
    // 充能动画的时长从 JS 注进 CSS。**不许两边各写一个 520** ——
    // 人类要的是"从淡到浓直到和已有六边形同色**就**打开"，动画早于或晚于
    // 开火都会让这句话变成假的。
    target.style.setProperty("--press-ms", LONGPRESS_MS + "ms");
    target.classList.add("is-pressing");
    press.timer = window.setTimeout(function () {
      if (!press) return;
      press.fired = true;
      press.timer = null;
      press.el.classList.remove("is-pressing");
      // 这一次的 click 是长按的尾巴，不能再当成"点击展开"。
      ctx.state.longPressAt = Date.now();
      if (opts.newProject) { ctx.openNewProject(cell); return; }
      longPressFire(target, opts);
    }, LONGPRESS_MS);
  }
  function onPressMove(ev) {
    if (!press || press.fired) return;
    if (Math.abs(ev.clientX - press.x) > LONGPRESS_SLOP ||
        Math.abs(ev.clientY - press.y) > LONGPRESS_SLOP) pressCancel();
  }
  // ⚠️ 松手时**重新打一次时间戳**，如果这一按已经开过火。
  //
  // 人类 2026-09-08 报的：「长按之后，短暂跳到中间圆环后，又回到了六边形
  // 放大视图」。病根是时间戳打在**开火那一刻**（按下满 LONGPRESS_MS 时），
  // 而吞 click 的窗口只有 700ms —— 按住超过约 1.2 秒再松手，那一次 click
  // 就落在窗口外，被当成普通点击，于是 expand() 把那一格展开了。
  // 计时是在长按开火时就开始的，所以画面顺序正是人类描述的那样：
  // 先飞进圆环，然后松手，然后莫名其妙弹出放大视图。
  // 窗口的起点本来就该是"手指离开"，不是"开火"——开火之后手还按着多久，
  // 完全由人决定，不该影响这一次 click 算不算长按的尾巴。
  function onPressUp() {
    if (press && press.fired) ctx.state.longPressAt = Date.now();
    pressCancel();
  }
  // 长按刚开过火 → 紧跟着那一次 click 要吞掉。用时间窗判定而不是一个
  // 长期布尔位：触摸上有可能根本不产生 click（被 preventDefault 或手指
  // 移出），布尔位会一直挂着，把下一次正常点击也吃了。
  function swallowClickAfterPress() {
    return ctx.state.longPressAt && (Date.now() - ctx.state.longPressAt) < 700;
  }


  window.NexusTableHexTimer = {
    // 长按判定时长只有这一份真相：CSS 的充能动画（--press-ms）与空白处
    // 长出六边形（hex-app.js::bindBlankPress）都从这里取。
    LONGPRESS_MS: LONGPRESS_MS,
    init: init,
    applyFocus: applyFocus,
    onPressDown: onPressDown,
    onPressMove: onPressMove,
    onPressUp: onPressUp,
    swallowClickAfterPress: swallowClickAfterPress
  };
})();
