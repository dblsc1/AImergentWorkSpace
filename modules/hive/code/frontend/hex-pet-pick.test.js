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
// 固定位：稳定、不跳、新来的占最小空位
{
  const l = [{ runId: "b", startAt: "2026-11-10T10:00:00Z" }, { runId: "a", startAt: "2026-11-10T10:00:00Z" }, { runId: "c", startAt: "2026-11-10T09:00:00Z" }];
  const s1 = P.assignSlots({}, l);
  assert.deepStrictEqual(s1, { c: 0, a: 1, b: 2 });
  assert.deepStrictEqual(P.assignSlots(s1, l.slice().reverse()), s1);                 // 重排不动
  const s2 = P.assignSlots(s1, l.filter((r) => r.runId !== "c"));                      // c 走了，a b 不动
  assert.deepStrictEqual(s2, { a: 1, b: 2 });
  assert.strictEqual(P.assignSlots(s2, l.filter((r) => r.runId !== "c").concat([{ runId: "d", startAt: "2026-11-10T11:00:00Z" }])).d, 0);   // d 占空出来的 0 位
  const rect = { x: 0, y: 0, w: 300, h: 100 };
  assert.deepStrictEqual(P.slotPos(0, rect), { x: 50, y: 25 }); assert.deepStrictEqual(P.slotPos(4, rect), { x: 150, y: 75 });
}
// 动作节奏：15 秒 → 3 秒临时 → 15 秒新动作
{
  const mv = { moods: { working: ["w1", "w2", "w3"], idle: ["i1"], click: ["c1", "c2"], happy: ["h"] } }.moods;
  const r0 = () => 0.5;
  let s = P.rhythm(null, mv, "working", 1000, () => 0);      // 首次偏移 0
  assert.strictEqual(s.step, "main"); assert.strictEqual(s.until, 1000); assert.ok(["w1", "w2", "w3"].includes(s.file));
  s = P.rhythm(s, mv, "working", 1000, r0);                   // 到点 → 临时
  assert.strictEqual(s.step, "temp"); assert.ok(["c1", "c2"].includes(s.file)); assert.strictEqual(s.until, 4000);
  const prev = s.main;
  assert.strictEqual(P.rhythm(s, mv, "working", 3999, r0), s);
  for (let i = 0; i < 30; i++) {
    const n = P.rhythm(s, mv, "working", 4000, Math.random);
    assert.strictEqual(n.step, "main"); assert.notStrictEqual(n.file, prev); assert.strictEqual(n.until, 4000 + P.MAIN_MS);
  }
  const m = P.rhythm(P.rhythm(null, mv, "working", 0, r0), mv, "waiting", 500, r0);  // 换 mood 立刻换、无 waiting 用 idle
  assert.strictEqual(m.file, "i1"); assert.strictEqual(m.until, 500 + P.MAIN_MS);
  const hp = P.rhythm({ mood: "idle", step: "main", file: "i1", main: "i1", until: 0 }, { idle: ["i1"], happy: ["h"] }, "idle", 0, r0);
  assert.strictEqual(hp.file, "h");                           // 没有 click 退到 happy
  const nt = P.rhythm({ mood: "idle", step: "main", file: "i1", main: "i1", until: 0 }, { idle: ["i1"] }, "idle", 0, r0);
  assert.strictEqual(nt.step, "main"); assert.strictEqual(nt.file, "i1");   // 没有临时动作：跳过
  assert.strictEqual(P.rhythm(null, {}, "idle", 0, r0).file, null);          // 包没下完：空，之后补
  assert.strictEqual(P.rhythm(P.rhythm(null, {}, "idle", 0, r0), { idle: ["z"] }, "idle", 1, r0).file, "z");
}
// 点 / 拖判定、挑点击、走回（hive.stage.v1「二」7）
assert.strictEqual(P.pressKind(3, 3), "click"); assert.strictEqual(P.pressKind(5, 0), "drag"); assert.strictEqual(P.pressKind(0, 0), "click");
assert.strictEqual(P.clickCounts(99), false); assert.strictEqual(P.clickCounts(100), true); assert.strictEqual(P.clickCounts(-1), true); assert.strictEqual(P.clickCounts(Infinity), true);
const cpack = { moods: { click: ["a", "b", "c"], happy: ["h"] } };
for (let i = 0; i < 50; i++) assert.notStrictEqual(P.clickClip(cpack, "b", Math.random), "b");
assert.strictEqual(P.clickClip({ moods: { click: ["a"] } }, "a", Math.random), "a");
assert.strictEqual(P.clickClip({ moods: { click: [], happy: ["h"] } }, null, Math.random), "h");
assert.strictEqual(P.clickClip({ moods: { happy: ["h"] } }, "h", () => 0.99), "h");
assert.strictEqual(P.clickClip({ moods: {} }, null, Math.random), null);
assert.strictEqual(P.clickClip(null, null, Math.random), null);
assert.strictEqual(P.clickClip(cpack, "a", () => 0), "b"); assert.strictEqual(P.clickClip(cpack, "a", () => 0.99), "c");
const A = { x: 0, y: 0 }, B = { x: 90, y: 0 };
let wk = P.walkBack(A, B, 2999); assert.strictEqual(wk.phase, "stay"); assert.strictEqual(wk.x, 0);
wk = P.walkBack(A, B, 4000); assert.strictEqual(wk.phase, "walk"); assert.ok(Math.abs(wk.x - 45) < 1e-9); assert.strictEqual(wk.flip, true);   // 45px/s
assert.strictEqual(P.walkBack(A, B, 5000).phase, "done"); assert.strictEqual(P.walkBack(A, B, 5000).x, 90);
assert.strictEqual(P.walkBack(B, A, 4000).flip, false);
const far = { x: 0, y: 1000 };                                                   // 远了加速：8 秒到
assert.strictEqual(P.walkBack(A, far, 3000 + 7999).phase, "walk"); assert.strictEqual(P.walkBack(A, far, 3000 + 8000).phase, "done");
assert.ok(Math.abs(P.walkBack(A, far, 3000 + 4000).y - 500) < 1e-9);
assert.strictEqual(P.walkBack(A, A, 0).phase, "done");
// 大六边形边界距离（hive.stage.v1「十二」4）：正左右 = 边心距，正上下 = 外接圆半径，斜 30° 处介于两者之间
const W = 400, apothem = 200, R6 = 200 / (Math.sqrt(3) / 2);
assert.ok(Math.abs(P.hexEdgeDistance(W, 1, 0) - apothem) < 1e-9);
assert.ok(Math.abs(P.hexEdgeDistance(W, -1, 0) - apothem) < 1e-9);
assert.ok(Math.abs(P.hexEdgeDistance(W, 0, -1) - R6) < 1e-9);
assert.ok(Math.abs(P.hexEdgeDistance(W, 0, 1) - R6) < 1e-9);
assert.ok(Math.abs(P.hexEdgeDistance(W, Math.cos(Math.PI / 3), Math.sin(Math.PI / 3)) - apothem) < 1e-9);
console.log("hex-pet-pick tests ok");

// 干活的小人平时站在格子里：右上 → 右下 → 左下 → 左上，多的离格心一半（「二十二」1）
assert.deepStrictEqual([0, 1, 2, 3].map((i) => { const s = P.restSlot(i, 100, 100); return [Math.sign(s.x), Math.sign(s.y)]; }), [[1, -1], [1, 1], [-1, 1], [-1, -1]]);
assert.deepStrictEqual(P.restSlot(0, 100, 200), { x: 20, y: -40 });
assert.deepStrictEqual(P.restSlot(4, 100, 200), { x: 10, y: -20 });
