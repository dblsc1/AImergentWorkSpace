// hive · hex-data.js + hex-ring.js 单元测试（无框架，Node 内置 assert）
// 跑法：node hex-data.test.js（run_tests.sh 会一并跑）
"use strict";

var assert = require("assert");
var H = require("./hex-data.js");
var Ring = require("./hex-ring.js");

var passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log("  ok - " + name);
}

// ── 工具：判「一组格子是连通的」──────────────────────────────
// 「相邻」= 中心距 ≤ CONTIG_MAX（2.75 个外接圆半径）。一个格距是 2.05，
// 这个阈值覆盖了相邻两圈之间半个槽位的角度错位，同时排除隔一个槽位的斜对角。
function isConnected(cells) {
  if (!cells.length) return false;
  var seen = [cells[0]];
  var grew = true;
  while (grew) {
    grew = false;
    cells.forEach(function (c) {
      if (seen.indexOf(c) !== -1) return;
      for (var i = 0; i < seen.length; i++) {
        if (H.isNeighbor(c, seen[i])) { seen.push(c); grew = true; return; }
      }
    });
  }
  return seen.length === cells.length;
}

// ══════════════════════════════════════════════════════════════
// 1. 「最近完成」去重 —— 本文件最重要的一组
//    实测流水里同一个任务被勾了又取消；漏了去重就是给用户显示假成绩。
// ══════════════════════════════════════════════════════════════

// 线上真实流水的最小切片（2026-09-07 实拉 /api/core/planner/audit）。
var SQ3 = Math.sqrt(3);

test("晶格：第 d 圈恰好 6d 格，格位角宽正好 60/d 度（15° 外延的几何来源）", function () {
  [1, 2, 3, 4, 5, 7].forEach(function (d) {
    assert.strictEqual(H.slotsInRing(d), 6 * d, "第 " + d + " 圈格数不对");
    assert.strictEqual(H.hexRing(d).length, 6 * d);
    assert.ok(Math.abs(H.slotWidthDeg(d) - 60 / d) < 1e-9);
  });
  // 第 4 圈角宽正好 15° —— 产品决定那条「小于 15° 就外延」的阈值不是写死的常数，
  // 是这张表的第 4 行。
  assert.strictEqual(H.slotWidthDeg(4), 15);
});

test("咬合（本轮的核心不变量）：相邻格中心距**恒等于** √3·S，不多不少", function () {
  // 上一版是 RING_STEP=2.05·S 的极坐标网格：>2S 保证不重叠，代价是永远有缝，
  // 判词「格子不咬合」。轴向晶格里相邻中心距恒为 √3·S = 六边形的对边宽，
  // 于是宽 √3·S 的六边形**边贴边**：既不重叠，也没有缝。这一条同时钉死两件事。
  var all = [{ q: 0, r: 0 }];
  for (var d = 1; d <= 4; d++) all = all.concat(H.hexRing(d));
  assert.strictEqual(all.length, 1 + 6 + 12 + 18 + 24);

  var minDist = Infinity, neighbours = 0;
  for (var i = 0; i < all.length; i++) {
    for (var j = i + 1; j < all.length; j++) {
      var dist = H.cellDistance(all[i], all[j]);
      minDist = Math.min(minDist, dist);
      if (H.isNeighbor(all[i], all[j])) {
        neighbours++;
        assert.ok(Math.abs(dist - SQ3) < 1e-9,
          "相邻两格中心距 " + dist.toFixed(9) + " ≠ √3（不咬合）");
      } else {
        assert.ok(dist > SQ3 + 1e-9, "非相邻的两格却挨得比相邻还近");
      }
    }
  }
  assert.ok(Math.abs(minDist - SQ3) < 1e-9, "全场最小中心距应恰好 = √3·S");
  assert.ok(neighbours > 0);
});

test("axialToPixel / cellAngle：0° 在正上方、顺时针；第 1 圈六格是 30° 起每 60° 一格", function () {
  var up = H.axialToPixel({ q: 1, r: -2 }, 10);       // 正上方
  assert.ok(Math.abs(up.x) < 1e-9);
  assert.ok(up.y < 0);
  assert.strictEqual(Math.round(H.cellAngle({ q: 1, r: -2 })), 0);
  assert.strictEqual(Math.round(H.cellAngle({ q: 1, r: 0 })), 90);      // 正右
  assert.strictEqual(Math.round(H.cellAngle({ q: -1, r: 2 })), 180);    // 正下
  assert.strictEqual(Math.round(H.cellAngle({ q: -1, r: 0 })), 270);    // 正左
  assert.deepStrictEqual(H.hexRing(1).map(function (c) { return Math.round(c.angleDeg); }),
    [30, 90, 150, 210, 270, 330]);
});

test("polarToPixel（只给标签用）：0° 正上方，半径按「一圈 = √3·S」", function () {
  var p = H.polarToPixel(2, 0, 10);
  assert.ok(Math.abs(p.x) < 1e-9);
  assert.ok(Math.abs(p.y + 2 * SQ3 * 10) < 1e-9);
  var q = H.polarToPixel(1, 90, 10);
  assert.ok(Math.abs(q.x - SQ3 * 10) < 1e-9);
});

test("扇区：角度 ∝ 项目数，首尾相接铺满 360°", function () {
  var secs = H.buildSectors([2, 1, 5]);
  assert.ok(Math.abs(secs[0].sweep - 90) < 1e-9);
  assert.ok(Math.abs(secs[1].sweep - 45) < 1e-9);
  assert.ok(Math.abs(secs[2].sweep - 225) < 1e-9);
  assert.strictEqual(secs[0].start, 0);
  assert.ok(Math.abs(secs[2].start + secs[2].sweep - 360) < 1e-9);
});

// 线上真实规模：9 分区 / 20 项目。
var LIVE_ZONES = [
  { id: "z_inbox", name: "未分类", color: "#999999", order: -1 },
  { id: "z_bbeb09", name: "园艺区", color: "#4a90d9", order: 0 },
  { id: "z_870928", name: "木工区", color: "#999999", order: 1 },
  { id: "z_a0672c", name: "核对区", color: "#999999", order: 2 },
  { id: "z_6b5773", name: "居家区", color: "#999999", order: 3 },
  { id: "z_019974", name: "手工区", color: "#999999", order: 5 },
  { id: "z_e72386", name: "烘焙区", color: "#999999", order: 6 },
  { id: "z_f1d54b", name: "影音", color: "#999999", order: 7 },
  { id: "z_5dbe2c", name: "收纳区", color: "#999999", order: 8 }
];
var LIVE_COUNTS = { z_inbox: 2, z_bbeb09: 2, z_870928: 2, z_a0672c: 1, z_6b5773: 3, z_019974: 5, z_e72386: 3, z_f1d54b: 1, z_5dbe2c: 1 };

function makeTree(zones, counts) {
  var projects = [];
  zones.forEach(function (z) {
    for (var i = 0; i < (counts[z.id] || 0); i++) {
      projects.push({ id: z.id + "_p" + i, zoneId: z.id, name: z.name + "项目" + i, tasks: [] });
    }
  });
  return { zones: zones, projects: projects };
}

// 整个蜂巢（项目格 + 中心格）分成几个连通块。1 = 一整块。
function componentCount(hive) {
  var all = hive.cells.concat([{ q: 0, r: 0 }]);
  var seen = {}, n = 0;
  all.forEach(function (seed) {
    if (seen[H.cellKey(seed)]) return;
    n++;
    var stack = [seed];
    seen[H.cellKey(seed)] = 1;
    while (stack.length) {
      var c = stack.pop();
      all.forEach(function (o) {
        var k = H.cellKey(o);
        if (!seen[k] && H.isNeighbor(c, o)) { seen[k] = 1; stack.push(o); }
      });
    }
  });
  return n;
}

function assertHoneycombInvariants(hive, expectedZoneCount) {
  assert.strictEqual(hive.zones.length, expectedZoneCount);
  // ① 中心格永远不被项目占用（q=0,r=0 不在任何一圈里）
  hive.cells.forEach(function (c) {
    assert.ok(c.ring >= 1, "有格子落在中心格上");
    assert.ok(!(c.q === 0 && c.r === 0), "有格子落在中心格上");
  });
  // ② 咬合且不重叠：任意两格中心距 ≥ √3·S，且没有两格坐标重合
  var seen = {};
  hive.cells.forEach(function (c) {
    var k = H.cellKey(c);
    assert.ok(!seen[k], "两个格子占了同一个晶格位 " + k);
    seen[k] = 1;
  });
  for (var i = 0; i < hive.cells.length; i++) {
    for (var j = i + 1; j < hive.cells.length; j++) {
      var d = H.cellDistance(hive.cells[i], hive.cells[j]);
      assert.ok(d >= Math.sqrt(3) - 1e-9,
        "两格挨太近会重叠：" + d.toFixed(6) + " < √3");
    }
  }
  // ③ 整块蜂巢（含中心格）是**一整块**，没有分区飘成孤岛
  assert.strictEqual(componentCount(hive), 1,
    "蜂巢裂成了 " + componentCount(hive) + " 块，有分区和主体断开了");
  // ④ 每个分区至少一格、且连续
  hive.zones.forEach(function (z) {
    assert.ok(z.cells.length >= 1, z.name + " 一个格子都没有");
    assert.ok(isConnected(z.cells), z.name + " 的格子不连续");
    assert.ok(z.cells.length >= z.projectCount, z.name + " 格子少于项目数");
  });
}

test("扇区布局：线上真实规模 9 区/20 项目 —— 不重叠、每区连续、中心格空着", function () {
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  assertHoneycombInvariants(hive, 9);
  assert.strictEqual(hive.cells.length, 20);
});

test("不预留空位：有项目的分区**一个占位格都没有**（建项目走蜂巢外的空白）", function () {
  // 2026-09-08 改判：「每个分区都多了一个高饱和度色块……我要的是整个分区
  // 长按点击任何一个黑色区域，都会在那里出现一个六边形逐渐显现。」
  // 于是 counts 回到只按项目数分格，占位格只剩"零项目分区"这一种。
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  assert.strictEqual(hive.cells.filter(function (c) { return c.placeholder; }).length, 0);
  hive.zones.forEach(function (z) {
    assert.strictEqual(z.cells.length, z.projectCount, z.name + " 的格数不等于项目数");
  });
});

test("占位格：只有一个项目都没有的分区才留一格（否则分区本身会消失）", function () {
  var zones = [{ id: "z_empty", name: "空区", color: "#4a90d9", order: 0 },
               { id: "z_full", name: "有货区", color: "#999999", order: 1 }];
  var hive = H.buildHoneycomb(makeTree(zones, { z_empty: 0, z_full: 3 }));
  var empty = hive.cells.filter(function (c) { return c.zoneName === "空区"; });
  assert.strictEqual(empty.length, 1);
  assert.strictEqual(empty[0].placeholder, true);
  var full = hive.cells.filter(function (c) { return c.zoneName === "有货区"; });
  assert.strictEqual(full.length, 3);                       // 3 个项目，不多留
  assert.strictEqual(full.filter(function (c) { return c.placeholder; }).length, 0);
});

test("扇区归属：从中心沿任一角度射出去，碰到的格子必属同一个分区", function () {
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  var byZone = {};
  hive.zones.forEach(function (z) { byZone[z.id] = z; });
  hive.cells.forEach(function (c) {
    var sec = byZone[c.zoneId];
    assert.strictEqual(c.relaxed, false, "真实数据规模不该触发放宽外延");
    assert.ok(H.inSector(c.angleDeg, { start: sec.startAngle, sweep: sec.sweepDeg }),
      c.zoneName + " 的格子 " + c.angleDeg.toFixed(1) + "° 落在自己扇区之外");
  });
  // 反向：任取 360 条射线，命中的格子只能属于射线角度所在的那个分区
  for (var a = 0; a < 360; a += 1) {
    var owner = hive.zones.filter(function (z) {
      return H.inSector(a, { start: z.startAngle, sweep: z.sweepDeg });
    });
    assert.strictEqual(owner.length, 1, a + "° 上不是恰好一个分区");
  }
});

test("扇区布局：项目多的分区扇区更宽（未补贴的分区之间仍严格 ∝ 项目数）", function () {
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  var byId = {};
  hive.zones.forEach(function (z) { byId[z.id] = z; });
  assert.strictEqual(byId.z_019974.cells.length, 5);
  // 2026-09-14 改判：单项目分区可能拿到「冷区补贴」的角度下限，所以不能再拿
  // 5 项目区 ÷ 1 项目区 来验比例。比例关系改在**两个都没被补贴的分区**之间验：
  // 5 个项目（z_019974）对 3 个项目（z_6b5773），应当正好 5:3。
  var ratio = byId.z_019974.sweepDeg / byId.z_6b5773.sweepDeg;
  assert.ok(Math.abs(ratio - 5 / 3) < 1e-9,
    "5 项目区 : 3 项目区 应当是 5:3，实得 " + ratio.toFixed(6));
});

// ── 冷区权重补贴（2026-09-14，产品决定：「给冷区（第二圈都排不上的）加一个权重补贴」）──
//
// 病根是几何而不是排队：扇区窄到内圈**一个格位中心都不落进来**时，挑格顺序排多前
// 都没用。先做的"顺序补贴"版实测前后一模一样，就是这个原因，留着这段注释免得再试一次。
test("冷区补贴：内圈够不着的分区拿到 30° 下限，其余分区之间比例不变", function () {
  var counts = [6, 8, 5, 4, 4, 3, 4, 2, 2, 2];      // 真机 10 区 / 40 项目
  var w = H.subsidizeColdWeights(counts);
  assert.notStrictEqual(w, counts, "这个分布必须触发补贴");
  var secs = H.buildSectors(w);
  var floorDeg = 30;                                 // slotWidthDeg(2)
  secs.forEach(function (sec, i) {
    assert.ok(sec.sweep >= floorDeg - 1e-9 || counts[i] > 3,
      "第 " + i + " 个分区补完还不到 30°");
    assert.ok(H.reachesInner(sec), "第 " + i + " 个分区补完仍然够不着第 2 圈以内");
  });
  // 没被补贴的两个分区（8 个项目 vs 6 个项目）之间，比例照旧
  var ratio = secs[1].sweep / secs[0].sweep;
  assert.ok(Math.abs(ratio - 8 / 6) < 1e-9, "未补贴分区之间比例被改坏了：" + ratio);
});

test("冷区补贴：孤格没了，半径反而更小（10 区 / 40 项目真机分布）", function () {
  var counts = [6, 8, 5, 4, 4, 3, 4, 2, 2, 2];
  var order = counts.map(function (_, i) { return i; });
  function shape(weights) {
    var blocks = H.allocateSectorCells(counts, H.buildSectors(weights), undefined, order);
    var max = 0, starts = [];
    blocks.forEach(function (b) {
      var lo = Infinity;
      b.forEach(function (c) { if (c.ring > max) max = c.ring; if (c.ring < lo) lo = c.ring; });
      starts.push(lo);
    });
    return { max: max, worstStart: Math.max.apply(null, starts) };
  }
  var before = shape(counts);
  var after = shape(H.subsidizeColdWeights(counts));
  assert.strictEqual(before.max, 5, "基线变了就该重新看这条测试");
  assert.strictEqual(before.worstStart, 3, "基线变了就该重新看这条测试");
  assert.ok(after.max < before.max, "补贴后半径没变小：" + after.max);
  assert.ok(after.worstStart <= H.COLD_START_RING,
    "补贴后仍有分区第一格掉在第 " + after.worstStart + " 圈");
});

// ── 2026-09-18 真机事故：新建一个分区之后「蜂巢直接消失了」──────────────
//
// 现象是一格落到第 60 圈（兜底圈上限写死 60），plan 尺寸 9118×8995px，
// 其余三十格被挤成看不见的一点。根因有两层，两层都补了断言：
//   ① 冷区补贴是从别的分区身上拿角度的，把木工区从 24° 瘦到 15°，
//      15° 的扇区在第 4 圈以内最多含一个格位中心 → 第二格没落点。
//   ② 兜底圈上限 60 让"没落点"从「有一格离得远」变成「整张图消失」。
test("真机 12 区 / 28 项目：没有格子被扔到外太空（2026-09-18 蜂巢消失事故）", function () {
  var counts = [1, 2, 2, 5, 5, 1, 1, 1, 5, 3, 3, 1];      // 未分类/园艺/木工/居家/手工/烘焙/影音/收纳/花艺/陶艺/教育/时间管理
  var zones = counts.map(function (_, i) {
    return { id: "z" + i, name: "z" + i, order: i, color: "#999999" };
  });
  var hive = H.buildHoneycomb(makeTree(zones, counts.reduce(function (m, c, i) {
    m["z" + i] = c; return m;
  }, {})));
  var max = 0;
  hive.cells.forEach(function (c) { max = Math.max(max, c.ring); });
  assert.ok(max <= H.ringCapFor(hive.cells.length),
    "有格子越过了兜底圈上限：第 " + max + " 圈");
  assert.ok(max <= 5, "最外圈到了第 " + max + " 圈 —— 蜂巢会被撑得看不见");
});

test("兜底圈上限：够装下全部格子，且绝不是那个写死的 60", function () {
  [1, 6, 19, 28, 40, 120].forEach(function (total) {
    var cap = H.ringCapFor(total);
    assert.ok(3 * cap * (cap + 1) >= total, total + " 个格子装不进 " + cap + " 圈");
    assert.ok(cap < 20, total + " 个格子的上限算成了 " + cap + " 圈，太远了");
  });
  // 单调：格子多，上限不能反而变小
  for (var n = 2; n < 200; n++) {
    assert.ok(H.ringCapFor(n) >= H.ringCapFor(n - 1), n + " 处上限不单调");
  }
});

test("冷区补贴：补不起就整体不补（分区多到人人都要补时不许把角度补成一锅平均）", function () {
  var counts = [];
  for (var i = 0; i < 20; i++) counts.push(1);       // 20 个单项目分区
  var w = H.subsidizeColdWeights(counts);
  assert.strictEqual(w, counts, "20 个分区人人都要补，应当整体放弃补贴");
});

test("扇区边界：浮点不许让两个分区同时认领同一条射线（补贴把权重变成小数后撞出来的）", function () {
  // 真机撞到的那一条：权重从整数变成小数之后，inSector 里的
  // `((angle - start) % 360 + 360) % 360` 规范化往返把 47.647058823529406
  // 改成了 47.64705882352939，300° 上两个分区同时判真。
  var w = H.subsidizeColdWeights([2, 2, 2, 1, 3, 5, 3, 1, 1]);
  var secs = H.buildSectors(w);
  for (var a = 0; a < 360; a += 0.5) {
    var owners = secs.filter(function (s) { return H.inSector(a, s); });
    assert.strictEqual(owners.length, 1, a + "° 上有 " + owners.length + " 个分区");
  }
});

test("窄区不再被推到外圈（2026-09-14 改判），但一步也不许占别人扇区的格位", function () {
  // 原判（2026-09-07）：扇区 <15° 的分区起始圈推到第 3 圈或更外 —— 原话
  // 「角度小于 15 度，小分区六边形可以适度外延排列」。
  // 改判（2026-09-14，两次真机截图）：扇区是圆周的**划分**，第 d 圈的每个格位有且只有它的
  // 扇区主人能领。把主人挡在本圈外 ⇒ 那个格位永远空着，谁也补不上 —— 「外延」在几何上
  // 就等于「在自己方向上挖洞」。用户先报「左上有一大块空洞，渲染逻辑出了大问题」，
  // 修完第 1 圈之后又报「把那个小空洞逻辑处理一下」。于是**起始圈一律取最内侧可落位处**，
  // 代价是窄区也可能贴着中心（第 1 圈只有 6 个方向，谁占都占 60° 视觉宽度，躲不掉）。
  var zones = [], counts = {}, i;
  for (i = 0; i < 20; i++) {
    zones.push({ id: "z" + i, name: "分区" + i, color: "#999999", order: i });
    counts["z" + i] = (i < 5) ? 8 : 1;
  }
  var hive = H.buildHoneycomb(makeTree(zones, counts));
  assertHoneycombInvariants(hive, 20);
  assert.strictEqual(componentCount(hive), 1, "蜂巢裂成了多块");
  var byId = {};
  hive.zones.forEach(function (z) { byId[z.id] = z; });
  hive.cells.forEach(function (c) {
    if (c.relaxed) return;          // 第 5 圈起的兜底（趟 0 / 趟 2）本来就允许越界，另有注释
    var z = byId[c.zoneId];
    assert.ok(H.inSector(c.angleDeg, { start: z.startAngle, sweep: z.sweepDeg }),
      z.name + " 的格子 " + c.angleDeg.toFixed(1) + "° 落在自己扇区之外");
  });
  var narrow = hive.zones.filter(function (z) { return z.sweepDeg < 15; });
  assert.ok(narrow.length >= 15, "构造失败：没造出足够多的窄扇区");
  // 这一档（20 区 / 55 项目，窄扇区 6.5°）窄区仍然落在外圈，但**原因变了**：不再是规则把它
  // 挡在外面，而是内圈根本没有**格位中心落进 6.5° 里**（第 d 圈格位间隔 60/d）。
  // 角度归属不让步的前提下这是几何必然；钉住"不比 MAX_PUSH_RING 更远"就够。
  var rings = narrow.map(function (z) { return z.startRing; }).sort(function (a, b) { return a - b; });
  assert.ok(rings[rings.length - 1] <= H.MAX_PUSH_RING,
    "窄区最远起始圈 " + rings[rings.length - 1] + "：被推得比兜底（MAX_PUSH_RING）还远");
  hive.zones.filter(function (z) { return z.sweepDeg >= 60; })
    .forEach(function (z) { assert.strictEqual(z.startRing, 1); });
});

test("不许有空圈：项目数越过 24 之后，单项目分区不许集体被推到外圈（2026-09-14 真机）", function () {
  // 用户截图：「左上有一大块空洞，渲染逻辑出了大问题」。
  // 根因是阈值踩线：12 个分区 / 25 个项目时，单项目分区扇区 = 360/25 = 14.4°，刚好掉到
  // NARROW_DEG(15°) 以下 ⇒ 要求 60/d ≤ 14.4 ⇒ d ≥ 4.17 ⇒ 六个单项目分区**同时**落到第 5 圈，
  // 而主体只长到第 3 圈：第 4 圈整圈空（实测每圈占用 {1:4, 2:9, 3:6, 5:6}）。
  // 项目总数一越过 24 就整体掉下去，是个静默悬崖 —— 所以断言钉的是"任何规模下都不许出现空圈"。
  var zones = [], counts = {}, i;
  var sizes = [2, 2, 2, 1, 5, 5, 3, 1, 1, 1, 1, 1];     // 真机当天的分布，合计 25
  for (i = 0; i < sizes.length; i++) {
    zones.push({ id: "z" + i, name: "分区" + i, color: "#999999", order: i });
    counts["z" + i] = sizes[i];
  }
  var hive = H.buildHoneycomb(makeTree(zones, counts));
  assertHoneycombInvariants(hive, sizes.length);
  assert.strictEqual(componentCount(hive), 1, "蜂巢裂成了多块");
  var used = {};
  hive.cells.forEach(function (c) { used[c.ring] = (used[c.ring] || 0) + 1; });
  for (i = 1; i <= hive.radius; i++) {
    assert.ok(used[i] > 0, "第 " + i + " 圈整圈空，外面却还有格子（半径 " + hive.radius + "）");
  }
  assert.ok(hive.radius <= 4, "半径 " + hive.radius + "：单项目分区又被推远了");
  // 中心格的六个邻居：扇区主人还有项目没落位就不许空着（2026-09-14 第二次报「小空洞」）
  assert.strictEqual(used[1], 6, "第 1 圈只填了 " + used[1] + " 格，中心旁边留洞了");
});

test("不许有孤岛：18° 的分区必须从内圈就开始填，不能被当成窄区推到外圈", function () {
  // 真机实测出来的 bug（截图 hexv2-01）：产品决定给的阈值是「角度小于 15 度才外延」，
  // 上一版把它做成了对**所有**分区都成立的几何推论（格位角宽 60/d ≤ 扇区角度），
  // 于是 18° 的分区也要 d ≥ 3.33 才够格 —— 第 1–3 圈一格都拿不到，直接落到
  // 第 4 圈，和蜂巢主体断成孤岛，中间空一大片。
  //
  // 红绿反向验证（实测，不是推断）：把 NARROW_DEG 调成 999（＝退回旧规则）跑
  // 同一棵线上真实树，连通块 = 3、半径 = 4；按 15° 判则连通块 = 1、半径 = 3。
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  assert.strictEqual(componentCount(hive), 1);
  assert.strictEqual(hive.radius, 3, "半径回退了，说明窄区判据又把宽区卷进去了");
  var byId = {};
  hive.zones.forEach(function (z) { byId[z.id] = z; });
  // 这三个分区各只有 1 个项目 → 扇区 18°，> 15°，不算窄区
  ["z_a0672c", "z_f1d54b", "z_5dbe2c"].forEach(function (id) {
    assert.ok(byId[id].sweepDeg > H.NARROW_DEG, id + " 构造前提不成立");
    assert.ok(byId[id].startRing <= 3,
      byId[id].name + " 扇区 " + byId[id].sweepDeg.toFixed(0) + "° 不该被推到第 " +
      byId[id].startRing + " 圈");
  });
});

test("零项目的分区也有一个占位格，且照样分到扇区角度", function () {
  var zones = LIVE_ZONES.slice(0, 3);
  var counts = { z_inbox: 0, z_bbeb09: 4, z_870928: 0 };
  var hive = H.buildHoneycomb(makeTree(zones, counts));
  assertHoneycombInvariants(hive, 3);
  var empty = hive.zones.filter(function (z) { return z.projectCount === 0; });
  assert.strictEqual(empty.length, 2);
  empty.forEach(function (z) {
    assert.strictEqual(z.cells.length, 1);
    assert.strictEqual(z.cells[0].placeholder, true);
    assert.ok(z.sweepDeg > 0);
  });
});

test("扇区布局：撑到 15 区/60 项目仍满足全部不变量（设计规模上限）", function () {
  var zones = [], counts = {};
  for (var i = 0; i < 15; i++) {
    zones.push({ id: "z" + i, name: "分区" + i, color: "#999999", order: i });
    counts["z" + i] = (i === 0) ? 0 : 4;
  }
  counts.z1 = 8;
  var hive = H.buildHoneycomb(makeTree(zones, counts));
  assertHoneycombInvariants(hive, 15);
  assert.strictEqual(hive.cells.length, 61);   // 60 个项目 + z0 那个零项目分区的一格
  // 外延是"适度"：放宽只发生在第 MAX_PUSH_RING 圈及以外，且落位仍是离扇区
  // 中心最近的空槽位（方向没跑偏），整张图的半径被这条规则钉住不失控。
  assert.ok(hive.radius <= H.MAX_PUSH_RING + 1, "半径失控：" + hive.radius);
  hive.cells.filter(function (c) { return c.relaxed; }).forEach(function (c) {
    assert.ok(c.ring >= H.MAX_PUSH_RING);
    var z = hive.zones.filter(function (x) { return x.id === c.zoneId; })[0];
    // 容差从 1 个格位放到 2 个：2026-09-08 起 claimNearest **连通优先于角度**
    // （本区已有格子时只在挨着它们的空位里挑）。代价就是落位可能再偏一格 ——
    // 这个交换是刻意的：飞地是硬伤（界线画法建立在每区连通之上），
    // 不够居中只是不好看。仍然钉住"方向没跑偏"，只是尺子松了一格。
    assert.ok(H.angleGap(c.angleDeg, z.centerAngle) <= H.slotWidthDeg(c.ring) * 2 + 1e-9,
      "放宽落位偏离扇区中心太远");
  });
});

test("扇区布局：只有一个分区 / 空树都不崩", function () {
  assertHoneycombInvariants(H.buildHoneycomb(makeTree([LIVE_ZONES[1]], { z_bbeb09: 1 })), 1);
  var empty = H.buildHoneycomb({ zones: [], projects: [] });
  assert.strictEqual(empty.cells.length, 0);
});

// ── 分区界线（本轮新增；咬合之后才存在"公共边"这个东西）────────────

test("界线：孤零零一格 → 6 条边全是外沿，一条分界都没有", function () {
  var edges = H.zoneBorderEdges([{ q: 0, r: 0, zoneId: "a" }]);
  assert.strictEqual(edges.length, 6);
  assert.ok(edges.every(function (e) { return e.outer === true; }));
  // 每条边长恰好 = 1 个外接圆半径 S（正六边形边长 = 外接圆半径）
  edges.forEach(function (e) {
    assert.ok(Math.abs(Math.hypot(e.x2 - e.x1, e.y2 - e.y1) - 1) < 1e-9);
  });
});

test("界线：同区两格贴着 → 中间那条边不画（内部边），外沿剩 10 条", function () {
  var edges = H.zoneBorderEdges([
    { q: 0, r: 0, zoneId: "a" }, { q: 1, r: 0, zoneId: "a" }
  ]);
  assert.strictEqual(edges.length, 10);
  assert.ok(edges.every(function (e) { return e.outer === true; }));
});

test("界线：异区两格贴着 → 中间那条画成分界（outer:false），且**只画一次**", function () {
  // 不去重就是同一条线描两遍：外沿边描一次、分界边描两遍，看上去像"有的界线
  // 粗有的细"的随机 bug。去重键与从哪一侧发现它无关。
  var edges = H.zoneBorderEdges([
    { q: 0, r: 0, zoneId: "a" }, { q: 1, r: 0, zoneId: "b" }
  ]);
  assert.strictEqual(edges.length, 11);
  var divider = edges.filter(function (e) { return !e.outer; });
  assert.strictEqual(divider.length, 1, "区与区之间应恰好一条分界边");
  assert.deepStrictEqual(divider[0].zoneIds.slice().sort(), ["a", "b"]);
  // 反向：颠倒输入顺序，结论必须完全一样（去重不依赖遍历顺序）
  var flipped = H.zoneBorderEdges([
    { q: 1, r: 0, zoneId: "b" }, { q: 0, r: 0, zoneId: "a" }
  ]);
  assert.strictEqual(flipped.length, 11);
  assert.strictEqual(flipped.filter(function (e) { return !e.outer; }).length, 1);
});

test("界线：整棵真实树上零重复，且每条边都落在某格的六条边上", function () {
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  assert.ok(hive.borders.length > 0);
  var seen = {};
  hive.borders.forEach(function (e) {
    var k = [e.x1.toFixed(4) + "_" + e.y1.toFixed(4),
             e.x2.toFixed(4) + "_" + e.y2.toFixed(4)].sort().join("|");
    assert.ok(!seen[k], "同一条边被画了两次");
    seen[k] = 1;
    assert.ok(Math.abs(Math.hypot(e.x2 - e.x1, e.y2 - e.y1) - 1) < 1e-9,
      "边长不等于 S，说明顶点表和晶格对不上");
  });
  // 中心格被单独圈出来：它周围那一圈必须是分界边，不能被算进任何分区的外沿
  var touchingCenter = hive.borders.filter(function (e) {
    return !e.outer && e.zoneIds.indexOf("__center__") >= 0;
  });
  assert.ok(touchingCenter.length > 0, "中心格没有被界线圈出来");
});

test("界线法向：单位长度，且**从 zoneIds[0] 指向 zoneIds[1]**（决定双色带哪半边是谁）", function () {
  // 分界线要画成"两个分区各自的色盘色对半开"，就得知道哪半边是谁的。
  // 法向不能靠"线段方向转 90°"现推：转哪个 90° 取决于顶点顺序，顺序一变
  // 左右就整体对调 —— 而且是**看得见但查不出来**的那种错（颜色镜像，
  // 图还是"有分界线"的样子）。所以这里正反两个方向各钉一次。
  var ab = H.zoneBorderEdges([{ q: 0, r: 0, zoneId: "a" }, { q: 1, r: 0, zoneId: "b" }])
    .filter(function (e) { return !e.outer; })[0];
  assert.deepStrictEqual(ab.zoneIds, ["a", "b"]);
  assert.deepStrictEqual(ab.colors, [undefined, undefined]);  // 这两格没给 color
  assert.ok(Math.abs(Math.hypot(ab.nx, ab.ny) - 1) < 1e-9, "法向不是单位向量");
  // a 在 (0,0)、b 在 (1,0) 即正东 → 法向必须指向 +x
  assert.ok(Math.abs(ab.nx - 1) < 1e-9 && Math.abs(ab.ny) < 1e-9, "法向没指向 zoneIds[1]");

  // 反向：把两格的分区对调，zoneIds 与法向必须一起翻过来（不是只翻一个）
  var ba = H.zoneBorderEdges([{ q: 0, r: 0, zoneId: "b" }, { q: 1, r: 0, zoneId: "a" }])
    .filter(function (e) { return !e.outer; })[0];
  assert.deepStrictEqual(ba.zoneIds, ["b", "a"]);
  assert.ok(Math.abs(ba.nx - 1) < 1e-9, "对调之后法向也得跟着走");

  // 从对面那格发现同一条边时（遍历顺序反过来），法向必须反号
  var flipped = H.zoneBorderEdges([{ q: 1, r: 0, zoneId: "b" }, { q: 0, r: 0, zoneId: "a" }])
    .filter(function (e) { return !e.outer; })[0];
  assert.deepStrictEqual(flipped.zoneIds, ["b", "a"]);
  assert.ok(Math.abs(flipped.nx + 1) < 1e-9, "从 b 侧发现这条边时法向该指向 −x");
});

test("界线颜色：colors 与 zoneIds 一一对应（双色带靠这个配色）", function () {
  var e = H.zoneBorderEdges([
    { q: 0, r: 0, zoneId: "a", color: "var(--zone-1)" },
    { q: 1, r: 0, zoneId: "b", color: "var(--zone-7)" }
  ]).filter(function (x) { return !x.outer; })[0];
  assert.deepStrictEqual(e.zoneIds, ["a", "b"]);
  assert.deepStrictEqual(e.colors, ["var(--zone-1)", "var(--zone-7)"]);
  // 外沿边只有一侧，colors 只有一个 —— 渲染层拿 colors[1] 会 undefined，
  // 所以它必须回落到 colors[0]，这里把"只有一个"这件事本身钉住。
  var out = H.zoneBorderEdges([{ q: 0, r: 0, zoneId: "a", color: "var(--zone-1)" }])[0];
  assert.strictEqual(out.outer, true);
  assert.strictEqual(out.colors.length, 1);
});

test("虚拟格延伸：沿本该在那儿的六边形继续折出去，边长仍恒为 S", function () {
  // 看过径向直线那一版后的判词：「射线不够好看啊，做成沿着虚拟的六边型的格式」。
  // 病根是笔迹不统一 —— 内部分界是沿六边形边走的折线，外面突然接一条笔直射线。
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  assert.ok(hive.extensions.length > 0, "一条延伸边都没有");
  hive.extensions.forEach(function (e) {
    assert.ok(Math.abs(Math.hypot(e.x2 - e.x1, e.y2 - e.y1) - 1) < 1e-9,
      "延伸边长不等于 S，说明没落在晶格上（又画成直线了）");
    assert.strictEqual(e.outer, false, "最外那圈虚拟格的外沿不该画——那是边框不是分界");
    assert.strictEqual(e.zoneIds.length, 2);
    assert.ok(e.zoneIds[0] !== e.zoneIds[1]);
  });
});

test("虚拟格延伸：与内部分界**零重叠**（同一条边不许两处各画一遍）", function () {
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  function key(e) {
    return [e.x1.toFixed(4) + "_" + e.y1.toFixed(4),
            e.x2.toFixed(4) + "_" + e.y2.toFixed(4)].sort().join("|");
  }
  var inner = {};
  hive.borders.forEach(function (e) { inner[key(e)] = 1; });
  var dup = hive.extensions.filter(function (e) { return inner[key(e)]; });
  assert.strictEqual(dup.length, 0, dup.length + " 条延伸边和内部界线重了");
  // 延伸边自己也不许重
  var seen = {};
  hive.extensions.forEach(function (e) {
    assert.ok(!seen[key(e)], "延伸边自身重复");
    seen[key(e)] = 1;
  });
});

test("虚拟格延伸：真的伸到了蜂巢之外（否则「往外拓展」是句空话）", function () {
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  var cellR = 0;
  hive.cells.forEach(function (c) {
    var p = H.axialToPixel(c, 1);
    cellR = Math.max(cellR, Math.hypot(p.x, p.y));
  });
  var extR = 0;
  hive.extensions.forEach(function (e) {
    extR = Math.max(extR, Math.hypot(e.x1, e.y1), Math.hypot(e.x2, e.y2));
  });
  assert.ok(extR > cellR + 1,
    "延伸最远只到 " + extR.toFixed(2) + "S，蜂巢格已经到 " + cellR.toFixed(2) + "S");
});

test("虚拟格延伸：只有一个分区就没有「之间」，一条都不画", function () {
  var one = H.buildHoneycomb(makeTree([LIVE_ZONES[1]], { z_bbeb09: 3 }));
  assert.strictEqual(one.zones.length, 1);
  assert.strictEqual(one.extensions.length, 0);
  assert.strictEqual(H.buildHoneycomb({ zones: [], projects: [] }).extensions.length, 0);
});

test("界线 dir：能反查对面那格（虚拟格延伸靠它判两侧是真格还是虚拟格）", function () {
  var e = H.zoneBorderEdges([
    { q: 0, r: 0, zoneId: "a" }, { q: 1, r: 0, zoneId: "b" }
  ]).filter(function (x) { return !x.outer; })[0];
  var d = H.HEX_DIRS[e.dir];
  assert.deepStrictEqual([e.q + d[0], e.r + d[1]], [1, 0], "dir 指的不是对面那格");
});

test("界线：展开/悬停都不改数据层——borders 只跟树走（布局位移在 hex-app.js 里做）", function () {
  var a = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  var b = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  assert.deepStrictEqual(a.borders, b.borders);
});

// ══════════════════════════════════════════════════════════════
// 3. 分区配色
// ══════════════════════════════════════════════════════════════

test("配色：默认灰（153,153,153）→ 映射到 var(--zone-N)，**不产出任何色值**", function () {
  var r = H.resolveZoneColor({ color: "#999999" }, 0);
  assert.strictEqual(r.source, "auto");
  assert.strictEqual(r.color, "var(--zone-1)");
});

test("配色：三位写法的默认灰同样识别为默认（#999）", function () {
  assert.strictEqual(H.isDefaultZoneColor("#999"), true);
  assert.strictEqual(H.resolveZoneColor({ color: "#999" }, 3).source, "auto");
});

test("配色：显式设过色 → 原样用它，且不参与色阶轮转（园艺区 #4a90d9）", function () {
  var r = H.resolveZoneColor({ color: "#4a90d9" }, 1);
  assert.strictEqual(r.source, "zone");
  assert.strictEqual(r.color, "#4a90d9");
  assert.strictEqual(r.slot, null);
});

test("配色：没设色 / 空串 → 按默认处理", function () {
  assert.strictEqual(H.resolveZoneColor({}, 2).source, "auto");
  assert.strictEqual(H.resolveZoneColor({ color: "" }, 2).source, "auto");
});

test("配色红线：hex-data.js 源码里不许出现任何色值字面量（契约 A2）", function () {
  var src = require("fs").readFileSync(__dirname + "/hex-data.js", "utf8");
  var body = src.split("\n").filter(function (l) {
    return !/^\s*(\/\/|\*|\/\*)/.test(l);      // 注释行不算（里面写了违规样例做说明）
  }).join("\n");
  assert.ok(!/#[0-9a-fA-F]{3,8}\b/.test(body), "出现十六进制色值");
  assert.ok(!/\b(hsl|rgb|oklch|oklab|lab|lch)a?\s*\(/.test(body), "出现运行时生成的色值函数");
});

test("配色：色阶序号按分区序号轮转，12 个之后回卷复用", function () {
  for (var i = 0; i < H.ZONE_SLOT_COUNT; i++) {
    assert.strictEqual(H.zoneColorVar(i), "var(--zone-" + (i + 1) + ")");
  }
  assert.strictEqual(H.zoneColorVar(H.ZONE_SLOT_COUNT), "var(--zone-1)");
});

test("配色：整棵树上——园艺区用自己的色，其余八个灰分区各自拿到不同色阶", function () {
  var hive = H.buildHoneycomb(makeTree(LIVE_ZONES, LIVE_COUNTS));
  var byId = {};
  hive.zones.forEach(function (z) { byId[z.id] = z; });
  assert.strictEqual(byId.z_bbeb09.colorSource, "zone");
  assert.strictEqual(byId.z_bbeb09.color, "#4a90d9");
  var autos = hive.zones.filter(function (z) { return z.colorSource === "auto"; });
  assert.strictEqual(autos.length, 8);
  assert.strictEqual(new Set(autos.map(function (z) { return z.color; })).size, 8);
});

test("配色：hex.css 的临时兜底块确实定义了 12 格色阶（tokens 落地后整块删）", function () {
  var css = require("fs").readFileSync(__dirname + "/hex.css", "utf8");
  var block = css.slice(css.indexOf("zone-ramp:begin"), css.indexOf("zone-ramp:end"));
  for (var i = 1; i <= H.ZONE_SLOT_COUNT; i++) {
    assert.ok(block.indexOf("--zone-" + i + ":") !== -1, "缺 --zone-" + i);
  }
  // 色环均布：相邻两格色相差 360/12 = 30°
  var hues = (block.match(/oklch\([\d.]+ [\d.]+ +([\d.]+)\)/g) || []).map(function (m) {
    return Number(/ ([\d.]+)\)$/.exec(m)[1]);
  }).slice(0, H.ZONE_SLOT_COUNT);
  for (i = 0; i + 1 < hues.length; i++) {
    var d = Math.abs(hues[i + 1] - hues[i]); d = Math.min(d, 360 - d);
    assert.ok(Math.abs(d - 30) < 0.01, "色环不均布：" + hues[i] + " → " + hues[i + 1]);
  }
});

// ══════════════════════════════════════════════════════════════
// 4. hover 待办取数
// ══════════════════════════════════════════════════════════════

var NEXT_ACTIONS = {
  today: "2026-09-07",
  zones: [
    { id: "z_870928", name: "木工区", actionable: [
      { id: "t_a", name: "第一条", projectId: "p_x", projectName: "X", overdue: false, dueToday: false, blockedBy: [] },
      { id: "t_b", name: "第二条", projectId: "p_x", projectName: "X", overdue: true, dueToday: false, blockedBy: [] }
    ], waiting: [
      { id: "t_c", name: "等着的", projectId: "p_x", projectName: "X", blockedBy: ["t_a"] }
    ] },
    { id: "z_019974", name: "手工区", actionable: [], waiting: [
      { id: "t_d", name: "只有等待", projectId: "p_y", projectName: "Y", blockedBy: ["t_a"] }
    ] }
  ]
};

test("hover 待办：有待办取第一条（后端已排好序，前端不重排）", function () {
  var map = H.nextActionsByProject(NEXT_ACTIONS);
  assert.strictEqual(H.hoverTodoText(map.p_x), "第一条");
});

test("hover 待办：只有 waiting 时取它并标「等待中」，不是空白", function () {
  var map = H.nextActionsByProject(NEXT_ACTIONS);
  assert.strictEqual(H.hoverTodoText(map.p_y), "等待中 · 只有等待");
});

test("hover 待办：一条都没有 → 明确文案「无待办」，不是空字符串", function () {
  var map = H.nextActionsByProject(NEXT_ACTIONS);
  assert.strictEqual(H.hoverTodoText(map.p_never), H.NO_TODO_TEXT);
  assert.strictEqual(H.hoverTodoText(undefined), "无待办");
  assert.strictEqual(H.hoverTodoText([]), "无待办");
});

test("hover 待办：actionable 排在 waiting 前面；topTodos 取三条", function () {
  var map = H.nextActionsByProject(NEXT_ACTIONS);
  assert.deepStrictEqual(map.p_x.map(function (t) { return t.state; }), ["actionable", "actionable", "waiting"]);
  assert.strictEqual(H.topTodos(map.p_x).length, 3);
  assert.strictEqual(H.topTodos(map.p_x, 2).length, 2);
});

test("hover 待办：next-actions 为空/缺字段不崩", function () {
  assert.deepStrictEqual(H.nextActionsByProject(null), {});
  assert.deepStrictEqual(H.nextActionsByProject({ zones: [{ id: "z" }] }), {});
});

// ══════════════════════════════════════════════════════════════
// 5. 中心格圆环（timer-ring/v1 规范的可机械核验部分）
// ══════════════════════════════════════════════════════════════

test("圆环：走秒用单调差值 Date.now()-Date.parse(sessionStartAt)", function () {
  var start = "2026-09-07T12:00:00+00:00";
  assert.strictEqual(Ring.elapsedSeconds(start, Date.parse(start) + 90 * 1000), 90);
  assert.strictEqual(Ring.elapsedSeconds(null, Date.now()), null);   // 空闲态不是 0
});

test("圆环：进度百分比 60 分钟一圈，超过一小时接着绕、不封顶", function () {
  assert.strictEqual(Ring.progressPercent(0), 0);
  assert.strictEqual(Ring.progressPercent(1800), 50);
  assert.strictEqual(Ring.progressPercent(3600), 0);
  assert.strictEqual(Ring.progressPercent(3600 + 900), 25);
});

test("圆环：几何与 pathLength 约定符合 timer-ring/v1（92/86/78，不算 2πr）", function () {
  assert.strictEqual(Ring.R_TICKS, 92);
  assert.strictEqual(Ring.R_PROGRESS, 86);
  assert.strictEqual(Ring.R_CONTRIB, 78);
  var svg = Ring.ringMarkup();
  assert.ok(svg.indexOf('viewBox="0 0 200 200"') !== -1);
  // 2026-09-12 起中心格只剩 轨道 + 分针弧 两圈（r=78 贡献环让位给秒针，见 hex-ring.js 文件头）
  assert.strictEqual(svg.split('pathLength="100"').length - 1, 2);
  assert.ok(svg.indexOf('class="tring-sec"') !== -1, "秒针要在");
  assert.ok(svg.indexOf('class="tring-num"') !== -1, "圆心读数要在");
  assert.ok(!/2\s*\*\s*Math\.PI\s*\*\s*R_/.test(require("fs").readFileSync(__dirname + "/hex-ring.js", "utf8")),
    "hex-ring.js 里出现了周长换算——规范硬要求用 pathLength=100");
});

test("圆心读数：少于 1 小时 分:秒，多于 1 小时 时:分（产品决定 2026-09-12）", function () {
  assert.deepStrictEqual(Ring.formatClock(0), { text: "00:00", unit: "分 · 秒" });
  assert.deepStrictEqual(Ring.formatClock(3599), { text: "59:59", unit: "分 · 秒" });
  assert.deepStrictEqual(Ring.formatClock(3600), { text: "1:00", unit: "时 · 分" });
  assert.deepStrictEqual(Ring.formatClock(3600 * 2 + 65), { text: "2:01", unit: "时 · 分" });
  assert.strictEqual(Ring.formatClock(null).text, "--:--");
});

test("深浅：外浅内深，等比例，项目再多也落在固定区间里（产品决定 2026-09-12）", function () {
  assert.strictEqual(H.zoneMix(0), H.DEPTH_MIX.max);
  assert.strictEqual(H.zoneMix(1), H.DEPTH_MIX.min);
  assert.strictEqual(H.zoneMix(0.5), (H.DEPTH_MIX.max + H.DEPTH_MIX.min) / 2);
  assert.strictEqual(H.zoneMix(7), H.DEPTH_MIX.min, "越界也不许比最浅还浅");
  assert.strictEqual(H.DEPTH_MIX.center - H.DEPTH_MIX.max, (H.DEPTH_MIX.max - H.DEPTH_MIX.min) / 3, "中心格 = 最深档再加一档（一档 = 全程的 1/3）");
  // 一个分区 40 个项目：外圈照样停在 min，不会无限变浅
  var zones = [{ id: "z1", name: "一", order: 0 }];
  var projects = [];
  for (var i = 0; i < 40; i++) projects.push({ id: "p" + i, name: "项目" + i, zoneId: "z1", order: i, tasks: [] });
  var hive = H.buildHoneycomb({ zones: zones, projects: projects });
  var mixes = hive.cells.map(function (c) { return c.mix; });
  assert.strictEqual(Math.max.apply(null, mixes), H.DEPTH_MIX.max);
  assert.strictEqual(Math.min.apply(null, mixes), H.DEPTH_MIX.min);
  hive.cells.forEach(function (a) {
    hive.cells.forEach(function (b) {
      if (a.ring < b.ring) assert.ok(a.mix > b.mix, "内圈必须比外圈深");
    });
  });
});

test("秒针角度是累计的，59→60 秒不倒转", function () {
  assert.strictEqual(Ring.secondAngle(59), 354);
  assert.strictEqual(Ring.secondAngle(60), 360);     // 不是 0 —— 取模会让过渡倒着转一圈
  assert.strictEqual(Ring.secondAngle(null), 0);
});

test("圆环：formatElapsed 一小时以内 mm:ss，超过带小时", function () {
  assert.strictEqual(Ring.formatElapsed(65), "01:05");
  assert.strictEqual(Ring.formatElapsed(3725), "1:02:05");
  assert.strictEqual(Ring.formatElapsed(null), "--:--");
});

test("圆环：贡献环拿不到 shareOfProject 就不画（不硬凑一段 0 分）", function () {
  assert.strictEqual(Ring.contributionSegments({ task: {} }), null);
  assert.deepStrictEqual(Ring.contributionSegments({ task: { shareOfProject: 32.1 } }),
    { current: 32.1, others: 67.9 });
});

// ── 长按自动建任务的命名（产品决定：「标题就弄个日期+时间任务」）──────────
test("长按建任务：名字是「年-月-日 时:分」，个位数补零", function () {
  assert.strictEqual(H.stampTaskName(new Date(2026, 8, 8, 0, 43)), "2026-09-08 00:43");
  assert.strictEqual(H.stampTaskName(new Date(2026, 11, 25, 9, 5)), "2026-12-25 09:05");
});

// 反向验证：把年份去掉这条就挂——证明"带年份"是被钉住的，不是顺手写的
test("长按建任务：带年份，跨年同月同日同时刻不会重名", function () {
  var a = H.stampTaskName(new Date(2025, 8, 8, 14, 30));
  var b = H.stampTaskName(new Date(2026, 8, 8, 14, 30));
  assert.notStrictEqual(a, b);
  assert.ok(/^\d{4}-/.test(a), "开头必须是四位年份，实得 " + a);
});

test("长按建任务：不传参用当前时间，格式恒为 YYYY-MM-DD HH:mm", function () {
  var s = H.stampTaskName();
  assert.strictEqual(s.length, 16);
  assert.ok(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(s), "实得 " + s);
});

// ── 最近完成热度 + 按热度靠中心（产品决定 2026-09-08）──────────────
function heatTree() {
  return { zones: [{ id: "zA", name: "A", order: 0 }, { id: "zB", name: "B", order: 1 }],
    projects: [
      { id: "p1", zoneId: "zA", name: "冷", order: 0, tasks: [{ id: "t1", name: "x" }] },
      { id: "p2", zoneId: "zA", name: "热", order: 1, tasks: [{ id: "t2", name: "y" }] },
      { id: "p3", zoneId: "zB", name: "B1", order: 0, tasks: [{ id: "t3", name: "z" }] }
    ] };
}
function doneAt(id, iso) {
  return { seq: 1, at: iso, op: "update", objectType: "tasks", objectId: id,
           outcome: "applied", changes: { done: true } };
}
var NOW = Date.parse("2026-09-08T00:00:00Z");

test("热度：按半衰期加权，不是数总次数——7 天前的一条只算半条", function () {
  var t = heatTree();
  var h = H.completionHeat([doneAt("t2", "2026-09-08T00:00:00Z"),
                            doneAt("t1", "2026-09-01T00:00:00Z")], t, { now: NOW });
  assert.strictEqual(+h.p2.toFixed(4), 1);        // 刚完成
  assert.strictEqual(+h.p1.toFixed(4), 0.5);      // 整整一个半衰期前
});

// 反向验证：把半衰期设成无穷大就退化成"数总次数"，两条一样重，
// 于是"最近"这件事消失——证明衰减确实在起作用，不是摆设。
test("热度：半衰期是真在衰减（半衰期拉到极大 → 退化成计次）", function () {
  var t = heatTree();
  var h = H.completionHeat([doneAt("t2", "2026-09-08T00:00:00Z"),
                            doneAt("t1", "2026-01-01T00:00:00Z")], t, { now: NOW, halfLifeDays: 1e9 });
  assert.strictEqual(+h.p1.toFixed(3), +h.p2.toFixed(3));
});

test("热度：已删除任务（树上查不到）不计，未来时间不给加成", function () {
  var t = heatTree();
  var h = H.completionHeat([doneAt("t_gone", "2026-09-08T00:00:00Z"),
                            doneAt("t1", "2026-12-01T00:00:00Z")], t, { now: NOW });
  assert.strictEqual(h.t_gone, undefined);
  assert.strictEqual(+h.p1.toFixed(4), 1);        // 未来的按 0 天算，不超过 1
});

test("热度：分区内热的项目拿到更靠内的圈", function () {
  var t = heatTree();
  var heat = H.completionHeat([doneAt("t2", "2026-09-08T00:00:00Z")], t, { now: NOW });
  var hive = H.buildHoneycomb(t, { heat: heat });
  var byName = {};
  hive.cells.forEach(function (c) { if (c.project) byName[c.project.name] = c; });
  assert.ok(byName["热"].ring <= byName["冷"].ring,
    "热的该不比冷的远（热 " + byName["热"].ring + " / 冷 " + byName["冷"].ring + "）");
});

test("热度：零热度时结果与不传 heat 完全一致（不改既有行为）", function () {
  var t = heatTree();
  var a = H.buildHoneycomb(t);
  var b = H.buildHoneycomb(t, { heat: {} });
  assert.deepStrictEqual(a.cells.map(function (c) { return c.q + "," + c.r; }),
                         b.cells.map(function (c) { return c.q + "," + c.r; }));
});

// ── 拖分区名 = 换绕圈角度（产品决定 2026-09-08：「不是机械的移动六边形」）──
test("排序：order 改变扇区角度的先后", function () {
  var t = heatTree();
  var a = H.buildHoneycomb(t);
  var b = H.buildHoneycomb(t, { order: ["zB", "zA"] });
  assert.ok(a.zones[0].id === "zA" && b.zones[0].id === "zB", "第一个扇区应换人");
  assert.ok(b.zones[0].centerAngle < b.zones[1].centerAngle, "角度仍然递增");
});

// 这一条是本轮最容易写错的地方：拖一次分区如果把配色也换了，
// 每个人辛苦认熟的"我那块地是什么颜色"当场作废。
test("排序：换了顺序但**配色不许跟着换**（色阶按分区原始序号，不按显示顺序）", function () {
  var t = heatTree();
  var a = H.buildHoneycomb(t);
  var b = H.buildHoneycomb(t, { order: ["zB", "zA"] });
  var slotA = {}, slotB = {};
  a.zones.forEach(function (z) { slotA[z.id] = z.colorSlot; });
  b.zones.forEach(function (z) { slotB[z.id] = z.colorSlot; });
  assert.deepStrictEqual(slotA, slotB);
});

test("排序：order 里没提到的分区（比如刚新建的）排在后面，不丢", function () {
  var t = heatTree();
  var b = H.buildHoneycomb(t, { order: ["zB"] });
  assert.deepStrictEqual(b.zones.map(function (z) { return z.id; }), ["zB", "zA"]);
});

console.log(passed + " passed");
