// hex-agent-page.js 纯逻辑：滚动窗口、按 agent 分组、段的项目归属、红条区间、排序。node hex-agent-page.test.js
"use strict";
process.env.TZ = "Asia/Shanghai";
const assert = require("assert");
const A = require("./hex-agent-page.js"), Pr = require("./hex-project-ruler.js");
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
// 默认范围（「十七」2）：7 天不等宽标尺，今天一格最宽；日 / 周 / 小时都不选中（mode 是 default）
const dw = A.windowOf("default", 3, now), DL = A.scaleOf(dw);
assert.strictEqual(dw.mode, "default"); assert.strictEqual(DL.days.length, 7); assert.strictEqual(dw.from, DL.from); assert.strictEqual(dw.label, "最近 7 天");
assert.ok(dw.hours > 24);   // 悬停提示带日期
const wid = DL.days.map((d) => d.w), today = DL.days[6];
assert.ok(today.today && today.w === Math.max(...wid) && wid.slice(0, 6).every((w) => w < today.w));
assert.ok(Math.abs(wid.reduce((a, b) => a + b, 0) - 1) < 1e-9);
assert.strictEqual(A.dayTicks(DL)[6].label, "今天"); assert.ok(A.dayTicks(DL).slice(0, 6).every((x) => /^周[日一二三四五六]$/.test(x.label)));
assert.deepStrictEqual(A.dayTicks(DL).map((x) => x.x), DL.days.map((d) => d.x0));
// 切换：点日 / 周 / 小时 = 等宽滚动窗口（一整格）；已选中的再点 = 回默认
const sw = A.scaleOf(A.windowOf("day", 3, now)); assert.strictEqual(sw.days.length, 1); assert.strictEqual(sw.days[0].w, 1);
assert.strictEqual(A.pickRange("default", "day"), "day"); assert.strictEqual(A.pickRange("hours", "week"), "week");
assert.strictEqual(A.pickRange("day", "day"), "default"); assert.strictEqual(A.pickRange("week", "week"), "default");
// 现在线：默认标尺里在今天那一格内（不在最右）；滚动窗口里在最右
const xn = Pr.xOf(DL, now); assert.ok(xn > today.x0 && xn < 1);
assert.strictEqual(Pr.xOf(A.scaleOf(A.windowOf("hours", 3, now)), now), 1);

// 正在工作的数：只要一个 agent 有一条运行在跑、没失联、相位 working 就算 1 个；窗口里没有干活区间（segments 为空）的在跑运行照算
const pr = (id, key, o) => run(id, key, key, [], Object.assign({ running: true }, o));
const cnt = (runs) => A.summary(A.groupRuns(runs), runs).working;
assert.strictEqual(cnt([pr("a", "k1"), pr("b", "k2")]), 2);                                   // 两个 agent 各一条在跑 working，窗口里都没有区间
assert.strictEqual(cnt([pr("a", "k1"), pr("b", "k1")]), 1);                                   // 同一个 agent 两条运行只算 1 个
assert.strictEqual(cnt([pr("a", "k1"), pr("b", "k1", { running: false }), pr("c", "k1", { lost: true, phase: "idle" })]), 1);
assert.strictEqual(cnt([pr("a", "k1", { lost: true }), pr("b", "k2", { phase: "idle" }), pr("c", "k3", { phase: "waiting_input" }), pr("d", "k4", { running: false })]), 0);
assert.strictEqual(cnt([pr("a", "k1", { running: false }), pr("b", "k1")]), 1);                // 老的已结束、新的在跑
assert.strictEqual(A.summary(A.groupRuns([pr("a", "k1")]), [pr("a", "k1")]).sec, 0);
// 混合页小字：你 / 代理各自总用时、效率、峰值、正在工作
assert.strictEqual(A.mixedSubtitle(3600, { sec: 3.2 * 3600, peak: 2, working: 2 }), "你 1 小时 · 代理 3.2 小时 · 你 1 小时，带出代理 3.2 小时的活 · 代理并行峰值 2 · 现在有 2 个正在工作");
assert.strictEqual(A.mixedSubtitle(0, { sec: 0, peak: 0, working: 0 }), "你 0 分钟 · 代理 0 分钟 · 代理并行峰值 0 · 现在有 0 个正在工作");
console.log("hex-agent-page.test.js ok");
