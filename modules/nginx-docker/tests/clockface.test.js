// static/clockface.js 纯函数（hive.stage.v1「二十七」）
const test = require("node:test"), assert = require("node:assert");
const H = require("../static/clockface.js");
const T0 = Date.parse("2026-11-13T10:00:00Z");

test("红弧：正计时 60 分钟一圈、满一圈从头；倒计时按剩余 / 总时长往回收", () => {
  assert.equal(H.timerArc(null, T0), 0);
  assert.equal(H.timerArc({ startAt: T0, deadlineAt: null }, T0 + 15 * 60000), 25);
  assert.equal(H.timerArc({ startAt: T0, deadlineAt: null }, T0 + 60 * 60000), 0);
  assert.equal(H.timerArc({ startAt: T0, deadlineAt: null }, T0 + 90 * 60000), 50);
  assert.equal(H.timerArc({ startAt: T0, deadlineAt: T0 + 1500000 }, T0), 100);
  assert.equal(H.timerArc({ startAt: T0, deadlineAt: T0 + 1500000 }, T0 + 750000), 50);
  assert.equal(H.timerArc({ startAt: T0, deadlineAt: T0 + 1500000 }, T0 + 2000000), 0);
});

test("时钟旁的字：mm:ss，满 1 小时 h:mm:ss；小字有项目名才带项目名；没在计时 = null", () => {
  assert.equal(H.clockFace({ timer: null, now: T0, todaySec: 60 }), null);
  var f = H.clockFace({ timer: { startAt: T0 }, now: T0 + 65000, projectName: "数学", todaySec: 4980 });
  assert.deepEqual([f.big, f.small], ["01:05", "数学 · 今天 1 小时 23 分"]);
  f = H.clockFace({ timer: { startAt: T0 }, now: T0 + 3725000, projectName: "", todaySec: 600 });
  assert.deepEqual([f.big, f.small], ["1:02:05", "今天 10 分"]);
  f = H.clockFace({ timer: { startAt: T0, deadlineAt: T0 + 1500000 }, now: T0 + 1000, projectName: "数学", todaySec: 0 });
  assert.equal(f.big, "24:59");
});

test("今天用时最多的项目：裁到今天、跑着的算到此刻、没归项目的不列、多到少", () => {
  var from = T0, now = T0 + 7200000, iso = (t) => new Date(t).toISOString();
  var top = H.topProjects([
    { projectId: "a", startAt: iso(from - 1800000), endAt: iso(from + 600000) },   // 只算今天的 10 分
    { projectId: "b", startAt: iso(from + 600000), endAt: iso(from + 4200000) },   // 60 分
    { projectId: null, startAt: iso(from), endAt: iso(from + 3600000) },
  ], { projectId: "c", startAt: iso(now - 1200000) }, from, now, 3);
  assert.deepEqual(top, [{ projectId: "b", sec: 3600 }, { projectId: "c", sec: 1200 }, { projectId: "a", sec: 600 }]);
  assert.equal(H.topProjects([], null, from, now, 3).length, 0);
  assert.equal(H.topProjects(top.map((p, i) => ({ projectId: "p" + i, startAt: iso(from), endAt: iso(from + 60000 * (i + 1)) })).concat([{ projectId: "z", startAt: iso(from), endAt: iso(from + 600000) }]), null, from, now, 3).length, 3);
});

test("时钟卡：没在计时 / 正计时 / 倒计时，没有的行不出现", () => {
  var base = { now: T0 + 65000, projectName: "数学", todaySec: 4980, focus: null, top: [] };
  var texts = (s) => H.clockCardLines(s).map((l) => l.text);
  assert.deepEqual(texts(Object.assign({}, base, { timer: null })), ["没在计时", "今天 1 小时 23 分"]);
  assert.equal(texts(Object.assign({}, base, { timer: { startAt: T0 } }))[0], "正在计时 · 数学 · 01:05");
  assert.equal(texts(Object.assign({}, base, { timer: { startAt: T0 }, projectName: "" }))[0], "正在计时 · 01:05");
  assert.equal(texts(Object.assign({}, base, { timer: { startAt: T0, deadlineAt: T0 + 1500000 } }))[0], "倒计时 · 数学 · 剩 23:55");
});

test("时钟卡：聚焦 项目名 / 窗口名、没归类只有窗口名、离开写离开；项目至多 3 个带色点", () => {
  var s = { timer: null, now: T0, todaySec: 60, top: [] }, texts = (x) => H.clockCardLines(x).map((l) => l.text);
  assert.equal(texts(Object.assign({}, s, { focus: { project: "数学", window: "code · a.js" } }))[2], "数学 / code · a.js");
  assert.equal(texts(Object.assign({}, s, { focus: { project: "", window: "code · a.js" } }))[2], "code · a.js");
  assert.equal(texts(Object.assign({}, s, { focus: { afk: true, project: "数学", window: "x" } }))[2], "离开");
  var lines = H.clockCardLines(Object.assign({}, s, { top: [1, 2, 3, 4].map((i) => ({ name: "p" + i, color: "#abc", sec: 600 * i })) }));
  assert.equal(lines.length, 2 + 3);
  assert.deepEqual(lines[2], { dot: "#abc", text: "p1 10 分" });
});
