// hex-record.js 纯逻辑：完成记录的两条时间线、合计、运行行。node hex-record.test.js
"use strict";
const assert = require("assert");
const R = require("./hex-record.js");

const T0 = Date.parse("2026-11-07T00:00:00+08:00");
const iso = (sec) => new Date(T0 + sec * 1000).toISOString();
const NOW = T0 + 10 * 3600e3;
const lanes = {
  windowStart: iso(0),
  human: { sessions: [
    { projectId: "p1", startAt: iso(3600), endAt: iso(7200) },
    { projectId: "p2", startAt: iso(8000), endAt: iso(9000) },
    { projectId: "p1", startAt: iso(-1800), endAt: iso(1800) }] },
  interactions: [
    { runId: "r1", kind: "attend", at: iso(100), until: iso(400) },
    { runId: "r9", kind: "attend", at: iso(100), until: iso(400) },
    { runId: "r1", kind: "other", at: iso(100), until: iso(400) },
    { runId: "r2", kind: "attend", at: iso(500), until: iso(560) }]
};
const runs = [
  { runId: "r1", agent: "claude-code", label: "garden", projectId: "p1", phase: "idle", startAt: iso(0), endAt: iso(1000),
    phases: [{ at: iso(0), phase: "working" }, { at: iso(300), phase: "idle" }, { at: iso(700), phase: "working" }] },
  { runId: "r2", agent: "codex", taskId: "t1", phase: "working", startAt: iso(0), phases: [{ at: iso(36000 - 90), phase: "working" }] },
  { runId: "r9", agent: "x", projectId: "p2", phase: "idle", phases: [] }];

const rec = R.projectRecord(lanes, runs, "p1", (t) => (t === "t1" ? "p1" : null), NOW);
// HV-B13：计时只取该项目的段，元素 {from,to,kind}，合计只算今天
assert.deepStrictEqual(rec.timer.spans[0], { from: T0 + 3600e3, to: T0 + 7200e3, kind: "timer" });
assert.strictEqual(rec.timer.spans.length, 2);
assert.strictEqual(rec.timer.total, 3600 + 1800);
// 对话：kind 为 attend 且 runId 属于该项目（含经任务归属的 r2）
assert.deepStrictEqual(rec.attend.spans.map((s) => s.kind), ["attend", "attend"]);
assert.strictEqual(rec.attend.total, 300 + 60);
// 运行行：working 秒数（r1：0–300 + 700–1000 = 600；r2：90 秒到此刻）
assert.deepStrictEqual(rec.runs.map((r) => [r.name, r.workingSeconds, r.phase]), [["garden", 600, "idle"], ["codex", 90, "working"]]);
assert.deepStrictEqual(R.projectRecord(null, [], "p1", () => null, NOW).runs, []);
// 正在进行的计时段补到此刻；until 为空的对话段也到此刻；重叠按并集
const live = R.projectRecord(Object.assign({}, lanes, {
  human: { sessions: [], running: { projectId: "p1", startAt: iso(36000 - 600) } },
  interactions: [
    { runId: "r1", kind: "attend", at: iso(100), until: iso(400) },
    { runId: "r2", kind: "attend", at: iso(300), until: iso(500) },
    { runId: "r1", kind: "attend", at: iso(36000 - 60), until: null }] }), runs, "p1", (t) => (t === "t1" ? "p1" : null), NOW);
assert.deepStrictEqual(live.timer.spans, [{ from: NOW - 600e3, to: NOW, kind: "timer" }]);
assert.strictEqual(live.timer.total, 600);
assert.strictEqual(live.attend.total, 400 + 60);
const byTask = R.projectRecord({ windowStart: iso(0), human: { running: { taskId: "t1", startAt: iso(36000 - 30) } } }, runs, "p1", (t) => (t === "t1" ? "p1" : null), NOW);
assert.strictEqual(byTask.timer.total, 30);
console.log("hex-record.test.js ok");
