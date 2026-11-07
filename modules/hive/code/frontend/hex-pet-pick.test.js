// hex-pet-pick.js 纯逻辑：挑 4 只 + +N、包对照、mood、左上角筛选与走动。node hex-pet-pick.test.js
"use strict";
const assert = require("assert");
const P = require("./hex-pet-pick.js");

const run = (id, extra) => Object.assign({ runId: id, agent: "garden", tool: "codex", projectId: "p1", phase: "working", startAt: "2026-11-07T08:00:00+08:00", lastSeenAt: null }, extra);
const mk = (n) => Array.from({ length: n }, (_, i) => run("r" + i, { lastSeenAt: "2026-11-07T09:0" + i + ":00+08:00" }));

// HV-P01..P04, P07
[[3, 3, 0], [4, 4, 0], [5, 4, 1], [9, 4, 5], [0, 0, 0]].forEach(([n, pets, plus]) => {
  const r = P.pickWorkers(mk(n));
  assert.strictEqual(r.pets.length, pets); assert.strictEqual(r.plus, plus);
});
// HV-P05 最近 4 条，去掉最久的两条
assert.deepStrictEqual(P.pickWorkers(mk(6)).pets.map((p) => p.runId), ["r5", "r4", "r3", "r2"]);
// lastSeenAt 为 null 取 startAt
assert.deepStrictEqual(P.pickWorkers([run("a", { startAt: "2026-11-07T07:00:00+08:00" }), run("b", { lastSeenAt: "2026-11-07T07:30:00+08:00" })]).pets.map((p) => p.runId), ["b", "a"]);
// HV-P06 并列按 runId 升序
const tie = ["r3", "r1", "r2"].map((id) => run(id, { lastSeenAt: "2026-11-07T09:00:00+08:00" }));
assert.deepStrictEqual(P.pickWorkers(tie).pets.map((p) => p.runId), ["r1", "r2", "r3"]);
// HV-P08 挂 taskId 的运行定位到任务所在项目
const w = P.pickWorkers([run("t", { projectId: null, taskId: "t9" })], { projectOfTask: (t) => (t === "t9" ? "p1" : null) });
assert.strictEqual(w.pets[0].projectId, "p1");
// 没挂目标的不进格子边；已结束的不算
assert.strictEqual(P.pickWorkers([run("x", { projectId: null }), run("y", { endAt: "2026-11-07T10:00:00+08:00" })]).pets.length, 0);
// HV-P09
["working", "waiting_input", "waiting_permission", "idle", "error", undefined].forEach((ph, i) =>
  assert.strictEqual(P.moodOf(ph), ["working", "waiting", "waiting", "idle", "idle", "idle"][i]));
assert.strictEqual(P.moodOf("working", true), "idle"); // 失联
// HV-P10..P12
const packs = { "claude-code": "x", "*": "dsh" };
assert.strictEqual(P.packOf({ tool: "claude-code" }, packs), "x");
assert.strictEqual(P.packOf({ tool: "codex" }, packs), "dsh");
assert.strictEqual(P.packOf({ tool: "codex" }, { "claude-code": "x" }), "deepseek");
assert.strictEqual(P.packOf({ tool: "codex" }, null), "gpt");
assert.strictEqual(P.packOf({ tool: "claude-code" }, null), "claude");
assert.strictEqual(P.packOf({ tool: "aider" }, null), "deepseek");
// HV-P13 名牌
const pet = P.pickWorkers([run("n", { label: "整理" })]).pets[0];
assert.strictEqual(pet.name, "整理");
assert.strictEqual(P.pickWorkers([run("n")]).pets[0].name, "garden");
assert.deepStrictEqual(Object.keys(pet).sort(), ["mood", "name", "pack", "projectId", "runId"]);
// HV-P14 / P15 / P16
const idle = P.pickIdle([run("a", { projectId: null }), run("b"), run("c", { projectId: null, taskId: "t1" }), run("d", { projectId: null, endAt: "2026-11-07T10:00:00+08:00" })]);
assert.deepStrictEqual(idle.pets.map((p) => p.runId), ["a"]);
const six = P.pickIdle(Array.from({ length: 6 }, (_, i) => run("i" + i, { projectId: null })));
assert.strictEqual(six.pets.length, 4); assert.strictEqual(six.plus, 2);
// 收件箱不是格子：挂 p_inbox（项目或收件箱里的任务）的算左上角
const inboxOpts = { projectOfTask: (id) => (id === "ti" ? "p_inbox" : "p1") };
const mixed = [run("w", { projectId: "p1" }), run("x", { projectId: "p_inbox" }), run("y", { projectId: null, taskId: "ti" }), run("z", { projectId: null, taskId: "tp" })];
assert.deepStrictEqual(P.pickWorkers(mixed, inboxOpts).pets.map((p) => p.runId).sort(), ["w", "z"]);
assert.deepStrictEqual(P.pickIdle(mixed, inboxOpts).pets.map((p) => p.runId).sort(), ["x", "y"]);
// HV-P17 走动 600 步不出区域，每步位移 ≤ 速度 × 时间
let seed = 12345; const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
const rect = { x: 0, y: 0, w: 200, h: 120 };
let s = P.initWander(rect, rnd);
for (let i = 0; i < 600; i++) {
  const n = P.stepWander(s, rect, rnd, 50, 30);
  assert.ok(n.x >= rect.x && n.x <= rect.x + rect.w && n.y >= rect.y && n.y <= rect.y + rect.h);
  assert.ok(Math.hypot(n.x - s.x, n.y - s.y) <= 30 * 50 / 1000 + 1e-9);
  s = n;
}
console.log("hex-pet-pick tests ok");
