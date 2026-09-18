// table · 蜂巢空白处长按 → 长出一格 → 建项目（2026-09-08 从 hex-app.js 拆出）
//
// 人类原话：「我要的是整个分区长按点击任何一个黑色区域，都会在那里出现一个
// 六边形逐渐显现。」
//
// 上一版做成"每个分区预留一格占位格"，人类看了实物否掉了
// （「每个分区都多了一个高饱和度色块」）。抓手不在蜂巢里，在蜂巢**外面**：
// 分区的扇区一路铺到画布边上，那一片黑色都是这个分区的地盘。
//
// 拆出来的直接原因是 hex-app.js 又撞了 1000 行硬线（1015）；但这条缝
// 本来就在：本文件从头到尾只跟一个**不属于布局系统**的临时元素打交道 ——
// applyLayout 只摆 state.cells，这一格由本文件自己插自己删。
// 它的坐标却算在**同一个晶格**上（hex-data 的 pixelToAxial + plan 的原点平移量），
// 所以长出来的位置和真格子严丝合缝，不是"大概在那儿"。
(function () {
  "use strict";

  var H = window.NexusTableHexData;
  var L = window.NexusTableHexLayout;
  var C = window.NexusTableHexCards;
  var T = window.NexusTableHexTimer;

  var SIZE = L.SIZE, DRAW_S = L.DRAW_S;
  // 长按判定时长与格子上的长按**同一个数**（hex-timer.js 那一份）。
  // 显现动画也读它（--press-ms）：长满那一刻正好开火，
  // 「长到和别人一样就打开」这句话才成立。
  var HOLD_MS = T.LONGPRESS_MS;

  // hex-app.js 在 boot() 里注进来（state + reduceMotion），本文件够不着它的闭包。
  var ctx = null;
  function init(c) { ctx = c; }

  // ── 空白处长按 → 一个六边形在那儿长出来 → 建项目（2026-09-08）────────
  //
  // 人类原话：「我要的是整个分区长按点击任何一个黑色区域，都会在那里出现一个
  // 六边形逐渐显现。」
  //
  // 上一版做成"每个分区预留一格占位格"，人类看了实物否掉了
  // （「每个分区都多了一个高饱和度色块」）。抓手不在蜂巢里，在蜂巢**外面**：
  // 分区的扇区一路铺到画布边上，那一片黑色都是这个分区的地盘。
  //
  // 长出来的这一格**不进布局系统**：applyLayout 只摆 ctx.state.cells，这个元素
  // 由本节自己插自己删。但它的坐标算在**同一个晶格**上（pixelToAxial 的逆变换
  // + plan 的原点平移量），所以长出来的位置和真格子严丝合缝，不是"大概在那儿"。
  var sprout = null;                       // { el, q, r, zone, timer, fired }

  function removeSprout() {
    if (!sprout) return;
    if (sprout.timer) clearTimeout(sprout.timer);
    if (sprout.el && sprout.el.parentNode) sprout.el.parentNode.removeChild(sprout.el);
    sprout = null;
  }

  // 屏幕点 → 晶格坐标。和 angleFromCenter 用同一套换算：蜂巢中心在 cam 局部
  // 坐标里是 (ox, oy)，cam 自己还可能有缩放。
  function axialAtPoint(clientX, clientY) {
    var camEl = ctx.state.camEl;
    if (!camEl) return null;
    var r = camEl.getBoundingClientRect();
    var cam = ctx.state.lastCam || 1;
    var ox = (ctx.state.lastPlan && ctx.state.lastPlan.ox) || 0;
    var oy = (ctx.state.lastPlan && ctx.state.lastPlan.oy) || 0;
    return H.pixelToAxial((clientX - r.left) / cam - ox,
                          (clientY - r.top) / cam - oy, SIZE);
  }

  // 这个点能不能长出一格？能就返回 { q, r, zone }。
  function blankSpotAt(clientX, clientY) {
    if (ctx.state.expandedId) return null;          // 展开态整张图 ×MAG，坐标另说，不接
    if (!ctx.state.hive) return null;
    var ax = axialAtPoint(clientX, clientY);
    if (!ax) return null;
    if (ax.q === 0 && ax.r === 0) return null;  // 中心格是计时圆环
    var occupied = ctx.state.cells.some(function (it) {
      return it.cell.q === ax.q && it.cell.r === ax.r;
    });
    if (occupied) return null;
    // 立方距离 = 到中心几圈。只接蜂巢外沿再往外一圈，再远就不是"分区的地盘"了，
    // 而是画布上随便一块黑，在那儿长一格会和蜂巢断开。
    var ring = (Math.abs(ax.q) + Math.abs(ax.r) + Math.abs(ax.q + ax.r)) / 2;
    if (ring > (ctx.state.hive.radius || 0) + 1) return null;
    var deg = H.cellAngle(ax);
    var zone = null;
    (ctx.state.hive.zones || []).forEach(function (z) {
      if (!zone && H.inSector(deg, { start: z.startAngle, sweep: z.sweepDeg })) zone = z;
    });
    if (!zone) return null;
    return { q: ax.q, r: ax.r, zone: zone };
  }

  // 伪造一份 cell，好让 hex-cards.js 那套 HTML 生成原样复用（它只读这几个字段）。
  function sproutCell(spot) {
    return { q: spot.q, r: spot.r, zoneId: spot.zone.id, zoneName: spot.zone.name,
             color: spot.zone.color, project: null, placeholder: true };
  }

  function placeSprout(el, spot, w, h) {
    var p = H.axialToPixel(spot, SIZE);
    var ox = (ctx.state.lastPlan && ctx.state.lastPlan.ox) || 0;
    var oy = (ctx.state.lastPlan && ctx.state.lastPlan.oy) || 0;
    el.style.left = (p.x + ox - w / 2) + "px";
    el.style.top = (p.y + oy - h / 2) + "px";
    el.style.width = w + "px";
    el.style.height = h + "px";
  }

  // 长满了 → 把这一格**真的插进蜂巢**，然后按正常路径展开。
  //
  // 上一版是让这个临时元素自己变成卡片，实测很突兀：它不在布局系统里，
  // 于是整张蜂巢不放大、别的分区不变灰，一张 600×693 的卡片直接压在原尺寸的
  // 蜂巢上。人类要的是"在那里出现一个六边形"——出现之后它就该是**一格**，
  // 点开的排场和别的格子一模一样。
  // 所以这里把 spot 转成一个真的 placeholder cell 交给 hex-app：
  // mount 一次 + expand 一次，聚焦、放大、推镜全都自动跟上。
  function sproutOpen() {
    if (!sprout) return;
    sprout.fired = true;
    var ring = (Math.abs(sprout.q) + Math.abs(sprout.r) + Math.abs(sprout.q + sprout.r)) / 2;
    var cell = {
      q: sprout.q, r: sprout.r, ring: ring, angleDeg: H.cellAngle(sprout),
      zoneId: sprout.zone.id, zoneName: sprout.zone.name,
      color: sprout.zone.color, colorSlot: sprout.zone.colorSlot,
      colorSource: sprout.zone.colorSource, relaxed: false,
      project: null, placeholder: true,
      boundaryDirs: [],          // 界线不为它重算：它是临时的，建完就 refresh 掉
      mix: H.DEPTH_MIX.min,      // 和上面长出来的那一格同一档深浅，插进去不跳色
      __sprout: true             // removeSproutCell 靠它认人
    };
    removeSprout();              // 动画那个替身让位给真格子
    ctx.addSproutCell(cell);
  }

  function bindBlankPress(hive) {
    hive.addEventListener("pointerdown", function (ev) {
      if (ev.button !== undefined && ev.button !== 0) return;
      if (ev.target.closest(".hex-cell")) return;      // 按在格子上 → 归 hex-timer
      removeSprout();
      var spot = blankSpotAt(ev.clientX, ev.clientY);
      if (!spot) return;
      var el = document.createElement("div");
      el.className = "hex-sprout";
      el.style.setProperty("--zone-color", spot.zone.color);
      // 长出来的这一格在分区外侧 → 长到最外圈那一档浅色（与插进蜂巢后的 cell.mix 同一个值）
      el.style.setProperty("--zone-mix", H.DEPTH_MIX.min + "%");
      // 显现时长 = 长按判定时长。长满那一刻正好开火 —— 两个数不同源，
      // 「长到和别人一样就打开」这句话就不成立了。
      el.style.setProperty("--press-ms", HOLD_MS + "ms");
      placeSprout(el, spot, Math.sqrt(3) * DRAW_S, 2 * DRAW_S);
      ctx.state.camEl.appendChild(el);
      sprout = { el: el, q: spot.q, r: spot.r, zone: spot.zone, timer: null, fired: false };
      void el.offsetWidth;                              // 提交初态，否则没有过渡
      el.classList.add("is-growing");
      sprout.timer = window.setTimeout(function () {
        if (sprout) { sprout.timer = null; sproutOpen(); }
      }, HOLD_MS);
    });
    // 手抖／滑走／提前松手 → 还没长成就收回去。长成了的不收（那是建项目的卡片）。
    function giveUp(ev) {
      if (!sprout || sprout.fired) return;
      if (ev && ev.type === "pointermove" &&
          Math.abs(ev.movementX || 0) + Math.abs(ev.movementY || 0) < 2) return;
      removeSprout();
    }
    window.addEventListener("pointerup", giveUp);
    window.addEventListener("pointercancel", giveUp);
    window.addEventListener("pointermove", giveUp);
  }
  window.NexusTableHexSprout = {
    init: init,
    bind: bindBlankPress,
    removeSprout: removeSprout
  };
})();
