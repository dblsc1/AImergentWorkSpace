// hex-clock-menu.js 纯逻辑测试
const assert = require("assert");
const C = require("./hex-clock-menu.js");

// 按压分类
assert.strictEqual(C.pressKind(100, 2, 1), "tap");
assert.strictEqual(C.pressKind(C.HOLD_MS, 0, 0), "hold");
assert.strictEqual(C.pressKind(C.HOLD_MS + 300, 4, 4), "hold");
assert.strictEqual(C.pressKind(900, 30, 0), "move");   // 按得久但拖远了 = 拖
assert.strictEqual(C.pressKind(50, 0, 40), "move");

// 倒计时剩余 / 格式 / 自定义分钟 / 结束文案
assert.strictEqual(C.remainingSeconds(10000, 0), 10);
assert.strictEqual(C.remainingSeconds(10000, 9001), 1);
assert.strictEqual(C.remainingSeconds(10000, 20000), 0);
assert.strictEqual(C.formatRemaining(25 * 60), "25:00");
assert.strictEqual(C.formatRemaining(65), "01:05");
assert.strictEqual(C.formatRemaining(3600 + 90), "1:01:30");
assert.strictEqual(C.formatRemaining(-5), "00:00");
assert.deepStrictEqual(["25", " 7 ", "1440", "0", "1441", "", "x", "1.5", "-3"].map(C.parseMinutes), [25, 7, 1440, null, null, null, null, null, null]);
assert.strictEqual(C.endedText(0, 25 * 60000), "倒计时结束 · 25 分钟已记下");
assert.deepStrictEqual(C.PRESETS, [25, 45, 60]);

// 人用时汇总：裁到今天与此刻，在跑的段算上，null = 未分类
const day = Date.parse("2026-11-10T00:00:00Z"), now = Date.parse("2026-11-10T10:00:00Z");
const lanes = { human: {
  sessions: [
    { projectId: "a", startAt: "2026-11-09T23:00:00Z", endAt: "2026-11-10T01:00:00Z" },   // 只算 1 小时
    { projectId: "b", startAt: "2026-11-10T02:00:00Z", endAt: "2026-11-10T02:30:00Z" },
    { projectId: "a", startAt: "2026-11-10T03:00:00Z", endAt: "2026-11-10T03:30:00Z" } ],
  running: { projectId: null, startAt: "2026-11-10T09:30:00Z" } } };
const al = C.allocation(lanes, now, day);
assert.deepStrictEqual(al.map((x) => [x.projectId, x.seconds]), [["a", 5400], ["b", 1800], [null, 1800]]);
assert.ok(Math.abs(al.reduce((s, x) => s + x.share, 0) - 1) < 1e-9);
assert.deepStrictEqual(C.allocation(null, now, day), []);

// 窗口行：拆程序 / 标题，代号标题 = 隐去
const rows = C.windowRows([
  { blockKey: "code · main.go", apps: ["code"], seconds: 100 },
  { blockKey: "chrome · 窗口名3", apps: ["chrome"], seconds: 300 },
  { blockKey: "kitty", apps: ["kitty"], seconds: 50 }]);
assert.deepStrictEqual(rows.map((r) => [r.app, r.title, r.hidden]), [["chrome", null, true], ["code", "main.go", false], ["kitty", null, false]]);

// 聚焦时间线：新的在前，相邻同窗口并成一段
const rec = (a, b, app, title) => ({ startAt: a, endAt: b, app, title });
const tl = C.focusTimeline([
  rec("2026-11-10T09:00:00Z", "2026-11-10T09:10:00Z", "code", "a"),
  rec("2026-11-10T09:10:02Z", "2026-11-10T09:20:00Z", "code", "a"),
  rec("2026-11-10T09:20:00Z", "2026-11-10T09:25:00Z", "chrome", "路径1"),
  rec("2026-11-10T09:30:00Z", "2026-11-10T09:30:00Z", "x", "zero")], 10);
assert.deepStrictEqual(tl.map((r) => r.app), ["chrome", "code"]);
assert.strictEqual(tl[0].hidden, true);
assert.strictEqual(tl[1].to - tl[1].from, 20 * 60000);
assert.strictEqual(C.focusTimeline(Array(50).fill(0).map((_, i) => rec(`2026-11-10T0${i % 9}:00:00Z`, `2026-11-10T0${i % 9}:00:09Z`, "a" + i, "t")), 5).length, 5);

console.log("hex-clock-menu ok");
