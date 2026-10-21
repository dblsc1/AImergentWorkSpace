// hive · hex-pending.js 纯逻辑单测：提议 + 报告 → 卡片（无框架，Node 内置 assert）
"use strict";
var assert = require("assert");
var P = require("./hex-pending.js");

var tree = { zones: [{ id: "z1", name: "学习" }],
  projects: [{ id: "p1", zoneId: "z1", name: "数学", unclassifiedTaskId: "t_unc_p1", tasks: [{ id: "t1", name: "刷题" }] }] };
var props = {
  projects: [{ id: "np_1", name: "新书", reason: "读书", zoneId: "z1", zoneName: "学习", tasks: [{ id: "nt_9", name: "第一章" }] }],
  tasks: [{ id: "nt_2", name: "复习", reason: "r", projectId: "p1", projectName: "数学" }]
};
var reports = [{ id: "r1", author: "hermes", summary: "整理", items: [
  { id: "i0", kind: "assign", status: "pending", taskId: "t1", suggestions: [{ durationSeconds: 600 }, { durationSeconds: 600 }] },
  { id: "i1", kind: "dismiss", status: "pending", suggestions: [{ durationSeconds: 30 }] },
  { id: "i2", kind: "newTask", status: "pending", newTask: { proposalId: "nt_2", projectId: "p1", name: "复习" } },   // 与 nt_2 重复
  { id: "i3", kind: "newTask", status: "pending", newTask: { proposalId: "nt_3", projectId: "p1", name: "错题本" } },
  { id: "i4", kind: "assign", status: "approved", taskId: "t1" },
  { id: "i5", kind: "assign", status: "failed", taskId: "t1", suggestions: [] }] }];

var out = P.build(props, reports, tree);
assert.deepStrictEqual(out.cards.map(function (c) { return c.verb; }), ["project", "task", "move", "noise", "task", "move"]);
var proj = out.cards[0];
assert.deepStrictEqual(proj.tasks, ["第一章"]);
assert.strictEqual(proj.path, "proposals/np_1");
var mv = out.cards[2];
assert.strictEqual(mv.projectId, "p1"); assert.strictEqual(mv.taskId, "t1"); assert.strictEqual(mv.name, "刷题");
assert.strictEqual(mv.targetLabel, "→ 数学"); assert.strictEqual(mv.meta, "2 个窗口 · 20 分");
assert.strictEqual(mv.path, "reports/r1/items/i0");
assert.strictEqual(out.cards[3].meta, "1 个窗口 · 1 分");
assert.strictEqual(out.cards[4].name, "错题本"); assert.strictEqual(out.cards[4].meta, "");
assert.strictEqual(out.cards[5].meta, "上次失败");
assert.deepStrictEqual(out.reports, [{ id: "r1", label: "hermes：整理", n: 5, path: "reports/r1" }]);
assert.deepStrictEqual(P.build(null, [], null), { cards: [], reports: [] });
console.log("hex-pending.test.js ok");
