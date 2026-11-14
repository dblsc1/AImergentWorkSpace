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
// 动作节奏（「二十六」）：15 秒主动作 → 3 秒临时 → 回到原来的主动作；主动作满 5 分钟才换
{
  const mv = { moods: { working: ["w1", "w2", "w3"], idle: ["i1"], click: ["c1", "c2"], happy: ["h"] } }.moods;
  const r0 = () => 0.5;
  let s = P.rhythm(null, mv, "working", 1000, () => 0);      // 首次偏移 0
  assert.strictEqual(s.step, "main"); assert.strictEqual(s.until, 1000); assert.ok(["w1", "w2", "w3"].includes(s.file));
  const first = s.file;
  s = P.rhythm(s, mv, "working", 1000, r0);                   // 到点 → 临时
  assert.strictEqual(s.step, "temp"); assert.ok(["c1", "c2"].includes(s.file)); assert.strictEqual(s.until, 4000);
  assert.strictEqual(P.rhythm(s, mv, "working", 3999, r0), s);
  for (let i = 0; i < 30; i++) {                               // 临时动作完：回到原来的主动作
    const n = P.rhythm(s, mv, "working", 4000, Math.random);
    assert.strictEqual(n.step, "main"); assert.strictEqual(n.file, first); assert.strictEqual(n.until, 4000 + P.MAIN_MS);
  }
  let t = 1000, seen = new Set();                              // 走满 5 分钟：一直是同一个主动作，之后换成别的
  while (t < 1000 + P.KEEP_MS) { s = P.rhythm(s, mv, "working", t, r0); if (s.step === "main") seen.add(s.file); t += 1000; }
  assert.deepStrictEqual([...seen], [first]);
  while (s.step !== "main" || s.file === first) { s = P.rhythm(s, mv, "working", t, Math.random); t += 1000; assert.ok(t < 1000 + P.KEEP_MS + 40000); }
  assert.notStrictEqual(s.file, first);
  const m = P.rhythm(P.rhythm(null, mv, "working", 0, r0), mv, "waiting", 500, r0);  // 换 mood 立刻换、无 waiting 用 idle
  assert.strictEqual(m.file, "i1"); assert.strictEqual(m.until, 500 + P.MAIN_MS);
  const hp = P.rhythm({ mood: "idle", step: "main", file: "i1", main: "i1", until: 0, keepUntil: 9e9 }, { idle: ["i1"], happy: ["h"] }, "idle", 0, r0);
  assert.strictEqual(hp.file, "h");                           // 没有 click 退到 happy
  const nt = P.rhythm({ mood: "idle", step: "main", file: "i1", main: "i1", until: 0, keepUntil: 9e9 }, { idle: ["i1"] }, "idle", 0, r0);
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
// 互动（hive.stage.v1「二十一」）：人挥手、小人跃动；对不上取 happy / click 第一段；都没有 = null
const MM = { happy: ["a.webm", "点击回应-开心跃动.webm", "点击回应-元气挥手.webm"], click: ["点击回应-害羞惊讶.webm", "点击回应-元气挥手.webm"] };
assert.strictEqual(P.meetClip(MM, "human"), "点击回应-元气挥手.webm");
assert.strictEqual(P.meetClip(MM, "pet"), "点击回应-开心跃动.webm");
assert.strictEqual(P.meetClip({ happy: ["x.webm"] }, "pet"), "x.webm");
assert.strictEqual(P.meetClip({ click: ["c.webm"] }, "human"), "c.webm");
assert.strictEqual(P.meetClip({ working: ["w.webm"] }, "human"), null);
assert.strictEqual(P.meetClip(null, "pet"), null);
const spotR = P.meetSpot({ x: 100, y: 100 }, { x: 300, y: 50 }, 1);        // 人从右边来：站右边、比小人低、人不翻小人翻
assert.ok(spotR.x > 100 && spotR.y > 100 && spotR.side === 1 && !spotR.eyeFlip && spotR.petFlip);
const spotL = P.meetSpot({ x: 100, y: 100 }, { x: 10, y: 50 }, 1);
assert.ok(spotL.x < 100 && spotL.side === -1 && spotL.eyeFlip && !spotL.petFlip);
assert.ok(Math.abs(P.meetSpot({ x: 0, y: 0 }, { x: 5, y: 0 }, 1.7).x) > Math.abs(spotR.x - 100));   // 小人变大，站得更远
// ── 状态 → 动作（hive.stage.v1「三十二」1）─────────────────────
{
  const T0 = Date.parse("2026-11-15T10:00:00+08:00"), iso = (ms) => new Date(ms).toISOString(), MIN = 60000;
  const ph = (list) => list.map(([min, phase]) => ({ at: iso(T0 + min * MIN), phase, detail: null }));
  const mk2 = (phase, phases, extra) => run("s", Object.assign({ phase, phases: ph(phases), startAt: iso(T0 - 600 * MIN) }, extra));
  // 最后干活结束时刻：最后一个 working 之后第一个相位；还在干 = null；没干过 = 开始时刻；折叠行的 lastWorkAt 优先于开始
  assert.strictEqual(P.lastWorkedAt(mk2("idle", [[0, "working"], [5, "idle"]])), T0 + 5 * MIN);
  assert.strictEqual(P.lastWorkedAt(mk2("idle", [[0, "working"], [5, "waiting_input"], [6, "working"], [9, "idle"], [12, "idle"]])), T0 + 9 * MIN);
  assert.strictEqual(P.lastWorkedAt(mk2("working", [[0, "idle"], [5, "working"]])), null);
  assert.strictEqual(P.lastWorkedAt(mk2("idle", [[3, "idle"]])), T0 - 600 * MIN);
  assert.strictEqual(P.lastWorkedAt(mk2("idle", [], { lastWorkAt: iso(T0 - 7 * MIN) })), T0 - 7 * MIN);
  assert.strictEqual(P.lastWorkedAt(mk2("idle", [[5, "idle"], [0, "working"]])), T0 + 5 * MIN);          // 乱序也按时刻排
  // 干活 / 等人 / 出问题
  const st = (r, min, watched) => P.stateOf(r, T0 + min * MIN, watched);
  assert.strictEqual(st(mk2("working", [[0, "working"]]), 99).mood, "working");
  assert.strictEqual(st(mk2("waiting_input", [[0, "working"], [1, "waiting_input"]]), 99).mood, "waiting");
  assert.strictEqual(st(mk2("error", [[0, "working"], [1, "error"]]), 1).mood, "worried");
  assert.strictEqual(st(mk2("waiting_permission", [[0, "working"], [1, "waiting_permission"]]), 1).mood, "worried");
  assert.strictEqual(P.stateOf(mk2("working", [[0, "working"]], { lost: true }), T0, null).mood, "idle");   // 失联照旧 idle
  // 对话中：压过干活 / 等人 / 空闲，不压出问题；只认被看着的那条
  assert.strictEqual(st(mk2("working", [[0, "working"]]), 1, "s").mood, "chatting");
  assert.strictEqual(st(mk2("waiting_input", [[0, "working"], [1, "waiting_input"]]), 5, "s").mood, "chatting");
  assert.strictEqual(st(mk2("idle", [[0, "working"], [1, "idle"]]), 90, "s").mood, "chatting");
  assert.strictEqual(st(mk2("idle", [[0, "working"], [1, "idle"]]), 90, "s").home, false);                // 被叫醒：不再飞回
  assert.strictEqual(st(mk2("error", [[0, "error"]]), 1, "s").mood, "worried");
  assert.strictEqual(st(mk2("working", [[0, "working"]]), 1, "other").mood, "working");
  // 空闲分档：从最后一次干活结束（第 1 分钟）算起 <1 / 1–5 / 5–30 / 30–60 / ≥60 分钟
  const idleRun = mk2("idle", [[0, "working"], [1, "idle"]]);
  [[1.5, "idle", false], [1.99, "idle", false], [2, "idle-bored", false], [5.99, "idle-bored", false], [6, "idle-slacking", false], [30.99, "idle-slacking", false], [31, "idle-sleeping", false],
    [60.99, "idle-sleeping", false], [61, "idle-sleeping", true], [600, "idle-sleeping", true]].forEach(([min, mood, home]) => {
    const r = st(idleRun, min);
    assert.strictEqual(r.mood, mood, "min " + min); assert.strictEqual(r.home, home, "home " + min);
  });
  // 没干过活：从运行开始算（开始在 600 分钟前 → 睡着在家）
  assert.deepStrictEqual([st(mk2("idle", []), 0.5).mood, st(mk2("idle", []), 0.5).home], ["idle-sleeping", true]);
  assert.strictEqual(st(mk2("idle", [], { startAt: iso(T0 - 30 * 1000) }), 0).mood, "idle");
  // 满 1 小时的从格子里走掉、进左上角那一排；没满的留在格子里
  const sleeper = mk2("idle", [[0, "working"], [1, "idle"]], { projectId: "p1" }), napper = Object.assign({}, sleeper, { runId: "n", phases: ph([[0, "working"], [40, "idle"]]) });
  const o = { now: T0 + 70 * MIN };
  assert.deepStrictEqual(P.pickWorkers([sleeper, napper], o).pets.map((p) => [p.runId, p.mood]), [["n", "idle-sleeping"]]);
  assert.deepStrictEqual(P.pickIdle([sleeper, napper], o).pets.map((p) => [p.runId, p.mood]), [["s", "idle-sleeping"]]);
  assert.deepStrictEqual(P.pickWorkers([sleeper], { now: T0 + 70 * MIN, watchedId: "s" }).pets.map((p) => p.mood), ["chatting"]);   // 对话中不在家
  assert.strictEqual(P.pickWorkers([sleeper]).pets[0].mood, "idle");                                    // 不带 now = 旧口径
  // 折叠掉的在跑运行补回来：按 agent + label 取最后干活时刻；已在 lanes 里的不重复
  const cur = [{ runId: "s", agent: "codex", tool: "codex", label: "a", taskId: null, startedAt: iso(T0), phase: "idle" },
    { runId: "f", agent: "codex", tool: "codex", model: null, label: "b", taskId: "t1", startedAt: iso(T0 - 300 * MIN), phase: "idle", elapsedSeconds: 9 },
    { runId: "g", agent: "claude-code", tool: "claude-code", label: null, taskId: null, startedAt: iso(T0 - 100 * MIN), phase: "error" }];
  const inactive = [{ agent: "codex", label: "b", reason: "idle", lastWorkAt: iso(T0 - 90 * MIN) }, { agent: "codex", label: "a", reason: "idle", lastWorkAt: iso(T0 - 1 * MIN) }];
  const all = P.withCurrentRuns([sleeper], cur, inactive);
  assert.deepStrictEqual(all.map((r) => r.runId), ["s", "f", "g"]);
  assert.strictEqual(all[1].taskId, "t1"); assert.strictEqual(all[1].lastWorkAt, iso(T0 - 90 * MIN));
  assert.strictEqual(all[2].lastWorkAt, null); assert.strictEqual(all[2].phase, "error");
  assert.strictEqual(P.stateOf(all[1], T0, null).home, true);                                              // 折叠行说它 90 分钟前就不干了
  assert.strictEqual(P.stateOf(all[2], T0, null).mood, "worried");
  assert.deepStrictEqual(P.withCurrentRuns(null, null, null), []);
  assert.strictEqual(P.withCurrentRuns([], [{ runId: "z", agent: "a", startedAt: iso(T0) }], [])[0].phase, "idle");
  // 包里没有新键的回退
  const old = { working: ["w"], waiting: ["wt"], idle: ["i1", "原地小憩沉眠.webm"], happy: ["h"], click: ["c"] };
  assert.deepStrictEqual(P.moodList(old, "chatting"), ["h"]);
  assert.deepStrictEqual(P.moodList({ idle: ["i"], click: ["c"] }, "chatting"), ["c"]);
  assert.deepStrictEqual(P.moodList(old, "idle-bored"), ["wt"]);
  assert.deepStrictEqual(P.moodList(old, "idle-slacking"), ["i1", "原地小憩沉眠.webm"]);
  assert.deepStrictEqual(P.moodList(old, "idle-sleeping"), ["原地小憩沉眠.webm"]);
  assert.deepStrictEqual(P.moodList({ idle: ["i"] }, "idle-sleeping"), ["i"]);
  assert.deepStrictEqual(P.moodList({ idle: ["i"] }, "worried"), ["i"]);
  assert.deepStrictEqual(P.moodList(Object.assign({ "idle-bored": ["b"], "idle-sleeping": ["z"] }, old), "idle-bored"), ["b"]);       // 有键用键
  assert.deepStrictEqual(P.moodList({}, "chatting"), []);
  // 睡着不插临时动作；其他 mood 照旧
  const sm = { "idle-sleeping": ["z"], idle: ["i"], click: ["c"] };
  let sl = P.rhythm(null, sm, "idle-sleeping", 0, () => 0);
  for (let t = 1000; t < 120000; t += 1000) { sl = P.rhythm(sl, sm, "idle-sleeping", t, () => 0); assert.strictEqual(sl.file, "z"); assert.strictEqual(sl.step, "main"); }
  let aw = P.rhythm(null, sm, "idle-bored", 0, () => 0), sawTemp = false;
  for (let t = 1000; t < 60000; t += 1000) { aw = P.rhythm(aw, sm, "idle-bored", t, () => 0); if (aw.step === "temp") sawTemp = true; }
  assert.ok(sawTemp);
  // 换状态立刻换动作
  assert.strictEqual(P.rhythm(P.rhythm(null, { working: ["w"], "idle-sleeping": ["z"], idle: ["i"] }, "working", 0, () => 0), { working: ["w"], "idle-sleeping": ["z"], idle: ["i"] }, "idle-sleeping", 10, () => 0).file, "z");
}
console.log("hex-pet-pick tests ok");

// 干活的小人平时站在格子里：右上 → 右下 → 左下 → 左上，多的离格心一半（「二十二」1）
assert.deepStrictEqual([0, 1, 2, 3].map((i) => { const s = P.restSlot(i, 100, 100); return [Math.sign(s.x), Math.sign(s.y)]; }), [[1, -1], [1, 1], [-1, 1], [-1, -1]]);
assert.deepStrictEqual(P.restSlot(0, 100, 200), { x: 20, y: -40 });
assert.deepStrictEqual(P.restSlot(4, 100, 200), { x: 10, y: -20 });
