// table · 蜂巢行动分区 · 纯逻辑层（无 DOM 依赖，2026-09-07）
//
// 定位与 rails.js / gtd-data.js 同一份纪律：逻辑与 DOM 分家，
// 逻辑层可在 Node 里 require() 直接单测，不需要浏览器/无头环境。
// DOM 侧是 hex-app.js（渲染 + FLIP 动效）与 hex-crud.js（编辑目录）。
//
// 四块职责：
//   1. 蜂巢坐标：**放射扇区** —— 每个分区从中心占一个角度扇区（角度 ∝ 项目数），
//      格子放在极坐标槽位网格上沿半径向外堆叠；窄扇区自动被推到"角度便宜"的外圈
//      （人类的 15° 外延规则）。中心格固定留给「当前在做的事」，项目占不到它。
//   2. 分区配色：只做「第 N 个分区用第几号色」的映射 —— 默认灰（153,153,153）
//      → `var(--zone-N)`；显式设过色 → 原样用它。**本文件不产出任何色值**
//      （契约 design-tokens-v1 §2：模块 CSS/JS 禁裸 hex，只准 var(--…)）。
//   3. 「最近完成」：从 planner/audit 流水里筛 done 相关动作，**按任务去重只看
//      最后一次动作**——实测数据里有人勾了又取消（12:08:32 done=true、
//      12:08:34 done=false），漏了去重就是给人类显示假成绩。
//   4. 待办取数：next-actions 已按分区分好组排好序，本文件只做 projectId 归并，
//      **不重新分类、不重新排序**（同 gtd-data.js 既有口径）。
//
// 契约依赖（只读，不改后端）：../../../nexus-core/module_docs/contract.md 的
// TreeOut / NextActionsOut / AuditOut 三节。
//
// A2（禁裸 hex）：本文件不出现任何颜色字面量——默认灰用 RGB 三元组表达（那是
// 后端数据的判据，不是样式），自动色只产出 `var(--zone-N)` 变量名。门禁脚本机械校验。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("./hex-audit.js"));
  } else {
    // 浏览器里 hex-audit.js 必须排在本文件**前面**（index.html 已经这么排）。
    root.NexusTableHexData = factory(root.NexusTableHexAudit);
  }
})(typeof self !== "undefined" ? self : this, function (A) {
  "use strict";

  // ── 1. 蜂巢坐标：轴向晶格 + 放射扇区（2026-09-07 第二轮，取代极坐标槽位）───
  //
  // 上一版把格子放在「第 d 圈半径 d·2.05S、整圈 floor(2πd) 个槽位」的**极坐标网格**
  // 上。那个网格上的点落在一圈圈**圆周**上，不在六边形晶格上，所以格子**永远咬合
  // 不上**——2.05S 的间距是为了"保证不重叠"故意留的余量，代价就是缝。人类的判词是
  // 「格子不咬合」。而且没有公共边，**也就画不出分区边界线**（第 2 条要求直接被堵死）。
  //
  // 现在换成**真正的轴向六边形晶格**（axial coordinates，尖顶朝上 pointy-top）：
  //
  //     x = √3·S·(q + r/2)        y = 1.5·S·r        （S = 外接圆半径）
  //
  // 相邻格中心距**恒等于 √3·S**，也就是六边形的对边宽（CELL_W）——所以画成
  // 宽 √3·S、高 2S 的六边形时**边贴边、零缝**，这是咬合的充要条件，不是调参调出来的。
  // 留一条视觉缝就把外接圆从 S 缩到 S−GAP/2（**等比缩**，见 hex-app.js），
  // 缩的是格子不是间距，晶格本身不动。
  //
  // 圈号（六边形距离）：ring = (|q| + |r| + |q+r|) / 2，第 d 圈恰好 6d 格。
  // 角度：angleDeg = atan2(x, −y)，0° 在正上方、顺时针——与上一版对外口径一致，
  // 扇区分配那套逻辑一个字不用改。
  //
  // 扇区模型不变：每个分区从中心占一个角度扇区（角度 ∝ 项目数），一圈圈往外发格子。
  // 「15° 外延」这条人类规则现在变得**更干净**：第 d 圈 6d 格，格位角宽正好 60/d 度
  //   —— 第 1 圈 60°、第 2 圈 30°、第 3 圈 20°、第 4 圈 15°、第 5 圈 12°。
  // 上一版是 360/floor(2πd)（60/28.6/20/14.4/11.6），同样的物理、脏一点的数字。
  // 阈值仍然不写死在代码里：扇区 <15° 的分区在第 1–3 圈整格放不进去，自然被推到外圈。
  //
  // 三条不变量，全部由构造保证（单测逐条核）：
  //   ① 咬合且不重叠：任意两格中心距 ≥ √3·S，且相邻格**恰好** = √3·S。
  //   ② 同分区连续：扇区宽度固定而每圈格数 6d 单调增，一个分区一旦在第 d 圈拿到
  //      格子，之后每一圈都至少还有一个 —— 径向不断开。
  //   ③ 中心格 (q=0,r=0) 不在任何分区的候选集里，项目永远占不到它。
  var CENTER = { q: 0, r: 0, ring: 0, angleDeg: 0 };
  var SQRT3 = Math.sqrt(3);

  // 六个轴向邻居方向，**顺序即边序**：第 i 个方向对面的那条边是 v[(i+1)%6]–v[(i+2)%6]。
  // 分区边界线全靠这条对应关系画（见 zoneBorderEdges）。
  var HEX_DIRS = [
    [1, 0],    // 0 东     → 边 v1–v2（右）
    [0, 1],    // 1 东南   → 边 v2–v3
    [-1, 1],   // 2 西南   → 边 v3–v4
    [-1, 0],   // 3 西     → 边 v4–v5（左）
    [0, -1],   // 4 西北   → 边 v5–v0
    [1, -1]    // 5 东北   → 边 v0–v1
  ];

  // 尖顶六边形的 6 个顶点（单位：外接圆半径 S，y 轴向下）。
  // 与 hex.css 的 clip-path `50% 0%,100% 25%,100% 75%,50% 100%,0% 75%,0% 25%`
  // 是同一个多边形：宽 √3·S、高 2S。两处改一处就得改另一处。
  var HEX_VERTS = [
    [0, -1],                 // v0 上
    [SQRT3 / 2, -0.5],       // v1 右上
    [SQRT3 / 2, 0.5],        // v2 右下
    [0, 1],                  // v3 下
    [-SQRT3 / 2, 0.5],       // v4 左下
    [-SQRT3 / 2, -0.5]       // v5 左上
  ];

  // 逐边内缩后的六边形顶点（单位＝外接圆半径 S，y 轴向下）。
  //
  // 六边形 = 六个半平面的交：{ p : p·m_i ≤ 内切圆半径 }，m_i 是第 i 条边的外法向。
  // 把某几条边的那个界限往里挪 extra，再取相邻两条边的交点，就是新的顶点。
  // **这样收边不会把格子变歪**：没动的边一点不动，动过的边平行内移，
  // 六个角还是 120°（半平面交天然保证）。
  //
  // 边法向就是该方向邻居的单位向量 —— 晶格里两格共享的那条边必然垂直于连心线。
  // 所以"哪条边朝着别的分区"和"哪个方向的邻居是别的分区"是同一件事，
  // boundaryDirs 直接就能当 dirs 用，不需要再做一次角度匹配。
  var APOTHEM = SQRT3 / 2;
  var EDGE_NORMALS = null;   // 依赖 axialToPixel，延后到定义之后再初始化

  function insetPolygon(dirs, extra) {
    var off = EDGE_NORMALS.map(function (_, i) {
      return APOTHEM - ((dirs && dirs[i]) ? extra : 0);
    });
    var pts = [];
    for (var k = 0; k < 6; k++) {
      // 顶点 k 由第 (k−1) 和第 (k−2) 条边夹出来（边 e 连接顶点 e+1 与 e+2）
      var a = (k + 5) % 6, b = (k + 4) % 6;
      var ma = EDGE_NORMALS[a], mb = EDGE_NORMALS[b];
      var det = ma[0] * mb[1] - ma[1] * mb[0];
      pts.push([(off[a] * mb[1] - off[b] * ma[1]) / det,
                (ma[0] * off[b] - mb[0] * off[a]) / det]);
    }
    return pts;
  }

  function axialRing(q, r) { return (Math.abs(q) + Math.abs(r) + Math.abs(q + r)) / 2; }
  function axialDistance(a, b) { return axialRing(a.q - b.q, a.r - b.r); }

  // 轴向 → 像素。size = 外接圆半径（像素）。相邻格中心距恒为 √3·size。
  function axialToPixel(cell, size) {
    return { x: SQRT3 * size * (cell.q + cell.r / 2), y: 1.5 * size * cell.r };
  }

  // axialToPixel 的逆：像素 → 轴向。空白处长按要知道"按在了哪个晶格位上"
  // （2026-09-08 人类：「整个分区长按点击任何一个黑色区域，都会在那里出现一个
  // 六边形逐渐显现」）。
  //   x = √3·S·(q + r/2)、y = 1.5·S·r  ⇒  r = y/(1.5S)、q = x/(√3·S) − r/2
  // 落在格子边界附近时必须**按立方坐标四舍五入**（q+r+s=0 的约束下取整），
  // 直接 Math.round(q)、Math.round(r) 会在六边形的三个角附近选错格子。
  function pixelToAxial(x, y, size) {
    var s = size || 1;
    var rf = y / (1.5 * s);
    var qf = x / (Math.sqrt(3) * s) - rf / 2;
    var sf = -qf - rf;
    var q = Math.round(qf), r = Math.round(rf), z = Math.round(sf);
    var dq = Math.abs(q - qf), dr = Math.abs(r - rf), dz = Math.abs(z - sf);
    if (dq > dr && dq > dz) q = -r - z;
    else if (dr > dz) r = -q - z;
    return { q: q, r: r };
  }

  // 极坐标 → 像素，**只给标签这类不在晶格上的东西用**（格子一律走 axialToPixel）。
  // 半径单位取「一圈 = √3·S」，与第 d 圈格子的外沿对得上。
  function polarToPixel(ringF, angleDeg, size) {
    var rad = ringF * SQRT3 * size;
    var a = (angleDeg - 90) * Math.PI / 180;
    return { x: rad * Math.cos(a), y: rad * Math.sin(a) };
  }

  // 0° 在正上方，顺时针到 360°。
  function cellAngle(cell) {
    var p = axialToPixel(cell, 1);
    var deg = Math.atan2(p.x, -p.y) * 180 / Math.PI;
    return (deg % 360 + 360) % 360;
  }

  function decorate(q, r) {
    var c = { q: q, r: r, ring: axialRing(q, r), angleDeg: 0 };
    c.angleDeg = cellAngle(c);
    return c;
  }

  // 第 d 圈的 6d 个格子，**按角度升序**返回（0° 起、顺时针）。
  function hexRing(d) {
    if (d <= 0) return [decorate(0, 0)];
    var out = [];
    var q = HEX_DIRS[4][0] * d, r = HEX_DIRS[4][1] * d;   // 从西北角出发绕一圈
    for (var i = 0; i < 6; i++) {
      for (var j = 0; j < d; j++) {
        out.push(decorate(q, r));
        q += HEX_DIRS[i][0]; r += HEX_DIRS[i][1];
      }
    }
    return out.sort(function (a, b) { return a.angleDeg - b.angleDeg; });
  }

  function slotsInRing(d) { return d <= 0 ? 1 : 6 * d; }
  function slotWidthDeg(d) { return 360 / slotsInRing(d); }   // 第 d 圈 = 60/d 度

  EDGE_NORMALS = HEX_DIRS.map(function (d) {
    var p = axialToPixel({ q: d[0], r: d[1] }, 1);
    var L = Math.hypot(p.x, p.y);
    return [p.x / L, p.y / L];
  });

  function cellKey(c) { return c.q + "," + c.r; }

  // 两格中心距（以 S 为单位）。相邻 = √3 ≈ 1.7320508。
  function cellDistance(a, b) {
    var pa = axialToPixel(a, 1), pb = axialToPixel(b, 1);
    return Math.hypot(pa.x - pb.x, pa.y - pb.y);
  }
  function isNeighbor(a, b) { return axialDistance(a, b) === 1; }

  // 扇区：角度 ∝ 权重，首尾相接铺满 360°。
  // ⚠️ 分界必须**先算边、再由边减出角宽**，不能各自算角宽再累加。
  // 各自算再累加时 `start + sweep` 和下一个 `start` 是两条不同的浮点路径，
  // 边界角上两个分区可以同时判真 —— 「射出去只碰到一个分区」那条不变式当场破。
  // 权重从整数（项目数）变成小数（冷区补贴）之后真的撞上了：300° 上两个分区。
  // 由边减出角宽后，边界处 (x - edges[k]) < (edges[k+1] - edges[k]) 两边是同一次
  // 浮点运算，恒为假，重叠在数学上就不可能了。最后一条边钉死 360，末尾不留缝。
  function buildSectors(weights) {
    var total = weights.reduce(function (a, b) { return a + b; }, 0) || 1;
    var edges = [0], acc = 0;
    weights.forEach(function (w) { acc += w; edges.push(360 * acc / total); });
    edges[edges.length - 1] = 360;
    return weights.map(function (_, i) {
      return { start: edges[i], sweep: edges[i + 1] - edges[i],
               center: edges[i] + (edges[i + 1] - edges[i]) / 2 };
    });
  }

  // ⚠️ 不许写成 `((angle - start) % 360 + 360) % 360`：那个规范化往返会把值**改掉**。
  // 实测 47.647058823529406 走一趟 +360 再 %360 就变成 47.64705882352939，
  // 于是边界角上 a < sweep 由假变真，两个分区同时认领 300°（单测「射出去只碰到
  // 一个分区」当场红）。这里只在真的越界时加/减一次 360，落在 [0,360) 的常路
  // 一次浮点运算都不做，差值精确。
  function inSector(angle, sec) {
    var a = angle - sec.start;
    if (a < 0) a += 360;
    else if (a >= 360) a -= 360;
    return a >= 0 && a < sec.sweep;
  }

  // 一圈圈往外发格子，直到每个分区都拿够。
  //
  // 每圈两趟：
  //   趟 1：格位中心落进扇区就发。**窄扇区（< NARROW_DEG）额外要求格位整格
  //     放得进扇区**（格位角宽 60/d ≤ 扇区角度），于是被推到外圈 —— 这就是
  //     人类的「角度小于 15 度，小分区六边形可以适度外延排列」。
  //
  //     ⚠️ 上一版把 15° 当成"几何推论"来做：对**所有**分区都要求 60/d ≤ sweep。
  //     后果在真机上很难看：一个 18° 的分区（9 区里占 1 个项目）也要 d ≥ 3.33
  //     才够格，于是它在第 1–3 圈一格都拿不到，直接落到第 4 圈，和蜂巢主体
  //     **断成孤岛**，中间空一大片（实测：两个只有一个项目的窄分区飘在
  //     左上角）。人类原话给的是 15°，不是"每一圈都得整格放得进去"——
  //     把阈值推广成通用规则，就是把 18° 也判成了窄区。
  //     现在阈值按原话写死成 NARROW_DEG，宽区从第 1 圈就开始填，不留内圈空洞。
  //
  //     窄区那条限制不能去掉：一个 6.5° 的窄扇区会因为"某个 60° 宽的格位中心
  //     恰好落在它里面"而抢到第 1 圈，占掉整整 60° 的视觉宽度（单测抓到过）。
  //   趟 2（放宽，只在第 MAX_PUSH_RING 圈及以外）：还没拿到格子的分区，
  //     直接领本圈**离自己扇区中心最近的空格位**，哪怕严格判据还差一点。
  //     没有这一趟，一个 15 区里只占 5.9° 的分区要等到第 10 圈才落位，
  //     整张图变成一个直径两米的空甜甜圈 —— 外延是"适度"，不是无限外推。
  //
  // 趟 0（2026-09-07 加）：到了 MAX_PUSH_RING 圈，**一格都还没拿到的分区先挑**。
  //   为什么必须先挑：趟 1 是按格位顺序贪心的，宽扇区会把内圈整圈吃干净
  //   （实测 15 区/60 项目：第 3 圈 18/18 满、第 4 圈 23/24），等轮到趟 2，
  //   饿着的那个分区在第 5 圈只能捡剩 —— 实测落位偏离自己扇区中心 16.2°，
  //   而该圈格位角宽才 12°，等于被甩进了别人的方向上，"这个方向是哪个分区"
  //   这条最核心的读图规则当场破掉。让饿着的先挑，它就落在自己扇区中心上。
  //   只给「零格分区」这个特权，不给"还差几格"的分区 —— 否则 d≥5 之后
  //   人人都能绕开严格判据，15° 外延规则就名存实亡了。
  var MAX_PUSH_RING = 5;
  var VIRTUAL_EXT = 1;      // 分界线往外**膨胀**几层虚拟格（贴着蜂巢外沿长，不铺满圆盘）
  var NARROW_DEG = 15;      // 人类定的阈值：扇区窄于它才外延（原话「角度小于15度」）

  // 窄区外延要**外延到哪一圈为止**：格位角宽 ≤ 本区扇区角，或 ≤「按分区数分到的公平角」
  // （360 / 分区数），两者取大的那个。
  //
  // ⚠️ 2026-09-14 真机 bug（人类截图：「左上有一大块空洞」）。只写「格位角宽 ≤ 扇区角」
  // 会随项目总数掉下悬崖：12 个分区、25 个项目时，只有 1 个项目的分区扇区 = 360/25 = 14.4°，
  // 刚好掉到 NARROW_DEG 以下，于是要 60/d ≤ 14.4 ⇒ d ≥ 4.17 ⇒ 六个单项目分区**同时**被推到
  // 第 5 圈；而蜂巢主体只长到第 3 圈 —— 第 4 圈整圈空，左上一大片空洞（实测每圈占用
  // {1:4, 2:9, 3:6, 5:6}）。项目数一越过 24（360/24 = 15°）就会整体掉下来，是个静默悬崖。
  //
  // 公平角这条下限把外延距离和**分区数**绑在一起，而不是和项目总数：12 个分区时公平角 30°，
  // 单项目分区从第 2 圈起就能落位，贴着主体长，不留空圈；20 个分区时公平角 18°，
  // 窄区仍要等到第 4 圈（单测「15° 外延」要求 ≥ 第 3 圈，仍然成立）。
  function fairDeg(zoneCount) { return 360 / Math.max(1, zoneCount); }
  function pushLimitDeg(sec, zoneCount) { return Math.max(sec.sweep, fairDeg(zoneCount)); }

  function angleGap(a, b) {
    var d = Math.abs(((a - b) % 360 + 360) % 360);
    return Math.min(d, 360 - d);
  }

  // zOrder：分区的**挑格顺序**（热的先挑，于是拿到更靠内的圈）。
  // 不传就按分区序号。注意这只改"谁先挑"，不改扇区角度 —— 角度归人类拖动。
  // 兜底圈的上限。原来写死 60，那是个灾难值：只要有一格落到第 60 圈，
  // plan 尺寸就变成 9000px，其余三十格被挤成看不见的一个点 ——
  // 人类看到的是「新建了一个分区，蜂巢直接消失了」（2026-09-18 真机）。
  //
  // 真正装得下 total 个格子的最小圈数是 3R(R+1) ≥ total 的那个 R，再留
  // MAX_PUSH_RING 圈余量给"角度不合适得往外挪"。到了上限那一圈 claimNearest
  // 允许飞地，所以一定放得下 —— 最坏情况退化成"有一格离得远"，而不是整张图消失。
  function ringCapFor(total) {
    var r = 1;
    while (3 * r * (r + 1) < total) r += 1;
    return r + MAX_PUSH_RING;
  }

  function allocateSectorCells(counts, sectors, maxRing, zOrder) {
    var need = counts.slice();
    var cap = maxRing || ringCapFor(counts.reduce(function (a, b) { return a + b; }, 0));
    var out = counts.map(function () { return []; });
    // 挑格顺序：热的分区先挑，于是它们拿到更靠内的圈（人类 2026-09-08）。
    // 缺省是分区序号，行为与加这个参数之前完全一致。
    var pick = (zOrder && zOrder.length === counts.length)
      ? zOrder.slice()
      : counts.map(function (_, i) { return i; });
    var done = need.every(function (n) { return n <= 0; });
    for (var d = 1; d <= cap && !done; d++) {
      var w = slotWidthDeg(d);
      var slots = hexRing(d).map(function (c) { return { cell: c, taken: false }; });
      var n = slots.length;
      var j, z;

      // 领离本扇区中心最近的空格位。
      // ⚠️ **连通优先于角度**：本区已经有格子的时候，只在「与本区某格相邻」的
      // 候选里挑；一个都没有才退回纯按角度（第一格，以及实在挤不下的时候）。
      // 纯按角度会破「每区格子连续」这条硬不变式 —— 窄扇区被推到外圈之后，
      // 角度最近的空位未必挨着本区已有的那格。界线画法（逐边判邻居归属）
      // 建立在连通之上，断开会画出飞地的边。
      // 2026-09-08 给每区加占位格时撞出来的：20 分区/15 个单项目分区那条
      // 极端用例直接红在「分区6 的格子不连续」。
      // 「这个格位挨着分区 z 已经领到的格子吗」。z 一格都还没领到时返回 true
      // —— 第一格无所谓挨着谁。
      function touchesZone(z, cell) {
        if (!out[z].length) return true;
        for (var i = 0; i < HEX_DIRS.length; i++) {
          var nq = cell.q + HEX_DIRS[i][0], nr = cell.r + HEX_DIRS[i][1];
          for (var k2 = 0; k2 < out[z].length; k2++) {
            if (out[z][k2].q === nq && out[z][k2].r === nr) return true;
          }
        }
        return false;
      }

      // allowDisjoint：最后一圈的兜底，允许领一个接不上的格位（宁可有飞地
      // 也别整个分配失败）。正常圈一律 false —— 本圈接不上就**这一圈不领**，
      // 留给下一圈（外圈更大、空位更多），这正是修好飞地的那一下。
      function claimNearest(z, relaxed, allowDisjoint) {
        var hasOwn = out[z].length > 0;
        var best = -1, bestGap = Infinity, bestAdj = false;
        for (var k = 0; k < n; k++) {
          if (slots[k].taken) continue;
          var adj = !hasOwn || touchesZone(z, slots[k].cell);
          if (!adj && !allowDisjoint) continue;
          // 挨着的一律胜过不挨着的；同类之间才比角度。
          if (bestAdj && !adj) continue;
          var g = angleGap(slots[k].cell.angleDeg, sectors[z].center);
          if (adj && !bestAdj) { best = k; bestGap = g; bestAdj = true; continue; }
          if (g < bestGap) { bestGap = g; best = k; }
        }
        if (best < 0) return false;
        slots[best].taken = true;
        out[z].push({ q: slots[best].cell.q, r: slots[best].cell.r, ring: d,
                      angleDeg: slots[best].cell.angleDeg, relaxed: relaxed });
        need[z] -= 1;
        return true;
      }

      if (d >= MAX_PUSH_RING) {                       // 趟 0：饿着的分区先挑
        for (var zi0 = 0; zi0 < pick.length; zi0++) {
          z = pick[zi0];
          if (need[z] <= 0 || out[z].length) continue;
          claimNearest(z, true, true);      // 第一格本来就没有"接不接得上"可言
        }
      }

      for (j = 0; j < n; j++) {                       // 趟 1：严格
        if (slots[j].taken) continue;                 // 趟 0 领走的不能再发一次
        for (var zi1 = 0; zi1 < pick.length; zi1++) {
          z = pick[zi1];
          if (need[z] <= 0) continue;
          // 窄扇区才要求"整格放得进去"；宽扇区只要格位中心落进来就发。
          // 窄区不再被"整格放得进扇区"挡住（2026-09-14）：扇区是圆周的划分，挡住 = 在自己
          // 方向上挖一个永远补不上的洞（见趟 1.5 的长注释）。角度归属仍然照旧只认 inSector。
          if (!inSector(slots[j].cell.angleDeg, sectors[z])) continue;
          // 连通优先，和 claimNearest 同一条规矩：本区已经有格子了，就只接
          // 挨着它们的格位。接不上就本圈先不领 —— 下一圈、或者趟 2 的
          // claimNearest 会连通地补上。角度对了但接不上，画出来就是飞地。
          if (!touchesZone(z, slots[j].cell)) continue;
          slots[j].taken = true;
          out[z].push({ q: slots[j].cell.q, r: slots[j].cell.r, ring: d,
                        angleDeg: slots[j].cell.angleDeg, relaxed: false });
          need[z] -= 1;
          break;
        }
      }
      // 趟 1.5（2026-09-14 修「空洞」）：本圈常规分配走完后，**一格都还没拿到**的分区，
      // 领本圈剩下的、中心落在自己扇区里的最近空位。
      //
      // ⚠️ 为什么窄区外延不能再挡这一步：扇区是圆周的**划分**，第 d 圈的每个格位有且只有
      // 一个分区能领（它的扇区主人）。主人被外延规则挡在本圈外，这个格位就**永远空着**
      // —— 没有别的分区能补上。所以"外延"在几何上等价于"在自己方向上挖洞"：
      // 真机 12 区 / 25 项目那次，六个 14.4° 的分区一起被挡到第 5 圈，第 4 圈整圈空（人类：
      // 「左上有一大块空洞」）；只治第 1 圈之后，第 2 圈又露出同源的两个洞（人类：
      // 「把那个小空洞逻辑处理一下」）。于是**分区的第一格一律按最内侧可落位处安排**，
      // NARROW_DEG 只继续管"第二格及以后"（趟 1 的严格判据），不再决定起始圈。
      // 代价是窄区也可能贴着中心：第 1 圈总共只有 6 个方向，谁占都占 60° 视觉宽度，
      // 这个取舍躲不掉 —— 洞比宽度更难看。
      //
      // ⚠️ 必须排在趟 1 **后面**：让饿着的先挑会把整圈吃光（实测 12 区 / 25 项目：第 2 圈 12 个
      // 格位全被单项目分区领走，还要继续长的分区在第 3 圈找不到与自己相邻的空位——第 3 圈
      // 碰不到第 1 圈——一路推到第 60 圈才被兜底接住）。连通优先不让位给"谁先挑"。
      //
      // ⚠️ 只放宽"整格放得进扇区"，**角度归属一步不让**：只领中心落在自己扇区里的格位，
      // 「从中心沿任一角度射出去碰到的格子必属同一分区」这条读图不变式有单测钉着。
      for (var zi15 = 0; zi15 < pick.length; zi15++) {
        z = pick[zi15];
        if (need[z] <= 0 || out[z].length) continue;
        if (d >= MAX_PUSH_RING) continue;              // 那边有趟 0 / 趟 2 管
        for (var k15 = 0, best15 = -1, bg15 = Infinity; k15 < n; k15++) {
          if (slots[k15].taken) continue;
          if (!inSector(slots[k15].cell.angleDeg, sectors[z])) continue;
          var g15 = angleGap(slots[k15].cell.angleDeg, sectors[z].center);
          if (g15 < bg15) { bg15 = g15; best15 = k15; }
        }
        if (best15 < 0) continue;                      // 本圈自己扇区里没空位，等下一圈
        slots[best15].taken = true;
        out[z].push({ q: slots[best15].cell.q, r: slots[best15].cell.r, ring: d,
                      angleDeg: slots[best15].cell.angleDeg, relaxed: false });
        need[z] -= 1;
      }
      if (d >= MAX_PUSH_RING) {                       // 趟 2：放宽
        for (var zi2 = 0; zi2 < pick.length; zi2++) {
          z = pick[zi2];
          while (need[z] > 0) { if (!claimNearest(z, true, d >= cap)) break; }
        }
      }
      done = need.every(function (x) { return x <= 0; });
    }
    if (!done) throw new Error("扇区分配失败：60 圈之内没放下全部项目");
    return out;
  }

  // ── 1b. 分区边界线：晶格邻接的直接推论 ────────────────────────────
  //
  // 咬合之后每条边都被**恰好两个格位**共享（或一个格位 + 外部空白）。
  // 一条边是边界边 ⟺ 对面那格不存在、或属于另一个分区。
  // 所以边界线不是"画一个多边形去套住这堆格子"，是**逐格逐边判一次**，
  // O(6n)，凹形分区、飞地、被推到外圈的孤格全都天然正确——
  // 凸包/多边形拟合那条路在这三种形状上都会画错。
  //
  // 两个分区贴着的那条边会被**两侧各判出一次**（几何完全相同），必须去重 ——
  // 不去重就是同一条线描两遍：视觉上白描一遍，而且外沿边和内部分界边的笔画权重
  // 会不一样（外沿只描一次），看上去像"有的界线粗有的细"的随机 bug。
  // 去重键取两个端点排序后的坐标，与从哪一侧发现它无关。
  //
  // 返回值是**以 S 为单位、相对蜂巢原点**的线段，调用方乘上像素 size 即可：
  //   [{ zoneIds:[…], colors:[…], q, r, dir, outer, x1, y1, x2, y2, nx, ny }, …]
  // `dir` 是 HEX_DIRS 里的方向序号 —— 对面那格 = (q+HEX_DIRS[dir][0], r+HEX_DIRS[dir][1])。
  // 有了它才能在虚拟格延伸那一步反查"这条边两侧是真格还是虚拟格"。
  // `outer` = 对面没有格子（蜂巢外沿）；false = 两个分区之间的分界。
  // `nx,ny` 是**单位法向**，从 zoneIds[0] 那一侧指向 zoneIds[1] 那一侧 ——
  // 分界线要画成"两个分区各自的色盘色对半开"的双色带，就得知道哪半边是谁的。
  // 法向不能靠"线段方向转 90°"现推：转哪个 90° 取决于顶点顺序，顺序一变就左右
  // 对调，颜色整体镜像，而且是**看得见但查不出来**的那种错。这里直接用晶格
  // 邻居方向算，方向由数据本身定死。
  // 一条边的几何身份：两个端点排序后拼起来，与从哪一侧发现它无关。
  // 去重用它，下面"外沿给分界让位"也用它 —— 两处必须是同一个判据，
  // 否则会出现"这边认为重了、那边认为没重"的半重叠。
  function edgeKey(e) {
    return [e.x1.toFixed(4) + "_" + e.y1.toFixed(4),
            e.x2.toFixed(4) + "_" + e.y2.toFixed(4)].sort().join("|");
  }

  function zoneBorderEdges(cells) {
    var own = {};
    (cells || []).forEach(function (c) { own[cellKey(c)] = c; });
    var out = [], seen = {};
    (cells || []).forEach(function (c) {
      var base = axialToPixel(c, 1);
      for (var i = 0; i < 6; i++) {
        var nb = own[(c.q + HEX_DIRS[i][0]) + "," + (c.r + HEX_DIRS[i][1])];
        if (nb && nb.zoneId === c.zoneId) continue;      // 同区内部边，不画
        var a = HEX_VERTS[(i + 1) % 6], b = HEX_VERTS[(i + 2) % 6];
        var p1 = { x: base.x + a[0], y: base.y + a[1] };
        var p2 = { x: base.x + b[0], y: base.y + b[1] };
        var nb2 = axialToPixel({ q: HEX_DIRS[i][0], r: HEX_DIRS[i][1] }, 1);
        var nlen = Math.hypot(nb2.x, nb2.y) || 1;
        var k = edgeKey({ x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y });
        if (seen[k]) continue;
        seen[k] = 1;
        out.push({
          zoneIds: nb ? [c.zoneId, nb.zoneId] : [c.zoneId],
          colors: nb ? [c.color, nb.color] : [c.color],
          color: c.color, outer: !nb, q: c.q, r: c.r, dir: i,
          x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y,
          nx: nb2.x / nlen, ny: nb2.y / nlen
        });
      }
    });
    return out;
  }
  // ── 1c. 虚拟格延伸：把分界线沿"本该在那儿的六边形"继续折出去 ──────────
  //
  // 人类：「颜色分界线往外拓展，更显眼」→ 第一版做成了**径向直线**射线。
  // 人类看过实物：「射线不够好看啊，做成沿着虚拟的六边型的格式」。
  //
  // 病根很清楚：蜂巢内部的分界线是沿六边形边走的**折线**，突然接一条笔直的
  // 放射线，两种语言拼在一起。正解是把蜂巢**假装再长大一圈** —— 空位上补虚拟格，
  // 虚拟格按"角度落在谁的扇区里"归属，然后照样用 zoneBorderEdges 判边界边。
  // 延伸线于是天然还是沿六边形边走的折线，和内部那段是同一种笔迹。
  //
  // 三条规矩：
  //   · 真格保持真格的归属（分配算法的结果，不重判）；空位才按角度归。
  //   · **最外那圈虚拟格的外沿不画**（e.outer）—— 画了就是给整张图套一个大六边形，
  //     那是"边框"不是"分界"。
  //   · 两侧**都是真格**的边不在这里出，它已经由内部分界带画过了；这里只出
  //     "至少一侧是虚拟格"的边。两边互不重叠，不会描两遍。
  // 全盘归属图：真格 + 虚拟格，每个晶格位都知道自己属于谁。
  // 延伸线和「逐边内缩」两处都要查它，所以抽出来只算一次。
  //
  // 虚拟层是**从真格往外膨胀 ext 层**，不是"把 maxRing+ext 圈的圆盘铺满"。
  // 铺满圆盘那一版实测很难看：蜂巢是凹的、maxRing 又只按最远那一格算，
  // 于是最外圈的虚拟格离真格好几格远，分界线一路折到画布边上，
  // 整张图变成一张网，喧宾夺主（截图 hexv5-02）。
  // 膨胀式只贴着蜂巢外沿长一圈，线自然跟着轮廓走，长度也自然收得住。
  // 顺带把蜂巢内部的空洞也填上（空洞和真格相邻，第一层就吃进来了），
  // 分界线在洞里不会断。
  function buildZoneMap(cells, zoneModels, ext) {
    var real = { "0,0": true };                 // 中心格算真格
    (cells || []).forEach(function (c) { real[cellKey(c)] = true; });
    var all = [{ q: 0, r: 0, zoneId: "__center__", color: "var(--line)" }];
    (cells || []).forEach(function (c) { all.push(c); });
    if (zoneModels.length) {
      var sectors = zoneModels.map(function (z) {
        return { start: z.startAngle, sweep: z.sweepDeg };
      });
      var seen = {};
      Object.keys(real).forEach(function (k) { seen[k] = 1; });
      var frontier = Object.keys(real).map(function (k) {
        var a = k.split(","); return { q: +a[0], r: +a[1] };
      });
      for (var L = 0; L < ext; L++) {
        var next = [];
        frontier.forEach(function (c) {
          HEX_DIRS.forEach(function (d) {
            var nq = c.q + d[0], nr = c.r + d[1], k = nq + "," + nr;
            if (seen[k]) return;
            seen[k] = 1;
            var v = decorate(nq, nr);
            var z = 0;
            for (; z < sectors.length; z++) { if (inSector(v.angleDeg, sectors[z])) break; }
            // 扇区首尾相接铺满 360°，正常一定命中；命中不了只可能是 0°/360°
            // 接缝上的浮点毛刺 —— 退给最后一个扇区，不是丢掉这一格。
            if (z >= sectors.length) z = sectors.length - 1;
            v.zoneId = zoneModels[z].id;
            v.color = zoneModels[z].color;
            v.virtual = true;
            all.push(v); next.push(v);
          });
        });
        frontier = next;
      }
    }
    var zoneAt = {};
    all.forEach(function (c) { zoneAt[cellKey(c)] = c.zoneId; });
    return { all: all, real: real, zoneAt: zoneAt };
  }

  function virtualBorderEdges(map) {
    return zoneBorderEdges(map.all).filter(function (e) {
      if (e.outer) return false;
      var nq = e.q + HEX_DIRS[e.dir][0], nr = e.r + HEX_DIRS[e.dir][1];
      return !(map.real[e.q + "," + e.r] && map.real[nq + "," + nr]);
    });
  }

  // 逐边内缩标记：一个格子的第 i 条边，对面那格是不是**别的分区**。
  //
  // 人类：「我要的不是每个六边形格子和格子之间间距变大，我要的是只有分割线的
  // 那一条变粗」。所以缝不能全局放宽（那会把同区之间的咬合也一起松掉），
  // 只能**逐边**：朝向别的分区的那几条边往里收，朝向同区的边一点不动。
  // 格子中心仍然钉在晶格上，收的是画出来的多边形，不是位置。
  function boundaryDirs(cell, map) {
    var mine = map.zoneAt[cellKey(cell)];
    return HEX_DIRS.map(function (d) {
      var other = map.zoneAt[(cell.q + d[0]) + "," + (cell.r + d[1])];
      return other !== undefined && other !== mine;
    });
  }

  // ── 2. 分区配色：只做「第 N 个分区用第几号色」的映射，**不产出色值** ────
  //
  // 契约红线（contracts/design-tokens-v1.md §2）：「模块 CSS/JS 禁裸 hex，
  // 只准 var(--…)；唯一豁免：兜底块本身（A2）」。在 JS 里现算 hsl()/oklch()
  // 同样是**运行时生成色值**，绕过 A1 对比度校验，改色也不再走
  // 「先改契约 → 再改 tokens.css → 机械同步各仓兜底块」的流程。
  // 本轮初版犯过这个错（产出 "hsl(204, 34%, 56%)" 字符串），查契约后撤回。
  //
  // 所以本文件**只决定第几号色**，不决定第几号色长什么样：
  //   · 默认灰（153,153,153）/ 没设色 → `var(--zone-N)`，N = 分区序号 % 色阶数 + 1
  //   · 显式设过色 → 原样用 zone.color（用户设定，不是主题，契约 §3 已豁免它留在 API 内）
  //
  // ⚠️ TODO：`--zone-1..--zone-12` 目前只有 hex.css 里的**临时兜底块**
  // 定义（哨兵注释包着，整块可删）。把分区色阶加进 design-tokens-v1.md 并落到
  // tokens.css 之后，删掉那个兜底块即可，本文件一行都不用改——它已经只消费变量名。
  var DEFAULT_ZONE_RGB = [153, 153, 153];   // 后端默认分区色（十进制，避免裸 hex）
  var ZONE_SLOT_COUNT = 12;                 // 色阶格数；分区多于它就回卷复用

  function zoneColorSlot(index) {
    return (Math.abs(index) % ZONE_SLOT_COUNT) + 1;
  }
  function zoneColorVar(index) {
    return "var(--zone-" + zoneColorSlot(index) + ")";
  }

  // 解析 3 位/6 位十六进制颜色字符串 → [r,g,b]；不认识就返回 null。
  function parseHexColor(value) {
    var s = String(value || "").trim();
    var m = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.exec(s);
    if (!m) return null;
    var body = m[1];
    if (body.length === 3) body = body[0] + body[0] + body[1] + body[1] + body[2] + body[2];
    return [
      parseInt(body.slice(0, 2), 16),
      parseInt(body.slice(2, 4), 16),
      parseInt(body.slice(4, 6), 16)
    ];
  }

  function isDefaultZoneColor(value) {
    if (!value) return true;                 // 没设色 = 默认
    var rgb = parseHexColor(value);
    if (!rgb) return false;                  // 认不出的写法当作"人设过的"，原样用
    return rgb[0] === DEFAULT_ZONE_RGB[0] &&
           rgb[1] === DEFAULT_ZONE_RGB[1] &&
           rgb[2] === DEFAULT_ZONE_RGB[2];
  }

  // 显式设过色的分区**不参与色阶轮转**，用它自己的颜色（学习区就是这一条）。
  function resolveZoneColor(zone, index) {
    var raw = zone && zone.color;
    if (isDefaultZoneColor(raw)) {
      return { color: zoneColorVar(index), slot: zoneColorSlot(index), source: "auto" };
    }
    return { color: raw, slot: null, source: "zone" };
  }

  // ── 蜂巢模型：分区 → 扇区 → 格子 → 项目 ────────────────────────
  // opts:
  //   · order —— 分区**绕圈的先后**（人类拖动分区名定的，见 hex-app.js::bindZoneDrag）。
  //     只影响扇区角度的排布顺序，**不影响配色** —— 配色永远按分区自己的原始序号
  //     （下面的 slotOf），否则拖一次分区全场换色，谁都认不出自己那块地了。
  //   · heat —— { projectId: 加权最近完成次数 }（completionHeat 算的）。
  //     项目按热度排 → 热的拿到本分区更靠内的格；分区按热度总和排挑格顺序 →
  //     热的分区整体更靠近中心。
  // 分区色混进 panel 的比例：最内圈 max、最外圈 min，中间等比例。
  // 中心计时格用 center（比 max 再深一档）—— 闪烁结束落到的颜色就是它。
  // center = 中心计时格：最深档再加一档（人类 2026-09-12：「闪烁完就是对应的分区最浓颜色再加一档」）。
  // 一档 = 8 个百分点（max − min = 24，正好三档）。
  var DEPTH_MIX = { max: 44, min: 20, center: 52 };   // 最浅也要看得出颜色（人类：「保证最浅的也要有一点颜色」）
  function zoneMix(depth) {
    var d = Math.max(0, Math.min(1, depth || 0));
    return +(DEPTH_MIX.max - d * (DEPTH_MIX.max - DEPTH_MIX.min)).toFixed(2);
  }

  // ── 冷区权重补贴（2026-09-14，人类判：「给冷区（第二圈都排不上的）加一个权重补贴」）──
  //
  // 病灶：扇区角度 ∝ 项目数，项目少的分区扇区就窄；窄到一定程度，**内圈根本没有
  // 一个格位中心落在它里面** —— 这不是"被热区抢走了"，是几何上就没有位置。
  // 真机 10 区 / 40 项目：财务 3 个项目 ⇒ 扇区 27°，落在 243°–270°；
  // 第 1 圈格位中心在 0/60/…/240/300（60° 一格），第 2 圈在 0/30/…/240/270（30° 一格）
  // —— 243°–270° 这个半开区间一个都不含，于是财务的第一格只能等到第 3 圈
  // （20° 一格，260° 落进来了），三个项目沿半径串成一根刺，最外那格孤零零挂在外面。
  //
  // 所以补贴必须加在**权重**上（＝扇区角度），不是加在挑格顺序上：
  // 顺序再靠前，自己扇区里没有格位也一样领不到。（先做的顺序补贴版实测
  // 前后完全一样，就是这个原因。）
  //
  // 补法：给"第 COLD_START_RING 圈以内一个格位中心都够不着"的分区一个**角度下限**
  // ——够得着第 2 圈就行，也就是 slotWidthDeg(2) = 30°。长度 ≥ 30° 的半开区间
  // 必然含一个 30 的倍数，所以这个下限是**够用且最小**的，不多占一度。
  // 其余分区按项目数瓜分剩下的角度，比例关系不变。
  //
  // ⚠️ 要迭代：把一个分区撑宽会把后面所有分区的起始角推走，可能把原本够得着的
  // 挤成够不着。所以反复算到集合稳定（最多 COLD_ROUNDS 轮）。
  // ⚠️ 要有刹车：补贴总量超过 COLD_BUDGET 就整体放弃 —— 分区多到人人都要补时，
  // "按项目数分角度"这条读图规则会被补成一锅平均，那还不如留着那根刺。
  var COLD_START_RING = 2;
  var COLD_ROUNDS = 6;
  var COLD_BUDGET = 0.6;          // 补贴后的下限合计不许超过整圈的 60%

  // 第 d 圈有没有格位中心落在这个扇区里（只看几何，不看谁占了）。
  function sectorHasSlot(sec, d) {
    var ring = hexRing(d);
    for (var i = 0; i < ring.length; i++) {
      if (inSector(ring[i].angleDeg, sec)) return true;
    }
    return false;
  }
  function reachesInner(sec) {
    for (var d = 1; d <= COLD_START_RING; d++) if (sectorHasSlot(sec, d)) return true;
    return false;
  }

  // 返回补贴后的权重（数组）。没有分区需要补贴时原样返回入参。
  function maxRingOfBlocks(blocks) {
    var max = 0;
    blocks.forEach(function (b) { b.forEach(function (c) { if (c.ring > max) max = c.ring; }); });
    return max;
  }

  function subsidizeColdWeights(counts) {
    var floorShare = slotWidthDeg(COLD_START_RING) / 360;   // 30/360
    var weights = counts.slice();
    var cold = counts.map(function () { return false; });
    for (var round = 0; round < COLD_ROUNDS; round++) {
      var secs = buildSectors(weights);
      var changed = false;
      for (var i = 0; i < secs.length; i++) {
        if (!cold[i] && !reachesInner(secs[i])) { cold[i] = true; changed = true; }
      }
      if (!changed) break;
      var coldN = cold.filter(Boolean).length;
      if (coldN * floorShare > COLD_BUDGET) return counts;   // 刹车：补不起就不补
      // 冷区各拿 floorShare，其余按项目数瓜分剩下的。
      var rest = 1 - coldN * floorShare;
      var restTotal = 0;
      counts.forEach(function (c, k) { if (!cold[k]) restTotal += c; });
      weights = counts.map(function (c, k) {
        if (cold[k]) return floorShare;
        return restTotal > 0 ? rest * c / restTotal : rest / Math.max(1, counts.length - coldN);
      });
    }
    return cold.some(Boolean) ? weights : counts;
  }

  function buildHoneycomb(tree, opts) {
    opts = opts || {};
    var heat = opts.heat || {};
    var zones = ((tree && tree.zones) || []).slice().sort(function (a, b) {
      return (a.order == null ? 0 : a.order) - (b.order == null ? 0 : b.order);
    });
    // 配色序号先钉死，之后怎么重排都不动它。
    var slotOf = {};
    zones.forEach(function (z, i) { slotOf[z.id] = i; });
    if (opts.order && opts.order.length) {
      var rank = {};
      opts.order.forEach(function (id, i) { rank[id] = i; });
      zones = zones.slice().sort(function (a, b) {
        // 没在 order 里的（新建的分区）排在后面，内部仍按原始序号，稳定。
        var ra = rank[a.id] === undefined ? 1e6 + slotOf[a.id] : rank[a.id];
        var rb = rank[b.id] === undefined ? 1e6 + slotOf[b.id] : rank[b.id];
        return ra - rb;
      });
    }
    var projects = (tree && tree.projects) || [];
    var byZone = {};
    zones.forEach(function (z) { byZone[z.id] = []; });
    projects.forEach(function (p) { if (byZone[p.zoneId]) byZone[p.zoneId].push(p); });
    // 分区内部：热的项目排前面 → 拿到本分区最靠内的那一格。
    // 并列时回落到项目自己的 order，保证结果稳定（同样的输入永远同样的图）。
    Object.keys(byZone).forEach(function (zid) {
      byZone[zid].sort(function (a, b) {
        var d = (heat[b.id] || 0) - (heat[a.id] || 0);
        if (d) return d;
        return (a.order == null ? 0 : a.order) - (b.order == null ? 0 : b.order);
      });
    });

    // 零项目的分区也要有存在感：至少一个空格子占位，扇区也照样分给它。
    // ⚠️ **不预留空位格**（2026-09-08 人类改判）。
    // 上一版为了"有个地方可以长按建项目"给每区 +1 格，人类看了实物：
    // 「每个分区都多了一个高饱和度色块……我要的是整个分区长按点击任何一个
    // 黑色区域，都会在那里出现一个六边形逐渐显现。」
    // 也就是说建项目的抓手是**蜂巢外的空白**，不是蜂巢里预留的一格
    // （落点在 hex-app.js::bindBlankPress）。这里回到只按项目数分格。
    // Math.max(1, n)：一个项目都没有的分区也占一格，否则分区本身会消失。
    var counts = zones.map(function (z) { return Math.max(1, byZone[z.id].length); });
    // 扇区角度 ∝ 项目数；只有"内圈一个格位都够不着"的冷区拿一份角度下限补贴。
    var weights = subsidizeColdWeights(counts);
    var sectors = buildSectors(weights);
    // 分区热度 = 它名下项目的热度之和。热的先挑格 → 整体更靠近中心。
    var zoneHeat = zones.map(function (z) {
      return byZone[z.id].reduce(function (a, p) { return a + (heat[p.id] || 0); }, 0);
    });
    var pickOrder = zones.map(function (_, i) { return i; }).sort(function (a, b) {
      var d = zoneHeat[b] - zoneHeat[a];
      return d || (a - b);
    });
    var blocks = zones.length ? allocateSectorCells(counts, sectors, undefined, pickOrder) : [];
    // ⚠️ 补贴是从**别的分区**身上拿角度的。拿多了，被瘦下去的那个分区可能反而
    // 塞不下自己的格子：真机 2026-09-18，工作区 24°→15°，15° 的扇区在第 4 圈以内
    // 最多含一个格位中心，第二格找不到落点，被兜底扔到最外圈。
    // 所以补完必须回头看一眼：最外圈变大了就整体不补，原样按项目数分角度。
    // 「只在不劣于时才采纳」——同一条纪律，冷区补贴第一版就该有，漏了。
    if (weights !== counts && zones.length) {
      var plainSectors = buildSectors(counts);
      var plain = allocateSectorCells(counts, plainSectors, undefined, pickOrder);
      if (maxRingOfBlocks(blocks) > maxRingOfBlocks(plain)) {
        sectors = plainSectors;
        blocks = plain;
      }
    }

    var cells = [];
    var zoneModels = zones.map(function (zone, zi) {
      var paint = resolveZoneColor(zone, slotOf[zone.id]);
      var mine = byZone[zone.id];
      var zoneCells = blocks[zi].map(function (c, ci) {
        var project = mine[ci] || null;
        var cell = {
          q: c.q, r: c.r, ring: c.ring, angleDeg: c.angleDeg,
          zoneId: zone.id, zoneName: zone.name,
          color: paint.color, colorSlot: paint.slot, colorSource: paint.source,
          relaxed: !!c.relaxed,
          project: project,
          placeholder: !project
        };
        cells.push(cell);
        return cell;
      });
      var rings = zoneCells.map(function (c) { return c.ring; });
      // 深浅（人类 2026-09-12）：「分区越外侧的项目六边形颜色越浅，越靠近中心（热度越高）颜色越深。
      // 要弄成等比例变浅到固定值，不要项目多了就无限变浅。」
      // 所以按**本分区内**的圈数归一化到 0–1（最内圈 0、最外圈 1），再线性映射到
      // [DEPTH_MIX.max, DEPTH_MIX.min] 这个固定区间：项目多只是分得更细，两端永远不变。
      // 用圈数而不用热度名次：热的本来就排在内圈（见上面的排序），而圈数是看得见的"外侧"。
      var r0 = Math.min.apply(null, rings), r1 = Math.max.apply(null, rings);
      zoneCells.forEach(function (c) {
        c.depth = r1 > r0 ? (c.ring - r0) / (r1 - r0) : 0;
        c.mix = zoneMix(c.depth);
      });
      return {
        id: zone.id, name: zone.name,
        color: paint.color, colorSlot: paint.slot, colorSource: paint.source,
        projectCount: mine.length,
        heat: +zoneHeat[zi].toFixed(4),
        sweepDeg: sectors[zi].sweep,
        startAngle: sectors[zi].start,
        centerAngle: sectors[zi].center,
        startRing: rings.length ? Math.min.apply(null, rings) : 0,
        endRing: rings.length ? Math.max.apply(null, rings) : 0,
        cells: zoneCells
      };
    });

    var maxRing = 0;
    cells.forEach(function (c) { maxRing = Math.max(maxRing, c.ring); });
    // 边界线连中心格一起算：中心格自成一"区"，于是它与四周项目格之间也有一圈线，
    // 中心那格在视觉上被圈出来，不会看着像某个分区的一员。
    var withCenter = cells.concat([{ q: 0, r: 0, zoneId: "__center__", color: "var(--line)" }]);
    // 真格 A 挨着虚拟格 B 时，那条边**既**是 A 的外沿**又**是 A|B 的分界。
    // 它本质上是分界（虚拟层存在的全部意义就是把分界接着往外画），所以外沿让位——
    // 不让就是同一条线描两遍：细外沿从粗分界带底下露出来，而且只在蜂巢边缘
    // 那一圈露，看着像随机的毛边。
    var zmap = buildZoneMap(cells, zoneModels, VIRTUAL_EXT);
    var borders = zoneBorderEdges(withCenter);
    var extensions = zoneModels.length ? virtualBorderEdges(zmap) : [];
    // 每个格子记下自己哪几条边朝着别的分区 —— 渲染层据此逐边内缩。
    cells.forEach(function (c) { c.boundaryDirs = boundaryDirs(c, zmap); });
    var extAt = {};
    extensions.forEach(function (e) { extAt[edgeKey(e)] = 1; });
    borders = borders.filter(function (e) { return !(e.outer && extAt[edgeKey(e)]); });
    // 中心格那一圈**整圈都算内部分界**：中心是计时井、不是分区，它六条边里有几条
    // 挨着真格（→内部分界）、有几条挨着空位（→虚拟延伸），分到两组就是一圈线
    // 一半粗一半淡，看着像画坏了。按"挨着中心的边一律走内部那组"收口。
    var centerEdges = extensions.filter(function (e) {
      return e.zoneIds.indexOf("__center__") >= 0;
    });
    extensions = extensions.filter(function (e) {
      return e.zoneIds.indexOf("__center__") < 0;
    });
    borders = borders.concat(centerEdges);
    return {
      center: CENTER, radius: maxRing, zones: zoneModels, cells: cells,
      borders: borders, extensions: extensions,
      // 中心格是计时井，六条边全都朝着分区 —— 整圈都要让开。
      centerBoundaryDirs: [true, true, true, true, true, true]
    };
  }

  return {
    NO_TODO_TEXT: A.NO_TODO_TEXT,
    NARROW_DEG: NARROW_DEG,
    DEFAULT_ZONE_RGB: DEFAULT_ZONE_RGB,
    ZONE_SLOT_COUNT: ZONE_SLOT_COUNT,
    zoneColorSlot: zoneColorSlot,
    zoneColorVar: zoneColorVar,
    cellKey: cellKey,
    slotsInRing: slotsInRing,
    fairDeg: fairDeg,
    slotWidthDeg: slotWidthDeg,
    MAX_PUSH_RING: MAX_PUSH_RING,
    angleGap: angleGap,
    SQRT3: SQRT3,
    HEX_DIRS: HEX_DIRS,
    HEX_VERTS: HEX_VERTS,
    APOTHEM: APOTHEM,
    insetPolygon: insetPolygon,
    CENTER: CENTER,
    axialRing: axialRing,
    axialDistance: axialDistance,
    axialToPixel: axialToPixel,
    pixelToAxial: pixelToAxial,
    polarToPixel: polarToPixel,
    cellAngle: cellAngle,
    hexRing: hexRing,
    edgeKey: edgeKey,
    zoneBorderEdges: zoneBorderEdges,
    buildZoneMap: buildZoneMap,
    virtualBorderEdges: virtualBorderEdges,
    boundaryDirs: boundaryDirs,
    VIRTUAL_EXT: VIRTUAL_EXT,
    cellDistance: cellDistance,
    isNeighbor: isNeighbor,
    buildSectors: buildSectors,
    inSector: inSector,
    allocateSectorCells: allocateSectorCells,
    subsidizeColdWeights: subsidizeColdWeights,
    ringCapFor: ringCapFor,
    reachesInner: reachesInner,
    COLD_START_RING: COLD_START_RING,

    parseHexColor: parseHexColor,
    isDefaultZoneColor: isDefaultZoneColor,
    resolveZoneColor: resolveZoneColor,
    buildHoneycomb: buildHoneycomb,
    DEPTH_MIX: DEPTH_MIX, zoneMix: zoneMix,

    // ── 审计流那一半（hex-audit.js）原样转发 ──────────────────────
    // 2026-09-14 拆文件时定的：**调用方一个字都不用改**。谁想直接用那一半，
    // require("./hex-audit.js") 也拿得到同一批函数，两条路同一份实现。
    isDoneChange: A.isDoneChange,
    auditOrder: A.auditOrder,
    lastDoneActions: A.lastDoneActions,
    indexTasks: A.indexTasks,
    formatStamp: A.formatStamp,
    recentCompletions: A.recentCompletions,
    latestCompletionForProject: A.latestCompletionForProject,
    nextActionsByProject: A.nextActionsByProject,
    hoverTodoText: A.hoverTodoText,
    topTodos: A.topTodos,
    stampTaskName: A.stampTaskName,
    completionHeat: A.completionHeat,
    HEAT_HALFLIFE_DAYS: A.HEAT_HALFLIFE_DAYS   // 分区规划面板的说明文字要和算法同一个数
  };
});
