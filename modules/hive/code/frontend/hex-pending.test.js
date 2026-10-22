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
var ed = P.build({ changes: [
  { id: "c1", op: "rename", target: "project", targetId: "p1", targetName: "数学", name: "代数" },
  { id: "c2", op: "move", target: "task", targetId: "t1", projectId: "p1", targetName: "刷题", toId: "p2", toName: "英语", reason: "r" },
  { id: "c3", op: "delete", target: "task", targetId: "t9", projectId: null, targetName: null }],
  ignores: [{ id: "g1", app: "Slack", titleContains: "闲聊" }, { id: "g2", app: "Steam", hasTitleFilter: true }] }, [], tree).cards;
assert.deepStrictEqual(ed.map(function (c) { return c.verb; }), ["rename", "cmove", "delete", "ignore", "ignore"]);
assert.strictEqual(ed[0].name, "数学 改成 代数"); assert.strictEqual(ed[0].projectId, "p1"); assert.strictEqual(ed[0].hlProject, true);
assert.strictEqual(ed[1].name, "刷题 移到 英语"); assert.strictEqual(ed[1].taskId, "t1"); assert.strictEqual(ed[1].destProjectId, "p2");
assert.strictEqual(ed[2].name, "（已不存在）"); assert.strictEqual(ed[2].path, "proposals/c3");
assert.strictEqual(ed[3].targetLabel, "标题含 “闲聊”"); assert.strictEqual(ed[4].targetLabel, "带标题条件");
assert.deepStrictEqual(P.build({ changes: [{ id: "cx", op: "teleport", target: "task" }, { id: "cy", op: "rename", target: "task", targetId: "t1", projectId: "p1", targetName: "a", name: null }],
  ignores: [{ id: "gx" }] }, [], tree).cards.map(function (c) { return c.name; }), ["a 改成 ？", "？"]);
console.log("hex-pending.test.js ok");
