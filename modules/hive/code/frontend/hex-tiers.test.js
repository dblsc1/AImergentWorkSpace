// hive · hex-tiers.js 纯逻辑单测：块分类、提议按 kind 分、建议、拆分点与 parts（无框架，Node 内置 assert）
"use strict";
var assert = require("assert");
var T = require("./hex-tiers.js");

var items = [
  { blockKey: "a", status: "pending" },
  { blockKey: "g1", status: "generic", parts: null },
  { blockKey: "g2", status: "generic", parts: [{ taskId: "t1" }] },
  { blockKey: "r1", status: "assigned", via: "rule", lastAt: "2026-10-24T01:00:00Z" },
  { blockKey: "r2", status: "assigned", via: "ai-direct", lastAt: "2026-10-24T03:00:00Z" },
  { blockKey: "h", status: "assigned", via: "human", lastAt: "2026-10-24T02:00:00Z" },
  { blockKey: "i", status: "ignored", via: "rule" }];
var sp = T.splitBlocks(items);
assert.deepStrictEqual(sp.pending.map(function (b) { return b.blockKey; }), ["a", "g1"], "pending + 没拆的通用窗口");
assert.deepStrictEqual(sp.hist.map(function (b) { return b.blockKey; }), ["r2", "r1"], "规则 / AI 直接施加的 assigned，新的在前");
assert.deepStrictEqual(T.splitBlocks(null), { pending: [], hist: [] });

var k = T.proposalKinds({ projects: [{ id: "np", kind: "project" }], windowRules: [{ id: "w", kind: "windowRule" }],
  items: [{ id: "q", kind: "question" }, { id: "b", kind: "blockRule", status: "pending" }, { id: "x", kind: "blockAction", status: "rejected" }, { kind: "blockRule" }] });
assert.deepStrictEqual([k.questions, k.windowRules, k.blockRules, k.blockActions].map(function (l) { return l.map(function (p) { return p.id; }); }), [["q"], ["w"], ["b"], []]);
assert.deepStrictEqual(T.proposalKinds(null), { questions: [], windowRules: [], blockRules: [], blockActions: [] });

var acts = [{ blockKey: "a", date: "2026-10-23", action: "assign", taskId: "old" },
  { blockKey: "a", date: "2026-10-24", action: "split", reason: "上午下午不同" }, { blockKey: "a", date: "2026-10-24", action: "assign", projectId: "p1", reason: "晚来的" }];
assert.deepStrictEqual(T.suggestion({ blockKey: "a" }, "2026-10-24", acts), { target: { projectId: "p1" }, reason: "上午下午不同" });
assert.deepStrictEqual(T.suggestion({ blockKey: "z" }, "2026-10-24", acts), { target: null, reason: "" });

assert.deepStrictEqual(T.parseTarget("t:t9"), { taskId: "t9" }); assert.deepStrictEqual(T.parseTarget("p:p9"), { projectId: "p9" });
assert.strictEqual(T.parseTarget(""), null); assert.strictEqual(T.targetValue({ taskId: "t9" }), "t:t9"); assert.strictEqual(T.targetValue(null), "");
assert.deepStrictEqual(T.blockTarget({ parts: [{ taskId: "t1", projectId: "p1" }] }), { taskId: "t1", projectId: "p1" });
assert.strictEqual(T.blockTarget({ parts: null }), null);

var rg = T.rangeOf([{ startAt: "2026-10-24T01:00:00Z", endAt: "2026-10-24T01:10:00Z" }, { startAt: "2026-10-24T02:00:00Z", endAt: "2026-10-24T03:00:00Z" }, { startAt: "x", endAt: "y" }]);
assert.strictEqual(rg.list.length, 2); assert.strictEqual(rg.t1 - rg.t0, 7200000); assert.strictEqual(T.rangeOf([]), null);
var pts = T.addPoint([], 0, 100000); assert.deepStrictEqual(pts, [50000]);
assert.deepStrictEqual(T.addPoint([30000], 0, 100000), [30000, 65000], "加在最宽的一段正中");
assert.deepStrictEqual(T.addPoint([500], 0, 1000), [500], "太窄不加");
assert.strictEqual(T.clampPoint([10000, 20000], 0, 25000, 0, 100000), 19000, "不越过后一个拆分点");
assert.strictEqual(T.clampPoint([10000, 20000], 1, -5, 0, 100000), 11000);
assert.strictEqual(T.clampPoint([10000], 0, 1e9, 0, 100000), 99000, "不越过终点");
assert.deepStrictEqual(T.partsFromPoints([10, 20], ["t:a", "p:b", "t:c"], 1, 30),
  [{ fromTs: 1, toTs: 10, taskId: "a" }, { fromTs: 10, toTs: 20, projectId: "b" }, { fromTs: 20, toTs: 30, taskId: "c" }], "首尾用块的实际首尾");
var L = [{ s: 0, e: 100 }, { s: 300, e: 400 }];
assert.strictEqual(T.snapPoint(L, 50), 50, "在记录里不动"); assert.strictEqual(T.snapPoint(L, 150), 100); assert.strictEqual(T.snapPoint(L, 250), 300);
assert.strictEqual(T.snapPoint(L, 999), 400); assert.strictEqual(T.snapPoint([], 5), 5);
assert.strictEqual(T.partsHaveRecords(L, [200], 0, 400), true, "两段各有记录");
assert.strictEqual(T.partsHaveRecords(L, [150, 250], 0, 400), false, "中间那段是空的");
assert.strictEqual(T.partsHaveRecords(L, [200], 0, 400), true);
var K = 10000, LK = [{ s: 0, e: 100 * K }, { s: 300 * K, e: 400 * K }];
assert.deepStrictEqual(T.addPoint([], 0, 400 * K, LK), [100 * K], "默认点吸附到记录边界");
assert.deepStrictEqual(T.addPoint([100 * K], 0, 400 * K, [{ s: 0, e: 100 * K }, { s: 100 * K, e: 400 * K }]), [100 * K, 250 * K], "最宽一段正中");
assert.deepStrictEqual(T.addPoint([100 * K], 0, 400 * K, [{ s: 0, e: 100 * K }, { s: 100 * K + 5, e: 100 * K + 10 }]), [100 * K], "吸附后撞上已有点就不加");
assert.strictEqual(T.dur(42 * 60), "42 分钟"); assert.strictEqual(T.dur(125 * 60), "2 小时 05 分"); assert.strictEqual(T.dur(10), "不到 1 分钟");
console.log("hex-tiers.test.js ok");
