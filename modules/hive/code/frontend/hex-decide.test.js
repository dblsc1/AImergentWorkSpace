// hive · hex-decide.js 纯逻辑单测：提议 + 报告 → 卡片（合并 / 去向文案）、标题标准化、撤销计时（无框架，Node 内置 assert）
"use strict";
var assert = require("assert");
var P = require("./hex-decide.js");

var tree = { zones: [{ id: "z1", name: "学习" }],
  projects: [{ id: "p1", zoneId: "z1", name: "数学", unclassifiedTaskId: "t_unc_p1", tasks: [{ id: "t1", name: "刷题" }] }] };
var props = {
  projects: [{ id: "np_1", name: "新书", reason: "读书", zoneId: "z1", zoneName: "学习", tasks: [{ id: "nt_9", name: "第一章" }] }],
  tasks: [{ id: "nt_2", name: "复习", reason: "r", projectId: "p1", projectName: "数学" }]
};
function sg(title, sec, app) { return { app: app || "code", title: title, durationSeconds: sec }; }
var reports = [{ id: "r1", author: "hermes", summary: "整理", items: [
  { id: "i0", kind: "assign", status: "pending", taskId: "t1", suggestions: [sg("✳ 写题", 600), sg("写题", 600)] },
  { id: "i1", kind: "assign", status: "pending", taskId: "t1", suggestions: [sg("(3) 写题", 300)] },     // 与 i0 同窗口同去向 → 合并
  { id: "i2", kind: "assign", status: "pending", taskId: null, projectId: "p1", suggestions: [sg("写题", 60)] },   // 去向不同 → 不合并
  { id: "i3", kind: "dismiss", status: "pending", suggestions: [sg("闲聊", 30)] },
  { id: "i4", kind: "newTask", status: "pending", newTask: { proposalId: "nt_2", projectId: "p1", name: "复习" } },   // 与 nt_2 重复
  { id: "i5", kind: "newTask", status: "pending", newTask: { proposalId: "nt_3", projectId: "p1", name: "错题本" }, suggestions: [sg("错题", 120)] },
  { id: "i6", kind: "assign", status: "approved", taskId: "t1" },
  { id: "i7", kind: "assign", status: "failed", taskId: "t1", suggestions: [] }] }];

// normTitle 与 assistant/suggestions.js 同一个算法
assert.strictEqual(P.normTitle("  ✳  写  题 "), "写 题");
assert.strictEqual(P.normTitle("(3) [2] * 写题"), "写题");
assert.strictEqual(P.normTitle("✳"), "✳");
assert.strictEqual(P.normTitle(null), "");

var out = P.build(props, reports, tree);
assert.deepStrictEqual(out.cards.map(function (c) { return c.verb; }), ["project", "task", "move", "move", "noise", "task", "move"]);
assert.strictEqual(out.cards[0].to, "↓ 新建项目（分区：学习） · 1 个任务");
assert.strictEqual(out.cards[1].to, "↓ 数学 - ＋ 复习（新任务）");
var mv = out.cards[2];
assert.strictEqual(mv.name, "写题"); assert.strictEqual(mv.to, "↓ 数学 - 刷题");
assert.deepStrictEqual(mv.items.map(function (i) { return i.path; }), ["reports/r1/items/i0", "reports/r1/items/i1"], "同名窗口合并");
assert.strictEqual(mv.n, 3); assert.strictEqual(mv.min, 25);
assert.strictEqual(out.cards[3].to, "↓ 数学 - 未分类"); assert.strictEqual(out.cards[3].items.length, 1);
assert.strictEqual(out.cards[4].tone, "ignore"); assert.strictEqual(out.cards[4].to, "↓ 当噪声，不记录");
assert.strictEqual(out.cards[5].name, "错题"); assert.strictEqual(out.cards[5].obj, "错题本");
assert.strictEqual(out.cards[5].to, "↓ 数学 - ＋ 错题本（新任务）");
assert.strictEqual(out.cards[6].items[0].path, "reports/r1/items/i7", "failed 的无明细条不并");
assert.strictEqual(out.cards[0].items[0].newItem, true); assert.strictEqual(mv.items[0].newItem, false);
assert.deepStrictEqual(out.reports, [{ id: "r1", label: "hermes：整理", n: 7, path: "reports/r1" }]);
assert.deepStrictEqual(P.build(null, [], null), { cards: [], reports: [] });
// 一条里有几种窗口
var two = P.build(null, [{ id: "r2", items: [{ id: "a", kind: "assign", status: "pending", taskId: "t1", suggestions: [sg("甲", 10), sg("乙", 90)] }] }], tree).cards[0];
assert.strictEqual(two.name, "乙（等 2 个窗口）");

var ed = P.build({ changes: [
  { id: "c1", op: "rename", target: "project", targetId: "p1", targetName: "数学", name: "代数" },
  { id: "c2", op: "move", target: "task", targetId: "t1", projectId: "p1", targetName: "刷题", toId: "p2", toName: "英语", reason: "r" },
  { id: "c3", op: "delete", target: "task", targetId: "t9", projectId: null, targetName: null }],
  ignores: [{ id: "g1", app: "Slack", titleContains: "闲聊" }, { id: "g2", app: "Steam", hasTitleFilter: true }] }, [], tree).cards;
assert.deepStrictEqual(ed.map(function (c) { return c.verb; }), ["rename", "cmove", "delete", "ignore", "ignore"]);
assert.strictEqual(ed[0].name, "数学"); assert.strictEqual(ed[0].to, "↓ 改名为 代数"); assert.strictEqual(ed[0].hlProject, true);
assert.strictEqual(ed[1].to, "↓ 移到 英语"); assert.strictEqual(ed[1].taskId, "t1"); assert.strictEqual(ed[1].destProjectId, "p2");
assert.strictEqual(ed[2].to, "↓ 删除"); assert.strictEqual(ed[2].tone, "del"); assert.strictEqual(ed[2].name, "（已不存在）");
assert.strictEqual(ed[3].to, "↓ 以后都忽略这个窗口"); assert.strictEqual(ed[3].name, "Slack · 闲聊");
assert.deepStrictEqual(P.build({ changes: [{ id: "cx", op: "teleport", target: "task" }, { id: "cy", op: "rename", target: "task", targetId: "t1", projectId: "p1", targetName: "a", name: null }],
  ignores: [{ id: "gx" }] }, [], tree).cards.map(function (c) { return c.name; }), ["a", "？"]);

// 撤销计时：注入时钟
var now = 0, timers = [], sent = [], done = [];
function mk() {
  return P.Commits({ ms: 5000,
    setTimer: function (f, ms) { var t = { f: f, at: now + ms, on: true }; timers.push(t); return t; },
    clearTimer: function (t) { if (t) t.on = false; },
    send: function (e, o) { sent.push([e.card.key, e.decision, !!(o && o.keepalive), !!(o && o.parallel)]); return Promise.resolve([]); },
    onDone: function (e, r) { done.push(e.card.key); } });
}
function tick(ms) { now += ms; timers.filter(function (t) { return t.on && t.at <= now; }).forEach(function (t) { t.on = false; t.f(); }); }
var cm = mk();
var e1 = cm.add({ key: "a" }, "approve"), e2 = cm.add({ key: "b" }, "reject"), e3 = cm.add({ key: "c" }, "approve");
assert.strictEqual(cm.pending(), 3); assert.ok(cm.has("a"));
tick(4999); assert.deepStrictEqual(sent, [], "5 秒前不发");
assert.ok(cm.undo(e2.id)); assert.strictEqual(cm.undo(e2.id), null);
tick(1);
assert.deepStrictEqual(sent, [["a", "approve", false, false], ["c", "approve", false, false]], "撤销的不发");
cm.add({ key: "d", verb: "task" }, "reject"); cm.add({ key: "p", verb: "project" }, "approve");
var fl = cm.flush(false);   // 转后台：一条接一条，项目提议排在任务提议前面
assert.deepStrictEqual(sent.slice(2), [["p", "approve", true, false]], "第一条同步发出，其余等它");
tick(60000); assert.deepStrictEqual(sent.slice(2), [["p", "approve", true, false]], "顺序发期间排队项的原计时器不抢跑");
fl.then(function () { return Promise.resolve(); }).then(function () {
  assert.deepStrictEqual(sent.slice(2, 4), [["p", "approve", true, false], ["d", "reject", true, false]]);
  cm.add({ key: "e" }, "approve"); cm.add({ key: "f", verb: "project" }, "approve");
  cm.flush(true);   // pagehide：并行、同步全部发出
  assert.deepStrictEqual(sent.slice(4), [["f", "approve", true, true], ["e", "approve", true, true]]);
  return Promise.resolve().then(function () { return Promise.resolve(); });
}).then(function () {
  assert.deepStrictEqual(done.sort(), ["a", "c", "d", "e", "f", "p"]); assert.strictEqual(cm.pending(), 0);
  console.log("hex-decide.test.js ok");
});
