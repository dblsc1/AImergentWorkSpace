// hive · rails.js 单元测试（无框架，Node 内置 assert）
// 跑法：node rails.test.js（run_tests.sh 会一并跑）
"use strict";

var assert = require("assert");
var R = require("./rails.js");

var passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log("  ok - " + name);
}

test("computeProjectRail：无 plan → hasPlan:false，事实天数不受窗口限制", function () {
  var rail = R.computeProjectRail({ plan: null, actual: [{ date: "2020-01-01", seconds: 60 }] }, "2026-08-07");
  assert.strictEqual(rail.hasPlan, false);
  assert.strictEqual(rail.factDaysTotal, 1);
});

test("computeProjectRail：plan 为 undefined（甘特端点 project.plan 缺省）同样按无排期处理", function () {
  var rail = R.computeProjectRail({ actual: [] }, "2026-08-07");
  assert.strictEqual(rail.hasPlan, false);
  assert.strictEqual(rail.factDaysTotal, 0);
});

test("computeProjectRail：设计稿 §4 示例——计划 07-30→08-06（8 天），今天 08-07，已结束，事实 3/8", function () {
  var actual = [
    { date: "2026-08-01", seconds: 3600 },
    { date: "2026-08-02", seconds: 1200 },
    { date: "2026-08-03", seconds: 600 },
    { date: "2026-07-15", seconds: 999 } // 窗口外，不计入 factDaysInWindow
  ];
  var rail = R.computeProjectRail({ plan: { start: "2026-07-30", end: "2026-08-06" }, actual: actual }, "2026-08-07");
  assert.strictEqual(rail.hasPlan, true);
  assert.strictEqual(rail.totalDays, 8);
  assert.strictEqual(rail.ended, true);
  assert.strictEqual(rail.planRatio, 100);
  assert.strictEqual(rail.factDaysInWindow, 3);
  assert.strictEqual(rail.factRatio, Math.round((3 / 8) * 100));
});

test("computeProjectRail：今天在计划期中间，未结束，planRatio 按已走天数算", function () {
  // 2026-08-01 → 2026-08-10（10 天），今天 2026-08-03 是第 3 天
  var rail = R.computeProjectRail({ plan: { start: "2026-08-01", end: "2026-08-10" }, actual: [] }, "2026-08-03");
  assert.strictEqual(rail.totalDays, 10);
  assert.strictEqual(rail.elapsedDays, 3);
  assert.strictEqual(rail.planRatio, 30);
  assert.strictEqual(rail.ended, false);
});

test("computeProjectRail：今天早于计划开始（还没到），elapsedDays=0，不是负数", function () {
  var rail = R.computeProjectRail({ plan: { start: "2026-09-01", end: "2026-09-10" }, actual: [] }, "2026-08-07");
  assert.strictEqual(rail.elapsedDays, 0);
  assert.strictEqual(rail.planRatio, 0);
  assert.strictEqual(rail.ended, false);
});

test("computeProjectRail：单日计划（start===end），totalDays 至少为 1，不除零", function () {
  var rail = R.computeProjectRail({ plan: { start: "2026-08-07", end: "2026-08-07" }, actual: [{ date: "2026-08-07", seconds: 60 }] }, "2026-08-07");
  assert.strictEqual(rail.totalDays, 1);
  assert.strictEqual(rail.planRatio, 100);
  assert.strictEqual(rail.factDaysInWindow, 1);
});

test("formatPlanCaption：已结束 / 进行中 / 未开始 三态", function () {
  assert.strictEqual(R.formatPlanCaption({ totalDays: 8, ended: true, elapsedDays: 8 }), "计划 8 天 · 已走完");
  assert.strictEqual(R.formatPlanCaption({ totalDays: 8, ended: false, elapsedDays: 3 }), "计划 8 天 · 进行中");
  assert.strictEqual(R.formatPlanCaption({ totalDays: 8, ended: false, elapsedDays: 0 }), "计划 8 天 · 未开始");
});

test("formatFactCaption：与设计稿逐字节一致的 M / N 天格式", function () {
  assert.strictEqual(R.formatFactCaption({ factDaysInWindow: 3, totalDays: 8 }), "事实 3 / 8 天");
});

test("formatMonthDay：YYYY-MM-DD → MM-DD", function () {
  assert.strictEqual(R.formatMonthDay("2026-07-30"), "07-30");
});

test("indexGanttByProjectId：按 id 建索引，空数组不崩", function () {
  var map = R.indexGanttByProjectId([{ id: "p_1", plan: null, actual: [] }]);
  assert.ok(map.p_1);
  assert.deepStrictEqual(R.indexGanttByProjectId(undefined), {});
});

console.log(passed + " passed");
