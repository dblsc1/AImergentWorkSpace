// hex-stage.js 纯逻辑：中心环、气泡、桶、抽屉、拖放请求、白条、手动计时。node hex-stage.test.js
"use strict";
const assert = require("assert");
const S = require("./hex-stage.js");

const NOW = Date.parse("2026-11-07T10:00:00+08:00");
const iso = (sec) => new Date(NOW + sec * 1000).toISOString();
const runs = [{ runId: "r1" }, { runId: "r2" }];
const at = (id, from, until) => ({ runId: id, kind: "attend", at: iso(from), until: iso(until) });

// HV-S01..S04
assert.strictEqual(S.pickRing(runs, [at("r1", -600, -300), at("r2", -100, -5)], NOW).runId, "r2");
assert.strictEqual(S.pickRing(runs, [at("r2", -900, -700), at("r1", -600, -300)], NOW).runId, "r1");
assert.strictEqual(S.pickRing(runs, [], NOW), null);
assert.strictEqual(S.pickRing(runs, [at("r1", -600, -20)], NOW).runId, "r1");
assert.strictEqual(S.pickRing(runs, [at("r1", -600, -20), at("r2", -20, -10)], NOW).runId, "r2");
// 今天之前的不算
assert.strictEqual(S.pickRing(runs, [at("r1", -86400, -86000)], NOW, NOW - 3600e3), null);
// 最后一条 until 刚好在 15 秒前也算此刻在看
assert.strictEqual(S.pickRing(runs, [at("r1", -600, -15), at("r2", -700, -100)], NOW).runId, "r1");

// HV-S05 / S06
assert.deepStrictEqual(S.eyeHost({ state: "present", projectId: "p1" }, null), { at: "project", project: "p1", by: "window" });
assert.deepStrictEqual(S.eyeHost({ state: "afk", projectId: "p1" }, null), { at: "clock", closed: true });
assert.deepStrictEqual(S.eyeHost(null, null), { at: "clock", closed: true });
assert.deepStrictEqual(S.eyeHost({ state: "present", projectId: null }, null), { at: "tray", by: "window" });
assert.deepStrictEqual(S.eyeHost({ state: "present", projectId: "p_inbox" }, null), { at: "tray", by: "window" });   // 收件箱不是格子
// 手动计时优先于窗口：记到了项目跟项目，没记到停时钟（睁眼）
assert.deepStrictEqual(S.eyeHost({ state: "present", projectId: "p1" }, { projectId: "p2" }), { at: "project", project: "p2", by: "timer" });
assert.deepStrictEqual(S.eyeHost({ state: "afk" }, { projectId: null }), { at: "clock", by: "timer" });
assert.strictEqual(S.focusLabel({ app: "firefox", title: "文档" }), "firefox · 文档");
assert.strictEqual(S.focusLabel({ app: "firefox", title: "窗口名7" }), "firefox · 标题已隐去");
assert.strictEqual(S.focusLabel({ app: "", title: "" }), "窗口");
// 未分类桶的过滤器：默认 2 分钟；满的才进
assert.deepStrictEqual([S.bucketMinutes(null), S.bucketMinutes("5"), S.bucketMinutes("0"), S.bucketMinutes("-1"), S.bucketMinutes("x")], [2, 5, 0, 2, 2]);
assert.deepStrictEqual(S.bucketFilter([{ seconds: 119 }, { seconds: 120 }, { seconds: 600 }], 2).map((x) => x.seconds), [120, 600]);
assert.strictEqual(S.focusLabel({ app: "chrome", title: "" }), "chrome");                                     // 读不到标题：不写「· —」（「二十四」）
assert.strictEqual(S.focusLabel({ app: "chrome", title: "", displayName: "chrome · HoneyComb" }), "chrome · HoneyComb");
assert.strictEqual(S.focusLabel({ app: "chrome", title: "", displayName: "chrome · 窗口名3" }), "chrome · 标题已隐去");
assert.strictEqual(S.focusLabel({ app: "chrome", title: "", displayName: "github.com" }), "chrome · github.com");
assert.strictEqual(S.focusLabel({ app: "chrome", title: "", displayName: "Chrome" }), "chrome");

// HV-S07 / S08
assert.strictEqual(S.bucketText(3720), "未分类 · 今天 1 小时 2 分");
assert.strictEqual(S.bucketText(0), "未分类 · 今天 0 分");
assert.strictEqual(S.bucketText(59), "未分类 · 今天 0 分");
assert.strictEqual(S.bucketText(3600), "未分类 · 今天 1 小时 0 分");
assert.strictEqual(S.bucketText(300), "未分类 · 今天 5 分");

// HV-S09 / S10
const B = (key, sec, legible, kind) => ({ date: "2026-11-07", blockKey: key, seq: 3, displayName: key, seconds: sec, legible, kind });
const rows = S.drawerRows([B("a", 600, true, "browser"), B("b", 1200, true, "browser"), B("c", 30, false, "browser"), B("d", 40, false, "terminal"), B("e", 50, false, "other"), B("f", 20, false, "browser")]);
assert.deepStrictEqual(rows.map((r) => r.name), ["b", "a", "浏览器（其他网站）", "终端（未认出）", "其他"]);
assert.deepStrictEqual(rows.map((r) => r.draggable), [true, true, false, false, false]);
assert.strictEqual(rows[2].seconds, 50);
assert.deepStrictEqual(S.drawerRows([B("c", 30, false, "terminal")]).map((r) => r.name), ["终端（未认出）"]);

// 灰行点开：一块 = 一组同名窗口，按秒降序，块带标题当 displayName
const G = (key, sec, apps) => ({ date: "2026-11-07", blockKey: key, seq: 2, displayName: null, seconds: sec, legible: false, kind: "terminal", apps });
const grey = S.drawerRows([G("ptyxis · ~/work", 60, ["ptyxis"]), G("ptyxis · vim a.go", 300, ["ptyxis"]), G("ptyxis", 10, ["ptyxis"])])[0].members;
assert.deepStrictEqual(grey.map((m) => [m.title, m.seconds]), [["vim a.go", 300], ["~/work", 60], ["ptyxis", 10]]);
assert.strictEqual(grey[0].block.blockKey, "ptyxis · vim a.go"); assert.strictEqual(grey[0].block.displayName, "ptyxis · vim a.go");
const hid = S.drawerRows([G("ptyxis · 窗口名12", 5, ["ptyxis"]), G("ptyxis · 路径3", 4, ["ptyxis"]), G("ptyxis · 窗口名x", 3, ["ptyxis"])])[0].members;
assert.deepStrictEqual(hid.map((m) => m.hidden), [true, true, false]); assert.strictEqual(grey[0].hidden, false);

// 白条
const ups = Array.from({ length: 7 }, (_, i) => ({ id: "u" + i, startAt: iso(i) }));
const strips = S.stripList(ups.slice().reverse());
assert.deepStrictEqual(strips.items.map((u) => u.id), ["u0", "u1", "u2", "u3", "u4"]); assert.strictEqual(strips.plus, 2);
assert.strictEqual(S.stripList(ups.slice(0, 5)).plus, 0);

// 请求组装
assert.deepStrictEqual(S.dropRequest({ kind: "block", block: B("g", 60, true, "browser") }, "p1"),
  { method: "POST", path: "activity/blocks/assign", body: { date: "2026-11-07", blockKey: "g", ifSeq: 3, projectId: "p1", remember: false } });
assert.deepStrictEqual(S.dropRequest({ kind: "pet", runId: "run_1" }, "p1"), { method: "POST", path: "agents/run_1/place", body: { projectId: "p1", remember: false } });
assert.strictEqual(S.dropRequest({ kind: "pet", runId: "run_1" }, "p1", true).body.remember, true);
assert.deepStrictEqual(S.dropRequest({ kind: "pet", runId: "run_1" }, "p1", true, "now").body, { projectId: "p1", remember: true, timeFrom: "now" });
assert.deepStrictEqual(S.dropRequest({ kind: "pet", runId: "run_1" }, "p1", false, "start").body, { projectId: "p1", remember: false });
assert.strictEqual(S.dropRequest({ kind: "block", block: B("g", 60, true, "browser") }, "p1", true).body.remember, true);
// 宠物名牌
assert.strictEqual(S.petTitle({ humanName: "我的", conversation: "c", label: "l", agent: "a" }, "数学"), "我的");
assert.strictEqual(S.petTitle({ conversation: "改错题", label: "l", agent: "a" }, "数学"), "数学·改错题");
assert.strictEqual(S.petTitle({ conversation: "改错题", label: "l", agent: "a" }, null), "改错题");
assert.strictEqual(S.petTitle({ conversation: null, label: "l", agent: "a" }, "数学"), "l");
assert.strictEqual(S.petTitle({ label: null, agent: "codex" }, null), "codex");
assert.deepStrictEqual(S.dropRequest({ kind: "strip", id: "u1" }, "p1"), { method: "POST", path: "timer/unplaced/u1/place", body: { projectId: "p1" } });
assert.deepStrictEqual(S.dropRequest({ kind: "clock" }, "p1"), { method: "POST", path: "timer/place", body: { projectId: "p1" } });
assert.strictEqual(S.dropRequest({ kind: "pet", runId: "r" }, null), null);
// 拖到任务条：只给 taskId（nexus-core 要求 taskId / projectId 恰好一个）
assert.deepStrictEqual(S.dropRequest({ kind: "pet", runId: "run_1" }, "p1", false, null, "t1").body, { taskId: "t1", remember: false });
assert.deepStrictEqual(S.dropRequest({ kind: "strip", id: "u1" }, "p1", false, null, "t1").body, { taskId: "t1" });
assert.deepStrictEqual(S.dropRequest({ kind: "clock" }, "p1", false, null, "t1").body, { taskId: "t1" });
assert.deepStrictEqual(S.dropRequest({ kind: "block", block: { date: "2026-11-07", blockKey: "k", seq: 3 } }, "p1", false, null, "t1").body, { date: "2026-11-07", blockKey: "k", ifSeq: 3, taskId: "t1", remember: false });

function mkStage(replies) {
  const sent = []; let t = 1000;
  const st = S.createStage({ now: () => t, projectName: (id) => ({ p1: "数学" })[id], send: async (req) => { sent.push(req); const r = replies.shift(); if (r instanceof Error) throw r; return r; } });
  return { st, sent, set: (n) => (t = n) };
}
(async () => {
  // HV-S11 / S12
  let k = mkStage([{ status: 200, body: {} }]);
  k.st.setBucket(1800, [B("g", 600, true, "browser")]);
  let r = await k.st.drop({ kind: "block", block: k.st.drawer()[0].block }, "p1");
  assert.strictEqual(k.sent[0].path, "activity/blocks/assign"); assert.strictEqual(r.ok, true);
  assert.strictEqual(k.st.drawer().length, 0); assert.strictEqual(k.st.bucketSeconds(), 1200);
  assert.strictEqual(k.st.notice(), null); assert.strictEqual(r.ok, true);
  // HV-S13
  for (const bad of [{ status: 409 }, { status: 422 }, new Error("net")]) {
    k = mkStage([bad]);
    k.st.setBucket(1800, [B("g", 600, true, "browser")]);
    r = await k.st.drop({ kind: "block", block: B("g", 600, true, "browser") }, "p1");
    assert.strictEqual(r.ok, false); assert.strictEqual(r.reverted, true);
    assert.ok(k.st.notice().length > 0); assert.strictEqual(k.st.drawer().length, 1); assert.strictEqual(k.st.bucketSeconds(), 1800);
  }
  // HV-S14 / S15 / S16 / P18
  k = mkStage([{ status: 200, body: { runId: "run_1", projectId: "p1", rule: { agent: "garden", projectId: "p1" } } }]);
  await k.st.drop({ kind: "pet", runId: "run_1", agent: "garden" }, "p1");
  assert.deepStrictEqual(k.sent[0].body, { projectId: "p1", remember: false });
  assert.strictEqual(k.st.notice(), null);
  k = mkStage([{ status: 200, body: {} }, { status: 200, body: {} }, { status: 409 }]);
  await k.st.drop({ kind: "pet", runId: "run_1", agent: "garden" }, "p1");
  assert.strictEqual((await k.st.remember({ kind: "pet", runId: "run_1", agent: "garden" }, "p1")).ok, true);
  assert.deepStrictEqual(k.sent[1], { method: "POST", path: "agents/run_1/place", body: { projectId: "p1", remember: true } });
  const b0 = B("g", 60, true, "browser");
  await k.st.remember({ kind: "block", block: b0 }, "p1", 7);
  assert.strictEqual(k.sent[2].body.ifSeq, 7); assert.strictEqual(k.sent[2].body.remember, true);
  assert.ok(k.st.notice());
  assert.strictEqual(k.st.petDock("run_1"), "p1");
  // HV-S17
  k = mkStage([]);
  r = await k.st.drop({ kind: "pet", runId: "r" }, null);
  assert.strictEqual(k.sent.length, 0); assert.strictEqual(r.reverted, true); assert.strictEqual(k.st.notice(), null);
  // HV-S18
  for (const bad of [{ status: 404 }, { status: 422 }, new Error("net")]) {
    k = mkStage([bad]); r = await k.st.drop({ kind: "pet", runId: "r", agent: "a" }, "p1");
    assert.strictEqual(r.reverted, true); assert.ok(k.st.notice()); assert.strictEqual(k.st.petDock("r"), null);
  }
  // 手动计时：点开始 / 停 -> 白条 -> 拖白条
  k = mkStage([{ status: 200, body: { taskId: null, projectId: null } }, { status: 200, body: { unplacedId: "u9" } }, { status: 200, body: { recorded: true } }]);
  await k.st.clickClock(); assert.strictEqual(k.sent[0].path, "timer/start-unplaced"); assert.strictEqual(k.st.timerRunning(), true);
  await k.st.clickClock(); assert.strictEqual(k.sent[1].path, "timer/stop"); assert.strictEqual(k.st.timerRunning(), false);
  assert.deepStrictEqual(k.st.strips().items.map((u) => u.id), ["u9"]);
  await k.st.drop({ kind: "strip", id: "u9" }, "p1");
  assert.strictEqual(k.sent[2].path, "timer/unplaced/u9/place"); assert.strictEqual(k.st.strips().items.length, 0);
  // 没在计时拖时钟 = 不发请求；计时中拖时钟 = timer/place
  k = mkStage([{ status: 200, body: {} }, { status: 200, body: {} }]);
  r = await k.st.drop({ kind: "clock" }, "p1"); assert.strictEqual(k.sent.length, 0); assert.strictEqual(r.reverted, true);
  await k.st.clickClock(); await k.st.drop({ kind: "clock" }, "p1");
  assert.strictEqual(k.sent[1].path, "timer/place"); assert.strictEqual(k.st.timerRunning(), true);
  // 有目标的计时停下不出白条
  k = mkStage([{ status: 200, body: {} }, { status: 200, body: {} }, { status: 200, body: { unplacedId: null } }]);
  await k.st.clickClock(); await k.st.drop({ kind: "clock" }, "p1"); await k.st.clickClock();
  assert.strictEqual(k.st.strips().items.length, 0);
  // 倒计时：start-unplaced 带 countdownSeconds，计时状态带 deadlineAt；停仍走 timer/stop
  k = mkStage([{ status: 200, body: {} }, { status: 200, body: { unplacedId: "u7" } }]);
  await k.st.startClock(1500); assert.deepStrictEqual(k.sent[0].body, { countdownSeconds: 1500 });
  assert.strictEqual(k.st.timerState().deadlineAt - k.st.timerState().startAt, 1500000);
  await k.st.clickClock(); assert.strictEqual(k.sent[1].path, "timer/stop"); assert.strictEqual(k.st.timerRunning(), false);
  // 开始失败：状态不变，有提示
  k = mkStage([{ status: 409 }]); await k.st.clickClock(); assert.strictEqual(k.st.timerRunning(), false); assert.ok(k.st.notice());
  console.log("hex-stage tests ok");
})().catch((e) => { console.error(e); process.exit(1); });
