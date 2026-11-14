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
assert.deepStrictEqual(C.tickerLines({ humanNow: null, working: 0, humanSec: 0, agentSec: 0, focus: null }), ["你 0m\n代理 0m"]);
assert.deepStrictEqual(C.tickerLines({ humanNow: "你在做 数学", working: 2, humanSec: 3600, agentSec: 7200, focus: { name: "garden", text: "00:20:00" } }),
  ["你在做 数学", "2 个代理并行", "效率 ×2", "你 1h\n代理 2h", "garden\n00:20:00"]);
assert.deepStrictEqual([C.fmtShort(0), C.fmtShort(25 * 60), C.fmtShort(3600), C.fmtShort(3.2 * 3600)], ["0m", "25m", "1h", "3.2h"]);

console.log("hex-centre-stats ok");

// 环上的弧跟着轮播句走、环卡的行（hive.stage.v1「二十七」B、7）
(function () {
  const assert = require("assert"), C = require("./hex-centre-stats.js"), near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, a + " vs " + b);
  const R = "var(--pr-human)", N = "var(--ink-3)", A = "var(--accent)";
  // 种类随句子
  const kinds = C.tickerEntries({ humanNow: "你在做 数学", working: 2, humanSec: 3600, agentSec: 7200, focus: { name: "g", text: "00:01" } }).map((e) => e.kind);
  assert.deepStrictEqual(kinds, ["human", "parallel", "efficiency", "totals", "agent"]);
  // 你 / 代理：人红色 + 每个 agent 自己的颜色，按各自占（人 + 全部 agent）的比例
  let arcs = C.ringArcs("totals", { humanSec: 50, agentSec: 50, agents: [{ sec: 30, color: "#a" }, { sec: 20, color: "" }] });
  assert.deepStrictEqual(arcs.map((a) => a.color), [R, "#a", N]);
  [0.5, 0.3, 0.2].forEach((v, i) => near(arcs[i].share, v));
  // 多于 6 个 agent：最小的合成一段中性色，agent 弧至多 6 段
  const many = Array.from({ length: 9 }, (_, i) => ({ sec: 10 * (9 - i), color: "#" + i }));
  arcs = C.ringArcs("totals", { humanSec: 100, agentSec: 450, agents: many });
  assert.strictEqual(arcs.length, 1 + 6);
  assert.strictEqual(arcs[6].color, N); near(arcs[6].share, (40 + 30 + 20 + 10) / 550);
  near(arcs.reduce((n, a) => n + a.share, 0), 1);
  // 并行：每个在干活的 agent 一段等长弧；没有就没有弧
  arcs = C.ringArcs("parallel", { workers: [{ color: "#a" }, {}, { color: "#c" }] });
  assert.deepStrictEqual(arcs.map((a) => a.color), ["#a", N, "#c"]); near(arcs[0].share, 1 / 3);
  assert.deepStrictEqual(C.ringArcs("parallel", { workers: [] }), []);
  // 某个 agent：整圈它的颜色；效率与其他句：人红 / 代理 --accent 两段
  assert.deepStrictEqual(C.ringArcs("agent", { focusColor: "#f" }), [{ color: "#f", share: 1 }]);
  assert.deepStrictEqual(C.ringArcs("agent", {}), [{ color: N, share: 1 }]);
  arcs = C.ringArcs("efficiency", { humanSec: 1, agentSec: 3, agents: [] });
  assert.deepStrictEqual(arcs.map((a) => a.color), [R, A]); near(arcs[0].share, 0.25);
  assert.deepStrictEqual(C.ringArcs("human", { humanSec: 0, agentSec: 0 }), []);
  // 环卡
  const T = (s) => C.ringCardLines(s).map((l) => l.text), w = (n) => Array.from({ length: n }, (_, i) => ({ name: "a" + i, project: i ? "" : "数学" }));
  assert.deepStrictEqual(T({ humanSec: 4980, agentSec: 600, workers: [], waiting: 0, peak: 0 }), ["今天：你 1 小时 23 分 · 代理 10 分", "现在没有 agent 在干活"]);
  assert.deepStrictEqual(T({ humanSec: 0, agentSec: 0, workers: w(5), waiting: 2, peak: 4 }),
    ["今天：你 0 分 · 代理 0 分", "现在 5 个 agent 在干活", "a0 · 数学", "a1", "a2", "+2", "2 个在等你", "今天代理并行峰值 4"]);
})();
