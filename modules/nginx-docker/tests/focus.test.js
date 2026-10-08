// 共享件 static/focus.js（此刻的焦点的那一句话）—— 纯函数，node 内置 assert，无依赖。
// 入口：modules/hive/code/frontend/run_tests.sh（CI 的「hive 前端测试」）。
"use strict";
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const F = require("../static/focus.js");

const SINCE = "2026-10-09T10:12:30+08:00";
const focus = over => Object.assign({
  state: "present", app: "code", title: "plot.gd — garden", since: SINCE,
  projectId: null, projectName: null, taskId: null, taskName: null, source: null,
}, over);
const auto = over => Object.assign({
  taskId: "t1", projectId: "p1", taskName: "做题", projectName: "数学", since: "2026-10-09T10:15:00+08:00",
  app: "code", title: "garden", source: "rules", key: "wk_1",
}, over);

// 什么都没有 / 老后端 / 坏数据 → null（调用方照旧画空闲）
[null, undefined, {}, { running: false }, { focus: null, auto: null }, { focus: focus({ since: "不是时间" }) }]
  .forEach(src => assert.strictEqual(F.describe(src), null));

// 在电脑前、认不出目标：「正在用」，芯片退到窗口
let d = F.describe({ focus: focus() });
assert.deepStrictEqual(
  [d.state, d.auto, d.lead, d.chip, d.target, d.window, d.hint, d.projectId, d.taskId],
  ["present", false, "正在用", "正在：code · plot.gd — garden", "", "code · plot.gd — garden", "", null, null]);
assert.strictEqual(d.since, Date.parse(SINCE));
assert.strictEqual(F.describe({ focus: focus({ app: "", title: "" }) }).chip, "正在：电脑");
assert.strictEqual(F.describe({ focus: focus({ title: "" }) }).window, "code");

// 认到任务 / 只到项目；每种出处的叫法
d = F.describe({ focus: focus({ projectId: "p1", projectName: "数学", taskId: "t1", taskName: "做题", source: "history" }) });
assert.deepStrictEqual([d.lead, d.chip, d.target, d.hint, d.projectId, d.taskId, d.projectName, d.taskName],
  ["正在：数学 / 做题", "正在：数学", "数学 / 做题", "按以往", "p1", "t1", "数学", "做题"]);
d = F.describe({ focus: focus({ projectId: "p1", projectName: "数学", source: "agent-session" }) });
assert.deepStrictEqual([d.lead, d.chip, d.hint, d.taskId], ["正在：数学", "正在：数学", "来自会话", null]);
assert.deepStrictEqual(
  ["rules", "choice", "ai", "agent-session", "history", "别的", null].map(source =>
    F.describe({ focus: focus({ projectId: "p1", projectName: "数学", source }) }).hint),
  ["规则", "你选的", "AI 认的", "来自会话", "按以往", "", ""]);

// 离开：不带目标（就算服务端给了也不带）
d = F.describe({ focus: focus({ state: "afk", app: "", title: "", projectId: "p1", projectName: "数学", source: "history" }) });
assert.deepStrictEqual([d.state, d.lead, d.chip, d.target, d.hint, d.projectId, d.window],
  ["afk", "离开", "离开", "", "", null, ""]);

// 自动跟踪：字与起点以 auto 为准（同以前的芯片 / 人那张卡）；老后端只有 auto 没有 focus 也一样
const both = F.describe({
  auto: auto(),
  focus: focus({ projectId: "p1", projectName: "数学", taskId: "t1", taskName: "做题", source: "rules" }),
});
assert.deepStrictEqual([both.auto, both.lead, both.chip, both.hint, both.state],
  [true, "自动 · 数学 / 做题", "自动 · 数学 / 做题", "规则", "present"]);
assert.strictEqual(both.since, Date.parse("2026-10-09T10:15:00+08:00"));
assert.deepStrictEqual(F.describe({ auto: auto() }), both);
assert.strictEqual(F.describe({ auto: auto({ taskId: null, taskName: null }) }).lead, "自动 · 数学");
assert.strictEqual(F.describe({ auto: auto(), focus: focus({ state: "afk" }) }).state, "present");

// 走秒的字
assert.deepStrictEqual([0, 5, 65, 3599, 3600, 3725, 36000 + 61, -3, NaN, 59.9].map(F.clock),
  ["00:00", "00:05", "01:05", "59:59", "1:00:00", "1:02:05", "10:01:01", "00:00", "00:00", "00:59"]);

// 纯函数：不碰 DOM、不发请求、不读时钟；一个 innerHTML 都没有
const src = fs.readFileSync(path.join(__dirname, "../static/focus.js"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
["innerHTML", "document.", "fetch(", "Date.now", "setInterval", "localStorage"].forEach(word =>
  assert.ok(!src.includes(word), "focus.js 不该出现 " + word));

console.log("focus.test.js ok");
