// hive · data.js 单元测试（tree 读取 + zones/projects/tasks CRUD，无框架，Node 内置 assert）
// 跑法：node data.test.js（或 ./run_tests.sh 一次跑全部）
// 只读聚合视图（计时档案/甘特/导出）与排期依赖字段在同目录 data-views.test.js
// （原为单文件，超 500 行后按被测行为分组拆成两个自包含测试文件，无逻辑改动）。
"use strict";

var assert = require("assert");
var D = require("./data.js");

var passed = 0;
function test(name, fn) {
  return Promise.resolve().then(fn).then(function () {
    passed += 1;
    console.log("  ok - " + name);
  });
}

function fakeStorage() {
  var data = {};
  return {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
    setItem: function (k, v) { data[k] = String(v); },
    removeItem: function (k) { delete data[k]; }
  };
}

var SAMPLE_TREE = {
  zones: [{ id: "z_life", key: "710", name: "居家区", color: "#ff9d45", order: 0 }],
  projects: [
    {
      id: "p_1", key: "710-820", zoneId: "z_life", name: "阳台种菜",
      status: "active", progress: 72, progressSource: "computed",
      deadline: "2026-08-04",
      tasks: [
        { id: "t_1", key: "710-820-930-1", name: "浇水 3 盆", done: true, kind: "normal", flags: [] },
        { id: "t_2", key: "710-820-930-2", name: "临时任务", done: false, kind: "ephemeral", flags: ["ai-added"] }
      ]
    }
  ]
};

Promise.resolve()
  .then(function () { return test("buildTreeUrl 默认不带 includeEphemeral", function () {
    assert.strictEqual(D.buildTreeUrl(false), "/api/core/views/tree");
  }); })
  .then(function () { return test("buildTreeUrl(true) 带 includeEphemeral=true", function () {
    assert.strictEqual(D.buildTreeUrl(true), "/api/core/views/tree?includeEphemeral=true");
  }); })
  .then(function () { return test("isEmptyTree：zones/projects 都空 → true", function () {
    assert.strictEqual(D.isEmptyTree({ zones: [], projects: [] }), true);
    assert.strictEqual(D.isEmptyTree(null), true);
  }); })
  .then(function () { return test("isEmptyTree：有 zone 或 project → false", function () {
    assert.strictEqual(D.isEmptyTree(SAMPLE_TREE), false);
  }); })
  .then(function () { return test("deriveProjectColor：按 zoneId 从 zone.color 派生（R5）", function () {
    var zonesById = D.indexZonesById(SAMPLE_TREE.zones);
    var color = D.deriveProjectColor(SAMPLE_TREE.projects[0], zonesById);
    assert.strictEqual(color, "#ff9d45");
  }); })
  .then(function () { return test("deriveProjectColor：zone 找不到时给默认色，不崩", function () {
    var color = D.deriveProjectColor({ zoneId: "nope" }, {});
    assert.strictEqual(typeof color, "string");
    assert.ok(color.length > 0);
  }); })
  .then(function () { return test("deriveProjectIcon：取名字首字，空名字给占位符", function () {
    assert.strictEqual(D.deriveProjectIcon({ name: "阳台种菜" }), "阳");
    assert.strictEqual(D.deriveProjectIcon({ name: "" }), "•");
    assert.strictEqual(D.deriveProjectIcon({}), "•");
  }); })
  .then(function () { return test("computeSummary：不重算单项 progress，只做展示聚合", function () {
    var summary = D.computeSummary(SAMPLE_TREE);
    assert.strictEqual(summary.projectCount, 1);
    assert.strictEqual(summary.taskDone, 1);
    assert.strictEqual(summary.taskTotal, 2);
    assert.strictEqual(summary.avgProgress, 72); // 直接用 project.progress，不是前端重算
  }); })
  .then(function () { return test("fetchTree：网络成功 → source=network，写入缓存", function () {
    var storage = fakeStorage();
    var fetchImpl = function () {
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(SAMPLE_TREE); } });
    };
    return D.fetchTree({ fetchImpl: fetchImpl, storage: storage, includeEphemeral: false }).then(function (result) {
      assert.strictEqual(result.source, "network");
      assert.deepStrictEqual(result.tree, SAMPLE_TREE);
      assert.strictEqual(D.readCache(storage) !== null, true);
    });
  }); })
  .then(function () { return test("fetchTree：网络失败但有缓存 → source=cache（R3/R9）", function () {
    var storage = fakeStorage();
    D.writeCache(storage, SAMPLE_TREE);
    var fetchImpl = function () { return Promise.reject(new Error("network down")); };
    return D.fetchTree({ fetchImpl: fetchImpl, storage: storage }).then(function (result) {
      assert.strictEqual(result.source, "cache");
      assert.deepStrictEqual(result.tree, SAMPLE_TREE);
      assert.ok(result.error);
    });
  }); })
  .then(function () { return test("fetchTree：网络失败且无缓存 → source=none，不抛异常（R9）", function () {
    var storage = fakeStorage();
    var fetchImpl = function () { return Promise.reject(new Error("network down")); };
    return D.fetchTree({ fetchImpl: fetchImpl, storage: storage }).then(function (result) {
      assert.strictEqual(result.source, "none");
      assert.strictEqual(result.tree, null);
    });
  }); })
  .then(function () { return test("fetchTree：非 2xx 响应按失败处理，走缓存兜底路径", function () {
    var storage = fakeStorage();
    var fetchImpl = function () { return Promise.resolve({ ok: false, status: 500 }); };
    return D.fetchTree({ fetchImpl: fetchImpl, storage: storage }).then(function (result) {
      assert.strictEqual(result.source, "none");
    });
  }); })
  .then(function () { return test("fetchTree：includeEphemeral=true 时请求带查询参数", function () {
    var seenUrl = null;
    var fetchImpl = function (url) {
      seenUrl = url;
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ zones: [], projects: [] }); } });
    };
    return D.fetchTree({ fetchImpl: fetchImpl, storage: fakeStorage(), includeEphemeral: true }).then(function () {
      assert.strictEqual(seenUrl, "/api/core/views/tree?includeEphemeral=true");
    });
  }); })
  // ── 写操作（C9：新增写操作函数的单元测试）──────────────────────────
  .then(function () { return test("createZone：POST /api/core/planner/zones，只带 name（R1 统一入口）", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () {
        return Promise.resolve({ id: "z_1", key: "1", name: "灵感区", color: "#999999", order: 1 });
      } });
    };
    return D.createZone("灵感区", { fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(seen.url, "/api/core/planner/zones");
      assert.strictEqual(seen.method, "POST");
      assert.deepStrictEqual(seen.body, { name: "灵感区" });
      assert.strictEqual(result.ok, true);
      assert.strictEqual(result.data.id, "z_1");
    });
  }); })
  .then(function () { return test("renameZone：PATCH /api/core/planner/zones/{id} 只带 name（C6 + R1）", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "z_1" }); } });
    };
    return D.renameZone("z_1", "新名字", { fetchImpl: fetchImpl }).then(function () {
      assert.strictEqual(seen.url, "/api/core/planner/zones/z_1");
      assert.strictEqual(seen.method, "PATCH");
      assert.deepStrictEqual(seen.body, { name: "新名字" });
    });
  }); })
  .then(function () { return test("deleteZone：DELETE，204 → ok:true", function () {
    var fetchImpl = function () { return Promise.resolve({ ok: true, status: 204 }); };
    return D.deleteZone("z_1", { fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, true);
      assert.strictEqual(result.data, null);
    });
  }); })
  .then(function () { return test("deleteZone：409（还有子对象）→ ok:false，原样透传 detail（C3）", function () {
    var fetchImpl = function () {
      return Promise.resolve({ ok: false, status: 409, json: function () {
        return Promise.resolve({ detail: "分区 'z_1' 下还有 3 个项目——不做级联删除，先清空再删" });
      } });
    };
    return D.deleteZone("z_1", { fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 409);
      assert.strictEqual(result.message, "分区 'z_1' 下还有 3 个项目——不做级联删除，先清空再删");
    });
  }); })
  .then(function () { return test("createProject：POST /api/core/planner/projects，zoneId+name 必带，plannedWeight 可选（R1）", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "p_1" }); } });
    };
    return D.createProject("z_1", "新项目", { fetchImpl: fetchImpl }).then(function () {
      assert.strictEqual(seen.url, "/api/core/planner/projects");
      assert.deepStrictEqual(seen.body, { zoneId: "z_1", name: "新项目" });
      return D.createProject("z_1", "新项目2", { fetchImpl: fetchImpl, plannedWeight: 40 });
    }).then(function () {
      assert.deepStrictEqual(seen.body, { zoneId: "z_1", name: "新项目2", plannedWeight: 40 });
    });
  }); })
  .then(function () { return test("moveProject：PATCH /api/core/planner/projects/{id} 只带 zoneId，不带 name（C6 + R1）", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "p_1" }); } });
    };
    return D.moveProject("p_1", "z_2", { fetchImpl: fetchImpl }).then(function () {
      assert.strictEqual(seen.url, "/api/core/planner/projects/p_1");
      assert.deepStrictEqual(seen.body, { zoneId: "z_2" });
    });
  }); })
  .then(function () { return test("deleteProject：400 校验失败原样透传 detail（C4）", function () {
    var fetchImpl = function () {
      return Promise.resolve({ ok: false, status: 400, json: function () {
        return Promise.resolve({ detail: "zoneId 不存在：'z_nope'" });
      } });
    };
    return D.createProject("z_nope", "x", { fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 400);
      assert.strictEqual(result.message, "zoneId 不存在：'z_nope'");
    });
  }); })
  .then(function () { return test("createTask：POST /api/core/planner/tasks，projectId+name，不带 id（C5 + R1），plannedWeight 可选", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "t_1" }); } });
    };
    return D.createTask("p_1", "新任务", { fetchImpl: fetchImpl }).then(function () {
      assert.strictEqual(seen.url, "/api/core/planner/tasks");
      assert.deepStrictEqual(seen.body, { projectId: "p_1", name: "新任务" });
      assert.strictEqual(Object.prototype.hasOwnProperty.call(seen.body, "id"), false);
      return D.createTask("p_1", "新任务2", { fetchImpl: fetchImpl, plannedWeight: 30 });
    }).then(function () {
      assert.deepStrictEqual(seen.body, { projectId: "p_1", name: "新任务2", plannedWeight: 30 });
    });
  }); })
  .then(function () { return test("moveTask：PATCH /api/core/planner/tasks/{id} 只带 projectId（C6 + R1）", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "t_1" }); } });
    };
    return D.moveTask("t_1", "p_2", { fetchImpl: fetchImpl }).then(function () {
      assert.strictEqual(seen.url, "/api/core/planner/tasks/t_1");
      assert.deepStrictEqual(seen.body, { projectId: "p_2" });
    });
  }); })
  .then(function () { return test("toggleTaskDone：PATCH 只带 done（bool 化）", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "t_1" }); } });
    };
    return D.toggleTaskDone("t_1", true, { fetchImpl: fetchImpl }).then(function () {
      assert.deepStrictEqual(seen.body, { done: true });
      return D.toggleTaskDone("t_1", 0, { fetchImpl: fetchImpl });
    }).then(function () {
      assert.deepStrictEqual(seen.body, { done: false });
    });
  }); })
  .then(function () { return test("deleteTask：网络异常（fetch reject）→ ok:false，不抛异常（C8）", function () {
    var fetchImpl = function () { return Promise.reject(new Error("network down")); };
    return D.deleteTask("t_1", { fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 0);
      assert.ok(result.message.indexOf("network down") !== -1);
    });
  }); })
  .then(function () {
    console.log(passed + " passed");
  })
  .catch(function (err) {
    console.error("FAIL: " + (err && err.stack || err));
    process.exit(1);
  });
