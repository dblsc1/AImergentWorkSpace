// hive · hex-timer.js 单元测试（无框架，Node 内置 assert）
// 跑法：node hex-timer.test.js（run_tests.sh 会一并跑）
//
// 钉的是仓主 2026-10-08 的改判：长按项目格**不再建**「日期 时间」任务，
// 而是取（没有就建）这个项目的「未分类」时间桶，对它计时。
// hex-timer.js 是浏览器里的 IIFE，这里给它最小的假 window / document，只看它发了哪些请求。
"use strict";

var assert = require("assert");

var timers = [];
global.window = { setTimeout: function (fn) { timers.push(fn); return timers.length; } };
global.document = { querySelector: function () { return null; }, getElementById: function () { return null; } };
require("./hex-timer.js");
var T = global.window.NexusTableHexTimer;

function fakeEl(cls, dataset, parent) {
  return {
    dataset: dataset || {}, parent: parent || null, textContent: "",
    style: { setProperty: function () {}, removeProperty: function () {} },
    classList: { contains: function (c) { return cls.indexOf(c) !== -1; }, add: function () {}, remove: function () {} },
    querySelector: function () { return null; },
    closest: function (sel) {
      for (var n = this; n; n = n.parent) {
        if (sel === ".hex-cell" && n.classList.contains("hex-cell")) return n;
        if (sel === ".hex-todo-card[data-task-id]" && n.classList.contains("hex-todo-card") && n.dataset.taskId) return n;
      }
      return null;
    }
  };
}

// 按下 → 让长按定时器到点 → 等请求链走完，返回期间发出的全部 POST 与状态栏文案
function longPress(target, current) {
  var posts = [], status = [];
  T.init({
    state: { current: current || null, byId: { p_1: { cell: { project: { id: "p_1", name: "数学" } } } }, centerItem: null },
    reduceMotion: function () { return true; },
    setStatus: function (s) { status.push(s); },
    paintCenter: function () {}, refresh: function () {},
    currentPath: "/api/core/views/current",
    getJson: function () { return Promise.resolve({ ok: true, data: { running: true } }); },
    postJson: function (path, body) {
      posts.push({ path: path, body: body });
      return Promise.resolve(path.indexOf("/unclassified") !== -1
        ? { ok: true, data: { taskId: "t_unc_p_1" } } : { ok: true, data: {} });
    }
  });
  timers.length = 0;
  T.onPressDown({ button: 0, clientX: 0, clientY: 0, target: target });
  assert.strictEqual(timers.length, 1, "按下应该只挂一个长按定时器");
  timers[0]();
  return new Promise(function (resolve) { setTimeout(resolve, 20); })
    .then(function () { T.onPressUp(); return { posts: posts, status: status }; });
}

var passed = 0;
function test(name, fn) {
  return Promise.resolve().then(fn).then(function () { passed += 1; console.log("  ok - " + name); });
}

var cell = fakeEl(["hex-cell"], { projectId: "p_1" });
var card = fakeEl(["hex-todo-card"], { taskId: "t_9" }, cell);

Promise.resolve()
  .then(function () { return test("长按项目格：取未分类时间桶 → 对它计时；绝不建任务", function () {
    return longPress(cell, { running: false }).then(function (r) {
      assert.deepStrictEqual(r.posts, [
        { path: "/api/core/planner/projects/p_1/unclassified", body: undefined },
        { path: "/api/core/timer/start", body: { taskId: "t_unc_p_1" } }
      ]);
      assert.ok(!r.posts.some(function (p) { return /planner\/tasks/.test(p.path); }), "不许再 POST planner/tasks");
      assert.deepStrictEqual(r.status, ["开始计时：数学…", "正在计时：数学"]);   // 只说项目名，没有时间戳
    });
  }); })
  .then(function () { return test("长按项目格：正在计别的 → 先 stop 再 start，仍然不建任务", function () {
    return longPress(cell, { running: true }).then(function (r) {
      assert.deepStrictEqual(r.posts.map(function (p) { return p.path; }), [
        "/api/core/planner/projects/p_1/unclassified", "/api/core/timer/stop", "/api/core/timer/start"
      ]);
    });
  }); })
  .then(function () { return test("长按待办卡片：那条任务已经存在，直接计时，不碰未分类", function () {
    return longPress(card, { running: false }).then(function (r) {
      assert.deepStrictEqual(r.posts, [{ path: "/api/core/timer/start", body: { taskId: "t_9" } }]);
    });
  }); })
  .then(function () { console.log(passed + " passed"); })
  .catch(function (err) { console.error("FAIL: " + (err && err.stack || err)); process.exit(1); });
