// hive · hex-audit.js 单元测试（无框架，Node 内置 assert）
// 跑法：node hex-audit.test.js（run_tests.sh 会一并跑）
//
// 2026-09-14 从 hex-data.test.js 拆出：那个文件撞了 C1 的 1000 行硬线（1002），
// 和源码同一条缝 —— 审计流那一半不认识几何，几何那一半也不认识它。
// **直接 require("./hex-audit.js")**，不绕 hex-data.js 的转发：拆出来的模块
// 自己就该有自己的测试，绕转发等于只测了转发。
"use strict";

var assert = require("assert");
var A = require("./hex-audit.js");
var H = require("./hex-data.js");      // 只用来核「转发的确指向同一份实现」

var passed = 0;
function test(name, fn) {
  fn();
  passed += 1;
  console.log("  ok - " + name);
}

var REAL_AUDIT = [
  { seq: 66, at: "2026-09-07T12:08:34.274234+00:00", objectType: "tasks", objectId: "t_1dea8d", changes: { done: false } },
  { seq: 65, at: "2026-09-07T12:08:32.781414+00:00", objectType: "tasks", objectId: "t_1dea8d", changes: { done: true } },
  { seq: 64, at: "2026-09-07T12:08:30.334514+00:00", objectType: "tasks", objectId: "t_89647b", changes: { done: true } },
  { seq: 40, at: "2026-08-18T15:15:02.559887+00:00", objectType: "tasks", objectId: "t_3f5880", changes: { done: true } },
  { seq: 33, at: "2026-08-18T10:22:55.109294+00:00", objectType: "tasks", objectId: "t_90266e", changes: { done: true } },
  { seq: 20, at: "2026-08-15T09:00:00.000000+00:00", objectType: "tasks", objectId: "t_1dea8d", changes: { name: "改名，不是 done 动作" } },
  { seq: 10, at: "2026-08-10T09:00:00.000000+00:00", objectType: "projects", objectId: "p_zzz", changes: { done: true } }
];

var REAL_TREE = {
  zones: [
    { id: "z_870928", name: "木工区", color: "#999999", order: 1 },
    { id: "z_019974", name: "手工区", color: "#999999", order: 5 }
  ],
  projects: [
    { id: "p_1661d0", zoneId: "z_870928", name: "木架改造", tasks: [
      { id: "t_1dea8d", name: "台锯改造-底座重做", done: false },
      { id: "t_89647b", name: "木架打磨", done: true }
    ] },
    { id: "p_craft", zoneId: "z_019974", name: "皮具工坊", tasks: [
      { id: "t_3f5880", name: "定尺寸+试装方法", done: true }
    ] },
    { id: "p_ai", zoneId: "z_019974", name: "藤编工坊", tasks: [
      { id: "t_90266e", name: "第一件样品+配色", done: true }
    ] }
  ]
};

test("最近完成去重：同一任务 done:true 之后又 done:false → 不算完成（本轮最贵的一条）", function () {
  var rows = A.recentCompletions(REAL_AUDIT, REAL_TREE, { offsetMinutes: 0 });
  var ids = rows.map(function (r) { return r.taskId; });
  assert.ok(ids.indexOf("t_1dea8d") === -1,
    "t_1dea8d 最后一次动作是 done:false，绝不能出现在最近完成里，实际：" + JSON.stringify(ids));
});

test("最近完成去重：同一任务只出现一次（不因多条流水重复计数）", function () {
  var rows = A.recentCompletions(REAL_AUDIT, REAL_TREE, { offsetMinutes: 0 });
  var ids = rows.map(function (r) { return r.taskId; });
  assert.strictEqual(new Set(ids).size, ids.length);
});

test("最近完成去重：反向——把那条取消动作抽掉，t_1dea8d 就必须出现（红绿反向验证）", function () {
  var without = REAL_AUDIT.filter(function (it) { return it.seq !== 66; });
  var rows = A.recentCompletions(without, REAL_TREE, { offsetMinutes: 0 });
  assert.strictEqual(rows[0].taskId, "t_1dea8d");
  assert.strictEqual(rows[0].path, "木工区/木架改造/台锯改造-底座重做");
});

test("最近完成：结果按时间倒序，路径是 分区/项目/任务", function () {
  var rows = A.recentCompletions(REAL_AUDIT, REAL_TREE, { offsetMinutes: 0 });
  assert.deepStrictEqual(rows.map(function (r) { return r.stamp + " " + r.path; }), [
    "2026-09-07 12:08 木工区/木架改造/木架打磨",
    "2026-08-18 15:15 手工区/皮具工坊/定尺寸+试装方法",
    "2026-08-18 10:22 手工区/藤编工坊/第一件样品+配色"
  ]);
});

test("最近完成：非 tasks 的 done 变更（projects）不进结果", function () {
  var rows = A.recentCompletions(REAL_AUDIT, REAL_TREE, { offsetMinutes: 0 });
  assert.ok(rows.every(function (r) { return r.taskId !== "p_zzz"; }));
});

test("最近完成：输入顺序被打乱也得到同一结果（去重不靠调用方先排好序）", function () {
  var shuffled = REAL_AUDIT.slice().reverse();
  var a = A.recentCompletions(REAL_AUDIT, REAL_TREE, { offsetMinutes: 0 }).map(function (r) { return r.taskId; });
  var b = A.recentCompletions(shuffled, REAL_TREE, { offsetMinutes: 0 }).map(function (r) { return r.taskId; });
  assert.deepStrictEqual(a, b);
});

test("最近完成：树里查不到的任务不崩，标 missing 并显示 id", function () {
  var rows = A.recentCompletions(
    [{ seq: 1, at: "2026-01-01T00:00:00+00:00", objectType: "tasks", objectId: "t_gone", changes: { done: true } }],
    REAL_TREE, { offsetMinutes: 0 });
  assert.strictEqual(rows[0].missing, true);
  assert.strictEqual(rows[0].path, "已删除的任务 t_gone");
});

test("最近完成：缺 seq 时退回时间戳排序，去重结论不变", function () {
  var noSeq = REAL_AUDIT.map(function (it) { var c = Object.assign({}, it); delete c.seq; return c; });
  var ids = A.recentCompletions(noSeq, REAL_TREE, { offsetMinutes: 0 }).map(function (r) { return r.taskId; });
  assert.ok(ids.indexOf("t_1dea8d") === -1);
  assert.strictEqual(ids[0], "t_89647b");
});

test("latestCompletionForProject：按项目取最近一条；没有就 null", function () {
  var rows = A.recentCompletions(REAL_AUDIT, REAL_TREE, { offsetMinutes: 0 });
  assert.strictEqual(A.latestCompletionForProject(rows, "p_1661d0").taskName, "木架打磨");
  assert.strictEqual(A.latestCompletionForProject(rows, "p_none"), null);
});

test("formatStamp：偏移显式传入，不依赖跑测机器时区", function () {
  assert.strictEqual(A.formatStamp("2026-09-07T12:08:30+00:00", 0), "2026-09-07 12:08");
  assert.strictEqual(A.formatStamp("2026-09-07T12:08:30+00:00", 480), "2026-09-07 20:08");
  assert.strictEqual(A.formatStamp("坏值", 0), "");
});

// ══════════════════════════════════════════════════════════════
// 2. 蜂巢坐标分配
// ══════════════════════════════════════════════════════════════

test("转发没断：hex-data.js 上的这批名字必须就是 hex-audit.js 的同一个函数", function () {
  ["isDoneChange", "auditOrder", "lastDoneActions", "indexTasks", "formatStamp",
   "recentCompletions", "latestCompletionForProject", "nextActionsByProject",
   "hoverTodoText", "topTodos", "stampTaskName", "completionHeat"].forEach(function (k) {
    assert.strictEqual(H[k], A[k], "hex-data.js 的 " + k + " 不是 hex-audit.js 那一个");
  });
  assert.strictEqual(H.NO_TODO_TEXT, A.NO_TODO_TEXT);
  assert.strictEqual(H.HEAT_HALFLIFE_DAYS, A.HEAT_HALFLIFE_DAYS);
});

console.log(passed + " passed");
