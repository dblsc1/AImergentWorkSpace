// hex-project-ruler.js 纯逻辑：7 天绝对刻度（前 6 天各占 70%/6，今天占 30%）、时间戳 → 横向位置、段与提示。node hex-project-ruler.test.js
"use strict";
process.env.TZ = "Asia/Shanghai";
const assert = require("assert");
const R = require("./hex-project-ruler.js");

const NOW = Date.parse("2026-11-10T15:00:00+08:00");
const L = R.layout(NOW);
const t = (s) => Date.parse(s + "+08:00");
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, a + " ≠ " + b);

// 7 天：前 6 天等宽共 70%，今天 30%
assert.strictEqual(L.days.length, 7);
assert.strictEqual(L.from, t("2026-11-04T00:00:00"));
assert.strictEqual(L.to, t("2026-11-11T00:00:00"));
L.days.slice(0, 6).forEach((d, i) => { near(d.w, 0.7 / 6); near(d.x0, i * 0.7 / 6); assert.strictEqual(d.today, false); });
near(L.days[6].x0, 0.7); near(L.days[6].w, 0.3); assert.strictEqual(L.days[6].today, true);

// 位置：两头、天界、今天的钟点
near(R.xOf(L, L.from), 0); near(R.xOf(L, L.to), 1);
near(R.xOf(L, t("2026-11-10T00:00:00")), 0.7);
near(R.xOf(L, t("2026-11-10T12:00:00")), 0.85);
near(R.xOf(L, t("2026-11-04T12:00:00")), 0.7 / 12);
near(R.xOf(L, 0), 0); near(R.xOf(L, NOW + 9e12), 1);   // 窗口外钳住

// 段：跨零点是一条，宽度 = 两侧之和；窗口外 = null；零长 / 倒置 = null
const r = R.spanRect(L, t("2026-11-09T23:00:00"), t("2026-11-10T06:00:00"));
near(r.left, 0.7 - (0.7 / 6) / 24); near(r.width, (0.7 / 6) / 24 + 0.3 / 4);
assert.strictEqual(R.spanRect(L, t("2026-11-01T00:00:00"), t("2026-11-03T00:00:00")), null);
assert.strictEqual(R.spanRect(L, t("2026-11-10T08:00:00"), t("2026-11-10T08:00:00")), null);
assert.strictEqual(R.spanRect(L, t("2026-11-10T09:00:00"), t("2026-11-10T08:00:00")), null);
// 窗口之前开始的段被裁到最左
near(R.spanRect(L, t("2026-11-03T00:00:00"), t("2026-11-04T12:00:00")).left, 0);

// 过去的一天很窄：一分钟的段按比例只有几万分之一宽（最小 1px 由样式保证）
assert.ok(R.spanRect(L, t("2026-11-05T10:00:00"), t("2026-11-05T10:01:00")).width < 1e-4);

// 标尺与泳道的 HTML：6 个天界、今天的钟点刻度、提示格式、上人下代理、项目色
const rh = R.rulerHtml(L);
assert.strictEqual((rh.match(/class="pr-div"/g) || []).length, 6);
assert.ok(rh.includes("今天") && rh.includes("周三") && ["00", "06", "12", "18", "24"].every((h) => rh.includes(">" + h + "<")));
const lh = R.lanesHtml(L, [{ start: "2026-11-10T14:00:00+08:00", end: "2026-11-10T15:30:00+08:00" }],
  [{ start: "2026-11-09T09:05:00+08:00", end: "2026-11-09T09:40:00+08:00", agent: "garden", runId: "r1" }], "#35c9eb");
assert.ok(lh.includes('title="11/10 14:00–15:30 你"') && lh.includes('title="11/09 09:05–09:40 garden"'));
assert.ok(lh.indexOf("pr-lane pr-human") < lh.indexOf("pr-lane pr-agent") && lh.includes("--pr-agent:#35c9eb"));
assert.ok(lh.includes('class="pr-seg pr-h"') && lh.includes('class="pr-seg pr-a"'));
console.log("hex-project-ruler.test.js ok");

// 等宽刻度：一天 / 一周
const D = R.linearScale(t("2026-11-10T15:00:00"), 1), W = R.linearScale(t("2026-11-09T00:00:00"), 7);
assert.strictEqual(D.from, t("2026-11-10T00:00:00")); assert.strictEqual(D.to, t("2026-11-11T00:00:00"));
near(R.xOf(D, t("2026-11-10T06:00:00")), 0.25);
assert.strictEqual(W.days.length, 7); assert.strictEqual(W.to, t("2026-11-16T00:00:00"));
near(R.xOf(W, t("2026-11-11T12:00:00")), 2.5 / 7);
near(R.spanRect(W, t("2026-11-09T12:00:00"), t("2026-11-10T12:00:00")).width, 1 / 7);
