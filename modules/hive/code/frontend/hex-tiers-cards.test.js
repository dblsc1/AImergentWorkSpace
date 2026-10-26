// hive · hex-tiers-cards.js 纯逻辑单测：置信度分组、文案、跳一跳概率、卡片顺序（无框架，Node 内置 assert）
"use strict";
var assert = require("assert");
var B = require("./hex-tiers-cards.js");

// 置信度：缺 = null，阈值缺 = 80，>= 阈值才算高
assert.strictEqual(B.confOf(undefined), null); assert.strictEqual(B.confOf("90"), null); assert.strictEqual(B.confOf(87.4), 87); assert.strictEqual(B.confOf(130), 100);
assert.strictEqual(B.thresholdOf(null), 80); assert.strictEqual(B.thresholdOf({}), 80); assert.strictEqual(B.thresholdOf({ highConfidenceThresholdPercent: 65 }), 65);
assert.strictEqual(B.isHigh(80, 80), true); assert.strictEqual(B.isHigh(79, 80), false); assert.strictEqual(B.isHigh(null, 0), false); assert.strictEqual(B.isHigh(80), true);
var g = B.groupByConfidence([{ k: "a", c: 95 }, { k: "b", c: 80 }, { k: "c", c: 79 }, { k: "d", c: null }], function (x) { return x.c; }, 80);
assert.deepStrictEqual(g.high.map(function (x) { return x.k; }), ["a", "b"]); assert.deepStrictEqual(g.low.map(function (x) { return x.k; }), ["c", "d"]);
assert.strictEqual(B.groupByConfidence([{ c: 50 }], function (x) { return x.c; }, 50).high.length, 1, "阈值可调");
assert.deepStrictEqual(B.groupByConfidence(null, function () { return 1; }, 80), { high: [], low: [] });

// 副标题：每种操作 × 对象
var T = { op: "create", kind: "task", name: "刷题", projectName: "数学" };
assert.strictEqual(B.subtitle(T), "到 数学 项目 下");
assert.strictEqual(B.subtitle({ op: "create", kind: "project", name: "新书", zoneName: "学习" }), "到 学习 分区 下");
assert.strictEqual(B.subtitle({ op: "change", kind: "task", name: "刷题", projectName: "数学", toName: "英语", toKind: "project" }), "更改 数学 的 刷题 任务 到 英语 项目 下");
assert.strictEqual(B.subtitle({ op: "change", kind: "task", name: "刷题", projectName: "数学", toName: "错题", toKind: "task" }), "更改 数学 的 刷题 任务 到 错题 任务 下");
assert.strictEqual(B.subtitle({ op: "change", kind: "task", name: "刷题", projectName: "数学", toName: "英语" }), "更改 数学 的 刷题 任务 到 英语 项目 下", "任务默认目标 = 项目");
assert.strictEqual(B.subtitle({ op: "change", kind: "project", name: "数学", toName: "学习", toKind: "zone" }), "更改 数学 项目 到 学习 分区 下");
assert.strictEqual(B.subtitle({ op: "change", kind: "project", name: "数学", toName: "学习" }), "更改 数学 项目 到 学习 分区 下", "项目默认目标 = 分区");
assert.strictEqual(B.subtitle({ op: "rename", kind: "task", name: "a", newName: "b" }), "改名为 b");
assert.strictEqual(B.subtitle({ op: "rename", kind: "project", name: "a", newName: "b" }), "改名为 b");
assert.strictEqual(B.subtitle({ op: "delete", kind: "task", name: "刷题", projectName: "数学" }), "删除 数学 的 刷题 任务");
assert.strictEqual(B.subtitle({ op: "delete", kind: "project", name: "数学" }), "删除 数学 项目");
assert.strictEqual(B.subtitle({ op: "session", toTaskName: "刷题", toProjectName: "数学" }), "改挂到 刷题 任务");
assert.strictEqual(B.subtitle({ op: "session", toTaskName: "", toProjectName: "数学" }), "改挂到 数学 项目");
assert.strictEqual(B.subtitle({ op: "create", kind: "task", name: "x" }), "到 ？ 项目 下", "缺名字不抛");
assert.strictEqual(B.subtitle({ op: "teleport" }), "");

// 标签 / 标题 / 时间段
assert.deepStrictEqual([B.tagOf({ op: "create" }), B.tagOf({ op: "change" }), B.tagOf({ op: "rename" }), B.tagOf({ op: "session" }), B.tagOf({ op: "delete" })].map(function (t) { return t[0]; }), ["新建", "更改", "更改", "更改", "删除"]);
assert.deepStrictEqual(B.titleParts(T), { name: "刷题", proj: "数学", suffix: "（任务）" });
assert.deepStrictEqual(B.titleParts({ kind: "project", name: "数学", projectName: "x" }), { name: "数学", proj: "", suffix: "（项目）" });
var st = new Date(2026, 9, 24, 9, 0);
assert.strictEqual(B.sessionTitle({ startAt: st.toISOString(), durationSeconds: 42 * 60 }), "09:00–09:42 · 42 分钟");
assert.strictEqual(B.sessionTitle({ startAt: "x", durationSeconds: 10 }), "不到 1 分钟");

// 窗口规则一行
assert.strictEqual(B.ruleLine({ kind: "windowRule", block: "写代码" }), "收进「写代码」");
assert.strictEqual(B.ruleLine({ kind: "blockRule", taskName: "刷题", projectName: "数学" }), "记到 刷题 任务（数学）");
assert.strictEqual(B.ruleLine({ kind: "blockRule", projectName: "数学" }), "记到 数学 项目");
assert.strictEqual(B.ruleLine({ kind: "blockRule", op: "delete" }), "删除这条规则");

// 跳一跳：概率 = 1 / (1 + 批准数 / 20)；方向 / 幅度
assert.strictEqual(B.hintChance(0), 1); assert.strictEqual(B.hintChance(20), 0.5); assert.strictEqual(B.hintChance(80), 0.2); assert.strictEqual(B.hintChance("x"), 1); assert.strictEqual(B.hintChance(-5), 1);
assert.strictEqual(B.shouldHint(0, 0.999), true); assert.strictEqual(B.shouldHint(20, 0.49), true); assert.strictEqual(B.shouldHint(20, 0.5), false);
assert.strictEqual(B.jumpDx(0.1, 0), -10); assert.strictEqual(B.jumpDx(0.9, 1), 16); assert.ok(Math.abs(B.jumpDx(0.7, 0.5)) >= 10 && Math.abs(B.jumpDx(0.2, 0.5)) <= 16);
function mem() { var d = {}; return { getItem: function (k) { return k in d ? d[k] : null; }, setItem: function (k, v) { d[k] = String(v); }, d: d }; }
var m = mem(); assert.strictEqual(B.readApprovals(m), 0); B.bumpApprovals(m); B.bumpApprovals(m); assert.strictEqual(B.readApprovals(m), 2);
var bad = { getItem: function () { throw new Error("blocked"); }, setItem: function () { throw new Error("blocked"); } };
assert.strictEqual(B.readApprovals(bad), 0); B.bumpApprovals(bad);   // 存不下不抛

// 顺序：缺的补上、多的丢掉、拖动占目标的位置、存取
assert.deepStrictEqual(B.normOrder(null), ["q", "s", "r", "b", "h"]);
assert.deepStrictEqual(B.normOrder(["h", "x", "s", "h"]), ["h", "s", "q", "r", "b"]);
assert.deepStrictEqual(B.moveOnto(["q", "s", "r", "b", "h"], "q", "r"), ["s", "r", "q", "b", "h"], "往后拖落在目标后面");
assert.deepStrictEqual(B.moveOnto(["q", "s", "r", "b", "h"], "h", "s"), ["q", "h", "s", "r", "b"], "往前拖落在目标前面");
assert.deepStrictEqual(B.moveOnto(["q", "s"], "q", "q"), ["q", "s"]); assert.deepStrictEqual(B.moveOnto(["q", "s"], "z", "q"), ["q", "s"]);
var ms = mem(); assert.deepStrictEqual(B.loadOrder(ms), B.DEF_ORDER);
B.saveOrder(["b", "s"], ms); assert.deepStrictEqual(B.loadOrder(ms), ["b", "s", "q", "r", "h"]);
ms.d["hive.decide.order"] = "{坏的"; assert.deepStrictEqual(B.loadOrder(ms), B.DEF_ORDER, "存的是坏数据就用默认");
assert.deepStrictEqual(B.loadOrder(bad), B.DEF_ORDER); B.saveOrder(["q"], bad);
console.log("hex-tiers-cards.test.js ok");
