// gantt/code/frontend · gantt-data.js 单元测试（无框架，Node 内置 assert）
// 跑法：node gantt-data.test.js（或 ./run_tests.sh），与 hive/data.test.js 同一套约定
// （新增功能代码同批新增测试）。
"use strict";

var assert = require("assert");
var D = require("./gantt-data.js");

var passed = 0;
function test(name, fn) {
  return Promise.resolve().then(fn).then(function () {
    passed += 1;
    console.log("  ok - " + name);
  });
}

function fakeFetch(status, body, capture) {
  return function (path, init) {
    if (capture) capture.push({ path: path, init: init });
    return Promise.resolve({
      ok: status >= 200 && status < 300,
      status: status,
      json: function () { return Promise.resolve(body); }
    });
  };
}

var SAMPLE_VIEW = {
  projects: [
    { id: "p_1", key: "1-2", name: "学吉他",
      plan: { start: "2026-08-01", end: "2026-08-10" },
      actual: [
        { date: "2026-08-01", seconds: 12600 },
        { date: "2026-08-02", seconds: 0 }
      ] },
    { id: "p_2", key: "1-3", name: "练字计划", plan: null, actual: [] }
  ],
  today: "2026-08-02"
};

// 事实层相邻分段视觉连成一组（见 style.css 对应说明）样本：
// 01-04 连续四天（连续多天打卡类任务的常见形状）、跳过 05-07（0秒/缺失，
// 中间空 3 天）、08-09 连续两天——覆盖"连续多天要挂 join 类"和"有间隔不挂"
// 两种边界，任务层同一约定另建一个带 actual 的 task 样本。
var SAMPLE_VIEW_WITH_JOIN = {
  projects: [
    { id: "p_30", key: "4-1", name: "连续事实分段",
      plan: { start: "2026-08-01", end: "2026-08-18" },
      actual: [
        { date: "2026-08-01", seconds: 100 },
        { date: "2026-08-02", seconds: 100 },
        { date: "2026-08-03", seconds: 100 },
        { date: "2026-08-04", seconds: 100 },
        { date: "2026-08-08", seconds: 100 },
        { date: "2026-08-09", seconds: 100 }
      ],
      tasks: [
        { id: "t_30", key: "4-1-1", name: "连续任务事实分段", done: false, plan: null,
          actual: [
            { date: "2026-08-01", seconds: 50 },
            { date: "2026-08-02", seconds: 50 },
            { date: "2026-08-04", seconds: 50 }
          ] }
      ] }
  ],
  today: "2026-08-02"
};

// L2：任务层样本——特意覆盖「在界内」「超出项目窗口」「项目无计划」「未排期」
// 四种任务，isTaskOutOfBounds 的判定矩阵靠这份样本喂（不复用 SAMPLE_VIEW，
// 避免改动它牵连既有 D1-D12 测试的下标假设）。
var SAMPLE_VIEW_WITH_TASKS = {
  projects: [
    { id: "p_10", key: "2-1", name: "带任务的项目",
      plan: { start: "2026-08-01", end: "2026-08-10" },
      actual: [],
      tasks: [
        { id: "t_1", key: "2-1-1", name: "在界内的任务", done: false,
          plan: { start: "2026-08-02", end: "2026-08-05" },
          actual: [{ date: "2026-08-02", seconds: 1800 }] },
        { id: "t_2", key: "2-1-2", name: "越出项目窗口的任务", done: true,
          plan: { start: "2026-08-09", end: "2026-08-15" }, actual: [] },
        { id: "t_3", key: "2-1-3", name: "未排期的任务", done: false,
          plan: null, actual: [] }
      ] },
    { id: "p_11", key: "2-2", name: "无计划期的项目", plan: null, actual: [],
      tasks: [
        { id: "t_4", key: "2-2-1", name: "挂在无计划项目下的任务", done: false,
          plan: { start: "2026-08-01", end: "2026-08-03" }, actual: [] }
      ] }
  ],
  today: "2026-08-02"
};

// L3：依赖箭头样本——覆盖正常/冲突/未排期端（有实际记录可锚/完全没有可锚）/
// 跨项目/依赖指向不存在的任务五种形状，dependencyConflict/dependencyArrowState/
// taskAnchorItemId/buildDependencyArrows/computeConflictProjectIds 都靠这份样本喂。
var SAMPLE_VIEW_WITH_DEPS = {
  projects: [
    { id: "p_20", key: "3-1", name: "项目A", plan: { start: "2026-08-01", end: "2026-08-20" }, actual: [],
      tasks: [
        { id: "tA1", key: "3-1-1", name: "前置A", done: false,
          plan: { start: "2026-08-01", end: "2026-08-05" }, dependsOn: [], actual: [] },
        { id: "tA2", key: "3-1-2", name: "后置A-正常", done: false,
          plan: { start: "2026-08-06", end: "2026-08-10" }, dependsOn: ["tA1"], actual: [] },
        { id: "tA3", key: "3-1-3", name: "后置A-冲突", done: false,
          plan: { start: "2026-08-03", end: "2026-08-08" }, dependsOn: ["tA1"], actual: [] },
        { id: "tA4", key: "3-1-4", name: "无计划但有实际", done: false,
          plan: null, dependsOn: ["tA1"],
          actual: [{ date: "2026-08-03", seconds: 0 }, { date: "2026-08-04", seconds: 900 }] },
        { id: "tA5", key: "3-1-5", name: "完全未排期无实际", done: false,
          plan: null, dependsOn: ["tA1"], actual: [] },
        { id: "tA6", key: "3-1-6", name: "依赖指向不存在的任务", done: false,
          plan: { start: "2026-08-01", end: "2026-08-02" }, dependsOn: ["t_ghost"], actual: [] }
      ] },
    { id: "p_21", key: "3-2", name: "项目B（跨项目依赖 A1）", plan: { start: "2026-08-01", end: "2026-08-10" }, actual: [],
      tasks: [
        { id: "tB1", key: "3-2-1", name: "跨项目后置-冲突", done: false,
          plan: { start: "2026-08-02", end: "2026-08-04" }, dependsOn: ["tA1"], actual: [] }
      ] },
    { id: "p_22", key: "3-3", name: "无依赖的项目", plan: { start: "2026-08-01", end: "2026-08-05" }, actual: [],
      tasks: [
        { id: "tC1", key: "3-3-1", name: "不依赖任何任务", done: false,
          plan: { start: "2026-08-01", end: "2026-08-02" }, dependsOn: [], actual: [] }
      ] }
  ],
  today: "2026-08-02"
};

Promise.resolve()
  // ── 日期：本地日历日构造，避开 UTC 解析错位（红线 bug 同一类）────────
  .then(function () { return test("localDateFromISO：按本地日历日构造，不经 UTC", function () {
    var d = D.localDateFromISO("2026-08-02");
    assert.strictEqual(d.getFullYear(), 2026);
    assert.strictEqual(d.getMonth(), 7); // 0-indexed
    assert.strictEqual(d.getDate(), 2);
  }); })
  .then(function () { return test("localDateFromISO：非法输入返回 null，不崩", function () {
    assert.strictEqual(D.localDateFromISO(""), null);
    assert.strictEqual(D.localDateFromISO(null), null);
  }); })
  .then(function () { return test("formatLocalISO 与 localDateFromISO 互为逆运算", function () {
    assert.strictEqual(D.formatLocalISO(D.localDateFromISO("2026-01-05")), "2026-01-05");
  }); })
  .then(function () { return test("addDaysISO：跨月正确进位", function () {
    assert.strictEqual(D.addDaysISO("2026-01-30", 3), "2026-02-02");
  }); })
  .then(function () { return test("addDaysISO：跨年正确进位", function () {
    assert.strictEqual(D.addDaysISO("2026-12-30", 3), "2027-01-02");
  }); })

  // ── 时长展示 ─────────────────────────────────────────────────────
  .then(function () { return test("formatMinutes：四舍五入到分钟", function () {
    assert.strictEqual(D.formatMinutes(12600), "210分钟");
    assert.strictEqual(D.formatMinutes(90), "2分钟");
  }); })
  .then(function () { return test("formatMinutes：不足一分钟但有时长给 <1分钟，不是 0分钟", function () {
    assert.strictEqual(D.formatMinutes(20), "<1分钟");
  }); })
  .then(function () { return test("formatMinutes：确实是 0 就给 0分钟", function () {
    assert.strictEqual(D.formatMinutes(0), "0分钟");
  }); })

  // ── 未排期判定（D9）────────────────────────────────────────────────
  .then(function () { return test("isUnscheduled：plan 为 null 时为 true", function () {
    assert.strictEqual(D.isUnscheduled({ plan: null }), true);
    assert.strictEqual(D.isUnscheduled({}), true);
  }); })
  .then(function () { return test("isUnscheduled：plan 有值时为 false", function () {
    assert.strictEqual(D.isUnscheduled({ plan: { start: "a", end: "b" } }), false);
  }); })

  // ── group 模型：未排期标记出来（D9），怎么画交给 DOM 层 ─────────────────
  .then(function () { return test("buildGroupModels：已排期 unscheduled=false", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW.projects);
    assert.strictEqual(models.length, 2);
    assert.strictEqual(models[0].id, "p_1");
    assert.strictEqual(models[0].name, "学吉他");
    assert.strictEqual(models[0].unscheduled, false);
  }); })
  .then(function () { return test("buildGroupModels：未排期 unscheduled=true", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW.projects);
    assert.strictEqual(models[1].id, "p_2");
    assert.strictEqual(models[1].unscheduled, true);
  }); })

  // ── L6：group 模型新增 plan 字段（预填"改期"弹窗用，D9 之外的新分支）────
  .then(function () { return test("buildGroupModels：已排期项目的 plan 是 {start,end} 快照", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW.projects);
    assert.deepStrictEqual(models[0].plan, { start: "2026-08-01", end: "2026-08-10" });
  }); })
  .then(function () { return test("buildGroupModels：未排期项目的 plan 是 null，不是 undefined", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW.projects);
    assert.strictEqual(models[1].plan, null);
  }); })
  .then(function () { return test("buildGroupModels：已排期任务的 plan 是 {start,end} 快照", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW_WITH_TASKS.projects);
    var t1 = models.filter(function (m) { return m.id === "t_1"; })[0];
    assert.deepStrictEqual(t1.plan, { start: "2026-08-02", end: "2026-08-05" });
  }); })
  .then(function () { return test("buildGroupModels：未排期任务的 plan 是 null", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW_WITH_TASKS.projects);
    var t3 = models.filter(function (m) { return m.id === "t_3"; })[0]; // plan:null
    assert.strictEqual(t3.plan, null);
  }); })
  .then(function () { return test("buildGroupModels：plan 快照是浅拷贝，不是同一个对象引用（防调用方改了顺手污染源数据）", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW.projects);
    assert.notStrictEqual(models[0].plan, SAMPLE_VIEW.projects[0].plan);
    assert.deepStrictEqual(models[0].plan, SAMPLE_VIEW.projects[0].plan);
  }); })

  // ── L2：任务层 group 建模（nestedGroups 用的扁平数组，kind 区分行类型）────
  .then(function () { return test("buildGroupModels：项目行带 kind/taskIds，紧跟其任务子行", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW_WITH_TASKS.projects);
    assert.strictEqual(models[0].kind, "project");
    assert.strictEqual(models[0].id, "p_10");
    assert.deepStrictEqual(models[0].taskIds, ["t_1", "t_2", "t_3"]);
    assert.strictEqual(models[1].kind, "task");
    assert.strictEqual(models[1].id, "t_1");
    assert.strictEqual(models[1].projectId, "p_10");
  }); })
  .then(function () { return test("buildGroupModels：无任务的项目 taskIds 是空数组，不是 undefined", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW.projects); // 沿用旧样本，无 tasks 字段
    assert.deepStrictEqual(models[0].taskIds, []);
  }); })
  .then(function () { return test("buildGroupModels：任务行的 done/unscheduled 各自独立判定", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW_WITH_TASKS.projects);
    var t2 = models.filter(function (m) { return m.id === "t_2"; })[0];
    var t3 = models.filter(function (m) { return m.id === "t_3"; })[0];
    assert.strictEqual(t2.done, true);
    assert.strictEqual(t2.unscheduled, false);
    assert.strictEqual(t3.done, false);
    assert.strictEqual(t3.unscheduled, true); // plan:null
  }); })

  // ── L2：越界判定矩阵（正常/超出/项目无计划/未排期，PRD F-GANTT-2）────────
  .then(function () { return test("isTaskOutOfBounds：任务计划落在项目窗口内 → 不越界", function () {
    var p = SAMPLE_VIEW_WITH_TASKS.projects[0];
    var t1 = p.tasks[0];
    assert.strictEqual(D.isTaskOutOfBounds(t1, p), false);
  }); })
  .then(function () { return test("isTaskOutOfBounds：任务 end 超出项目 end → 越界", function () {
    var p = SAMPLE_VIEW_WITH_TASKS.projects[0];
    var t2 = p.tasks[1]; // plan.end 2026-08-15 > 项目 plan.end 2026-08-10
    assert.strictEqual(D.isTaskOutOfBounds(t2, p), true);
  }); })
  .then(function () { return test("isTaskOutOfBounds：项目无计划期 → 任何已排期任务都算越界", function () {
    var p = SAMPLE_VIEW_WITH_TASKS.projects[1]; // plan:null
    var t4 = p.tasks[0];
    assert.strictEqual(D.isTaskOutOfBounds(t4, p), true);
  }); })
  .then(function () { return test("isTaskOutOfBounds：任务未排期 → 没有条可画，不算越界", function () {
    var p = SAMPLE_VIEW_WITH_TASKS.projects[0];
    var t3 = p.tasks[2]; // plan:null
    assert.strictEqual(D.isTaskOutOfBounds(t3, p), false);
  }); })

  // ── L3：依赖冲突判定矩阵（正常/冲突/未排期端/跨项目，PRD F-GANTT-3）───────
  .then(function () { return test("dependencyConflict：后置任务在前置结束后才开始 → 不冲突", function () {
    var tasks = SAMPLE_VIEW_WITH_DEPS.projects[0].tasks;
    var tA1 = tasks[0], tA2 = tasks[1];
    assert.strictEqual(D.dependencyConflict(tA1, tA2), false); // tA2.start 08-06 不早于 tA1.end 08-05
  }); })
  .then(function () { return test("dependencyConflict：后置任务在前置结束前就开始 → 冲突", function () {
    var tasks = SAMPLE_VIEW_WITH_DEPS.projects[0].tasks;
    var tA1 = tasks[0], tA3 = tasks[2];
    assert.strictEqual(D.dependencyConflict(tA1, tA3), true); // tA3.start 08-03 早于 tA1.end 08-05
  }); })
  .then(function () { return test("dependencyConflict：任一端未排期 → 不判冲突（false，不是抛错）", function () {
    var tasks = SAMPLE_VIEW_WITH_DEPS.projects[0].tasks;
    var tA1 = tasks[0], tA5 = tasks[4]; // tA5 plan:null
    assert.strictEqual(D.dependencyConflict(tA1, tA5), false);
    assert.strictEqual(D.dependencyConflict(null, tA1), false);
  }); })
  .then(function () { return test("dependencyConflict：跨项目依赖同样判定（不因跨项目豁免）", function () {
    var tA1 = SAMPLE_VIEW_WITH_DEPS.projects[0].tasks[0];
    var tB1 = SAMPLE_VIEW_WITH_DEPS.projects[1].tasks[0];
    assert.strictEqual(D.dependencyConflict(tA1, tB1), true); // tB1.start 08-02 早于 tA1.end 08-05
  }); })
  .then(function () { return test("dependencyArrowState：三态映射（normal/conflict/unscheduled）", function () {
    var tasks = SAMPLE_VIEW_WITH_DEPS.projects[0].tasks;
    assert.strictEqual(D.dependencyArrowState(tasks[0], tasks[1]), "normal");
    assert.strictEqual(D.dependencyArrowState(tasks[0], tasks[2]), "conflict");
    assert.strictEqual(D.dependencyArrowState(tasks[0], tasks[4]), "unscheduled");
  }); })

  // ── L3：箭头锚点解析（vendor 只能连真实 item，没计划时退而求其次）────
  .then(function () { return test("taskAnchorItemId：有计划 → 锚在计划条", function () {
    var tA1 = SAMPLE_VIEW_WITH_DEPS.projects[0].tasks[0];
    assert.strictEqual(D.taskAnchorItemId(tA1), "tA1:plan");
  }); })
  .then(function () { return test("taskAnchorItemId：无计划但有实际 → 锚在第一条有时长的事实块（跳过 0 秒天）", function () {
    var tA4 = SAMPLE_VIEW_WITH_DEPS.projects[0].tasks[3];
    assert.strictEqual(D.taskAnchorItemId(tA4), "tA4:actual:2026-08-04"); // 08-03 是 0 秒，跳过
  }); })
  .then(function () { return test("taskAnchorItemId：无计划无实际 → 没有锚点，返回 null", function () {
    var tA5 = SAMPLE_VIEW_WITH_DEPS.projects[0].tasks[4];
    assert.strictEqual(D.taskAnchorItemId(tA5), null);
  }); })

  // ── L3：依赖箭头构造（跳过无解析目标/无锚点，不崩；跨项目照常收）────────────
  .then(function () { return test("buildDependencyArrows：跳过依赖指向不存在任务、跳过两端都没锚点的箭头", function () {
    var arrows = D.buildDependencyArrows(SAMPLE_VIEW_WITH_DEPS.projects);
    var ids = arrows.map(function (a) { return a.id; });
    assert.ok(ids.indexOf("t_ghost->tA6") === -1); // 依赖指向不存在的任务
    assert.ok(ids.indexOf("tA1->tA5") === -1); // tA5 两端都没有可锚定 item
    assert.ok(ids.indexOf("tA1->tA2") !== -1);
    assert.ok(ids.indexOf("tA1->tA3") !== -1);
    assert.ok(ids.indexOf("tA1->tA4") !== -1); // tA4 用事实块当锚点，这条箭头画得出
  }); })
  .then(function () { return test("buildDependencyArrows：id_item_1/id_item_2 用锚点 id，state 按判定矩阵挂对", function () {
    var arrows = D.buildDependencyArrows(SAMPLE_VIEW_WITH_DEPS.projects);
    var byId = {};
    arrows.forEach(function (a) { byId[a.id] = a; });
    assert.strictEqual(byId["tA1->tA2"].id_item_1, "tA1:plan");
    assert.strictEqual(byId["tA1->tA2"].id_item_2, "tA2:plan");
    assert.strictEqual(byId["tA1->tA2"].state, "normal");
    assert.strictEqual(byId["tA1->tA3"].state, "conflict");
    assert.strictEqual(byId["tA1->tA4"].id_item_2, "tA4:actual:2026-08-04");
    assert.strictEqual(byId["tA1->tA4"].state, "unscheduled");
  }); })
  .then(function () { return test("buildDependencyArrows：跨项目依赖照常收进结果", function () {
    var arrows = D.buildDependencyArrows(SAMPLE_VIEW_WITH_DEPS.projects);
    var cross = arrows.filter(function (a) { return a.id === "tA1->tB1"; })[0];
    assert.ok(cross, "跨项目箭头 tA1->tB1 应该存在");
    assert.strictEqual(cross.state, "conflict");
  }); })

  // ── L3：折叠态冲突聚合章（按全量数据算，两端项目都要挂）────────────────────
  .then(function () { return test("computeConflictProjectIds：冲突涉及的两个项目（含跨项目）都在结果里", function () {
    var ids = D.computeConflictProjectIds(SAMPLE_VIEW_WITH_DEPS.projects);
    assert.ok(ids.indexOf("p_20") !== -1); // tA3 与 tA1 冲突，p_20 内部
    assert.ok(ids.indexOf("p_21") !== -1); // tB1 跨项目依赖 tA1 且冲突
  }); })
  .then(function () { return test("computeConflictProjectIds：没有依赖关系的项目不会被牵连", function () {
    var ids = D.computeConflictProjectIds(SAMPLE_VIEW_WITH_DEPS.projects);
    assert.ok(ids.indexOf("p_22") === -1);
  }); })
  .then(function () { return test("buildGroupModels：hasConflict 只出现在项目行，且与 computeConflictProjectIds 一致", function () {
    var models = D.buildGroupModels(SAMPLE_VIEW_WITH_DEPS.projects);
    var p20 = models.filter(function (m) { return m.id === "p_20"; })[0];
    var p22 = models.filter(function (m) { return m.id === "p_22"; })[0];
    assert.strictEqual(p20.hasConflict, true);
    assert.strictEqual(p22.hasConflict, false);
  }); })

  // ── L2：任务层 items（className 独立命名空间 + 越界修饰类）───────────────
  .then(function () { return test("buildTaskPlanItem：className 挂 gantt-task-plan，可拖", function () {
    var p = SAMPLE_VIEW_WITH_TASKS.projects[0];
    var item = D.buildTaskPlanItem(p.tasks[0], p);
    assert.strictEqual(item.id, "t_1:plan");
    assert.strictEqual(item.group, "t_1");
    assert.strictEqual(item.className, "gantt-item gantt-task-plan");
    assert.strictEqual(item.editable.updateTime, true);
  }); })
  .then(function () { return test("buildTaskPlanItem：越界任务 className 多挂 --oob 修饰类", function () {
    var p = SAMPLE_VIEW_WITH_TASKS.projects[0];
    var item = D.buildTaskPlanItem(p.tasks[1], p);
    assert.strictEqual(item.className, "gantt-item gantt-task-plan gantt-task-plan--oob");
  }); })
  .then(function () { return test("buildTaskActualItems：与项目层同一约定，0 秒不占 item", function () {
    var p = SAMPLE_VIEW_WITH_TASKS.projects[0];
    var items = D.buildTaskActualItems(p.tasks[0]);
    assert.strictEqual(items.length, 1);
    assert.strictEqual(items[0].className, "gantt-item gantt-task-actual");
    assert.strictEqual(items[0].editable, false);
  }); })
  .then(function () { return test("buildItems：任务层的计划/事实项随项目一起展开", function () {
    var items = D.buildItems(SAMPLE_VIEW_WITH_TASKS.projects);
    var taskPlanIds = items.filter(function (it) { return it.className.indexOf("gantt-task-plan") !== -1; })
      .map(function (it) { return it.id; });
    // t_1（在界内）、t_2（越界）各有计划条；t_3 未排期没有；t_4 挂在无计划项目下但自己已排期，仍要画出来。
    assert.deepStrictEqual(taskPlanIds.sort(), ["t_1:plan", "t_2:plan", "t_4:plan"].sort());
    var taskActualIds = items.filter(function (it) { return it.className.indexOf("gantt-task-actual") !== -1; })
      .map(function (it) { return it.id; });
    assert.deepStrictEqual(taskActualIds, ["t_1:actual:2026-08-02"]);
  }); })

  // ── items：三个通道靠 className，事实层锁死（D2/D7）───────────────────
  .then(function () { return test("buildPlanItem：className 挂 gantt-plan，可拖（editable.updateTime）", function () {
    var item = D.buildPlanItem(SAMPLE_VIEW.projects[0]);
    assert.strictEqual(item.className, "gantt-item gantt-plan");
    assert.strictEqual(item.editable.updateTime, true);
    assert.strictEqual(item.editable.updateGroup, false);
    assert.strictEqual(item.editable.remove, false);
    assert.strictEqual(D.formatLocalISO(item.start), "2026-08-01");
    assert.strictEqual(D.formatLocalISO(item.end), "2026-08-10");
  }); })
  .then(function () { return test("buildActualItems：跳过 seconds<=0 的天，不占 item", function () {
    var items = D.buildActualItems(SAMPLE_VIEW.projects[0]);
    assert.strictEqual(items.length, 1); // 第二天 seconds:0 被跳过
    assert.strictEqual(items[0].className, "gantt-item gantt-actual");
    assert.strictEqual(items[0].editable, false); // D7：锁死
  }); })
  .then(function () { return test("buildActualItems：单日 item 的 end 是 start+1天", function () {
    var items = D.buildActualItems(SAMPLE_VIEW.projects[0]);
    assert.strictEqual(D.formatLocalISO(items[0].start), "2026-08-01");
    assert.strictEqual(D.formatLocalISO(items[0].end), "2026-08-02");
  }); })
  // ── 事实层相邻分段视觉连成一组 ──────
  .then(function () { return test("buildActualItems：连续四天挂 join-l/join-r，两端不挂", function () {
    var items = D.buildActualItems(SAMPLE_VIEW_WITH_JOIN.projects[0]);
    assert.strictEqual(items.length, 6); // 01-04 四天 + 08-09 两天
    // 第一段：01(只挂join-r) 02(l+r) 03(l+r) 04(只挂join-l)
    assert.strictEqual(items[0].className.indexOf("gantt-actual--join-l") !== -1, false);
    assert.strictEqual(items[0].className.indexOf("gantt-actual--join-r") !== -1, true);
    assert.strictEqual(items[1].className.indexOf("gantt-actual--join-l") !== -1, true);
    assert.strictEqual(items[1].className.indexOf("gantt-actual--join-r") !== -1, true);
    assert.strictEqual(items[3].className.indexOf("gantt-actual--join-l") !== -1, true);
    assert.strictEqual(items[3].className.indexOf("gantt-actual--join-r") !== -1, false);
  }); })
  .then(function () { return test("buildActualItems：间隔 >0 天（05-07 缺失）两段之间不挂 join 类", function () {
    var items = D.buildActualItems(SAMPLE_VIEW_WITH_JOIN.projects[0]);
    // items[3] = 08-04（第一段末尾），items[4] = 08-08（第二段开头），中间隔 3 天
    assert.strictEqual(items[3].className.indexOf("gantt-actual--join-l") !== -1, true); // 段内左邻仍有
    assert.strictEqual(items[3].className.indexOf("--join-r") !== -1, false); // 04 和 08 不相邻
    assert.strictEqual(items[4].className.indexOf("--join-l") !== -1, false);
    assert.strictEqual(items[4].className.indexOf("gantt-actual--join-r") !== -1, true); // 08-09 相邻
  }); })
  .then(function () { return test("buildTaskActualItems：连续两天（01-02）挂 join，跳过 03 的第三天（04）不挂", function () {
    var items = D.buildTaskActualItems(SAMPLE_VIEW_WITH_JOIN.projects[0].tasks[0]);
    assert.strictEqual(items.length, 3); // 01,02,04（03 不在 actual 里，等于间隔 2 天）
    assert.strictEqual(items[0].className.indexOf("gantt-task-actual--join-r") !== -1, true);
    assert.strictEqual(items[1].className.indexOf("gantt-task-actual--join-l") !== -1, true);
    assert.strictEqual(items[2].className.indexOf("--join-l") !== -1, false); // 04 离 02 隔了 2 天，不算相邻
  }); })
  .then(function () { return test("buildItems：未排期项目只有事实层 item，没有计划 item（D9）", function () {
    var items = D.buildItems([SAMPLE_VIEW.projects[1]]);
    var hasPlan = items.some(function (it) { return it.className.indexOf("gantt-plan") !== -1; });
    assert.strictEqual(hasPlan, false);
    assert.strictEqual(items.length, 0); // 练字计划 actual 也是空数组
  }); })
  .then(function () { return test("buildItems：已排期项目同时有计划与事实两层 item", function () {
    var items = D.buildItems([SAMPLE_VIEW.projects[0]]);
    var planCount = items.filter(function (it) { return it.className.indexOf("gantt-plan") !== -1; }).length;
    var actualCount = items.filter(function (it) { return it.className.indexOf("gantt-actual") !== -1; }).length;
    assert.strictEqual(planCount, 1);
    assert.strictEqual(actualCount, 1);
  }); })

  // ── 排期默认区间 ───────────────────────────────────────────────────
  .then(function () { return test("defaultScheduleRange：start=今天，end=今天+7天", function () {
    var range = D.defaultScheduleRange("2026-08-02");
    assert.strictEqual(range.start, "2026-08-02");
    assert.strictEqual(range.end, "2026-08-09");
  }); })

  // ── request()：ok/status/message 归一化，从不 reject ──────────────────
  .then(function () { return test("request：200 json 成功", function () {
    return D.request(fakeFetch(200, { a: 1 }), "GET", "/x").then(function (r) {
      assert.deepStrictEqual(r, { ok: true, data: { a: 1 } });
    });
  }); })
  .then(function () { return test("request：204 无 body 视为成功", function () {
    return D.request(fakeFetch(204, null), "DELETE", "/x").then(function (r) {
      assert.deepStrictEqual(r, { ok: true, data: null });
    });
  }); })
  .then(function () { return test("request：4xx 原样透传 detail（D10：后端 detail 原样展示）", function () {
    return D.request(fakeFetch(404, { detail: "项目不存在：'p_x'" }), "PATCH", "/x").then(function (r) {
      assert.strictEqual(r.ok, false);
      assert.strictEqual(r.status, 404);
      assert.strictEqual(r.message, "项目不存在：'p_x'");
    });
  }); })
  .then(function () { return test("request：4xx 无 detail 时给通用文案，不崩", function () {
    return D.request(fakeFetch(500, null), "GET", "/x").then(function (r) {
      assert.strictEqual(r.ok, false);
      assert.ok(r.message.indexOf("500") !== -1);
    });
  }); })
  .then(function () { return test("request：网络异常不 reject，归一化成 ok:false", function () {
    var throwingFetch = function () { return Promise.reject(new Error("network down")); };
    return D.request(throwingFetch, "GET", "/x").then(function (r) {
      assert.strictEqual(r.ok, false);
      assert.strictEqual(r.status, 0);
      assert.ok(r.message.indexOf("network down") !== -1);
    });
  }); })
  .then(function () { return test("request：fetch 不可用时给明确提示", function () {
    return D.request(null, "GET", "/x").then(function (r) {
      assert.strictEqual(r.ok, false);
      assert.strictEqual(r.message, "fetch 不可用");
    });
  }); })

  // ── 端点：走统一 planner 入口，不为甘特开专用端点 ─────────────────────
  .then(function () { return test("projectPlanPath：id 做 URL 编码", function () {
    assert.strictEqual(D.projectPlanPath("p 1"), "/api/core/planner/projects/p%201");
  }); })
  .then(function () { return test("fetchGanttView：GET /api/core/views/gantt", function () {
    var calls = [];
    return D.fetchGanttView({ fetchImpl: fakeFetch(200, SAMPLE_VIEW, calls) }).then(function () {
      assert.strictEqual(calls[0].path, "/api/core/views/gantt");
      assert.strictEqual(calls[0].init.method, "GET");
    });
  }); })
  .then(function () { return test("patchProjectPlan：PATCH planner/projects/{id}，body 只带 plan", function () {
    var calls = [];
    var plan = { start: "2026-08-01", end: "2026-08-10" };
    return D.patchProjectPlan("p_1", plan, { fetchImpl: fakeFetch(200, {}, calls) }).then(function () {
      assert.strictEqual(calls[0].path, "/api/core/planner/projects/p_1");
      assert.strictEqual(calls[0].init.method, "PATCH");
      assert.deepStrictEqual(JSON.parse(calls[0].init.body), { plan: plan });
    });
  }); })
  .then(function () { return test("taskPlanPath：id 做 URL 编码", function () {
    assert.strictEqual(D.taskPlanPath("t 1"), "/api/core/planner/tasks/t%201");
  }); })
  .then(function () { return test("patchTaskPlan：PATCH planner/tasks/{id}，body 只带 plan（不为甘特开专用端点，D6 同款）", function () {
    var calls = [];
    var plan = { start: "2026-08-02", end: "2026-08-05" };
    return D.patchTaskPlan("t_1", plan, { fetchImpl: fakeFetch(200, {}, calls) }).then(function () {
      assert.strictEqual(calls[0].path, "/api/core/planner/tasks/t_1");
      assert.strictEqual(calls[0].init.method, "PATCH");
      assert.deepStrictEqual(JSON.parse(calls[0].init.body), { plan: plan });
    });
  }); })

  // ── UI 重做：左侧信息列（起/止/工期）───────────────────────────────
  .then(function () { return test("planDurationDays：含首尾两天", function () {
    assert.strictEqual(D.planDurationDays({ start: "2026-08-01", end: "2026-08-10" }), 10);
  }); })
  .then(function () { return test("planDurationDays：单日排期算 1 天", function () {
    assert.strictEqual(D.planDurationDays({ start: "2026-08-01", end: "2026-08-01" }), 1);
  }); })
  .then(function () { return test("planDurationDays：未排期（plan 为 null）返回 null", function () {
    assert.strictEqual(D.planDurationDays(null), null);
  }); })
  .then(function () { return test("formatPlanRange：同年短格式 MM-DD → MM-DD", function () {
    assert.strictEqual(D.formatPlanRange({ start: "2026-08-01", end: "2026-08-10" }), "08-01 → 08-10");
  }); })
  .then(function () { return test("formatPlanRange：跨年落回完整 ISO", function () {
    assert.strictEqual(D.formatPlanRange({ start: "2026-12-28", end: "2027-01-03" }), "2026-12-28 → 2027-01-03");
  }); })
  .then(function () { return test("formatPlanRange：未排期返回空字符串，不崩", function () {
    assert.strictEqual(D.formatPlanRange(null), "");
  }); })

  // ── UI 重做：缩放粒度预设（日/周/月/季按钮）───────────────────────────
  .then(function () { return test("zoomWindowForGranularity：day 档以今天为中心，窗口最小", function () {
    var w = D.zoomWindowForGranularity("2026-08-12", "day");
    assert.strictEqual(w.start, "2026-08-09");
    assert.strictEqual(w.end, "2026-08-16");
  }); })
  .then(function () { return test("zoomWindowForGranularity：四档窗口宽度依次递增（day<week<month<quarter）", function () {
    var order = ["day", "week", "month", "quarter"];
    var spans = order.map(function (g) {
      var w = D.zoomWindowForGranularity("2026-08-12", g);
      return D.planDurationDays({ start: w.start, end: w.end });
    });
    for (var i = 1; i < spans.length; i++) {
      assert.ok(spans[i] > spans[i - 1], order[i] + " 档应比 " + order[i - 1] + " 档窗口更宽");
    }
  }); })
  .then(function () { return test("zoomWindowForGranularity：未知档位/缺 today 返回 null，不崩", function () {
    assert.strictEqual(D.zoomWindowForGranularity("2026-08-12", "century"), null);
    assert.strictEqual(D.zoomWindowForGranularity(null, "day"), null);
  }); })

  .then(function () {
    console.log(passed + " passed");
  })
  .catch(function (err) {
    console.error("FAIL: " + (err && err.stack || err));
    process.exit(1);
  });
