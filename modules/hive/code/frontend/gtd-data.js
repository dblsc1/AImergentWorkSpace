// hive · GTD 数据层（纯逻辑，无 DOM 依赖，gtd-v1 波2-B）
//
// 三块职责，全部 nexus-only、不依赖 ai-planner（硬约束）：
//   1. 快速捕捉（F-INBOX-2）：只填标题建 task 到 well-known `p_inbox`。
//   2. 待办区（F-TODO-1）读端：GET /api/core/views/next-actions，逐字段透传，
//      **不在前端重新分类/重新排序**——后端已经算好可做/等待/过期/权重排序
//      （contract.md v1.5「下一步行动读端」），前端只负责忠实渲染。
//   3. 回顾（F-REVIEW-1）读端：GET /api/core/views/review，同样只读透传。
//   4. AI 写者角标数据源（F-ACTOR-3）：见下 fetchLastWriterMaps 的详细理由——
//      views/tree、views/next-actions、views/review 三个读端都**不带** lastWriter
//      字段（读 nexus-core 源码 code/backend/app/modules/views/schemas.py 确认，
//      三个 *_Out 模型都是 extra="forbid" 严格模型，字段表里没有这一列，不是猜）。
//      唯一带 lastWriter 的是 planner CRUD 的列表响应（ZoneOut/ProjectOut/TaskOut，
//      GET /api/core/planner/{type}，contract.md「写者字段 actor/lastWriter」节），
//      所以本文件并行拉一次 projects/tasks 列表，只取 id→lastWriter 的映射。
//
// 依赖 data.js 的 window.NexusTableData（quickCapture 复用既有 D.createTask，
// 不新开写路径——F-INBOX-2 建任务用的还是 planner.crud.v1 统一入口，只是固定
// projectId=p_inbox、不要求调用方选项目/分区）。GET 请求走本文件自己的最小
// request 包装（data.js 的 request() 是模块内部函数，未导出，本文件按 rails.js
// 当初"独立文件各自持有一份最小网络层"的先例，不为此改 data.js 的导出面）。
//
// 同时可在浏览器（<script src="gtd-data.js"></script>，挂 window.NexusTableGtdData）
// 与 Node（run_tests.sh 用 require()）里跑，不引入构建工具（同 data.js/rails.js 纪律）。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(require("./data.js"));
  } else {
    root.NexusTableGtdData = factory(root.NexusTableData);
  }
})(typeof self !== "undefined" ? self : this, function (D) {
  "use strict";

  // well-known 收件箱项目 id（nexus-core contract.md v1.5「收件箱」节，固定 id，
  // 不走三类对象平时的 uuid 生成——种子脚本 ensure_inbox() 幂等创建）。
  var P_INBOX_ID = "p_inbox";

  var NEXT_ACTIONS_PATH = "/api/core/views/next-actions";
  var REVIEW_PATH = "/api/core/views/review";
  // 只为取 lastWriter 映射而读（见文件头注释 4），不塑形成别的用途。
  var PLANNER_PROJECTS_PATH = "/api/core/planner/projects";
  var PLANNER_TASKS_PATH = "/api/core/planner/tasks";

  function resolveFetch(options) {
    options = options || {};
    return options.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  }

  // 最小 GET 包装：同 data.js 的 request() 同一份纪律——从不 reject，网络/HTTP
  // 错误都归一化成 {ok:false,status,message}，调用方不用套 try/catch。
  function get(path, options) {
    var fetchImpl = resolveFetch(options);
    if (!fetchImpl) {
      return Promise.resolve({ ok: false, status: 0, message: "fetch 不可用" });
    }
    return fetchImpl(path).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (payload) {
        if (res.ok) return { ok: true, data: payload };
        var message = (payload && payload.detail) ? payload.detail : ("请求失败（HTTP " + res.status + "）");
        return { ok: false, status: res.status, message: message };
      });
    }).catch(function (err) {
      var message = (err && err.message) || String(err);
      return { ok: false, status: 0, message: "网络请求失败：" + message };
    });
  }

  // ── F-TODO-1：下一步行动读端，逐字段透传 ─────────────────────────
  function fetchNextActions(options) {
    return get(NEXT_ACTIONS_PATH, options);
  }

  // ── F-REVIEW-1：每周回顾读端，逐字段透传 ────────────────────────
  function fetchReview(options) {
    return get(REVIEW_PATH, options);
  }

  // ── F-ACTOR-3：AI 写者角标的数据源（见文件头注释 4）──────────────
  // 并行拉 projects+tasks 两份 planner 列表，只取 id→lastWriter；其余字段已经从
  // views/tree、views/next-actions、views/review 拿到了，不重复消费（避免"同一份
  // 数据两个来源"的既有纪律，同 handoff.md 决定 #13 对 gantt/tree 的处理）。
  // 任一列表失败都不让另一份陪葬——角标本身是锦上添花的展示，取不到就都当
  // "human"（不显示角标），不阻塞主流程渲染（同 renderRails 对 gantt 失败的
  // 降级哲学一致）。
  function fetchLastWriterMaps(options) {
    return Promise.all([get(PLANNER_PROJECTS_PATH, options), get(PLANNER_TASKS_PATH, options)])
      .then(function (results) {
        var projectResult = results[0];
        var taskResult = results[1];
        var projects = {};
        var tasks = {};
        if (projectResult.ok) {
          (projectResult.data || []).forEach(function (p) {
            projects[p.id] = p.lastWriter || "human";
          });
        }
        if (taskResult.ok) {
          (taskResult.data || []).forEach(function (t) {
            tasks[t.id] = t.lastWriter || "human";
          });
        }
        return { projects: projects, tasks: tasks };
      });
  }

  function isAiWritten(map, id) {
    return !!(map && id != null && map[id] === "ai");
  }

  // ── F-INBOX-2：全局快速捕捉，只填标题，落 p_inbox，不要求选项目/分区 ──
  // 复用既有 D.createTask（planner.crud.v1 统一写入口），不新开写路径——
  // 唯一区别是固定 projectId=p_inbox，调用方（UI）不提供项目选择器。
  function quickCapture(name, options) {
    return D.createTask(P_INBOX_ID, name, options);
  }

  // ── F-INBOX-3/4：收件箱视图的数据来源是已加载的 tree，不单独拉一条
  // "p_inbox 的任务"接口（没有这样的端点，tree 本身就嵌套好了）──
  function listInboxTasks(tree) {
    var project = D.findProjectById(tree, P_INBOX_ID);
    return (project && project.tasks) || [];
  }

  // 收件箱"归类到…"下拉框的候选项目：排除 p_inbox 自己（收件箱不能归类到
  // 收件箱），跨 zone，标签同既有 fillProjectSelect 的"分区 / 项目"措辞。
  function listNonInboxProjects(tree) {
    var zones = (tree && tree.zones) || [];
    var zonesById = {};
    zones.forEach(function (zone) { zonesById[zone.id] = zone; });
    var projects = (tree && tree.projects) || [];
    return projects.filter(function (p) { return p.id !== P_INBOX_ID; }).map(function (p) {
      var zoneName = (zonesById[p.zoneId] && zonesById[p.zoneId].name) || "?";
      return { id: p.id, label: zoneName + " / " + p.name };
    });
  }

  return {
    P_INBOX_ID: P_INBOX_ID,
    fetchNextActions: fetchNextActions,
    fetchReview: fetchReview,
    fetchLastWriterMaps: fetchLastWriterMaps,
    isAiWritten: isAiWritten,
    quickCapture: quickCapture,
    listInboxTasks: listInboxTasks,
    listNonInboxProjects: listNonInboxProjects
  };
});
