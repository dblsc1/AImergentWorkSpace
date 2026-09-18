// table · 蜂巢分区界线层（2026-09-08 从 hex-app.js 拆出）
//
// 拆出来的理由不是"文件太长"这一条，虽然确实撞了 1000 行硬线：
// 这一层**只画不改状态** —— 输入是一份算好的 plan（边的像素坐标 + 平移量），
// 输出是几个 <path> 的 d 属性，不碰 state、不碰布局、不碰事件。
// 它和 hex-app.js 之间只有两个函数的接触面，本来就该是两个文件。
//
// 线宽常量跟着代码一起搬过来：偏移量与线宽必须同源（半线偏 ±W/4、宽 W/2），
// 留在 hex-app.js 会变成"改一个忘一个"的经典形状。
(function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";

  // 分界带：每条边拆成**两条半宽偏移线**，各描各那一侧分区的色。
  // W 是整条带子的宽度，单侧半线宽 W/2、沿法向偏移 ±W/4，两半刚好拼满 W。
  // 分界 = **单倍宽的双色细线**：一条边拆成两条半线，各描各那侧分区的色盘色。
  //
  // 上一版是一条 7.4px 的粗白线。粗线暴露了一个几何问题：每条边是独立子路径、
  // 端头 butt，**拐角处两段接不上**，粗到 7px 就是一排看得见的豁口（人类：
  // 「粗白线还不接头尾好丑」）。这一版两手一起治：
  //   · 回到单倍宽（槽 2×1.8=3.6，线也 3.6），细线本来就不容易露豁口；
  //   · 端头改 round，相邻两段在共享顶点上各盖半个圆头，正好合缝。
  var DIVIDER_W = 5.4;      // 内部分界线总宽（两条半线各 W/2、偏移 ±W/4）＝ 单倍宽 ×1.5
  var EXT_W = 4.5;          // 虚拟格延伸段（同比例）
  // ── 分区界线层：一个 SVG 盖在格子上，线画在格与格之间那条缝里 ──────
  //
  // 为什么现在画得出来、上一版画不出来：上一版格子放在极坐标圆周上，两格之间
  // **没有公共边**，"界线"无从谈起。换成轴向晶格之后每条边恰好被两格共享，
  // 边界边 = 对面那格不存在或属于别的分区（判据在 hex-data.js::zoneBorderEdges）。
  //
  // 分三层描，因为它们是三件事（2026-09-07 第三轮，人类：「加粗颜色分界线，
  // 并且颜色分界线也按照色盘来」「颜色分界线往外拓展，更显眼」）：
  //
  //   · 外沿（对面没格子）→ 描本分区的颜色，一眼看出"这一坨是谁的地盘"。
  //   · 区与区之间 → **加粗的双色带**：一条线，跨向渐变，左半边是左边那个分区的
  //     色盘色、右半边是右边那个的。上一版描的是中性灰（--ink-2），"分界"这件事
  //     跟色盘脱节，看不出分的是哪两个区。
  //   · **外延射线** → 格子之间那条边走到蜂巢外沿就断了，于是"这两个方向分属
  //     两个分区"在蜂巢外面完全看不出来，而分区名标签恰恰在外面。每两个相邻扇区
  //     之间补一条径向射线拉到标签圈，同样是双色带，内外接上。
  //
  // 双色带靠**每段一个 linearGradient**：渐变方向取边的法向（不是边的方向），
  // 端点跨度 = 描边宽度，50% 处硬切。真实数据只有个位数条分界，defs 不会爆。
  // 每分区一条外沿 <path>，分界与射线各自一个 <g>，布局变化时复用元素只改属性。
  function mountBorders(hive, zones) {
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "hex-borders");
    svg.setAttribute("aria-hidden", "true");
    var g = document.createElementNS(SVG_NS, "g");
    svg.appendChild(g);
    mountBorders.uid = (mountBorders.uid || 0) + 1;
    var defs = document.createElementNS(SVG_NS, "defs");
    svg.insertBefore(defs, g);
    var layer = { svg: svg, g: g, defs: defs, uid: mountBorders.uid,
                  outer: {}, half: {}, ext: {}, grads: {}, key: null };

    function addPath(cls, color) {
      var pth = document.createElementNS(SVG_NS, "path");
      pth.setAttribute("class", cls);
      if (color) pth.style.setProperty("--zone-color", color);
      g.appendChild(pth);
      return pth;
    }
    // 画的顺序就是压盖顺序：外沿在最下，然后延伸段，再内部分界，芯线最上。
    var ids = zones.map(function (z) { return z.id; });
    var colorOf = {};
    zones.forEach(function (z) { colorOf[z.id] = z.color; });
    ids.push("__center__"); colorOf.__center__ = "var(--line)";

    // 每个分区一条**径向渐变**：圆心就是蜂巢中心，同一个色相从中心往外逐渐变淡
    // （人类：「渐变色调还是色盘色调，从中心往外逐渐变浅」）。
    // 变的是 stop-opacity 不是色相 —— 分区的身份色必须一路保持同一个色相，
    // 中途换色就不是"同一个分区的边界"了；而且色相变化在 OKLCH 里根本不改明度，
    // 想让它"变浅"只能走透明度或明度，透明度对两个主题都成立。
    //
    // 坐标用 userSpaceOnUse，圆心写死 (0,0)：界线层的 <g> 上挂着 translate(ox,oy)，
    // 组内局部坐标系的原点就是蜂巢中心，布局怎么挪都不用改 cx/cy，只有半径要跟。
    function addGrad(id, color) {
      var g2 = document.createElementNS(SVG_NS, "radialGradient");
      g2.setAttribute("id", "hexrad" + layer.uid + "_" + id);
      g2.setAttribute("gradientUnits", "userSpaceOnUse");
      g2.setAttribute("cx", "0"); g2.setAttribute("cy", "0");
      // 色值本身写在 CSS（.hex-stop 那条规则），这里只挂变量和透明度 ——
      // 契约 A2：模块 JS 不产出色值，只产出变量名。
      g2.style.setProperty("--zone-color", color);
      [[0, 1], [0.55, 0.86], [1, 0.44]].forEach(function (st) {
        var sp = document.createElementNS(SVG_NS, "stop");
        sp.setAttribute("class", "hex-stop");
        sp.setAttribute("offset", (st[0] * 100) + "%");
        sp.style.stopOpacity = st[1];
        g2.appendChild(sp);
      });
      defs.appendChild(g2);
      layer.grads[id] = g2;
      return "url(#hexrad" + layer.uid + "_" + id + ")";
    }

    ids.forEach(function (id) { layer.outer[id] = addPath("hex-border-outer", colorOf[id]); });
    ids.forEach(function (id) {
      var url = addGrad(id, colorOf[id]);
      layer.ext[id] = addPath("hex-border-ext", colorOf[id]);
      layer.ext[id].setAttribute("stroke", url);
      layer.ext[id].setAttribute("stroke-width", EXT_W / 2);
      layer.half[id] = addPath("hex-border-divider", colorOf[id]);
      layer.half[id].setAttribute("stroke", url);
      // 线宽在 JS 定：两条半线的偏移量按同一组常量算，偏移和线宽必须同源。
      layer.half[id].setAttribute("stroke-width", DIVIDER_W / 2);
    });

    hive.appendChild(svg);
    return layer;
  }

  // 一条边 → 两条半宽偏移线（各归各的分区）+ 一条居中芯线。
  //
  // 上一版是**每段一个 SVG linearGradient**（跨向渐变、50% 硬切）。换掉有两个原因：
  //   · 视觉：渐变两半用的是 var(--zone-N) 原色，和格子填充**同一个明度** ——
  //     OKLCH 里明度只看 L，色相转多少度都不改明度。所以带子和格子糊成一坨，
  //     人类判词「内部分界线确实不好看」。现在每半边把本区色 color-mix 压向
  //     --ink（亮色主题变暗、暗色主题自动变亮），反差从**明度**来，不从色相来。
  //   · 开销：渐变必须一段一条 <path> + 一条 <linearGradient>，加上虚拟格延伸
  //     就是上百个节点，而 applyLayout 连悬停都要跑（悬停会改包围盒 → 边界要跟着挪）。
  //     偏移半线可以**按分区归并成一条 path**，27 条边 + 37 条延伸边合起来只剩
  //     十几个元素，每次布局只改 d。
  //
  // 顺带解释一下为什么没用互补色（人类问过）：分区色相就是扇区角度，H+180
  // **正好是对面那个分区的身份色**；而且 tokens 把 L/C 定死，互补色与原色明度完全
  // 相同 —— 零明度反差，两块贴一起只会"振"不会"分"。走明度是唯一有效的那条路。
  function pushHalf(buf, e, off) {
    var ox = e.nx * off, oy = e.ny * off;
    buf.push("M" + (e.x1 + ox).toFixed(2) + " " + (e.y1 + oy).toFixed(2) +
             "L" + (e.x2 + ox).toFixed(2) + " " + (e.y2 + oy).toFixed(2));
  }
  function pushSeg(buf, e) {
    buf.push("M" + e.x1.toFixed(2) + " " + e.y1.toFixed(2) +
             "L" + e.x2.toFixed(2) + " " + e.y2.toFixed(2));
  }

  function paintBorders(L, plan, key) {
    if (!L) return;
    L.svg.setAttribute("width", plan.width);
    L.svg.setAttribute("height", plan.height);
    L.svg.setAttribute("viewBox", "0 0 " + plan.width.toFixed(2) + " " + plan.height.toFixed(2));
    // 整体平移交给 <g transform>，**路径只在展开态变化时重拼**。
    // 悬停也会走 applyLayout（放大那一格会改包围盒 → 原点 ox/oy 跟着动），
    // 但那只是整体平移，132 段路径一个点都没动。不分开就是每次鼠标移动都重拼
    // 一遍全部路径字符串 —— 实测偶发单帧 33~50ms，正好砸在展开动画的第一帧上。
    // 界线画在格与格的缝里，格子一放大（展开态整张图 ×EXPAND_MAG），
    // 界线不跟着缩放就会留在原处、和格子彻底对不上。缩放中心是局部原点
    // （蜂巢中心），所以 translate 之后再叠一个 scale 正好。
    var mag = plan.mag || 1;
    L.g.setAttribute("transform",
      "translate(" + plan.ox.toFixed(2) + "," + plan.oy.toFixed(2) + ")" +
      (mag === 1 ? "" : " scale(" + mag.toFixed(4) + ")"));

    if (L.key === key) return;
    L.key = key;

    // 渐变半径跟着当前布局走（展开时蜂巢裂开会变大）。半径变了颜色分布才对得上。
    Object.keys(L.grads).forEach(function (id) {
      L.grads[id].setAttribute("r", Math.max(1, plan.gradR).toFixed(1));
    });

    var outer = {}, half = {}, ext = {};
    function bucket(map, id) { return (map[id] = map[id] || []); }

    plan.borders.forEach(function (e) {
      if (e.outer) { pushSeg(bucket(outer, e.zoneIds[0]), e); return; }
      // 法向从 zoneIds[0] 指向 zoneIds[1]：前者往负法向让，后者往正法向让。
      pushHalf(bucket(half, e.zoneIds[0]), e, -DIVIDER_W / 4);
      pushHalf(bucket(half, e.zoneIds[1]), e, DIVIDER_W / 4);
    });
    (plan.extensions || []).forEach(function (e) {
      pushHalf(bucket(ext, e.zoneIds[0]), e, -EXT_W / 4);
      pushHalf(bucket(ext, e.zoneIds[1]), e, EXT_W / 4);
    });

    [[L.outer, outer], [L.half, half], [L.ext, ext]].forEach(function (pair) {
      Object.keys(pair[0]).forEach(function (id) {
        pair[0][id].setAttribute("d", (pair[1][id] || []).join(""));
      });
    });
  }


  window.NexusTableHexBorders = {
    DIVIDER_W: DIVIDER_W,
    EXT_W: EXT_W,
    mount: mountBorders,
    paint: paintBorders
  };
})();
