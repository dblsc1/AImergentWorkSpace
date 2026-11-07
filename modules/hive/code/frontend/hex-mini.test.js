// hex-mini.js 的纯逻辑：停留分钟、卡的显示、拖放 / 不管它的请求、409 重试判定、原因文案。node hex-mini.test.js
"use strict";
const assert = require("assert");
const M = require("./hex-mini.js");

// 停留分钟：整分钟取整
assert.strictEqual(M.minutesText(372), "6 分钟");
assert.strictEqual(M.minutesText(359), "5 分钟");
assert.strictEqual(M.minutesText(59), "0 分钟");
assert.strictEqual(M.minutesText(null), "0 分钟");

// 卡的显示：有 ask = 名字 + 时长；null 或外壳说离开了 = 空态
const ask = { blockKey: "chrome · figma.com · 首页改版", displayName: "figma.com · 首页改版", focusedSeconds: 372, date: "2026-11-07", seq: 3 };
assert.deepStrictEqual(M.cardView(ask, false), { kind: "ask", name: "figma.com · 首页改版", dwell: "6 分钟" });
assert.deepStrictEqual(M.cardView(null, false), { kind: "gone", text: "这个窗口已经离开" });
assert.deepStrictEqual(M.cardView(ask, true), { kind: "gone", text: "这个窗口已经离开" });
assert.strictEqual(M.cardView({ ...ask, displayName: null }, false).name, "未命名窗口", "displayName 为 null 也能显示");

// 请求：体恰好是契约的键，没有 taskId
assert.deepStrictEqual(M.assignRequest(ask, "p1"), { method: "POST", path: "activity/blocks/assign", body: { date: "2026-11-07", blockKey: ask.blockKey, ifSeq: 3, projectId: "p1", remember: true } });
assert.strictEqual(M.assignRequest(ask, null), null, "放到空白处不发");
assert.deepStrictEqual(M.answerRequest(ask), { method: "POST", path: "activity/live-ask/answer", body: { blockKey: ask.blockKey, answer: "unmanaged" } });

// 409 之后：块还在且 seq 变了 = 用新 seq 重试；块没了 = 离开；seq 没变 = 不重试
assert.deepStrictEqual(M.retryPlan(ask, { ...ask, seq: 4 }), { action: "retry", ask: { ...ask, seq: 4 } });
assert.deepStrictEqual(M.retryPlan(ask, null), { action: "gone" });
assert.deepStrictEqual(M.retryPlan(ask, { ...ask, blockKey: "别的块", seq: 9 }), { action: "gone" });
assert.deepStrictEqual(M.retryPlan(ask, { ...ask }), { action: "fail" });

// 原因文案
assert.ok(M.reasonText(409));
assert.ok(M.reasonText(404));
assert.ok(M.reasonText(500));
assert.notStrictEqual(M.reasonText(409), M.reasonText(404));

// 项目名：太长截断，空名有兜底
assert.strictEqual(M.shortName("数学"), "数学");
assert.strictEqual(M.shortName("录音棚后期制作"), "录音棚后…");
assert.strictEqual(M.shortName(""), "未命名");

console.log("hex-mini.test.js ok");
