// hex-centre-stats.js 纯逻辑：效率、并行峰值、轮播短句。node hex-centre-stats.test.js
"use strict";
process.env.TZ = "Asia/Shanghai";
const assert = require("assert");
const C = require("./hex-centre-stats.js");
const t = (s) => "2026-11-10T" + s + "+08:00";

assert.strictEqual(C.fmtHours(45 * 60), "45 分钟");
assert.strictEqual(C.fmtHours(3600), "1 小时");
assert.strictEqual(C.fmtHours(3.2 * 3600), "3.2 小时");

// 效率 = 代理 ÷ 你
assert.strictEqual(C.efficiencyLine(3600, 3.2 * 3600).line, "你 1 小时，带出代理 3.2 小时的活");
assert.strictEqual(C.efficiencyLine(7200, 3600).line, "你 1 小时，带出代理 0.5 小时的活");
assert.strictEqual(C.efficiencyLine(0, 3600), null);
assert.strictEqual(C.efficiencyLine(3600, 0), null);

// 并行峰值：按运行数；同一条运行首尾相接的段不叠
const agents = [
  { runId: "a", segments: [{ start: t("09:00:00"), end: t("10:00:00") }, { start: t("10:00:00"), end: t("11:00:00") }] },
  { runId: "b", segments: [{ start: t("09:30:00"), end: t("10:30:00") }] },
  { runId: "c", segments: [{ start: t("10:10:00"), end: t("10:20:00") }] }];
assert.strictEqual(C.peakParallel(agents), 3);
assert.strictEqual(C.peakParallel([agents[0]]), 1);
assert.strictEqual(C.peakParallel([]), 0);
const tt = C.totals({ human: [{ start: t("09:00:00"), end: t("10:00:00") }], agents });
assert.strictEqual(tt.humanSec, 3600); assert.strictEqual(tt.agentSec, 7200 + 3600 + 600); assert.strictEqual(tt.peak, 3);

// 轮播短句：有就列，没有就跳过，总计一定有
assert.deepStrictEqual(C.tickerLines({ humanNow: null, working: 0, humanSec: 0, agentSec: 0, focus: null }), ["你 0 分钟\n代理 0 分钟"]);
assert.deepStrictEqual(C.tickerLines({ humanNow: "你在做 数学", working: 2, humanSec: 3600, agentSec: 7200, focus: { name: "garden", text: "00:20:00" } }),
  ["你在做 数学", "2 个代理并行", "你 1 小时，带出代理 2 小时的活", "你 1 小时\n代理 2 小时", "garden\n00:20:00"]);

console.log("hex-centre-stats ok");
