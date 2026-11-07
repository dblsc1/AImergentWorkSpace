// hex-claim-card.js 的纯逻辑：候选排序与默认勾选、小时文案、claim 请求、结果提示。node hex-claim-card.test.js
"use strict";
const assert = require("assert");
const C = require("./hex-claim-card.js");

const it = (blockKey, match, seconds) => ({ blockKey, displayName: blockKey, match, seconds });
const sorted = C.sortCandidates([it("a", "ai", 100), it("b", "agent", 900), it("c", "name", 500)]);
assert.deepStrictEqual(sorted.map((x) => x.blockKey), ["b", "c", "a"], "按秒降序");
assert.deepStrictEqual(C.sortCandidates([it("a", "ai", 5), it("b", "ai", 5)]).map((x) => x.blockKey), ["a", "b"], "同秒保持原序");

assert.strictEqual(C.defaultChecked(it("x", "agent", 1)), true);
assert.strictEqual(C.defaultChecked(it("x", "name", 1)), true);
assert.strictEqual(C.defaultChecked(it("x", "ai", 1)), false);
assert.deepStrictEqual(C.defaultCheckedKeys([it("a", "agent", 3), it("b", "name", 2), it("c", "ai", 1)]), ["a", "b"]);

assert.strictEqual(C.hoursText(9000), "2.5 小时");
assert.strictEqual(C.hoursText(7200), "2 小时", "去掉 .0");
assert.strictEqual(C.hoursText(3600), "1 小时");
assert.strictEqual(C.hoursText(359), "不到 6 分钟");
assert.strictEqual(C.hoursText(0), "不到 6 分钟");
assert.strictEqual(C.hoursText(360), "0.1 小时");
assert.strictEqual(C.hoursText(61200), "17 小时");

const three = [it("a", "agent", 3600), it("b", "name", 3600), it("c", "ai", 1800)];
assert.strictEqual(C.titleText(three), "找到 3 个窗口块，共 2.5 小时，可能属于它");
assert.strictEqual(C.titleText([it("a", "ai", 60)]), "找到 1 个窗口块，共不到 6 分钟，可能属于它");

assert.deepStrictEqual(C.claimBody({ taskId: "t1" }, ["a", "b"]), { taskId: "t1", blockKeys: ["a", "b"], remember: true });
assert.deepStrictEqual(C.claimBody({ projectId: "p1" }, ["a"]), { projectId: "p1", blockKeys: ["a"], remember: true });
assert.strictEqual(C.candidatesPath({ taskId: "t 1" }), "api/core/activity/unclassified/candidates?taskId=t%201");
assert.strictEqual(C.candidatesPath({ projectId: "p1" }), "api/core/activity/unclassified/candidates?projectId=p1");

assert.strictEqual(C.resultText(7200), "已记进 2 小时");
assert.strictEqual(C.resultText(9000), "已记进 2.5 小时");
console.log("hex-claim-card.test.js ok");
