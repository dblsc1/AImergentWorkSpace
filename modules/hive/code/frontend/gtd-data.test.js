// hive · gtd-data.js 单元测试（无框架，Node 内置 assert）
// 跑法：node gtd-data.test.js（run_tests.sh 会一并跑）
"use strict";

var assert = require("assert");
var G = require("./gtd-data.js");

var passed = 0;
function test(name, fn) {
  return Promise.resolve().then(fn).then(function () {
    passed += 1;
    console.log("  ok - " + name);
  });
}

var SAMPLE_TREE = {
  zones: [
    { id: "z_life", key: "1", name: "居家区", color: "#ff9d45", order: 0 },
    { id: "z_inbox", key: "999", name: "未分类", color: "#888888", order: 99 }
  ],
  projects: [
    {
      id: "p_1", key: "1-1", zoneId: "z_life", name: "多肉养护",
      status: "active", progress: 0, progressSource: "computed", deadline: null,
      tasks: []
    },
    {
      id: "p_inbox", key: "999-1", zoneId: "z_inbox", name: "收件箱",
      status: "active", progress: 0, progressSource: "computed", deadline: null,
      tasks: [
        { id: "t_a", key: "999-1-1", name: "记一下要买牛奶", done: false, kind: "normal", flags: [] },
        { id: "t_b", key: "999-1-2", name: "记一下要还书", done: false, kind: "normal", flags: [] }
      ]
    }
  ]
};

Promise.resolve()
  .then(function () { return test("P_INBOX_ID：固定 well-known id，不是猜的", function () {
    assert.strictEqual(G.P_INBOX_ID, "p_inbox");
  }); })
  .then(function () { return test("fetchNextActions：GET /api/core/views/next-actions，原样透传响应体", function () {
    var seenUrl = null;
    var fetchImpl = function (url) {
      seenUrl = url;
      return Promise.resolve({
        ok: true, status: 200,
        json: function () { return Promise.resolve({ today: "2026-08-10", zones: [] }); }
      });
    };
    return G.fetchNextActions({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(seenUrl, "/api/core/views/next-actions");
      assert.strictEqual(result.ok, true);
      assert.deepStrictEqual(result.data, { today: "2026-08-10", zones: [] });
    });
  }); })
  .then(function () { return test("fetchReview：GET /api/core/views/review，原样透传", function () {
    var seenUrl = null;
    var fetchImpl = function (url) {
      seenUrl = url;
      return Promise.resolve({
        ok: true, status: 200,
        json: function () { return Promise.resolve({ today: "2026-08-10", weekStart: "2026-08-10", weekEnd: "2026-08-16" }); }
      });
    };
    return G.fetchReview({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(seenUrl, "/api/core/views/review");
      assert.strictEqual(result.data.weekStart, "2026-08-10");
    });
  }); })
  .then(function () { return test("fetchNextActions：网络错误从不 reject（同 data.js 既有纪律）", function () {
    var fetchImpl = function () { return Promise.reject(new Error("boom")); };
    return G.fetchNextActions({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 0);
    });
  }); })
  .then(function () { return test("fetchLastWriterMaps：并行拉 projects+tasks，取 id→lastWriter 映射", function () {
    var fetchImpl = function (url) {
      if (url === "/api/core/planner/projects") {
        return Promise.resolve({ ok: true, status: 200, json: function () {
          return Promise.resolve([{ id: "p_1", lastWriter: "human" }, { id: "p_2", lastWriter: "ai" }]);
        } });
      }
      if (url === "/api/core/planner/tasks") {
        return Promise.resolve({ ok: true, status: 200, json: function () {
          return Promise.resolve([{ id: "t_1", lastWriter: "ai" }, { id: "t_2", lastWriter: "human" }]);
        } });
      }
      throw new Error("unexpected url " + url);
    };
    return G.fetchLastWriterMaps({ fetchImpl: fetchImpl }).then(function (maps) {
      assert.deepStrictEqual(maps.projects, { p_1: "human", p_2: "ai" });
      assert.deepStrictEqual(maps.tasks, { t_1: "ai", t_2: "human" });
    });
  }); })
  .then(function () { return test("fetchLastWriterMaps：任一列表失败不让另一份陪葬，失败侧映射为空对象", function () {
    var fetchImpl = function (url) {
      if (url === "/api/core/planner/projects") {
        return Promise.resolve({ ok: false, status: 500, json: function () { return Promise.resolve(null); } });
      }
      return Promise.resolve({ ok: true, status: 200, json: function () {
        return Promise.resolve([{ id: "t_1", lastWriter: "ai" }]);
      } });
    };
    return G.fetchLastWriterMaps({ fetchImpl: fetchImpl }).then(function (maps) {
      assert.deepStrictEqual(maps.projects, {});
      assert.deepStrictEqual(maps.tasks, { t_1: "ai" });
    });
  }); })
  .then(function () { return test("isAiWritten：命中 ai 为真，命中 human/缺失/空映射均为假", function () {
    var map = { t_1: "ai", t_2: "human" };
    assert.strictEqual(G.isAiWritten(map, "t_1"), true);
    assert.strictEqual(G.isAiWritten(map, "t_2"), false);
    assert.strictEqual(G.isAiWritten(map, "t_missing"), false);
    assert.strictEqual(G.isAiWritten(null, "t_1"), false);
    assert.strictEqual(G.isAiWritten(map, null), false);
  }); })
  .then(function () { return test("quickCapture：POST /api/core/planner/tasks，projectId 固定 p_inbox，只带 name（F-INBOX-2）", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "t_new" }); } });
    };
    return G.quickCapture("买牛奶", { fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, true);
      assert.strictEqual(seen.url, "/api/core/planner/tasks");
      assert.deepStrictEqual(seen.body, { projectId: "p_inbox", name: "买牛奶" });
    });
  }); })
  .then(function () { return test("listInboxTasks：只列 p_inbox 下的任务，树里没有 p_inbox 时返回空数组不崩", function () {
    var tasks = G.listInboxTasks(SAMPLE_TREE);
    assert.strictEqual(tasks.length, 2);
    assert.strictEqual(tasks[0].id, "t_a");
    assert.deepStrictEqual(G.listInboxTasks({ zones: [], projects: [] }), []);
    assert.deepStrictEqual(G.listInboxTasks(null), []);
  }); })
  .then(function () { return test("listNonInboxProjects：排除 p_inbox 自己，标签是「分区 / 项目」", function () {
    var options = G.listNonInboxProjects(SAMPLE_TREE);
    assert.strictEqual(options.length, 1);
    assert.deepStrictEqual(options[0], { id: "p_1", label: "居家区 / 多肉养护" });
  }); })
  .then(function () {
    console.log(passed + " passed");
  })
  .catch(function (err) {
    console.error("FAIL:", err);
    process.exitCode = 1;
  });
