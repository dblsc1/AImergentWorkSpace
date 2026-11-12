// hex-agent-page.js 纯逻辑：滚动窗口、按 agent 分组、段的项目归属、红条区间、排序。node hex-agent-page.test.js
"use strict";
process.env.TZ = "Asia/Shanghai";
const assert = require("assert");
const A = require("./hex-agent-page.js");
const t = (s) => Date.parse("2026-11-12T" + s + "+08:00"), iso = (s) => "2026-11-12T" + s + "+08:00";
const now = t("12:00:00");
const seg = (a, b, p) => ({ start: iso(a), end: iso(b), projectId: p || null, taskId: null });
const run = (id, key, name, segments, o) => Object.assign({ runId: id, laneKey: key, name, agent: "cc", tool: "claude-code", projectId: null, running: false, lost: false, phase: "working", attend: [], segments }, o || {});

// 滚动窗口：往前倒推
let w = A.windowOf("day", 3, now); assert.strictEqual(w.from, now - 24 * 3600000); assert.strictEqual(w.label, "今天");
w = A.windowOf("week", 3, now); assert.strictEqual(w.from, now - 7 * 24 * 3600000); assert.strictEqual(w.label, "最近 7 天");
w = A.windowOf("hours", 3, now); assert.strictEqual(w.from, now - 3 * 3600000); assert.strictEqual(w.label, "最近 3 小时");
assert.strictEqual(A.windowOf("hours", 99, now).hours, 24); assert.strictEqual(A.windowOf("hours", 0, now).hours, 3);

// 同一个 laneKey 并成一组；名字取最近干活的那条
const r1 = run("r1", "k1", "旧名", [seg("09:00:00", "10:00:00", "p_a")]), r2 = run("r2", "k1", "新名", [seg("10:30:00", "11:00:00", "p_b")], { running: true, projectId: "p_b" });
const r3 = run("r3", "k2", "other", [seg("11:00:00", "11:30:00")], { running: true, phase: "waiting_input" });
const groups = A.groupRuns([r2, r1, r3]);
assert.strictEqual(groups.length, 2); assert.deepStrictEqual(groups[0].runs.map((r) => r.runId), ["r1", "r2"]); assert.strictEqual(groups[0].name, "新名");

// 段的颜色归属：每段带自己的项目；没项目 = null
assert.deepStrictEqual(A.segmentsOf(groups[0]).map((s) => s.projectId), ["p_a", "p_b"]);
assert.strictEqual(A.segmentsOf(groups[1])[0].projectId, null);

// 卡片颜色 = 现在所属项目（在跑的最近那条）；没有在跑的取最近开始的
assert.strictEqual(A.currentProject(groups[0]), "p_b");
assert.strictEqual(A.currentProject(A.groupRuns([run("x", "k", "n", [seg("09:00:00", "10:00:00")], { projectId: "p_z" })])[0]), "p_z");

// 红条：并集、裁到窗口、相连的并成一段
const att = (a, b) => ({ start: iso(a), end: iso(b) });
const g = A.groupRuns([run("a", "k", "n", [seg("09:00:00", "11:00:00")], { attend: [att("09:00:00", "09:30:00"), att("11:00:00", "11:30:00")] }),
  run("b", "k", "n", [seg("09:10:00", "10:00:00")], { attend: [att("09:20:00", "09:40:00"), att("08:00:00", "08:30:00")] })])[0];
const spans = A.focusSpans(g, { from: t("08:15:00"), to: t("11:15:00") });
assert.deepStrictEqual(spans, [{ start: t("08:15:00"), end: t("08:30:00") }, { start: t("09:00:00"), end: t("09:40:00") }, { start: t("11:00:00"), end: t("11:15:00") }]);
assert.deepStrictEqual(A.focusSpans(g, { from: t("12:00:00"), to: t("13:00:00") }), []);

// 排序：在等人 → 干活 → 空闲 → 已结束；同档近 3 小时干活多的在前
const mk = (id, o, segs) => run(id, id, id, segs || [seg("11:00:00", "11:10:00")], o);
const order = A.rankGroups(A.groupRuns([mk("ended", {}), mk("idle", { running: true, phase: "idle" }), mk("work_less", { running: true }, [seg("11:50:00", "11:55:00")]),
  mk("work_more", { running: true }, [seg("10:00:00", "11:30:00")]), mk("wait", { running: true, phase: "waiting_permission" }), mk("lost", { running: true, lost: true })]), now);
assert.deepStrictEqual(order.map((x) => x.key), ["wait", "work_more", "work_less", "idle", "lost", "ended"]);

// 刻度：3 小时窗口每小时一格；24 小时每 3 小时；一周每天零点
assert.deepStrictEqual(A.ticks(t("09:30:00"), t("12:30:00")).map((x) => x.label), ["10", "11", "12"]);
assert.deepStrictEqual(A.ticks(now - 24 * 3600000, now).map((x) => x.label), ["15", "18", "21", "00", "03", "06", "09", "12"]);
const wk = A.ticks(now - 7 * 24 * 3600000, now); assert.strictEqual(wk.length, 7); assert.ok(wk.every((x) => /^周[日一二三四五六]$/.test(x.label)));

// 页头小字：并行峰值、现在在工作的卡数
const all = [r1, r2, r3, run("r4", "k3", "p", [seg("10:40:00", "10:50:00")])];
const sm = A.summary(A.groupRuns(all), all);
assert.deepStrictEqual(sm, { sec: 3600 + 1800 + 1800 + 600, peak: 2, working: 1 });
assert.strictEqual(A.subtitle(A.windowOf("hours", 3, now), sm), "agent 最近 3 小时工作了 2 小时 10 分钟，并行峰值 2，现在有 1 个正在工作");
assert.strictEqual(A.subtitle(A.windowOf("day", 3, now), { sec: 3900, peak: 0, working: 0 }), "agent 今天工作了 1 小时 5 分钟，并行峰值 0，现在有 0 个正在工作");

// 最常用的 tool = 干活最久的
assert.strictEqual(A.topTool([run("a", "k", "n", [seg("09:00:00", "10:00:00")], { tool: "codex" }), run("b", "k2", "n", [seg("09:00:00", "09:30:00")], { tool: "claude-code" })]), "codex");
assert.strictEqual(A.topTool([]), null);
console.log("hex-agent-page.test.js ok");
