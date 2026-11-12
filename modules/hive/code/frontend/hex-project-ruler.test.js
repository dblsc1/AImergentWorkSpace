// hex-project-ruler.js 纯逻辑：7 天绝对刻度（今天 38.4%、昨天 23.7%、前天 15%、更早 4 天各 5.725%，hive.stage.v1「十五」）、刻度线、时间戳 → 横向位置、段与提示、任务排序。node hex-project-ruler.test.js
"use strict";
process.env.TZ = "Asia/Shanghai";
const assert = require("assert");
const R = require("./hex-project-ruler.js");

const NOW = Date.parse("2026-11-10T15:00:00+08:00");
const L = R.layout(NOW);
const t = (s) => Date.parse(s + "+08:00");
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, a + " ≠ " + b);

// 7 天：今天 0.384、昨天 0.237、前天 0.15、更早 4 天各 0.05725，合计 1
assert.strictEqual(L.days.length, 7);
assert.strictEqual(L.from, t("2026-11-04T00:00:00"));
assert.strictEqual(L.to, t("2026-11-11T00:00:00"));
L.days.slice(0, 4).forEach((d, i) => { near(d.w, 0.05725); near(d.x0, i * 0.05725); assert.strictEqual(d.today, false); });
near(L.days[4].w, 0.15); near(L.days[5].w, 0.237); near(L.days[6].w, 0.384);
near(L.days[6].x0, 0.616); near(L.days[5].x0, 0.379); near(L.days[4].x0, 0.229);
assert.strictEqual(L.days[6].today, true); assert.deepStrictEqual(L.days.map((d) => d.ago), [6, 5, 4, 3, 2, 1, 0]);
near(L.days.reduce((n, d) => n + d.w, 0), 1);
near(R.TODAY_SHARE, 0.384); assert.strictEqual(Math.round(0.616 * 0.384 * 1000), 237);   // 昨天 = 0.616 × 0.384（取到三位）

// 刻度线：今天每小时一条（25 条含两头）、每 3 小时带数字；昨天 0 / 6 / 12 / 18 带数字；前天只有 12 点；更早的没有
const tk = (i) => L.days[i].ticks;
assert.strictEqual(tk(6).length, 25); assert.deepStrictEqual(tk(6).filter((x) => x.label).map((x) => x.h), [0, 3, 6, 9, 12, 15, 18, 21, 24]);
assert.deepStrictEqual(tk(5).map((x) => [x.h, x.label]), [[0, true], [6, true], [12, true], [18, true]]);
assert.deepStrictEqual(tk(4).map((x) => x.h), [12]);
[0, 1, 2, 3].forEach((i) => assert.deepStrictEqual(tk(i), []));
near(tk(6)[12].x, 0.616 + 0.384 / 2); near(tk(5)[1].x, 0.379 + 0.237 / 4); near(tk(4)[0].x, 0.229 + 0.075);

// 位置：两头、天界、今天的钟点
near(R.xOf(L, L.from), 0); near(R.xOf(L, L.to), 1);
near(R.xOf(L, t("2026-11-10T00:00:00")), 0.616);
near(R.xOf(L, t("2026-11-10T12:00:00")), 0.808);
near(R.xOf(L, t("2026-11-04T12:00:00")), 0.05725 / 2);
near(R.xOf(L, 0), 0); near(R.xOf(L, NOW + 9e12), 1);   // 窗口外钳住

// 段：跨零点是一条，宽度 = 两侧之和；窗口外 = null；零长 / 倒置 = null
const r = R.spanRect(L, t("2026-11-09T23:00:00"), t("2026-11-10T06:00:00"));
near(r.left, 0.616 - 0.237 / 24); near(r.width, 0.237 / 24 + 0.384 / 4);
assert.strictEqual(R.spanRect(L, t("2026-11-01T00:00:00"), t("2026-11-03T00:00:00")), null);
assert.strictEqual(R.spanRect(L, t("2026-11-10T08:00:00"), t("2026-11-10T08:00:00")), null);
assert.strictEqual(R.spanRect(L, t("2026-11-10T09:00:00"), t("2026-11-10T08:00:00")), null);
// 窗口之前开始的段被裁到最左
near(R.spanRect(L, t("2026-11-03T00:00:00"), t("2026-11-04T12:00:00")).left, 0);

// 过去的一天很窄：一分钟的段按比例只有几万分之一宽（最小 1px 由样式保证）
assert.ok(R.spanRect(L, t("2026-11-05T10:00:00"), t("2026-11-05T10:01:00")).width < 1e-4);

// 标尺与泳道的 HTML：6 个天界、刻度线与数字、今天 / 昨天 / 周几、提示格式、上人下代理、项目色
const rh = R.rulerHtml(L);
assert.strictEqual((rh.match(/class="pr-div"/g) || []).length, 6);
assert.ok(rh.includes(">今天<") && rh.includes(">昨天<") && rh.includes("周三") && rh.includes("周日"));
assert.strictEqual((rh.match(/class="pr-day/g) || []).length, 7);
assert.strictEqual((rh.match(/class="pr-hr"/g) || []).length, 16);              // 今天 25 条里 9 条带数字，其余 16 条细线
assert.strictEqual((rh.match(/class="pr-tick"/g) || []).length, 9 + 4 + 1);     // 今天 9 + 昨天 4 + 前天 1
assert.ok(["00", "03", "06", "09", "12", "15", "18", "21", "24"].every((h) => rh.includes(">" + h + "<")));
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

// 滚动窗口：一整段，不按天切（hive.stage.v1「十三」2）
const RS = R.rollingScale(t("2026-11-10T09:00:00"), t("2026-11-10T12:00:00"));
assert.strictEqual(R.xOf(RS, t("2026-11-10T10:30:00")), 0.5);
assert.deepStrictEqual(R.spanRect(RS, t("2026-11-10T08:00:00"), t("2026-11-10T09:30:00")), { left: 0, width: 1 / 6 });
assert.strictEqual(R.spanRect(RS, t("2026-11-10T12:00:00"), t("2026-11-10T13:00:00")), null);
// 最近完成一行里的时长（hive.stage.v1「十二」3）
assert.strictEqual(R.minutesText(0), "0 分钟");
assert.strictEqual(R.minutesText(20), "不到 1 分钟");
assert.strictEqual(R.minutesText(89), "1 分钟"); assert.strictEqual(R.minutesText(180), "3 分钟");
assert.strictEqual(R.spentText(false, 180, 720), "完成 · 人花了 3 分钟 · agent 花了 12 分钟");
assert.strictEqual(R.spentText(true, 0, 30), "取消 · 人花了 0 分钟 · agent 花了 不到 1 分钟");

// 任务排序 = 优先级接口（hive.stage.v1「十五」）：priority 小的在前（缺 = 并列）→ 最近活动新的在前 → 名字
const tasks = [{ id: "a", name: "乙" }, { id: "b", name: "甲" }, { id: "c", name: "丙" }, { id: "d", name: "丁", priority: 2 }, { id: "e", name: "戊", priority: 1 }];
assert.deepStrictEqual(R.taskOrder(tasks, { a: 100, b: 100, c: 300 }).map((x) => x.id), ["e", "d", "c", "b", "a"]);   // 有 priority 的先（1、2），其余按活动 300 > 100，并列按名字甲 < 乙
assert.deepStrictEqual(R.taskOrder(tasks.slice(0, 3), {}).map((x) => x.id), ["c", "b", "a"]);   // 都没活动：按名字（拼音）丙 < 甲 < 乙
assert.strictEqual(tasks[0].id, "a");   // 不改原数组
assert.deepStrictEqual(R.taskOrder(null, null), []);
const act = R.lastActivity([{ taskId: "a", human: [{ start: "2026-11-10T09:00:00+08:00", end: "2026-11-10T10:00:00+08:00" }], agent: [{ start: "2026-11-10T11:00:00+08:00", end: "2026-11-10T12:00:00+08:00" }] }, { taskId: null, human: [], agent: [] }]);
assert.strictEqual(act.a, t("2026-11-10T12:00:00")); assert.strictEqual(act[""], undefined);
