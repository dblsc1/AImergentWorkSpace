// hex-hover-card.js 纯逻辑：本周 / 本日合计、正在进行的任务怎么挑。node hex-hover-card.test.js
"use strict";
process.env.TZ = "Asia/Shanghai";
const assert = require("assert");
const H = require("./hex-hover-card.js");
const at = (s) => new Date("2026-11-12T" + s + "+08:00").getTime();   // 2026-11-12 是周四
const iso = (s) => "2026-11-12T" + s + "+08:00";
const iso1 = (d, s) => "2026-11-" + d + "T" + s + "+08:00";
const NOW = at("12:00:00");

// 周起点 = 周一 0 点；周日算上周
assert.strictEqual(H.weekStart(NOW), new Date("2026-11-09T00:00:00+08:00").getTime());
assert.strictEqual(H.weekStart(new Date("2026-11-15T10:00:00+08:00").getTime()), new Date("2026-11-09T00:00:00+08:00").getTime());
assert.strictEqual(H.weekStart(new Date("2026-11-09T00:00:00+08:00").getTime()), new Date("2026-11-09T00:00:00+08:00").getTime());

const payload = {
  now: iso("12:00:00"),
  tasks: [
    { taskId: null, human: [], agent: [{ start: iso("08:00:00"), end: iso("09:00:00"), agent: "x" }] },
    { taskId: "a", human: [{ start: iso1("08", "22:00:00"), end: iso1("09", "01:00:00") }, { start: iso("10:00:00"), end: iso("10:30:00") }],
      agent: [{ start: iso("09:00:00"), end: iso("10:00:00") }, { start: iso("09:30:00"), end: iso("10:00:00") }] },
  ],
};
const t = H.totals(payload, NOW);
assert.strictEqual(t.day.human, 1800);                   // 今天人 30 分钟
assert.strictEqual(t.day.agent, 3600 + 3600 + 1800);     // 08-09 + 09-10 + 09:30-10（并行各自累加）
assert.strictEqual(t.week.human, 3600 + 1800);           // 周日 22 点那段不到周一，周一 0–1 点算；见下
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
