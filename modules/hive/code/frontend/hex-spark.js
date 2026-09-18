/* table/frontend · hex-spark.js —— 中心圆环往项目六边形撒的轻粒子（2026-09-14）。
 *
 * 人类：「中间的圆环一个轻粒子动画，半透明稀疏粒子飞向项目六边形。」
 *
 * 语义：时间从中心那只表**流进正在计的那个项目**。所以粒子从圆环边缘出发（不是圆心），
 * 落到**当前计时任务所属项目**的那一格（人类 2026-09-14 第二句：「我要的是计时是飞向对应的
 * 六边形格子」——第一版是随机撒向所有项目格，看着热闹但不表达任何事实）。
 * 没在计时就一颗都不撒：没有时间在流，撒了就是假信号。
 *
 * 三条硬约束，都是本模块踩过的坑：
 *   1. **粒子活在相机层 `.hive-cam` 里**，用的是和格子同一套局部坐标（plan 的 left/top）。
 *      挂在 body 上就得处理推镜的 scale 与滚动，展开时会整片飞偏 —— 神灯替身那一版
 *      就是栽在"屏幕坐标 vs 局部坐标"上（hex-genie.js 文件头）。
 *   2. **只动 transform / opacity**，不用 filter、不用 box-shadow 动画：相机层本身在跑
 *      scale 过渡，带 filter 的后代每帧都要重新光栅化（hex.css「一律不用 filter」那条）。
 *   3. 同时在场的粒子数封顶 MAX_ALIVE；页面不可见（切标签页）时不生成 —— 后台标签里
 *      堆几百个 animation 是纯浪费。
 *
 * 减少动效（prefers-reduced-motion）下整段不跑：它纯装饰，没有任何信息。
 */
(function () {
  "use strict";

  var SPAWN_RUNNING_MS = 750;    // 计时中：大约每 0.75s 一颗
  var IDLE_CHECK_MS = 1500;      // 空闲：不撒，只是隔一会儿回来看看开始计时没有
  var FLY_MIN_MS = 1500, FLY_MAX_MS = 2400;
  var MAX_ALIVE = 8;
  var RING_R = 0.30;             // 出发点 = 中心格半宽的 30%（圆环那一圈上）

  var ctx = null, timer = null, alive = 0;

  function reduceMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  // 落点 = **正在计时的那个任务所属项目**的格子。没在计时、或者那个项目不在蜂巢里
  // （刚建还没重拉树、或者被归档），就返回 null —— 宁可不撒，也不乱撒到别人头上。
  function targetCell() {
    var st = ctx.state, cur = st && st.current;
    if (!cur || !cur.running || !cur.project) return null;
    var hit = st.byId && st.byId[cur.project.id];
    if (hit && hit.el && hit.el.__geom) return hit;
    var items = st.cells || [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      if (it && it.cell && it.cell.project && it.cell.project.id === cur.project.id) return it;
    }
    return null;
  }

  // 元素在相机层局部坐标里的中心（布局层算好写在 __geom 上，不用量 DOM——
  // 量 rect 会触发强制同步布局，而且推镜的 scale 会让屏幕像素对不上局部坐标）。
  function centerOf(el) {
    var g = el && el.__geom;
    if (!g || !g.w) return null;
    return { x: g.left + g.w / 2, y: g.top + g.h / 2 };
  }

  function spawn() {
    var layer = ctx.state && ctx.state.sparkLayer;
    var centerItem = ctx.state && ctx.state.centerItem;
    if (!layer || !centerItem || alive >= MAX_ALIVE) return;
    var from = centerOf(centerItem.el);
    if (!from) return;
    var pick = targetCell();
    if (!pick) return;
    var to = centerOf(pick.el);
    if (!to) return;

    var ang = Math.atan2(to.y - from.y, to.x - from.x);
    var r = (centerItem.el.__geom.w || 0) * RING_R;
    var sx = from.x + Math.cos(ang) * r, sy = from.y + Math.sin(ang) * r;
    var dx = to.x - sx, dy = to.y - sy;
    // 轻微弧线：中途往运动方向的法线偏一点，别走直线（直线看着像激光，不像飘）
    // 落点固定成同一格之后，弧度要拉开一点，否则一串粒子叠成一条直线像珠子（第一版随机落点
    // 时每颗航线都不同，看不出来）。左右各半、弯度 6%–18%。
    var side = Math.random() < 0.5 ? -1 : 1;
    var bow = Math.hypot(dx, dy) * (0.06 + Math.random() * 0.12) * side;
    var mx = dx * 0.5 - Math.sin(ang) * bow, my = dy * 0.5 + Math.cos(ang) * bow;

    var dot = document.createElement("i");
    dot.className = "hex-spark";
    dot.setAttribute("aria-hidden", "true");
    var size = 4 + Math.random() * 3;
    dot.style.width = dot.style.height = size.toFixed(1) + "px";
    dot.style.left = sx.toFixed(1) + "px";
    dot.style.top = sy.toFixed(1) + "px";
    // 颜色跟着落点那一格的分区色走。**不要去读 --zone-raw / --zone-color**：自定义属性的值
    // 常常还是 `var(--zone-1)` 这种引用，甚至是 oklch()，直接往 background 上写要么无效
    // 要么在旧浏览器上是黑块（真机量过：--zone-raw = "var(--zone-1)"）。
    // 格子**自身的 background** 就是那一圈亮分区色，已经被浏览器算成了具体色值，拿它最稳。
    var solid = window.getComputedStyle(pick.el).backgroundColor;
    if (solid && solid !== "transparent" && solid.indexOf("rgba(0, 0, 0, 0)") !== 0) {
      dot.style.setProperty("--c", solid);
    }
    layer.appendChild(dot);
    alive += 1;

    var ms = FLY_MIN_MS + Math.random() * (FLY_MAX_MS - FLY_MIN_MS);
    var anim = dot.animate([
      { transform: "translate3d(0,0,0) scale(.6)", opacity: 0 },
      { transform: "translate3d(" + mx.toFixed(1) + "px," + my.toFixed(1) + "px,0) scale(1)",
        opacity: 0.6, offset: 0.45 },
      { transform: "translate3d(" + dx.toFixed(1) + "px," + dy.toFixed(1) + "px,0) scale(.35)",
        opacity: 0 }
    ], { duration: ms, easing: "cubic-bezier(.32,.06,.62,1)", fill: "forwards" });
    anim.onfinish = anim.oncancel = function () {
      if (dot.parentNode) dot.parentNode.removeChild(dot);
      alive -= 1;
    };
  }

  function tick() {
    var running = !!(ctx.state && ctx.state.current && ctx.state.current.running);
    if (running && !document.hidden) spawn();
    timer = window.setTimeout(tick, running
      ? SPAWN_RUNNING_MS * (0.75 + Math.random() * 0.5)   // 抖一下，别踩成节拍器
      : IDLE_CHECK_MS);
  }

  // ⚠️ 相机层 `.hive-cam` 是**每次 mount 都重建**的（hex-app.js::mount），粒子层跟着一起没。
  // 所以 mount 收尾会再调一次 attach；这里按"层不在或不在当前相机层里就重建"写，重复调用安全。
  function attach() {
    if (!ctx || reduceMotion()) return;
    var cam = ctx.state && ctx.state.camEl;
    if (!cam) return;
    var layer = ctx.state.sparkLayer;
    if (!layer || layer.parentNode !== cam) {
      layer = document.createElement("div");
      layer.className = "hex-spark-layer";
      layer.setAttribute("aria-hidden", "true");
      cam.appendChild(layer);
      ctx.state.sparkLayer = layer;
    }
  }

  function init(c) {
    ctx = c;
    if (reduceMotion()) return;          // 纯装饰，减少动效下一颗都不撒
    attach();
    if (timer) clearTimeout(timer);
    timer = window.setTimeout(tick, IDLE_CHECK_MS);
  }

  window.NexusTableHexSpark = { init: init, attach: attach };
})();
