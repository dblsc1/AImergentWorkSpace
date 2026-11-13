// hex-hover-card.js 纯逻辑：本周 / 本日合计、正在进行的任务怎么挑。node hex-hover-card.test.js
"use strict";
process.env.TZ = "Asia/Shanghai";
const assert = require("assert");
const H = require("./hex-hover-card.js");
const at = (s) => new Date("2026-11-12T" + s + "+08:00").getTime();   // 2026-11-12 是周四
const iso = (s) => "2026-11-12T" + s + "+08:00";
const iso1 = (d, s) => "2026-11-" + d + "T" + s + "+08:00";
const NOW = at("12:00:00");

const payload = {
  now: iso("12:00:00"),
  tasks: [
    { taskId: null, human: [], agent: [{ start: iso("08:00:00"), end: iso("09:00:00"), agent: "x" }] },
    { taskId: "a", human: [{ start: iso1("05", "10:00:00"), end: iso1("05", "13:00:00") }, { start: iso1("11", "11:00:00"), end: iso1("11", "13:00:00") }, { start: iso("10:00:00"), end: iso("10:30:00") }],
      agent: [{ start: iso("09:00:00"), end: iso("10:00:00") }, { start: iso("09:30:00"), end: iso("10:00:00") }] },
  ],
};
const t = H.totals(payload, NOW);
// 「二十九」：本日 = 此刻往前 24 小时，本周 = 往前 7 × 24 小时（都不按 0 点 / 周一切）
assert.strictEqual(t.day.human, 3600 + 1800);            // 昨天 11–13 点只有 12 点之后那 1 小时在 24 小时内 + 今天 30 分钟
assert.strictEqual(t.day.agent, 3600 + 3600 + 1800);     // 08-09 + 09-10 + 09:30-10（并行各自累加）
assert.strictEqual(t.week.human, 3600 + 7200 + 1800);    // 7 天前 10–13 点只有 12 点之后那 1 小时在窗口内
assert.strictEqual(H.totals(null, NOW).week.human, 0);
assert.strictEqual(H.totals({ tasks: [{ taskId: "a", human: [{ start: iso("13:00:00"), end: iso("14:00:00") }], agent: [] }] }, NOW).day.human, 0);   // 还没到的不算

const tasks = [{ id: "a", name: "写作业" }, { id: "b", name: "改论文" }, { id: "c", name: "旧事", done: true }];
const seg = (a, b) => ({ start: iso(a), end: iso(b) });
const mk = (...ts) => ({ now: iso("12:00:00"), tasks: ts });

// 此刻在干的（段止于 now）：多个取最近开始的
assert.strictEqual(H.pickCurrentTask(mk(
  { taskId: "a", human: [seg("09:00:00", "12:00:00")], agent: [] },
  { taskId: "b", human: [], agent: [seg("11:00:00", "12:00:00")] }), tasks, NOW), "改论文");
// 此刻没有：取最近有过活动的未完成任务
assert.strictEqual(H.pickCurrentTask(mk(
  { taskId: "a", human: [seg("09:00:00", "10:00:00")], agent: [] },
  { taskId: "b", human: [seg("08:00:00", "09:00:00")], agent: [] }), tasks, NOW), "写作业");
// 在干的压过更晚结束的（后者不在 now 前 60 秒内）
assert.strictEqual(H.pickCurrentTask(mk(
  { taskId: "a", human: [seg("09:00:00", "11:00:00")], agent: [seg("11:30:00", "12:00:00")] },
  { taskId: "b", human: [seg("11:00:00", "11:50:00")], agent: [] }), tasks, NOW), "写作业");
// 已完成的、没挂任务的、已删的都不算；一个都没有 = null
assert.strictEqual(H.pickCurrentTask(mk(
  { taskId: "c", human: [seg("11:00:00", "12:00:00")], agent: [] },
  { taskId: null, human: [seg("11:00:00", "12:00:00")], agent: [] },
  { taskId: "gone", human: [seg("11:00:00", "12:00:00")], agent: [] }), tasks, NOW), null);
assert.strictEqual(H.pickCurrentTask(null, tasks, NOW), null);

// 骨架：有标尺和泳道，没有数字也没有任务行；读到之后才有
const dur = (s) => Math.round(s / 60) + " 分";
const sk = H.cardHtml(NOW, null, tasks, "#09f", dur);
assert.ok(sk.includes("pr-ruler") && sk.includes("pr-lanes") && !sk.includes("hv-sum") && !sk.includes("hv-task") && !sk.includes("title="));
const full = H.cardHtml(NOW, mk({ taskId: "a", human: [seg("09:00:00", "12:00:00")], agent: [] }), [{ id: "a", name: "<b>x</b>" }], "", dur);
assert.ok(full.includes('class="hv-task">&lt;b&gt;x&lt;/b&gt;') && (full.match(/class="hv-sum"/g) || []).length === 2 && full.includes("本周") && full.includes("本日"));
console.log("hex-hover-card: ok");
