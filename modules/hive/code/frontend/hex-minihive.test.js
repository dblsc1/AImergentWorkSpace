// hex-minihive.js 的纯逻辑：目标解析（含目标已不存在）、每种操作的标记、小蜂巢几何、拖拽停留计时。node hex-minihive.test.js
"use strict";
const assert = require("assert");
const M = require("./hex-minihive.js");

const tree = {
  zones: [{ id: "z_a", name: "学习" }, { id: "z_b", name: "制作" }],
  projects: [
    { id: "p_math", zoneId: "z_a", name: "数学", unclassifiedTaskId: "t_unc_p_math", tasks: [{ id: "t1", name: "刷题", done: false }, { id: "t2", name: "错题", done: true }] },
    { id: "p_studio", zoneId: "z_b", name: "录音棚", tasks: [] },
  ],
};
const R = (slot) => M.resolveSlot(tree, slot);

// 目标解析：在 = 不缺；不在 = missing；没指定位置 = 不缺
assert.strictEqual(R({ op: "create", kind: "task", tProject: "p_math" }).missing, false);
assert.strictEqual(R({ op: "create", kind: "task", tProject: "p_gone" }).missing, true, "新建任务：项目没了");
assert.strictEqual(R({ op: "create", kind: "project", tZone: "z_gone" }).missing, true, "新建项目：分区没了");
assert.strictEqual(R({ op: "create", kind: "project" }).missing, false, "没指定分区不算缺");
assert.strictEqual(R({ op: "delete", kind: "task", tProject: "p_math", tTask: "t1" }).missing, false);
assert.strictEqual(R({ op: "delete", kind: "task", tProject: "p_math", tTask: "t_gone" }).missing, true, "删除：任务没了");
assert.strictEqual(R({ op: "delete", kind: "project", tProject: "p_gone" }).missing, true);
assert.strictEqual(R({ op: "rename", kind: "project", tProject: "p_gone" }).missing, true);
assert.strictEqual(R({ op: "block", tTask: "t_unc_p_math", tProject: "p_math" }).missing, false, "未分类任务一直在");
assert.strictEqual(R({ op: "block", tTask: "t_gone" }).missing, true, "只有任务 id：全树里找不到");
assert.strictEqual(R({ op: "block", tTask: "t1" }).missing, false, "只有任务 id：全树里找得到");
assert.strictEqual(R({ op: "windowRule", kind: "window", block: "x" }).missing, false, "窗口规则没有目标");
// 更改：目的地和原位置哪个没了都算缺；改挂（session）只看目的地
assert.strictEqual(R({ op: "change", kind: "task", oProject: "p_math", oTask: "t1", tProject: "p_studio" }).missing, false);
assert.strictEqual(R({ op: "change", kind: "task", oProject: "p_math", oTask: "t_gone", tProject: "p_studio" }).missing, true, "原位置没了");
assert.strictEqual(R({ op: "change", kind: "task", oProject: "p_math", oTask: "t1", tProject: "p_gone" }).missing, true, "目的地没了");
assert.strictEqual(R({ op: "change", kind: "project", oProject: "p_math", tZone: "z_gone" }).missing, true);
assert.strictEqual(R({ op: "session", kind: "task", oProject: "p_gone", tProject: "p_math", tTask: "t1" }).missing, false, "改挂：原位置没了不挡");
assert.strictEqual(R({ op: "session", kind: "task", tProject: "p_math", tTask: "t_gone" }).missing, true);
assert.strictEqual(M.resolveSlot(null, { op: "delete", tProject: "x" }).missing, false, "树还没读到：不乱标");

// 标记种类
const types = { create: "create", delete: "delete", change: "change", session: "change", rename: "hl", block: "hl", blockRule: "hl", windowRule: "none", teleport: "hl" };
Object.keys(types).forEach((op) => assert.strictEqual(M.markType(op), types[op], op));

// 标记计划：缩放目标、框的种类、连线的原位置
const P = (slot) => M.plan(slot, R(slot));
let p = P({ op: "create", kind: "task", name: "新", tProject: "p_math" });
assert.deepStrictEqual([p.style, p.dest.level, p.zoom, p.origin], ["frame", "newTask", "p_math", null]);
p = P({ op: "create", kind: "project", tZone: "z_a" });
assert.deepStrictEqual([p.style, p.dest.level, p.zoom, p.ghost], ["frame", "newProject", null, true]);
p = P({ op: "change", kind: "task", oProject: "p_math", oTask: "t1", tProject: "p_studio" });
assert.deepStrictEqual([p.style, p.dest.level, p.zoom, p.origin.level, p.origin.taskId], ["frame", "newTask", "p_studio", "task", "t1"], "移任务：推近目的项目、连回原任务");
p = P({ op: "change", kind: "project", oProject: "p_math", tZone: "z_b" });
assert.deepStrictEqual([p.style, p.dest.level, p.zoom, p.origin.level], ["frame", "zone", null, "project"], "移项目：不推近");
p = P({ op: "delete", kind: "task", tProject: "p_math", tTask: "t1" });
assert.deepStrictEqual([p.style, p.dest.level, p.zoom], ["del", "task", "p_math"]);
p = P({ op: "delete", kind: "project", tProject: "p_math" });
assert.deepStrictEqual([p.style, p.dest.level, p.zoom], ["del", "project", null]);
p = P({ op: "blockRule", kind: "task", tProject: "p_math", tTask: "t1" });
assert.deepStrictEqual([p.style, p.dest.level, p.zoom], ["hl", "task", "p_math"]);
p = P({ op: "session", kind: "task", oProject: "p_math", oTask: "t1", tProject: "p_math", tTask: "t_unc_p_math" });
assert.deepStrictEqual([p.dest.taskId, p.origin.taskId], ["t_unc_p_math", "t1"]);
assert.strictEqual(P({ op: "windowRule", block: "x" }), null, "窗口规则不标");
assert.strictEqual(P({ op: "delete", kind: "task", tProject: "p_math", tTask: "gone" }), null, "目标没了不标");

// dataset → slot
assert.deepStrictEqual(M.slotFromDataset({ op: "create", targetProjectId: "p_math", originTaskId: "t1", name: "x", other: 1 }), { op: "create", tProject: "p_math", oTask: "t1", name: "x" });

// 小蜂巢几何：同一套晶格，整张进 vb，正方形
const cells = [{ q: 0, r: 0, zoneId: "z_a", project: { id: "p_math" } }, { q: 1, r: 0, zoneId: "z_a", project: { id: "p_x" } }, { q: 0, r: 1, zoneId: "z_b", project: null }, { q: 5, r: 5, __sprout: true }];
const g = M.geom(cells, 50, 88);
assert.strictEqual(g.cells.length, 3, "幽灵格不画");
assert.ok(Math.abs(g.cells[1].cx - Math.sqrt(3) * 50) < 1e-9 && g.cells[1].cy === 0, "相邻格距 √3·S");
assert.ok(Math.abs(g.cells[2].cx - Math.sqrt(3) * 25) < 1e-9 && g.cells[2].cy === 75);
assert.strictEqual(g.vb[2], g.vb[3], "视口是正方形");
g.cells.forEach((c) => assert.ok(c.cx - 43 >= g.vb[0] - 1e-6 && c.cx + 43 <= g.vb[0] + g.vb[2] + 1e-6 && c.cy - 50 >= g.vb[1] - 1e-6 && c.cy + 50 <= g.vb[1] + g.vb[3] + 1e-6), "每格都在视口里");
assert.ok(Math.abs(g.cellPx - 88 / g.vb[2] * Math.sqrt(3) * 50) < 1e-9);
assert.strictEqual(g.cells[2].empty, true);
assert.deepStrictEqual(M.geom([], 50, 88).vb, [-100, -100, 200, 200], "空蜂巢不抛");
assert.strictEqual(M.hexPoints(0, 0, 10).split(" ").length, 6);
assert.deepStrictEqual(M.taskDots(0, 0, 50, 1), [{ x: 0, y: 0 }]);
assert.strictEqual(M.taskDots(0, 0, 50, 5).length, 5);
assert.strictEqual(M.taskDots(0, 0, 50, 40).length, M.MAX_DOTS, "点数封顶");
assert.deepStrictEqual(M.taskDots(0, 0, 50, 0), []);
// 标记落点：项目 -> 格心；任务 -> 点（格够大）/ 格心（太小）；分区 -> 各格；新项目 -> 分区里的空位
const big = M.geom(cells, 50, 600);
assert.deepStrictEqual([M.pointOf(big, tree, { level: "project", projectId: "p_math" }, cells).kind, M.pointOf(big, tree, { level: "project", projectId: "p_gone" }, cells)], ["cell", null]);
assert.strictEqual(M.pointOf(big, tree, { level: "task", projectId: "p_math", taskId: "t1" }, cells).kind, "dot");
assert.strictEqual(M.pointOf(M.geom(cells, 50, 20), tree, { level: "task", projectId: "p_math", taskId: "t1" }, cells).kind, "cell", "格太小：退回整格");
assert.strictEqual(M.pointOf(big, tree, { level: "task", projectId: "p_math", taskId: "t2" }, cells).kind, "cell", "已完成的任务不画点");
assert.strictEqual(M.pointOf(big, tree, { level: "zone", zoneId: "z_a" }, cells).cells.length, 2);
const fs = M.freeSlot(cells, "z_a");
assert.ok(fs && !cells.some((c) => c.q === fs.q && c.r === fs.r) && !(fs.q === 0 && fs.r === 0));
assert.strictEqual(M.freeSlot(cells, "z_none"), null);
assert.strictEqual(M.pointOf(big, tree, { level: "newProject", zoneId: "z_a" }, cells).kind, "slot");

// 停留计时：同一处停满才触发一次；挪出 slop / 换 key 重新数；stop 取消
let now = 0;
const timers = [], fired = [];
const D = M.Dwell({ ms: 2000, slop: 10, set: (f, ms) => { const t = { f, at: now + ms, on: true }; timers.push(t); return t; }, clear: (t) => { if (t) t.on = false; }, fire: (k) => fired.push(k) });
const tick = (ms) => { now += ms; timers.filter((t) => t.on && t.at <= now).forEach((t) => { t.on = false; t.f(); }); };
D.at("a", 100, 100); tick(1999); assert.deepStrictEqual(fired, [], "不到 2 秒不触发");
D.at("a", 104, 103); tick(1); assert.deepStrictEqual(fired, ["a"], "slop 内的小动不重数，满 2 秒触发");
D.at("a", 104, 103); tick(5000); assert.deepStrictEqual(fired, ["a"], "触发过不再重复");
D.at("a", 130, 100); tick(1999); assert.strictEqual(fired.length, 1, "挪出 slop 重新数"); tick(1); assert.strictEqual(fired.length, 2);
D.at("b", 130, 100); tick(1000); D.at("c", 130, 100); tick(1999); assert.strictEqual(fired.length, 2, "换了 key 重新数"); tick(1); assert.deepStrictEqual(fired.slice(2), ["c"]);
D.at("d", 0, 0); D.stop(); tick(5000); assert.strictEqual(fired.length, 3, "stop 取消");
D.at("e", 0, 0); D.at(null, 0, 0); tick(5000); assert.strictEqual(fired.length, 3, "key = null 等于停");
console.log("hex-minihive.test.js ok");
