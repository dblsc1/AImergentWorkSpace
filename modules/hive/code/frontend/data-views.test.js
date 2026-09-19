// hive · data.js 单元测试（只读视图 + 排期/依赖 + 导出，无框架，Node 内置 assert）
// 拆分理由（单文件超 500 行）：
// 与 data.test.js 是同一个 data.js 的两半测试——data.test.js 覆盖 tree 读取与
// zones/projects/tasks 的 CRUD 写路径；本文件覆盖只读聚合视图（计时档案/甘特/导出）
// 与排期依赖字段（F-TABLE-3），按被测行为分组而非按物理模块，边界干净、互不依赖。
// 跑法：node data-views.test.js（run_tests.sh 会一并跑）
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
  // ── 计时档案（R5-R10）──────────────────────────────────────────────
  .then(function () { return test("buildArchiveUrl：默认 type=session.completed，limit=50，offset=0（R8）", function () {
    assert.strictEqual(D.buildArchiveUrl(), "/api/core/events?type=session.completed&limit=50&offset=0");
    assert.strictEqual(D.ARCHIVE_DEFAULT_LIMIT, 50);
  }); })
  .then(function () { return test("buildArchiveUrl：limit/offset 可覆盖（R8 分页）", function () {
    assert.strictEqual(D.buildArchiveUrl({ limit: 10, offset: 20 }),
      "/api/core/events?type=session.completed&limit=10&offset=20");
  }); })
  .then(function () { return test("fetchArchive：网络成功 → ok:true，请求带 type/limit/offset", function () {
    var seenUrl = null;
    var fetchImpl = function (url) {
      seenUrl = url;
      return Promise.resolve({ ok: true, status: 200, json: function () {
        return Promise.resolve({ total: 1, items: [{ id: "evt_1" }] });
      } });
    };
    return D.fetchArchive({ fetchImpl: fetchImpl, limit: 5, offset: 0 }).then(function (result) {
      assert.strictEqual(seenUrl, "/api/core/events?type=session.completed&limit=5&offset=0");
      assert.strictEqual(result.ok, true);
      assert.strictEqual(result.data.total, 1);
      assert.strictEqual(result.data.items.length, 1);
    });
  }); })
  .then(function () { return test("fetchArchive：网络异常 → ok:false，不抛异常（C8 同款约定）", function () {
    var fetchImpl = function () { return Promise.reject(new Error("network down")); };
    return D.fetchArchive({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 0);
      assert.ok(result.message.indexOf("network down") !== -1);
    });
  }); })
  .then(function () { return test("formatArchiveDate / formatArchiveClock：年月日不补零，时分补零", function () {
    assert.strictEqual(D.formatArchiveDate(new Date("2026-08-01T13:15:00+00:00")), "2026年8月1日");
    assert.strictEqual(D.formatArchiveClock(new Date("2026-08-01T13:05:00+00:00")), "21:05");
  }); })
  .then(function () { return test("formatArchiveWhen：起止时间取 data.startAt（起）与 time（止），R6 示例逐字节核对", function () {
    assert.strictEqual(
      D.formatArchiveWhen("2026-08-01T13:15:00+00:00", "2026-08-01T13:47:00+00:00"),
      "2026年8月1日 21:15–21:47"
    );
  }); })
  .then(function () { return test("formatArchiveDuration：durationSeconds 四舍五入到分钟，不足一分钟给 <1分钟", function () {
    assert.strictEqual(D.formatArchiveDuration(1920), "32分钟");
    assert.strictEqual(D.formatArchiveDuration(90), "2分钟");
    assert.strictEqual(D.formatArchiveDuration(3), "<1分钟");
    assert.strictEqual(D.formatArchiveDuration(0), "0分钟");
  }); })
  .then(function () { return test("resolveArchiveLabel：project+task 都能查到 → 「项目 / 任务」（R7）", function () {
    assert.strictEqual(D.resolveArchiveLabel(SAMPLE_TREE, { project: "p_1", task: "t_1" }), "阳台种菜 / 浇水 3 盆");
  }); })
  .then(function () { return test("resolveArchiveLabel：task 查不到（已删除）→ id + 「（任务已删除）」，不崩（R7）", function () {
    assert.strictEqual(
      D.resolveArchiveLabel(SAMPLE_TREE, { project: "p_1", task: "t_ghost" }),
      "阳台种菜 / t_ghost（任务已删除）"
    );
  }); })
  .then(function () { return test("resolveArchiveLabel：project 查不到（已删除）→ id + 「（项目已删除）」，不崩（项目/任务两类 id 同样泛化）", function () {
    assert.strictEqual(D.resolveArchiveLabel(SAMPLE_TREE, { project: "p_ghost" }), "p_ghost（项目已删除）");
  }); })
  .then(function () { return test("resolveArchiveLabel：subject.task 未填（选填字段）→ 只展示项目，不拼 \" / \"", function () {
    assert.strictEqual(D.resolveArchiveLabel(SAMPLE_TREE, { project: "p_1" }), "阳台种菜");
  }); })
  .then(function () { return test("mapArchiveEvent：完整映射一条事实，summary 与设计示例格式逐字节一致（R6）", function () {
    var event = {
      id: "evt_1",
      time: "2026-08-01T13:47:00+00:00",
      subject: { zone: "z_life", project: "p_1", task: "t_1" },
      data: { startAt: "2026-08-01T13:15:00+00:00", durationSeconds: 1920 }
    };
    var mapped = D.mapArchiveEvent(event, SAMPLE_TREE);
    assert.strictEqual(mapped.id, "evt_1");
    assert.strictEqual(mapped.summary, "2026年8月1日 21:15–21:47 · 阳台种菜 / 浇水 3 盆 · 32分钟");
  }); })
  .then(function () { return test("mapArchiveEvent：task 已删除仍能出结果，不抛异常（R7）", function () {
    var event = {
      id: "evt_2",
      time: "2026-08-01T13:47:00+00:00",
      subject: { zone: "z_life", project: "p_1", task: "t_ghost" },
      data: { startAt: "2026-08-01T13:15:00+00:00", durationSeconds: 60 }
    };
    var mapped = D.mapArchiveEvent(event, SAMPLE_TREE);
    assert.ok(mapped.summary.indexOf("t_ghost（任务已删除）") !== -1);
  }); })
  .then(function () { return test("mapArchiveEvent：tree 为 null（未加载完）也不抛异常", function () {
    var event = {
      id: "evt_3",
      time: "2026-08-01T13:47:00+00:00",
      subject: { zone: "z_life", project: "p_1" },
      data: { startAt: "2026-08-01T13:15:00+00:00", durationSeconds: 60 }
    };
    var mapped = D.mapArchiveEvent(event, null);
    assert.ok(mapped.summary.indexOf("（项目已删除）") !== -1);
  }); })
  // ── 任务排期 + 前置任务（F-TABLE-3，防御式）─────────────────────────
  .then(function () { return test("hasScheduleFields：任务对象含 dependsOn 键 → true（feature-detect 信号）", function () {
    assert.strictEqual(D.hasScheduleFields({ id: "t_1", dependsOn: [] }), true);
    assert.strictEqual(D.hasScheduleFields({ id: "t_1", dependsOn: null }), true); // 键在，值是 null 也算"有"
  }); })
  .then(function () { return test("hasScheduleFields：没有 dependsOn 键 → false（今天的 nexus-core 现状）", function () {
    assert.strictEqual(D.hasScheduleFields({ id: "t_1", name: "x" }), false);
    assert.strictEqual(D.hasScheduleFields(null), false);
    assert.strictEqual(D.hasScheduleFields(undefined), false);
  }); })
  .then(function () { return test("updateTaskPlan：PATCH 只带 plan，plan:null 用于清空排期", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "t_1" }); } });
    };
    return D.updateTaskPlan("t_1", { start: "2026-08-01", end: "2026-08-10" }, { fetchImpl: fetchImpl }).then(function () {
      assert.strictEqual(seen.url, "/api/core/planner/tasks/t_1");
      assert.deepStrictEqual(seen.body, { plan: { start: "2026-08-01", end: "2026-08-10" } });
      return D.updateTaskPlan("t_1", null, { fetchImpl: fetchImpl });
    }).then(function () {
      assert.deepStrictEqual(seen.body, { plan: null });
    });
  }); })
  .then(function () { return test("updateTaskDependsOn：PATCH 只带 dependsOn，缺省/null 归一化成空数组", function () {
    var seen = null;
    var fetchImpl = function (url, init) {
      seen = { url: url, method: init.method, body: JSON.parse(init.body) };
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ id: "t_1" }); } });
    };
    return D.updateTaskDependsOn("t_1", ["t_2", "t_3"], { fetchImpl: fetchImpl }).then(function () {
      assert.deepStrictEqual(seen.body, { dependsOn: ["t_2", "t_3"] });
      return D.updateTaskDependsOn("t_1", null, { fetchImpl: fetchImpl });
    }).then(function () {
      assert.deepStrictEqual(seen.body, { dependsOn: [] });
    });
  }); })
  .then(function () { return test("updateTaskDependsOn：422（今天后端还没这个字段）原样透传 detail（哪怕是非字符串）", function () {
    // 现状真实行为（读 nexus-core schemas.py 源码确认，TaskUpdate extra=\"forbid\"）：
    // pydantic 拒绝未知字段会给 422，detail 是数组不是字符串。data.js 不需要为这个
    // 特殊形状加逻辑——本函数只应该在 hasScheduleFields() 判定为 true 后才被调用，
    // 这条用例确认"就算真的打过去了"，也不会抛异常、能拿到某种 message。
    var fetchImpl = function () {
      return Promise.resolve({ ok: false, status: 422, json: function () {
        return Promise.resolve({ detail: [{ loc: ["body", "dependsOn"], msg: "Extra inputs are not permitted", type: "extra_forbidden" }] });
      } });
    };
    return D.updateTaskDependsOn("t_1", ["t_2"], { fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 422);
      assert.ok(result.message); // 是个数组也行，只要不是 undefined/抛异常
    });
  }); })
  .then(function () { return test("listOtherTasks：跨项目列出除自己以外的全部任务，标签\"项目 / 任务\"", function () {
    var tree = {
      projects: [
        { id: "p_1", name: "多肉养护", tasks: [{ id: "t_1", name: "换盆" }, { id: "t_2", name: "施肥" }] },
        { id: "p_2", name: "香草盆栽", tasks: [{ id: "t_3", name: "修剪" }] }
      ]
    };
    var list = D.listOtherTasks(tree, "t_1");
    assert.deepStrictEqual(list, [
      { id: "t_2", label: "多肉养护 / 施肥" },
      { id: "t_3", label: "香草盆栽 / 修剪" }
    ]);
  }); })
  .then(function () { return test("listOtherTasks：空树不崩，返回空数组", function () {
    assert.deepStrictEqual(D.listOtherTasks(null, "t_1"), []);
  }); })
  // ── 甘特读端（F-TABLE-1 双轨的数据源）───────────────────────────────
  .then(function () { return test("fetchGantt：GET /api/core/views/gantt，不带查询参数", function () {
    var seenUrl = null;
    var fetchImpl = function (url) {
      seenUrl = url;
      return Promise.resolve({ ok: true, status: 200, json: function () {
        return Promise.resolve({ projects: [], today: "2026-08-07" });
      } });
    };
    return D.fetchGantt({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(seenUrl, "/api/core/views/gantt");
      assert.strictEqual(result.ok, true);
      assert.strictEqual(result.data.today, "2026-08-07");
    });
  }); })
  .then(function () { return test("fetchGantt：网络异常 → ok:false，不抛异常（C8 同款约定）", function () {
    var fetchImpl = function () { return Promise.reject(new Error("network down")); };
    return D.fetchGantt({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 0);
    });
  }); })
  // ── 只读全量导出（顶栏「导出数据」按钮，contract.md v1.3）───────────
  .then(function () { return test("fetchExport：GET /api/core/export，不带查询参数", function () {
    var seenUrl = null;
    var fetchImpl = function (url) {
      seenUrl = url;
      return Promise.resolve({ ok: true, status: 200, json: function () {
        return Promise.resolve({
          zones: [], projects: [], tasks: [], events: [],
          projections: { proj_current: null, proj_daily_stats: [] },
          exportedAt: "2026-08-09T12:00:00+00:00"
        });
      } });
    };
    return D.fetchExport({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(seenUrl, "/api/core/export");
      assert.strictEqual(result.ok, true);
      assert.strictEqual(result.data.exportedAt, "2026-08-09T12:00:00+00:00");
    });
  }); })
  .then(function () { return test("fetchExport：404（端点未上线）→ ok:false, status:404，不抛异常", function () {
    var fetchImpl = function () {
      return Promise.resolve({ ok: false, status: 404, json: function () { return Promise.resolve(null); } });
    };
    return D.fetchExport({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 404);
    });
  }); })
  .then(function () { return test("fetchExport：网络异常 → ok:false, status:0，不抛异常（C8 同款约定）", function () {
    var fetchImpl = function () { return Promise.reject(new Error("network down")); };
    return D.fetchExport({ fetchImpl: fetchImpl }).then(function (result) {
      assert.strictEqual(result.ok, false);
      assert.strictEqual(result.status, 0);
    });
  }); })
  .then(function () { return test("exportFileName：文件名日期取自 exportedAt 前 10 位，不本地拼日期", function () {
    assert.strictEqual(D.exportFileName("2026-08-09T12:00:00+00:00"), "cockpit-export-2026-08-09.json");
    assert.strictEqual(D.exportFileName("2026-01-01T00:00:00+08:00"), "cockpit-export-2026-01-01.json");
  }); })
  .then(function () { return test("exportFileName：exportedAt 缺失/非字符串不崩，回落 unknown-date", function () {
    assert.strictEqual(D.exportFileName(undefined), "cockpit-export-unknown-date.json");
    assert.strictEqual(D.exportFileName(null), "cockpit-export-unknown-date.json");
    assert.strictEqual(D.exportFileName(12345), "cockpit-export-unknown-date.json");
  }); })
  .then(function () {
    console.log(passed + " passed");
  })
  .catch(function (err) {
    console.error("FAIL: " + (err && err.stack || err));
    process.exit(1);
  });
