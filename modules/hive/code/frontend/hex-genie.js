/* table/frontend · hex-genie.js —— 「神灯」动画：被长按的那一块被吸进中心圆环（2026-09-12）。
 *
 * 人类：「能不能做出 mac 那种变形 - 移动到回收站 - 从头到尾进入的动画效果？」
 * 同一句前面还报了两个毛病，这里一起治：
 *   ·「形状不对」：上一版替身是一个写死的正六边形，而且 `padding: 0 12%` 在
 *     position:fixed 下按**视口宽**算（1400 × 12% × 2 = 336px），把替身撑成 336 宽的扁块。
 *     现在替身**复刻被按的那一块**：格子就是它自己那条 --hex-clip 六边形，
 *     待办卡片就是圆角矩形，底色 / 字色 / 字号 / 文字全从源元素上量。
 *   ·「飞向的位置也不是中心」：同一个 336px 让替身中心整整偏右 83px（真机量的），
 *     落点也跟着偏；展开态下中心格在取景框外（y=1175 > 视口 1000），替身直接飞出屏幕。
 *     现在落点 = 中心格的**可见**中心；不可见时落到取景框边缘上朝向中心的那一点。
 *
 * 做法（纯 DOM + Web Animations，不用 canvas）：
 *   把替身沿"朝向目标"的那条轴切成 N 条。每一条 = 一个全尺寸的替身，外面套一层
 *   clip-path: inset() 只露出自己那一条。每条各自动画：先往目标方向弯、同时横向收窄
 *   （漏斗），再缩成一个点落进目标。离目标最近的那一条先走，其余依次跟上 ——
 *   这就是「从头到尾进入」。总时长 = 调用方给的 totalMs（hex-timer 的 FLY_MS），
 *   落地回调照旧在 totalMs 触发。
 */
(function () {
  "use strict";

  var STRIPS = 12;

  function faceOf(fromEl) {
    var isCell = fromEl.classList.contains("hex-cell");
    // 格子的"脸"画在 ::before 上（底色 + 六边形裁剪），元素本身只是一圈描边底色。
    var face = isCell ? getComputedStyle(fromEl, "::before") : getComputedStyle(fromEl);
    var own = getComputedStyle(fromEl);
    var titleEl = isCell ? fromEl.querySelector(".hex-title") : null;
    var text = ((titleEl ? titleEl.textContent : fromEl.textContent) || "").trim().split("\n")[0];
    return {
      clip: face.clipPath && face.clipPath !== "none" ? face.clipPath : "",
      radius: isCell ? "0" : own.borderRadius,
      bg: face.backgroundColor,
      color: own.color,
      fontSize: own.fontSize,
      text: text.length > 40 ? text.slice(0, 40) + "…" : text
    };
  }

  // 落点：目标元素在屏幕上**看得见**的那部分的中心。整块都不可见（展开态下中心格在
  // 取景框外）时，取「取景框 ∩ 视口」这个矩形里离目标中心最近的点 —— 吸向画面边缘
  // 朝着中心的方向，而不是飞出屏幕。
  function landingPoint(targetEl, frameEl) {
    var t = targetEl.getBoundingClientRect();
    var vw = window.innerWidth, vh = window.innerHeight;
    var f = frameEl ? frameEl.getBoundingClientRect() : { left: 0, top: 0, right: vw, bottom: vh };
    var box = {
      left: Math.max(0, f.left) + 16, top: Math.max(0, f.top) + 16,
      right: Math.min(vw, f.right) - 16, bottom: Math.min(vh, f.bottom) - 16
    };
    var cx = t.left + t.width / 2, cy = t.top + t.height / 2;
    return {
      x: Math.min(box.right, Math.max(box.left, cx)),
      y: Math.min(box.bottom, Math.max(box.top, cy))
    };
  }

  function run(fromEl, targetEl, frameEl, totalMs, onDone) {
    var a = fromEl.getBoundingClientRect();
    if (!a.width || !a.height || !targetEl || typeof fromEl.animate !== "function") {
      window.setTimeout(onDone, 0);
      return;
    }
    var face = faceOf(fromEl);
    var p = landingPoint(targetEl, frameEl);
    var acx = a.left + a.width / 2, acy = a.top + a.height / 2;
    // 沿哪条轴切：目标主要在上下方就横着切（一条条横带从下往上/从上往下吸），
    // 主要在左右就竖着切。Mac 的程序坞在底部，所以它总是横带；这里按方向自适应。
    var vertical = Math.abs(p.y - acy) >= Math.abs(p.x - acx);
    var towardEnd = vertical ? (p.y > acy) : (p.x > acx);   // 目标在"末端"那一侧（下 / 右）
    var perStrip = Math.max(160, totalMs * 0.62);
    var stagger = (totalMs - perStrip) / (STRIPS - 1);

    var host = document.createElement("div");
    host.className = "hex-genie";
    host.setAttribute("aria-hidden", "true");
    document.body.appendChild(host);

    for (var i = 0; i < STRIPS; i++) {
      var s0 = i / STRIPS * 100, s1 = (STRIPS - 1 - i) / STRIPS * 100;
      var band = document.createElement("div");
      band.className = "hex-genie-band";
      band.style.left = a.left + "px"; band.style.top = a.top + "px";
      band.style.width = a.width + "px"; band.style.height = a.height + "px";
      // 相邻两条各多露 0.4%，免得亚像素缝在动画开头闪出一道道细线
      band.style.clipPath = vertical
        ? "inset(" + Math.max(0, s0 - 0.4) + "% 0 " + Math.max(0, s1 - 0.4) + "% 0)"
        : "inset(0 " + Math.max(0, s1 - 0.4) + "% 0 " + Math.max(0, s0 - 0.4) + "%)";
      var faceEl = document.createElement("div");
      faceEl.className = "hex-genie-face";
      if (face.clip) faceEl.style.clipPath = face.clip;
      faceEl.style.borderRadius = face.radius;
      faceEl.style.background = face.bg;
      faceEl.style.color = face.color;
      faceEl.style.fontSize = face.fontSize;
      faceEl.textContent = face.text;
      band.appendChild(faceEl);
      host.appendChild(band);

      // 这一条的中心（屏幕坐标）与它离目标的先后
      var frac = (i + 0.5) / STRIPS;
      var bx = vertical ? acx : a.left + a.width * frac;
      var by = vertical ? a.top + a.height * frac : acy;
      var order = towardEnd ? (STRIPS - 1 - i) : i;           // 离目标最近的先走
      var dx = p.x - bx, dy = p.y - by;
      band.style.transformOrigin = (bx - a.left) + "px " + (by - a.top) + "px";
      // 漏斗：半路上横向（垂直于运动方向）先收窄、并往目标那条线上弯过去；
      // 最后整条缩成一点落进目标。缩放只作用在垂直于运动的那条轴上，
      // 沿运动方向的那条轴留到最后才收 —— 条带看起来是被"拉长着吸进去"的。
      var mid = vertical
        ? "translate(" + (dx * 0.7).toFixed(1) + "px," + (dy * 0.38).toFixed(1) + "px) scale(0.42,1)"
        : "translate(" + (dx * 0.38).toFixed(1) + "px," + (dy * 0.7).toFixed(1) + "px) scale(1,0.42)";
      // 一路保持源格子的分区色：中心格那边闪的也是分区色（人类 2026-09-12 定稿），
      // 替身撞进去就接上中心格的闪烁，不再中途变紫。
      var timing = { duration: perStrip, delay: order * stagger, easing: "cubic-bezier(.55,0,.8,.4)", fill: "both" };
      band.animate([
        { transform: "none", opacity: 1 },
        { transform: mid, opacity: 1, offset: 0.55 },
        { transform: "translate(" + dx.toFixed(1) + "px," + dy.toFixed(1) + "px) scale(0.04)", opacity: 0.15 }
      ], timing);                                            // 越近越快：被吸进去，不是飘过去
    }
    window.setTimeout(function () {
      if (host.parentNode) host.parentNode.removeChild(host);
      onDone();
    }, totalMs);
  }

  window.NexusTableHexGenie = { run: run, landingPoint: landingPoint };
})();
