// hex-onboard.js 的纯逻辑：显示条件、分区 / 项目 / 任务的硬限制、自动 claim 的挑选与去重、时长文案。node hex-onboard.test.js
"use strict";
const assert = require("assert");
const O = require("./hex-onboard.js");

// HV-O01–O04：显示条件
const none = { zones: [], projects: [] }, some = { zones: [{ id: "z1" }], projects: [] };
assert.strictEqual(O.shouldShowOnboarding({ onboardingDoneAt: null }, none), true, "O01");
assert.strictEqual(O.shouldShowOnboarding({ onboardingDoneAt: null }, some), false, "O02 老账号");
assert.strictEqual(O.shouldShowOnboarding({ onboardingDoneAt: "2026-11-08T00:00:00Z" }, none), false, "O03");
assert.strictEqual(O.shouldShowOnboarding(null, none), false, "O04 设置读不到");
assert.strictEqual(O.shouldShowOnboarding({ onboardingDoneAt: null }, null), false, "O04 树读不到");
assert.strictEqual(O.shouldShowOnboarding({}, none), false, "设置里没有这个键不算 null");

// HV-O05 / O06：硬限制
let d = O.newDraft();
assert.strictEqual(O.canNext(d), false, "没分区不能进下一步");
d = O.addZone(d, { id: "z1", name: "学习" });
assert.strictEqual(O.canAddZone(d), true);
assert.strictEqual(O.canNext(d), true);
d = O.addZone(d, { id: "z2", name: "制作" });
assert.strictEqual(O.canAddZone(d), false, "第 3 个分区被拒");
assert.strictEqual(O.addZone(d, { id: "z3", name: "x" }).zones.length, 2, "函数本身也不放行");
d = Object.assign({}, d, { step: 2 });
assert.strictEqual(O.canNext(d), false, "分区没有项目");
d = O.addProject(d, "z1", { id: "p1", name: "数学" });
d = O.addProject(d, "z1", { id: "p2", name: "钢琴" });
assert.strictEqual(O.canAddProject(d, "z1"), false, "每分区至多 2 个项目");
assert.strictEqual(O.canAddProject(d, "z2"), true);
assert.strictEqual(O.addProject(d, "z1", { id: "p3", name: "x" }).zones[0].projects.length, 2);
d = O.addProject(d, "z2", { id: "p4", name: "录音" });
assert.strictEqual(O.canNext(d), false, "项目没有任务（任务数 0）");
d = O.addTask(O.addTask(O.addTask(d, "p1", { id: "t1", name: "刷题" }), "p2", { id: "t2", name: "练琴" }), "p4", { id: "t4", name: "混音" });
assert.strictEqual(O.canNext(d), true);
assert.deepStrictEqual(O.targetsOf(d).map((x) => x.projectId + ":" + x.taskIds), ["p1:t1", "p2:t2", "p4:t4"]);
assert.strictEqual(O.newDraft().zones.length, 0, "newDraft 互不共享");

// HV-O08–O10：挑选
const it = (k, match, seconds) => ({ blockKey: k, displayName: "名" + k, match, seconds });
const pick = (r) => O.pickAutoClaims(r);
assert.deepStrictEqual(pick([{ target: { taskId: "t1" }, items: [it("a", "agent", 600), it("b", "name", 600)] }]),
  [{ taskId: "t1", blockKeys: ["a", "b"], names: { a: "名a", b: "名b" } }], "O08 恰好 600 也选");
assert.deepStrictEqual(pick([{ target: { taskId: "t1" }, items: [it("a", "name", 599), it("b", "ai", 5000), it("c", "agent", 599)] }]), [], "O09");
const dup = pick([{ target: { taskId: "t1" }, items: [it("a", "agent", 900)] }, { target: { projectId: "p1" }, items: [it("a", "agent", 900), it("b", "name", 700)] }]);
assert.deepStrictEqual(dup, [{ taskId: "t1", blockKeys: ["a"], names: { a: "名a" } }, { projectId: "p1", blockKeys: ["b"], names: { b: "名b" } }], "O10 去重，先到先得");
assert.deepStrictEqual(pick([{ target: { taskId: "t1" }, items: null }, null]), [], "坏数据不炸");

// 时长文案（onboarding.v1 八·3）
assert.strictEqual(O.hoursText(9000), "2.5 小时");
assert.strictEqual(O.hoursText(7200), "2 小时");
assert.strictEqual(O.hoursText(300), "不到 6 分钟");
assert.strictEqual(O.hoursText(360), "0.1 小时");
console.log("hex-onboard.test.js ok");
