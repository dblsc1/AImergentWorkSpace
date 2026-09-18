// table · 蜂巢布局 + 推镜（2026-09-08 从 hex-app.js 拆出）
//
// 拆出来的直接原因还是 1000 行硬线（上一轮就登记了这一步的偿还计划）。但缝本来就在：
// **这个文件只做几何** —— 吃 state 里那份蜂巢模型，吐一份 plan
// （每个元素该在哪、多大、相机该缩多少），再把 plan 落到 transform 上。
// 它不认识事件、不认识待办、不发一个请求。
//
// 几何常量全部住在这里，是**唯一事实源**：hex-app.js 从下面的导出里取
// （SIZE / DRAW_S 它算 clip-path 要用）。两处各写一份迟早漂移。
(function () {
  "use strict";

  var H = window.NexusTableHexData;
  var B = window.NexusTableHexBorders;

  var state = null;
  function init(s) { state = s; }
  function $(sel) { return document.querySelector(sel); }
  function reduceMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  var SIZE = 50, GAP = 0;
  var DRAW_S = SIZE - GAP / 2;
  var CELL_W = Math.sqrt(3) * DRAW_S;
  var CELL_H = 2 * DRAW_S;
  // 三档尺寸（人类原话：「鼠标放上去，六边形稍微扩大显示最近待办，点击再扩大」）：
  //   静置 CELL → 悬停 HOVER（只多一条待办）→ 展开 CARD（编辑栏）。
  // 悬停档从 1.72 提到 1.95：这一档现在要装下**三张待办卡片**（人类：
  // 「把这些待办弄成漂亮的卡片式」）。卡片有内距和边框，比裸文本高出一截，
  // 1.72 那个尺寸最下面一张会被六边形的斜边切掉一角。
  var HOVER_SCALE = 1.95;
  var HOVER_W = CELL_W * HOVER_SCALE, HOVER_H = CELL_H * HOVER_SCALE;
  // 展开卡是**正六边形**（人类：「打开后的框也可以再放大点做成正六边形」），
  // 所以宽高比必须是 √3:2，不能各自拍一个数。
  var CARD_H = 470, CARD_W = Math.sqrt(3) / 2 * CARD_H;
  // 展开时**同分区的其他格子**放大成这个外接圆半径，并按"六边形相切"贴在
  // 大卡片的六条边上（人类 2026-09-08：「同分区的六边形也一起放大，只是灰一点
  // 粘在旁边」）。62 不是拍的：卡片外接半径 235，同伴 62，相切距离
  // √3/2·(235+62)=257，两边加起来横向 638 局部像素 ×1.45 相机 ≈ 925 屏幕像素，
  // 1150 宽的取景框装得下。改卡片尺寸就得重算这个数。
  // 展开时**整张蜂巢放大这么多倍**（人类 2026-09-08 第三轮：「我需要的是整个放大到
  // 和主 view 六边形一样大的水平，其他的那么小一点都不协调」）。
  //
  // 倍数不是拍的，是**解出来的**：要让别的格子和展开卡一样大，就得
  // MAG = CARD_H / CELL_H = 470/100 = 4.7。这个数一定下来会发生一件很漂亮的事 ——
  // 放大后的格宽 = CELL_W × 4.7 = 86.60 × 4.7 = 407.03 = CARD_W（√3/2·CARD_H），
  // **分毫不差**。也就是说：展开卡不再是"一个特殊的大盒子塞在蜂巢里"，
  // 它就是放大后晶格里的**普普通通的一格**，只不过那一格里装了详情内容。
  //
  // 直接后果：上一版那整套「同伴按六边形相切公式贴在卡片六条边上 + 就近占位」
  // 全部**删掉**了。同伴回到自己的晶格位就天然严丝合缝地贴着卡片，
  // 不需要任何特殊摆位 —— 这是尺寸对齐之后白送的。
  var EXPAND_MAG = CARD_H / CELL_H;
  // 展开的那一格在此基础上**再大一点**，侵占一圈邻居的边角（人类 2026-09-08：
  // 「稍微大一些，侵占一点四周的位置，但不要遮住别人的标题」）。
  //
  // 人类要 600×700，倒推 OVER = 600 / CARD_W = 600 / 407.03 = 1.474。
  //
  // 这个数**顶到了几何上限**，所以配套还改了一处 CSS：邻居标题在它自己格子的
  // **正中**，距卡心恰好一个晶格步长 √3·(CARD_H/2) = 407；卡片放大后半宽
  // = 203.5·1.474 = 300，留给邻居标题的半宽只剩 107。而灰格里 22px 的中文标题
  // 铺满内容区能到 167 半宽 —— 会被盖。
  // 所以 hex.css 给聚焦态的灰格标题加了 `max-width: 55%`（半宽 ≈ 92），
  // 间隙回到 +15px。**这两个数是一对，改一个必须重算另一个。**
  var CARD_OVER = 1.474;
  // 推镜：展开时**整张蜂巢**放大这么多倍，并把焦点滚到取景框中心。
  // 人类 2026-09-08 第二轮：「我没有看到有那种整体放大，注意力聚焦在那个
  // 分区和卡片的感觉」—— 只把本区格子挪一挪不够，那点位移被 407×470 的
  // 卡片整个吞掉了。这一档是**相机**，不是布局。
  // 相机：格子自己已经放大 4.7 倍，再叠相机就只剩卡片一格看得见了。
  // 所以这一档设成 1 —— "推近"这件事完全由 EXPAND_MAG 承担，
  // applyCamera 现在只做一件事：把焦点滚到取景框中心。
  var CAM = 1;
  var PAD = 46;
  // 460ms 不是随手调大的：人类 2026-09-08「放大缩小的动画效果还不是很好，
  // 没有那种整体放大的感觉，需要优化。可以慢一点，动画要做漂亮」。
  // 300ms 太短，4.7 倍的放大在那么短的时间里看着像闪了一下，不像推近。
  var ANIM_MS = 460;
  var HOVER_MS = 190;
  // 缓动换成「慢起 → 快 → 长长地收尾」，没有回弹。原来那条 (.22,1,.36,1)
  // 前 15% 就冲掉了大半距离，配上 4.7 倍放大显得很躁。
  var EASE = "cubic-bezier(.32,.06,.13,1)";
  var EPS = 1;
  var SVG_NS = "http://www.w3.org/2000/svg";
  // ── 布局：一次算清所有格子的中心点与尺寸 ────────────────────
  function computeItems(expandedId) {
    var items = state.cells.map(function (it) {
      var p = H.axialToPixel(it.cell, SIZE);
      var hovered = (it === state.hoverItem);
      return {
        el: it.el, cx: p.x, cy: p.y, bx: p.x, by: p.y,
        w: hovered ? HOVER_W : CELL_W,
        h: hovered ? HOVER_H : CELL_H,
        ref: it
      };
    });
    // 分区名标签：**全部放在同一个圆上**（半径 = 蜂巢外接半径 + 大半格），
    // 角度 = 本分区扇区中心。一圈标签围着蜂巢外沿，谁都不压。
    //
    // 试过的两版都不行，记在这里省得后面的人再试一遍：
    //   · 按 (endRing + 0.9)·√3·S 的极坐标公式放 —— 那是"第 d 圈的外接圆"，
    //     可晶格上第 d 圈的格子实际落在 1.5·S·d 到 √3·S·d 之间（角上最远、
    //     边中间最近，差 15%）。真机实测标签飘出三四个格子远，不知道在标谁。
    //   · 贴本分区最外那一格再往外推 —— 归属清楚了，但**内圈的分区**（比如
    //     只有一格、落在第 2 圈的「娱乐」）推出去正好压在别人的格子上。
    // 统一半径同时解掉这两条：归属靠角度（扇区本来就是角度），不压靠半径。
    var hiveR = 0;
    state.cells.forEach(function (it) {
      var q = H.axialToPixel(it.cell, SIZE);
      hiveR = Math.max(hiveR, Math.hypot(q.x, q.y));
    });
    var labelR = hiveR + CELL_H * 0.62;

    state.labels.forEach(function (lb) {
      var a = (lb.zone.centerAngle - 90) * Math.PI / 180;
      // 人类拖过的标签用拖到的位置；没拖过的走默认圈位。
      // 偏移是**相对默认位置的增量**，不是绝对坐标 —— 项目增减导致蜂巢长大缩小时，
      // 标签跟着默认圈一起动，拖出来的那点个人调整还在。存绝对坐标就会
      // 在数据一变之后集体错位。
      items.push({
        el: lb.el, cx: labelR * Math.cos(a), cy: labelR * Math.sin(a),
        bx: labelR * Math.cos(a), by: labelR * Math.sin(a),
        w: lb.el.offsetWidth || 0, h: lb.el.offsetHeight || 0, isLabel: true, label: lb
      });
    });

    // 展开不再**挤开周围**（人类：「不要拆开整个六边形蜂巢」）。
    // 改成"聚焦"：本分区的同伴放大后贴着大卡片排一圈，其余分区原地不动、
    // 由 CSS 灰掉压暗（.is-dimmed）。蜂巢的相对结构一个字节不动 ——
    // 上一版把右边的格子整体右移、下边整体下移，展开一次整张图就碎了。
    //
    // 悬停档同样不挤：鼠标扫过一片格子时整张蜂巢跟着抖是灾难，而且每动一格
    // 都要重量一次全场 rect。能这么做的前提是格子全 position:absolute。
    var target = expandedId ? state.byId[expandedId] : null;
    var hit = null;
    if (target) {
      items.forEach(function (it) { if (it.ref === target) hit = it; });
    }
    if (hit) {
      var fz = hit.ref.cell.zoneId;

      // ── 第一步：**整张图一起长大**（人类 2026-09-08：「别的小的不放大啊，
      // 都要像被点击了一样放大，包括相邻的其他分区的六边形，只是变灰色不能点击」）
      //
      // 尺寸和晶格位置**乘同一个数**，所以放大之后照样严丝合缝 —— 只改一个就散架。
      // 位置绕的是局部原点，也就是蜂巢中心（axialToPixel 的 0,0）。
      // 标签只挪位置不改尺寸：它是一颗文字胶囊，跟着放大就成了大字报；
      // 但半径必须跟着走，否则整张图长大了、标签还贴在老半径上，压进格子里。
      items.forEach(function (it) {
        it.cx *= EXPAND_MAG; it.cy *= EXPAND_MAG;
        it.bx = it.cx; it.by = it.cy;
        if (it.isLabel) return;
        it.w *= EXPAND_MAG; it.h *= EXPAND_MAG;
      });

      // 第二步：展开的那一格换成大卡片。**位置已经在放大后的晶格位上了**，
      // 上面那一趟包含了它 —— 顺序不能反：同伴是绕 hit.cx/cy 排的，
      // 先排同伴再挪 hit，整圈同伴就会留在旧位置上。
      hit.w = CARD_W * CARD_OVER; hit.h = CARD_H * CARD_OVER;
      hit.mate = false;

      // ── 同分区标记 ────────────────────────────────────────────
      // 尺寸对齐之后，同伴已经在自己的晶格位上贴着卡片了（见 EXPAND_MAG 那段），
      // **不需要任何摆位计算**。这里只打个标记，让 CSS 把同区压得比别区淡一档 ——
      // 人类要的是"同区亮一点、别区更灰"，那是外观，不是布局。
      items.forEach(function (it) {
        if (it.isLabel || !it.ref || it.ref.isCenter || it === hit) return;
        it.mate = (it.ref.cell.zoneId === fz);
      });
      hit.bx = hit.cx; hit.by = hit.cy;
    }

    // 界线不再需要跟着位移 —— 展开已经不挪格子了。留一层薄封装只做单位换算。
    function toPixels(src) {
      return (src || []).map(function (e) {
        return {
          // ⚠️ 这个 map 是**显式白名单**：它把每条边重建成新对象，只抄这里
          // 列出的字段。新加的字段不写进来就是**静默丢失** —— 界线层拿不到、
          // 也不会报错。2026-09-08 真踩过一次（soft 标记漏抄，path 建出来了
          // 但 d 全是空串，靠真机探针量 d 总长为 0 才发现）。
          outer: e.outer, zoneIds: e.zoneIds, nx: e.nx, ny: e.ny,
          x1: e.x1 * SIZE, y1: e.y1 * SIZE, x2: e.x2 * SIZE, y2: e.y2 * SIZE
        };
      });
    }
    var borders = toPixels(state.hive.borders);
    var extensions = toPixels(state.hive.extensions);


    var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    // ⚠️ 包围盒用的是 **bx/by（没加拖动偏移的位置）**，不是 cx/cy。
    //
    // 拖动必须**完全不改** plan.width/height，否则会有一个很难看的回弹：
    // 把一个分区往右拖 90px → maxX 跟着右移 → #hive 变宽 90 →
    // `.hive { margin: 0 auto }` 重新居中，整张蜂巢往左挪 45 →
    // 松手瞬间刚拖到的位置缩回去一截（实测掉了 36px）。
    // 真凶是**居中**不是包围盒，但最稳的解法是让盒子对拖动完全免疫：
    // 拖了谁只有谁动，别人一个像素不动。
    //
    // 代价：拖得太远会伸出 #hive 的盒子。往右/下伸没事（.hive-wrap 能滚过去），
    // 往左/上伸出 PAD 之外会被裁。真要拖那么远，该调的是 PAD。
    // 展开卡不受影响 —— 它撑大盒子靠的是 w/h（CARD_W/H），不是偏移。
    items.forEach(function (it) {
      var bx = (it.bx === undefined) ? it.cx : it.bx;
      var by = (it.by === undefined) ? it.cy : it.by;
      minX = Math.min(minX, bx - it.w / 2); maxX = Math.max(maxX, bx + it.w / 2);
      minY = Math.min(minY, by - it.h / 2); maxY = Math.max(maxY, by + it.h / 2);
    });
    // 界线画在缝里；虚拟格延伸段还会伸到蜂巢之外一圈，包围盒要把它算进去。
    borders.concat(extensions).forEach(function (e) {
      minX = Math.min(minX, e.x1, e.x2); maxX = Math.max(maxX, e.x1, e.x2);
      minY = Math.min(minY, e.y1, e.y2); maxY = Math.max(maxY, e.y1, e.y2);
    });
    items.forEach(function (it) {
      it.left = it.cx - it.w / 2 - minX + PAD;
      it.top = it.cy - it.h / 2 - minY + PAD;
    });
    var ox = -minX + PAD, oy = -minY + PAD;
    // 推镜的焦点 = **展开卡的中心**（不是分区质心）：人类要的是"注意力聚焦在
    // 那个分区和卡片"，卡片才是要看的东西，分区是它的背景。
    var focus = hit ? { x: hit.cx - minX + PAD, y: hit.cy - minY + PAD } : null;
    // borders / extensions **不在这里加偏移** —— 交给界线层的 <g transform>，
    // 那样悬停时只改一个 transform，不用重拼上百段路径。
    var gradR = 0;
    borders.concat(extensions).forEach(function (e) {
      gradR = Math.max(gradR, Math.hypot(e.x1, e.y1), Math.hypot(e.x2, e.y2));
    });
    return {
      items: items, borders: borders, extensions: extensions, ox: ox, oy: oy,
      gradR: gradR,
      cam: hit ? CAM : 1, focus: focus, mag: hit ? EXPAND_MAG : 1,
      width: (maxX - minX) + PAD * 2, height: (maxY - minY) + PAD * 2
    };
  }

  // ── FLIP：First → Last → Invert → Play，只动 transform ─────────
  //
  // First 不再**量 DOM**，改成读上一次布局算出来的几何（挂在元素上的 __geom）。
  // 两个理由，都不是优化洁癖：
  //   1. **正确性**：推镜给 .hive-cam 加了 scale。getBoundingClientRect 返回的是
  //      屏幕像素，而子元素的 transform 走的是**局部坐标** —— 拿屏幕差值去做
  //      inverse，位移会被整整放大 CAM 倍，展开那一下全场乱飞。plan 里的
  //      left/top 本来就是局部坐标，天生对齐。
  //   2. 顺带干掉那次强制同步布局（30 个元素量一遍 rect，实测 15.1ms 砸在
  //      展开动画第一帧上）。
  // 代价：新挂上来的元素没有 __geom，第一次不做动画。这是对的 —— 它本来
  // 就没有"从哪来"。
  function applyLayout(animate, durationMs) {
    var hive = $("#hive"), camEl = state.camEl;
    if (!hive || !camEl) return;
    var ms = durationMs || ANIM_MS;
    var plan = computeItems(state.expandedId);
    var flip = animate && !reduceMotion();

    // 取景框尺寸 = **没展开时**的蜂巢尺寸。展开后 plan 会因为大卡片变大，
    // 拿那个当取景框就等于"框跟着内容长"，推镜的感觉当场消失。
    if (!state.expandedId) state.baseSize = { w: plan.width, h: plan.height };

    camEl.style.width = plan.width + "px";
    camEl.style.height = plan.height + "px";
    B.paint(state.borderLayer, plan, String(state.expandedId || ""));

    var moved = [];
    plan.items.forEach(function (it) {
      var el = it.el, prev = el.__geom;
      if (!it.isLabel) { el.style.width = it.w + "px"; el.style.height = it.h + "px"; }
      el.__base = "translate3d(" + it.left.toFixed(2) + "px," + it.top.toFixed(2) + "px,0)";
      el.__geom = { left: it.left, top: it.top, w: it.w, h: it.h };
      el.style.transition = "";
      el.style.transform = el.__base;
      if (!it.isLabel) el.classList.toggle("is-mate", !!it.mate);
      if (!flip || !prev || !prev.w || !it.w) return;
      var dx = (prev.left + prev.w / 2) - (it.left + it.w / 2);
      var dy = (prev.top + prev.h / 2) - (it.top + it.h / 2);
      var sx = prev.w / it.w, sy = prev.h / it.h;
      if (Math.abs(dx) < .5 && Math.abs(dy) < .5 &&
          Math.abs(sx - 1) < .005 && Math.abs(sy - 1) < .005) return;
      el.style.transition = "none";
      el.style.transform = el.__base + " translate3d(" + dx.toFixed(2) + "px," + dy.toFixed(2) + "px,0)" +
        " scale(" + sx.toFixed(4) + "," + sy.toFixed(4) + ")";
      var inner = el.querySelector(".hex-inner");
      if (inner) {
        inner.style.transition = "none";
        inner.style.transform = "scale(" + (1 / sx).toFixed(4) + "," + (1 / sy).toFixed(4) + ")";
      }
      moved.push(el);
    });

    state.lastPlan = plan; state.lastCam = plan.cam || 1;
    applyCamera(hive, camEl, plan, animate);

    if (!moved.length) return;
    void hive.offsetWidth;                       // 强制提交反相态，否则下一帧不触发过渡
    // will-change **只在这一段动画期间挂**，动完就摘。常驻在 CSS 里等于让
    // 21 个格子一直占着合成层；真正在动的每次只有几格（2026-09-08 性能回合）。
    moved.forEach(function (el) { el.style.willChange = "transform"; });
    if (layoutSettle) clearTimeout(layoutSettle);
    layoutSettle = setTimeout(function () {
      layoutSettle = null;
      moved.forEach(function (el) { el.style.willChange = ""; });
    }, ms + 60);
    requestAnimationFrame(function () {
      moved.forEach(function (el) {
        // 过渡里**只有 transform**：clip-path 是百分比多边形，尺寸一变形状
        // 自动跟着缩放，插值它等于每帧重算裁剪路径 + 重绘。
        el.style.transition = "transform " + ms + "ms " + EASE;
        el.style.transform = el.__base;
        var inner = el.querySelector(".hex-inner");
        if (inner) {
          inner.style.transition = "transform " + ms + "ms " + EASE;
          inner.style.transform = "";
        }
      });
    });
  }
  var layoutSettle = null;

  // ── 推镜：展开时整张蜂巢放大，并把焦点滚到取景框中心 ──────────
  //
  // 三件事必须一起做，少一件"聚焦"就不成立：
  //   · .hive-cam 放大（看得更大）
  //   · #hive 占位 = plan 尺寸 × cam（滚动区跟着变，放大出去的部分够得着）
  //   · .hive-wrap 高度钉在**未展开时**的高度（它变成取景框，而不是跟着长高）
  // 收起时三件事全部还原，一个不留。
  function applyCamera(hive, camEl, plan, animate) {
    var wrap = hive.parentNode;
    var c = plan.cam || 1;
    var smooth = animate && !reduceMotion();
    camEl.style.transformOrigin = "0 0";
    camEl.style.transition = smooth ? "transform " + ANIM_MS + "ms " + EASE : "";
    if (!wrap) return;

    // 判据是「有没有展开」（plan.focus），不是「相机倍数是不是 1」。
    // 写成 c === 1 踩过一次：CAM 调成 1 之后整段被跳过，取景框没被钉住，
    // 页面被 2767px 高的内容整个撑开、可滚区域为 0，焦点永远滚不过去。
    if (!plan.focus) {
      wrap.classList.remove("is-zoomed");
      wrap.style.height = "";
      hive.style.padding = "";
      hive.style.width = (plan.width * c) + "px";
      hive.style.height = (plan.height * c) + "px";
      camEl.style.transform = c === 1 ? "" : "scale(" + c + ")";
      return;
    }

    // 取景框高度：至少装得下卡片 + 上下各露出邻居一截，否则"周围还有一片蜂巢"
    // 完全看不见，聚焦就变成了"只剩一张卡"。
    // 移动端窄屏另有一条上限：取景框比视口还高就等于没框住
    // （人类 2026-09-08 报的「视角移动到不知道哪里去」，窄屏最明显）。
    var vhWant = CARD_H * CARD_OVER + CELL_H * EXPAND_MAG * 0.5;
    var vhMax = (window.innerHeight || 900) * 0.86;
    var vh = Math.max(240, Math.min(vhWant, vhMax));
    wrap.style.height = vh + "px";

    // ⚠️ **窄屏要把相机拉远**，否则 600×693 的卡片根本装不进 390px 的手机屏
    // （人类 2026-09-08 让做移动端优化；真机 390×844 实测卡片左右各溢出一大截）。
    // 这才是相机该干的活：格子的**布局尺寸**不动（EXPAND_MAG 那套咬合关系
    // 一个字节都不能改），装不下就整体缩，和"推近"是同一个旋钮的两个方向。
    // 0.94 / 0.96 留一点边，免得卡片顶着取景框边缘。
    var fitW = (wrap.clientWidth * 0.94) / (CARD_W * CARD_OVER);
    var fitH = (vh * 0.96) / (CARD_H * CARD_OVER);
    c = Math.min(c, fitW, fitH);
    wrap.classList.add("is-zoomed");
    hive.style.padding = "";

    // ── 推镜靠**相机平移**，不靠滚动容器 ──────────────────────────
    //
    // 上一版是 padding 撑开滚动区 + el.scrollIntoView()。人类 2026-09-08 一次
    // 报了三条，全出在那条路上：
    //   ·「点击六边形放大时，视角移动到不知道哪里去」
    //   ·「放大视图点别的六边形，会放大但视角不移动过去，点第二次才行」
    //   ·「每次放大视图添加完成，都会出现视角飞走的瞬间」
    // 病根是**滚动和放大是两件不同步的事**：格子的 transform 走 CSS 过渡，
    // 滚动走浏览器自己的 smooth，两条时间轴各跑各的；而且 scrollIntoView 必须
    // 等 FLIP 走完才量得准，于是先放大、再"咔"地滚一下 —— 那一下就是"飞走"。
    // 连点两格时更糟：上一次排的滚动还没执行，它捕获的是**旧元素**，
    // 先滚回旧位置再滚过来，看着就是"视角不跟过去，点第二次才行"。
    //
    // 改成把焦点用 transform 推到取景框正中，和格子放大**同一帧、同一条缓动**
    // 一起走 —— 这才是"整体放大"，而不是"放大完再挪一下"。
    // 数学很简单：cam 的 origin 是 0 0，焦点在 cam 局部坐标是 plan.focus，
    // 缩放后落在 focus×c，要它出现在取景框中心 ⇒ 平移 (中心 − focus×c)。
    // 不经过 DOM 位置，也就不受 margin:auto 居中、padding、FLIP 中间态影响
    // （手推 scrollLeft 那一版正是栽在这三层上，实测偏 556px）。
    hive.style.width = (plan.width * c) + "px";
    hive.style.height = (plan.height * c) + "px";
    var tx = wrap.clientWidth / 2 - plan.focus.x * c;
    var ty = vh / 2 - plan.focus.y * c;
    camEl.style.transform =
      "translate3d(" + tx.toFixed(1) + "px," + ty.toFixed(1) + "px,0) scale(" + c + ")";
    // 取景框自己不滚：位置全由 transform 决定，留着滚动位移只会和它打架
    // （收起再展开时，上一次的 scrollTop 会让新的一帧从歪的地方起步）。
    wrap.scrollLeft = 0;
    wrap.scrollTop = 0;
  }


  window.NexusTableHexLayout = {
    init: init,
    SIZE: SIZE, DRAW_S: DRAW_S,
    // 空白处长按长出来的那张建项目卡片要和真的展开卡一样大 —— 尺寸只有
    // 这一份真相（hex-app.js::sproutOpen 取用），别在那边再写一遍。
    CARD_W: CARD_W, CARD_H: CARD_H, CARD_OVER: CARD_OVER,
    ANIM_MS: ANIM_MS, HOVER_MS: HOVER_MS, EASE: EASE,
    compute: computeItems,
    apply: applyLayout
  };
})();
