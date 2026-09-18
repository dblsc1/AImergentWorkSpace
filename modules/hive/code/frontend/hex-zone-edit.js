/* hive · hex-zone-edit.js —— 分区名：短按 / 长按进编辑模式拖角度（2026-09-12）。
 *
 * 从 hex-app.js 拆出（它逼近 1000 行；早就登记了"拖动那一族拆出去"）。
 *
 * 人类 2026-09-12：「短按分区跳出分区规划页面，长按做原来的拖动效果。要有一个放大而且
 * 所有分区的栏目都在摇晃（学习苹果的编辑模式）。」
 *
 *   短按            → ctx.onShortPress(zoneId)（分区规划页，页面本身另算）
 *   长按 LONGPRESS  → 进**编辑模式**：#hive 挂 .is-zone-edit，全部分区名放大 + 摇晃；
 *                     按住的那一个直接变成"拿在手里"，跟着指针走
 *   拖动中           → 手里气泡的中心越过相邻分区气泡的中线（再多 LIVE_MARGIN 度防抖），当场重排（实时预览）
 *   松手            → 按松手处的角度最终落位，**同时退出编辑模式**（人类：「松开自动停止晃动」）
 *   点空白 / Esc     → 兜底退出（正常情况下松手已经退了）
 *   双击            → 回到后端给的默认顺序（原有行为）
 *
 * 摇晃 / 放大都在里层 `.hex-zone-pill` 的 transform 上 —— 外层 `.hex-zone-label` 的位置由布局层写在
 * inline transform 里（translate3d），两层分开才互不覆盖、摇晃中心才在气泡中心。
 *
 * ── 拖分区名 = 给这个分区换一个**绕圈的角度**（2026-09-08 原注释，照搬）────────
 * 人类判词：「不是机械的移动六边形，而是根据分区名位置改变各分区的相对角度」。
 * 分区是**扇区**，扇区的角度由绕圈顺序决定。拖标签只改一件事：这个分区的目标角度。
 * 松手时把它按新角度插回序列，整张蜂巢重排 —— 它的格子会真的在那边重新长出来，
 * 和邻居仍然严丝合缝。拖动过程中只动标签自己，松手才提交一次，走 FLIP 动画。
 * 角度约定与 computeItems 里的标签摆位一致：0° = 正上方，顺时针增大；
 * 屏幕 atan2 的 0° 在正右方，所以要 +90。
 */
(function () {
  "use strict";

  var ctx = null;           // { state, L, saveZoneOrder, rebuildHive, onShortPress, longPressMs }
  var press = null;         // 按下到"判定为短按 / 长按"之间
  var dragging = null;      // 编辑模式里正拿在手里的那一个（重排时 .el 会被换成新元素）
  var MOVE_TOL = 6;         // 长按判定期间允许的抖动（px）；超过 = 不是长按，是划走了

  function hive() { return document.getElementById("hive"); }
  function editing() { var h = hive(); return !!(h && h.classList.contains("is-zone-edit")); }

  function enterEdit() {
    var h = hive();
    if (h) h.classList.add("is-zone-edit");
  }
  function exitEdit() {
    var h = hive();
    if (h) h.classList.remove("is-zone-edit");
  }

  function angleFromCenter(clientX, clientY) {
    var state = ctx.state, camEl = state.camEl;
    if (!camEl) return 0;
    var r = camEl.getBoundingClientRect();
    var cam = state.lastCam || 1;
    // 蜂巢中心在 cam 局部坐标里是 (ox, oy)（computeItems 的原点平移量）。
    var ox = (state.lastPlan && state.lastPlan.ox) || 0;
    var oy = (state.lastPlan && state.lastPlan.oy) || 0;
    var cx = r.left + ox * cam, cy = r.top + oy * cam;
    var deg = Math.atan2(clientY - cy, clientX - cx) * 180 / Math.PI + 90;
    return ((deg % 360) + 360) % 360;
  }

  // 把 zoneId 放到 targetDeg 这个角度上，其余分区保持它们当前的角度次序。
  function reorderByAngle(zoneId, targetDeg) {
    var others = ctx.state.hive.zones
      .filter(function (z) { return z.id !== zoneId; })
      .map(function (z) { return { id: z.id, a: z.centerAngle }; });
    others.push({ id: zoneId, a: targetDeg });
    others.sort(function (p, q) { return p.a - q.a; });
    return others.map(function (o) { return o.id; });
  }

  // ── 拖动：实时预览重排（人类 2026-09-12：「加一个浏览功能，拖动超过相邻分区的气泡多一点直接重新渲染」）──
  //
  // 手里这颗气泡的**中心**（不是指针 —— 抓的可能是气泡边上）一越过相邻分区气泡的中线（= 它的 centerAngle，
  // hex-layout.js 就把标签摆在这个角度上），就当场按新顺序重排整张蜂巢（和松手时同一条 rebuildHive），
  // 手里这颗气泡继续跟着指针走。人类第二次调：「触发阈值高了一点点，做成刚超过另一个分区的气泡中线就触发」
  // —— 回差从 8° 降到 2°，只防在中线上来回抖；冷却也缩短。重排之后邻居的中线会往回挪一段，
  // 本身就带着天然回差，不会反复横跳。
  //
  // ⚠️ 重排会重建所有标签元素 —— 手里那颗 DOM 节点会被换掉，它身上的 pointer capture 随之失效。
  // 所以拖动期间的 move / up 挂在 window 上（不依赖任何一个会被替换的元素），
  // 重排后按分区 id 找回新的标签元素接着跟手。
  var LIVE_MARGIN = 2;          // 度（原 8°，人类嫌阈值高）
  var LIVE_COOLDOWN = 250;      // ms，FLIP 刚起步就允许下一次，快速拖过好几个也跟得上

  function labelOf(zoneId) {
    var hit = ctx.state.labels.filter(function (l) { return l.zone.id === zoneId; })[0];
    return hit ? hit.el : null;
  }
  // 两个顺序"绕一圈看"是否相同（扇区是环，[a,b,c] 和 [b,c,a] 是同一张图）
  function sameCycle(a, b) {
    if (a.length !== b.length) return false;
    var k = a.indexOf(b[0]);
    if (k < 0) return false;
    for (var i = 0; i < a.length; i++) if (a[(k + i) % a.length] !== b[i]) return false;
    return true;
  }

  // 让手里这颗停在指针下（抓取点不变）。先回到布局给的基准位置量一次，再加偏移 ——
  // 重排之后基准位置变了（分区换了角度），这样算不用管它原来在哪。
  function follow(ev) {
    var el = dragging.el;
    if (!el) return;
    el.style.transition = "none";
    el.style.transform = el.__base || "";
    var r = el.getBoundingClientRect();
    var cam = ctx.state.lastCam || 1;
    var dx = (ev.clientX - dragging.grabX - (r.left + r.width / 2)) / cam;
    var dy = (ev.clientY - dragging.grabY - (r.top + r.height / 2)) / cam;
    el.style.transform = (el.__base || "") + " translate3d(" + dx.toFixed(1) + "px," + dy.toFixed(1) + "px,0)";
  }

  // 手里气泡中心的角度：指针减去抓取偏移
  function bubbleAngle(ev) {
    return angleFromCenter(ev.clientX - dragging.grabX, ev.clientY - dragging.grabY);
  }

  function maybeLiveReorder(ev) {
    var now = Date.now();
    if (now - dragging.lastLive < LIVE_COOLDOWN) return;
    var deg = bubbleAngle(ev);
    var order = reorderByAngle(dragging.id, deg);
    var current = ctx.state.hive.zones.map(function (z) { return z.id; });
    if (sameCycle(order, current)) return;
    // 回差：往自己原来的角度退回 MARGIN 度，顺序仍然是新的，才算"超过得多一点"
    var own = ctx.state.hive.zones.filter(function (z) { return z.id === dragging.id; })[0];
    var diff = ((deg - (own ? own.centerAngle : deg)) % 360 + 540) % 360 - 180;
    var back = deg - (diff > 0 ? 1 : -1) * LIVE_MARGIN;
    if (!sameCycle(reorderByAngle(dragging.id, back), order)) return;
    dragging.lastLive = now;
    dragging.live = true;
    ctx.state.zoneOrder = order;
    ctx.saveZoneOrder();
    ctx.rebuildHive(true);
    var fresh = labelOf(dragging.id);
    if (fresh) {
      dragging.el = fresh;
      fresh.classList.add("is-dragging");
    }
    // 布局层的 FLIP 在下一帧会把标签 transform 设回基准位置；排在它后面再跟一次手
    requestAnimationFrame(function () { if (dragging) follow(ev); });
  }

  function onDragMove(ev) {
    if (!dragging) return;
    var dx = ev.clientX - dragging.x0, dy = ev.clientY - dragging.y0;
    if (!dragging.moved && Math.abs(dx) + Math.abs(dy) < 3) return;
    dragging.moved = true;
    follow(ev);
    maybeLiveReorder(ev);
  }

  function onDragEnd(ev) {
    window.removeEventListener("pointermove", onDragMove);
    window.removeEventListener("pointerup", onDragEnd);
    window.removeEventListener("pointercancel", onDragEnd);
    if (!dragging) return;
    var d = dragging;
    dragging = null;
    // 松手即退出编辑模式（人类 2026-09-12：「晃动不会停止，弄成鼠标 / 手指松开自动停止编辑晃动模式」）
    exitEdit();
    if (d.el) d.el.classList.remove("is-dragging");
    var h = hive();
    if (h) h.classList.remove("is-reordering");
    if (!d.moved) { ctx.L.apply(false); return; }
    // 松手：按松手处的角度最终落位（实时预览时已经排过的，这一下只是让气泡回到自己的圈位上）。
    ctx.state.zoneOrder = reorderByAngle(d.id, angleFromCenter(ev.clientX - d.grabX, ev.clientY - d.grabY));
    ctx.saveZoneOrder();
    ctx.rebuildHive(true);
  }

  function pickUp(el, ev) {
    var r = el.getBoundingClientRect();
    dragging = { el: el, id: el.dataset.zoneId, x0: ev.clientX, y0: ev.clientY,
                 grabX: ev.clientX - (r.left + r.width / 2), grabY: ev.clientY - (r.top + r.height / 2),
                 moved: false, lastLive: 0, live: false };
    el.classList.add("is-dragging");
    var h = hive();
    if (h) h.classList.add("is-reordering");      // 拖动时把界线压淡，突出"在挪地"
    window.addEventListener("pointermove", onDragMove);
    window.addEventListener("pointerup", onDragEnd);
    window.addEventListener("pointercancel", onDragEnd);
  }

  function bind(el) {
    el.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      ev.preventDefault();
      ev.stopPropagation();                        // 别让长按计时 / 空白长按把它当成自己的
      try { el.setPointerCapture(ev.pointerId); } catch (e) { /* 合成事件没有真指针 */ }
      if (editing()) { pickUp(el, ev); return; }   // 编辑模式里按住就拿起来
      press = { el: el, x0: ev.clientX, y0: ev.clientY, fired: false };
      el.classList.add("is-pressing");
      press.timer = window.setTimeout(function () {
        if (!press || press.el !== el) return;
        press.fired = true;
        el.classList.remove("is-pressing");
        enterEdit();
        pickUp(el, { clientX: press.x0, clientY: press.y0 });
      }, ctx.longPressMs);
    });
    // 长按判定期间挪太远 = 不是长按（是想划走），撤销。拖动本身走 window 上的监听。
    el.addEventListener("pointermove", function (ev) {
      if (press && press.el === el && !press.fired &&
          Math.abs(ev.clientX - press.x0) + Math.abs(ev.clientY - press.y0) > MOVE_TOL) cancelPress();
    });
    function finish(ev) {
      try { el.releasePointerCapture(ev.pointerId); } catch (e) { /* 已经释放过 */ }
      if (press && press.el === el) {
        var wasShort = !press.fired;
        cancelPress();
        if (wasShort && ev.type === "pointerup") ctx.onShortPress(el.dataset.zoneId);
      }
    }
    el.addEventListener("pointerup", finish);
    el.addEventListener("pointercancel", finish);
    el.addEventListener("dblclick", function (ev) {
      ev.stopPropagation();
      ctx.state.zoneOrder = [];                    // 双击 = 回到后端给的默认顺序
      ctx.saveZoneOrder();
      ctx.rebuildHive(true);
    });
  }

  function cancelPress() {
    if (!press) return;
    if (press.timer) clearTimeout(press.timer);
    press.el.classList.remove("is-pressing");
    press = null;
  }

  function init(c) {
    ctx = c;
    // 退出编辑模式：点在分区名以外的任何地方 / Esc。捕获阶段，赶在别的 click 处理之前 ——
    // 编辑模式里点一下格子，意思是"我改完了"，不该顺手把那一格展开。
    document.addEventListener("click", function (ev) {
      if (!editing()) return;
      if (ev.target.closest && ev.target.closest(".hex-zone-label")) return;
      exitEdit();
      ev.stopPropagation();
      ev.preventDefault();
    }, true);
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && editing()) exitEdit();
    });
  }

  window.NexusTableHexZoneEdit = { init: init, bind: bind, exitEdit: exitEdit };
})();
