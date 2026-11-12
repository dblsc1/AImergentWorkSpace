// hex-human-page.js 纯逻辑测试（hive.stage.v1「十四」）。TZ 由 node 默认；日期一律用本地时间构造，不依赖时区。
const assert = require("assert");
const P = require("./hex-human-page.js");

const L = (y, mo, d, h = 0, mi = 0) => new Date(y, mo - 1, d, h, mi).getTime();
const iso = (t) => new Date(t).toISOString();
const now = L(2026, 11, 12, 15, 0);

const Pr = require("./hex-project-ruler.js");
const labelOf0 = () => ({ key: "k", name: "k", projectId: null });
// 范围：滚动倒推
assert.deepStrictEqual(P.rangeOf("day", 3, now), { kind: "day", from: now - 24 * 3600000, to: now });
assert.strictEqual(P.rangeOf("week", 3, now).from, now - 7 * 24 * 3600000);
assert.strictEqual(P.rangeOf("hours", 5, now).from, now - 5 * 3600000);
assert.strictEqual(P.rangeOf("hours", "x", now).from, now - P.DEFAULT_HOURS * 3600000);   // 默认 3
assert.strictEqual(P.DEFAULT_HOURS, 3);
assert.deepStrictEqual(["1", " 24 ", "3", "0", "25", "", "x", "1.5", "-2", "123"].map(P.parseHours), [1, 24, 3, null, null, null, null, null, null, null]);

// 默认视图（十六）：范围 = Pr.layout(now) 的 7 天（昨天往前 5 天 + 今天），到此刻为止；不传 kind / null 都是它
const rd = P.rangeOf(null, 3, now), L7 = Pr.layout(now);
assert.strictEqual(rd.kind, "default");
assert.strictEqual(rd.from, L7.from);
assert.strictEqual(rd.from, L(2026, 11, 6));
assert.strictEqual(rd.to, now);
assert.strictEqual(rd.layout.days.length, 7);
assert.deepStrictEqual(P.rangeOf("default", 3, now).from, rd.from);
assert.deepStrictEqual([P.nextKind(null, "day"), P.nextKind("day", "day"), P.nextKind("day", "week"), P.nextKind("hours", "hours")], ["day", null, "week", null]);   // 再点一次回默认
// 默认视图的总用时 = 这 7 天里的计时段；7 天前的不算；正在跑的算到此刻
const sd = (a, b) => ({ startAt: iso(a), endAt: iso(b), projectId: "p1", taskId: null, source: "timer-backend" });
const week7 = [sd(L(2026, 11, 6, 9), L(2026, 11, 6, 10)), sd(L(2026, 11, 12, 9), L(2026, 11, 12, 9, 30)), sd(L(2026, 11, 5, 9), L(2026, 11, 5, 11))];
assert.strictEqual(P.totalSeconds(rd, week7, null), 3600 + 1800);
assert.strictEqual(P.totalSeconds(rd, week7, { startAt: iso(now - 600000) }), 3600 + 1800 + 600);
// 默认视图的泳道用项目面板那把 7 天不等宽标尺（各天宽度见「十五」9）
const dl = P.laneOf(rd, { sessions: [sd(L(2026, 11, 12, 0), L(2026, 11, 12, 12)), sd(L(2026, 11, 8, 0), L(2026, 11, 9, 0))] });
const dd = Pr.layout(now).days, near = (x, y) => Math.abs(x - y) < 1e-6;
assert.ok(near(dl.timer[0].left, dd[2].x0 * 100) && near(dl.timer[0].width, dd[2].w * 100));        // 11/8 是第 3 格
assert.ok(near(dl.timer[1].left, dd[6].x0 * 100) && near(dl.timer[1].width, dd[6].w * 100 / 2));    // 今天 0–12 点 = 今天那格的一半
assert.strictEqual(P.spanText(rd, L(2026, 11, 12, 9, 5), L(2026, 11, 12, 10, 30)), "11/12 09:05–10:30");
assert.strictEqual(P.dated(rd) && P.dated(P.rangeOf("week", 3, now)), true);
assert.strictEqual(P.dated(P.rangeOf("day", 3, now)) || P.dated(P.rangeOf("hours", 3, now)), false);

// 分段读本地日：一周往前 7 天跨 8 个日历日 → 两段；3 小时在同一天 = 一段；跨零点 = 一段两天
assert.deepStrictEqual(P.dateChunks(P.rangeOf("week", 3, now).from, now), [{ from: "2026-11-05", to: "2026-11-11" }, { from: "2026-11-12", to: "2026-11-12" }]);
assert.deepStrictEqual(P.dateChunks(L(2026, 11, 12, 9), now), [{ from: "2026-11-12", to: "2026-11-12" }]);
assert.deepStrictEqual(P.dateChunks(L(2026, 11, 11, 23), L(2026, 11, 12, 1)), [{ from: "2026-11-11", to: "2026-11-12" }]);
// 窗口块一次读整个范围：from / to = 范围跨的第一天和最后一天（周档 8 天，默认 7 天，日 2 天，3 小时 1 天）
const bq = (r) => P.blockQuery(r);
assert.strictEqual(bq(P.rangeOf("hours", 3, now)), "from=2026-11-12&to=2026-11-12");
assert.strictEqual(bq(P.rangeOf("hours", 24, now)), "from=2026-11-11&to=2026-11-12");
assert.strictEqual(bq(P.rangeOf("day", 3, now)), "from=2026-11-11&to=2026-11-12");
assert.strictEqual(bq(P.rangeOf("week", 3, now)), "from=2026-11-05&to=2026-11-12");
assert.strictEqual(bq(rd), "from=2026-11-06&to=2026-11-12");

// 计时段合并去重、总用时裁到范围
const r3 = P.rangeOf("hours", 3, now);
const s = (a, b, extra = {}) => ({ startAt: iso(a), endAt: iso(b), projectId: "p1", taskId: null, source: "timer-backend", ...extra });
const A = s(now - 5 * 3600000, now - 2.5 * 3600000);        // 只有后半小时在范围内
const B = s(now - 3600000, now - 1800000);
const merged = P.mergeSessions([[A, B], [A]]);
assert.strictEqual(merged.length, 2);
assert.strictEqual(P.totalSeconds(r3, merged, null), 1800 + 1800);
assert.strictEqual(P.totalSeconds(r3, merged, { startAt: iso(now - 600000) }), 3600 + 600);
assert.strictEqual(P.totalSeconds(r3, [], { startAt: iso(now - 9 * 3600000) }), 3 * 3600);   // 正在跑的从范围起点算
assert.strictEqual(P.totalSeconds(P.rangeOf("week", 3, now), merged, null), 2.5 * 3600 + 1800);   // 周：A 整段都在
// 画面右端取整到分钟，正在跑的计时仍算到真正的此刻（刚开始的计时不会不见）
const rounded = { ...P.rangeOf("hours", 3, now), now: now + 30000 };
assert.strictEqual(P.totalSeconds(rounded, [], { startAt: iso(now + 10000) }), 20);
assert.deepStrictEqual(P.taskCards(rounded, [], { startAt: iso(now + 10000) }, labelOf0).map((c) => c.seconds), [20]);
assert.strictEqual(P.laneOf(rounded, { running: { startAt: iso(now + 10000) } }).timer[0].width, 0);   // 超出右端的部分钳在 100%
assert.strictEqual(P.durText(0), "0 分钟");
assert.strictEqual(P.durText(59), "0 分钟");
assert.strictEqual(P.durText(3 * 3600 + 5 * 60), "3 小时 5 分钟");
assert.strictEqual(P.durText(125 * 60), "2 小时 5 分钟");

// 逐任务：同 key 并成一张；正在跑的置顶；其余最近聚焦的在前
const labelOf = (taskId, pid, live) => taskId ? { key: "t:" + taskId, name: taskId, projectId: pid } : pid ? { key: "p:" + pid, name: pid, projectId: pid } : live ? { key: "running", name: "手动计时", projectId: null } : { key: "none", name: "未分类", projectId: null };
const cards = P.taskCards(r3, [
  s(now - 170 * 60000, now - 150 * 60000, { taskId: "ta", projectId: "p1" }),     // ta 20 分，较早
  s(now - 100 * 60000, now - 90 * 60000, { taskId: "tb", projectId: "p2" }),      // tb 10 分
  s(now - 80 * 60000, now - 60 * 60000, { taskId: "ta", projectId: "p1" }),       // ta 又 20 分，最近
  s(now - 50 * 60000, now - 40 * 60000, { taskId: null, projectId: "p3" }),       // 只记到项目
  s(now - 40 * 60000, now - 30 * 60000, { taskId: null, projectId: null }),       // 没去向
], { startAt: iso(now - 20 * 60000), taskId: "tb", projectId: "p2" }, labelOf);
assert.deepStrictEqual(cards.map((c) => [c.key, c.seconds, c.running]), [["t:tb", 30 * 60, true], ["none", 600, false], ["p:p3", 600, false], ["t:ta", 2400, false]]);
const free = P.taskCards(r3, [], { startAt: iso(now - 600000), taskId: null, projectId: null }, labelOf);
assert.deepStrictEqual(free.map((c) => [c.name, c.seconds, c.running]), [["手动计时", 600, true]]);
assert.deepStrictEqual(P.taskCards(r3, [s(now - 9 * 3600000, now - 8 * 3600000)], null, labelOf), []);   // 范围外的不出现

// 窗口块：归类判断、代号标题
const assigned = { blockKey: "code · main.go", apps: ["code"], status: "assigned", seconds: 1800, lastAt: iso(now - 60000),
  parts: [{ fromTs: null, toTs: now - 3600000, projectId: "pa", seconds: 100 }, { fromTs: now - 3600000, toTs: null, projectId: "pb", seconds: 900 }] };
const pending = { blockKey: "chrome · 窗口名3", apps: ["chrome"], status: "pending", seconds: 600, lastAt: iso(now - 5 * 3600000), parts: [] };
assert.strictEqual(P.blockProject(assigned), "pb");               // 秒数最多的一段
assert.strictEqual(P.blockProject(pending), null);
assert.strictEqual(P.blockProject({ status: "assigned", parts: [] }), null);
assert.strictEqual(P.recordProject(assigned, now - 3000000), "pb");
assert.strictEqual(P.recordProject(assigned, now - 3 * 3600000), "pa");
assert.strictEqual(P.recordProject(pending, now), null);
assert.deepStrictEqual(P.splitKey("chrome · 窗口名3", ["chrome"]), { app: "chrome", title: null, hidden: true });
assert.strictEqual(P.windowLabel("chrome", null, true), "chrome · 标题已隐去");
assert.strictEqual(P.windowLabel("code", "main.go", false), "code · main.go");
assert.strictEqual(P.windowLabel("kitty", null, false), "kitty");

const rec = (a, b, app, title) => ({ startAt: iso(a), endAt: iso(b), app, title });
const blocks = [
  { item: assigned, records: [rec(now - 40 * 60000, now - 30 * 60000, "code", "main.go"), rec(now - 30 * 60000 + 2000, now - 20 * 60000, "code", "main.go")] },
  { item: pending, records: [rec(now - 60 * 60000, now - 50 * 60000, "chrome", "窗口名3"), rec(now - 4 * 3600000, now - 3.5 * 3600000, "chrome", "窗口名3")] }];
const segs = P.focusSegments(r3, blocks);
assert.deepStrictEqual(segs.map((x) => [x.app, x.hidden, x.projectId]), [["chrome", true, null], ["code", false, "pb"]]);   // 范围外那条丢掉；相邻同窗口并成一段
assert.strictEqual(segs[1].to - segs[1].from, 20 * 60000);

// 窗口记录行：按范围内用时排；归类的带项目；没读到记录的块退回块的 seconds
const rows = P.windowRows(r3, blocks);
assert.deepStrictEqual(rows.map((x) => [x.app, x.title, x.hidden, x.seconds, x.projectId]), [["code", "main.go", false, 1200 - 2, "pb"], ["chrome", null, true, 600, null]]);
const noRec = P.windowRows(r3, [{ item: assigned, records: null }, { item: pending, records: null }]);
assert.deepStrictEqual(noRec.map((x) => [x.app, x.seconds]), [["code", 1800]]);   // pending 的最后活动在范围外
// 每行带归类要用的：所属日期、seq、状态、块总秒数、显示名
const dated0 = P.windowRows(r3, [{ date: "2026-11-12", item: { ...pending, lastAt: iso(now - 60000), seq: 7, displayName: "x", seconds: 600 }, records: [rec(now - 60 * 60000, now - 50 * 60000, "chrome", "窗口名3")] }]);
assert.deepStrictEqual([dated0[0].date, dated0[0].seq, dated0[0].status, dated0[0].blockSeconds, dated0[0].displayName], ["2026-11-12", 7, "pending", 600, "x"]);
// 5 条折叠
const many = Array.from({ length: 8 }, (_, i) => ({ seconds: 100 - i }));
assert.strictEqual(P.foldRows(many, false).length, 5);
assert.strictEqual(P.foldRows(many, true).length, 8);
assert.strictEqual(P.FOLD, 5);

// 泳道：计时段（含在跑的）在上行，窗口段在下行；有原始记录覆盖的时段不再用台账段
const lane = P.laneOf(r3, {
  sessions: [s(now - 150 * 60000, now - 120 * 60000, { source: "timer-backend" }),
    s(now - 100 * 60000, now - 90 * 60000, { source: "activity-block", projectId: "pz" }),
    s(now - 5 * 3600000, now - 2.5 * 3600000, { source: "activity-block", projectId: "pold" })],
  running: { startAt: iso(now - 600000), projectId: null }, segments: segs, coverFrom: now - 70 * 60000 });
assert.deepStrictEqual(lane.timer.map((x) => [x.projectId, !!x.live]), [["p1", false], [null, true]]);
assert.strictEqual(Math.round(lane.timer[0].width * 100) / 100, Math.round(30 / 180 * 10000) / 100);
assert.deepStrictEqual(lane.windows.map((x) => [x.label, x.projectId]), [[null, "pold"], [null, "pz"], ["chrome · 标题已隐去", null], ["code · main.go", "pb"]]);
const last = lane.windows[lane.windows.length - 1];
assert.ok(last.left > 0 && last.left + last.width <= 100 + 1e-9);
assert.strictEqual(P.spanText(r3, L(2026, 11, 12, 9, 5), L(2026, 11, 12, 10, 30)), "09:05–10:30");
assert.strictEqual(P.spanText(P.rangeOf("week", 3, now), L(2026, 11, 12, 9, 5), L(2026, 11, 12, 10, 30)), "11/12 09:05–10:30");


// 泳道悬停（二十三）：段 → 逐任务卡 / 窗口块、置顶顺序
{
  const labelOf = (t, p, live) => t ? { key: "t:" + t, name: t, projectId: p } : p ? { key: "p:" + p, name: p, projectId: p } : live ? { key: "running", name: "手动计时", projectId: null } : { key: "none", name: "未分类", projectId: null };
  const tasks = [{ key: "running", projectId: null }, { key: "t:t1", projectId: "pa" }, { key: "none", projectId: null }];
  assert.strictEqual(P.taskCardFor(tasks, "t:t1", "pa").key, "t:t1");
  assert.strictEqual(P.taskCardFor(tasks, "p:pa", "pa").key, "t:t1");      // 同键没有卡 → 同项目的第一张
  assert.strictEqual(P.taskCardFor(tasks, "p:pz", "pz"), null);
  assert.strictEqual(P.taskCardFor(tasks, "none", null).key, "none");
  const blocks = [
    { item: { blockKey: "a · 1" }, records: [{ startAt: iso(now), endAt: iso(now + 60000) }] },
    { item: { blockKey: "b · 2" }, records: [{ startAt: iso(now + 60000), endAt: iso(now + 600000) }, { startAt: iso(now - 9e5), endAt: iso(now - 8e5) }] },
    { item: { blockKey: "c · 3" }, records: null }];
  assert.strictEqual(P.dominantBlock(blocks, now, now + 600000), "b · 2");                 // 重叠最久
  assert.strictEqual(P.dominantBlock(blocks, now, now + 30000), "a · 1");
  assert.strictEqual(P.dominantBlock(blocks, now + 9e6, now + 9.1e6), null);
  const timerSeg = { timer: true, a: now, b: now + 600000, projectId: null, taskId: null, live: true };
  assert.deepStrictEqual(P.hoverTargets(timerSeg, tasks, blocks, labelOf), { taskKey: "running", blockKey: "b · 2" });   // 手动计时段 → 计时卡 + 聚焦最久的块
  const winSeg = { a: now, b: now + 1, projectId: "pa", blockKey: "a · 1" };
  assert.deepStrictEqual(P.hoverTargets(winSeg, tasks, blocks, labelOf), { taskKey: "t:t1", blockKey: "a · 1" });
  assert.deepStrictEqual(P.hoverTargets({ a: now, b: now + 1, projectId: null, blockKey: null }, tasks, blocks, labelOf), { taskKey: "none", blockKey: null });   // 台账里的段没有窗口块
  assert.notStrictEqual(P.segId({ a: 1, b: 2 }), P.segId({ a: 1, b: 2, timer: true }));
  assert.strictEqual(P.segId({ timer: true, a: 1, b: 5, live: true }), P.segId({ timer: true, a: 1, b: 9, live: true }));   // 正在跑的不随右端变
  const rows = ["x", "y", "z", "w"].map((k) => ({ blockKey: k }));
  assert.deepStrictEqual(P.pinRows(rows, "z").map((r) => r.blockKey), ["z", "x", "y", "w"]);   // 其余顺序不变
  assert.strictEqual(P.pinRows(rows, "x"), rows);
  assert.strictEqual(P.pinRows(rows, null), rows);
  assert.strictEqual(P.pinRows(rows, "nope"), rows);
  assert.deepStrictEqual(P.foldRows(P.pinRows(["a", "b", "c", "d", "e", "f", "g"].map((k) => ({ blockKey: k })), "g"), false).map((r) => r.blockKey), ["g", "a", "b", "c", "d"]);   // 折叠时被藏起的也露出来
  // focusSegments 带块键；不同块不并
  const fs = P.focusSegments(P.rangeOf("day", 3, now), [{ item: { blockKey: "a · 1", status: "pending" }, records: [{ startAt: iso(now - 3e5), endAt: iso(now - 2e5), app: "a", title: "1" }] },
    { item: { blockKey: "a · 1x", status: "pending" }, records: [{ startAt: iso(now - 2e5), endAt: iso(now - 1e5), app: "a", title: "1" }] }]);
  assert.deepStrictEqual(fs.map((x) => x.blockKey), ["a · 1", "a · 1x"]);
}
console.log("hex-human-page ok");
