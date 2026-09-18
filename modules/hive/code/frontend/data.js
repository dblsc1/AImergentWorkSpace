// table · 数据层（纯逻辑，无 DOM 依赖）
//
// 派生自上游 NEXUS 控制台原型的数据处理部分。
// 三块职责：读 GET /api/core/views/tree 并整理成渲染层好用的形状；
// 分区/项目/任务的增删改写请求（对接 nexus-core.planner.crud.v1，
// 见 module_docs/contract.md v0.2——table 是四个前端里唯一允许写的例外，
// 2026-07-31 定）；
// 计时档案的只读展示（对接 nexus-core.events.read.v1，contract.md v0.4）。
//
// 契约依赖：../nexus-core/module_docs/contract.md 的 TreeOut / planner CRUD / 档案读端三节。
// 关键约束（不要在这个文件里破坏）：
//   - progress / progressSource 直接渲染，不在前端重算（契约原文见 R4）
//   - 项目级颜色前端按 zoneId 从 zone.color 派生，契约故意不提供 project.color（R5）
//   - flags 只读不解释，本文件不对 flags 的值做任何 if 分支（R7）
//   - 写操作本文件只管发请求、归一化 ok/status/message，不做本地乐观更新——
//     刷新展示是调用方（app 层）在写成功后重新 fetchTree 的责任（C2）
//   - 增删改写请求统一走 /api/core/planner/{type} 入口（2026-08-01 起）；
//     views/tree、views/current **不在** planner 命名空间下，路径不变——
//     上一轮改审核脚本前缀时把 views/tree 一起改错过，这里刻意把两组路径
//     分开成不同常量，别在“统一改前缀”的时候把它们揉到一起。
//   - 档案事实里只有 opaque id（subject.zone/project/task），任务/项目名字
//     由本文件现查 views/tree 的数据（R7）——后端故意不 join 名字，查不到
//     （已删除）就展示 id 并标注“已删除”，不崩（项目/任务两类 id
//     都可能被删，统一按同一套规则兜底，不只补任务这一种情况）。
//
// 同时可在浏览器（<script src="data.js"></script>，挂 window.NexusTableData）
// 与 Node（run_tests.sh 用 require()）里跑，不引入构建工具。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.NexusTableData = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var TREE_PATH = "/api/core/views/tree";
  var CACHE_KEY = "table-tree-cache-v1";
  // 兜底不是样式字面量，是"没有 zone.color 时用什么"的数据默认值；
  // 取 CSS 变量名字符串（不是 hex），这样它被塞进 inline `style="--zone:…"`
  // 时会链到当前主题的机身色，不会在暗/亮切换时变成一个写死的颜色（A2）。
  var DEFAULT_ZONE_COLOR = "var(--accent)";

  // views/tree、views/current 不在 planner 命名空间下（R4，路径不变）；
  // 写操作（zones/projects/tasks）统一入口是 PLANNER_PREFIX（R1）。
  // 两个常量分开定义，避免"统一改前缀"时把它们揉到一起。
  var PLANNER_PREFIX = "/api/core/planner/";
  var EVENTS_PATH = "/api/core/events";
  var ARCHIVE_TYPE = "session.completed";
  var ARCHIVE_DEFAULT_LIMIT = 50; // R8：默认拉最近 50 条

  // 甘特读端（nexus-core contract.md v0.8「甘特读端」，GET /api/core/views/gantt）。
  // table 本轮新增消费它——不是为了画甘特图，是为了拿 project.plan{start,end}
  // 与 project.actual[]（按天聚合的事实秒数），算项目卡双轨（F-TABLE-1，见 rails.js）。
  // **这是既有投影，不需要后端加任何东西**：queries.get_gantt() 早已返回全部项目
  // （含未排期、无事实的），本文件只是多读一条已经存在的只读端点。
  // ⚠️ 契约缺口：module_docs/contract.md 尚未把这条登记进 consumes（待补）。
  var GANTT_PATH = "/api/core/views/gantt";

  // 只读全量导出（nexus-core contract.md v1.3「只读全量导出」，
  // GET /api/core/export）。给顶栏「导出数据」按钮用——用户要看/下载自己的
  // 全部数据副本，不是某个渲染组件的数据源，所以不塑形、原样透传给调用方。
  var EXPORT_PATH = "/api/core/export";

  // ── URL ──────────────────────────────────────────────────────────
  function buildTreeUrl(includeEphemeral) {
    return TREE_PATH + (includeEphemeral ? "?includeEphemeral=true" : "");
  }

  // ── 缓存（断网兜底，不是主数据源，R3）──────────────────────────────
  function readCache(storage) {
    if (!storage) return null;
    try {
      var raw = storage.getItem(CACHE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return parsed && parsed.tree ? parsed.tree : null;
    } catch (err) {
      return null;
    }
  }

  function writeCache(storage, tree) {
    if (!storage) return;
    try {
      storage.setItem(CACHE_KEY, JSON.stringify({ tree: tree, cachedAt: new Date().toISOString() }));
    } catch (err) {
      // 存储满/被禁用时静默放弃缓存，不影响主流程（缓存只是兜底，不是必须）
    }
  }

  function readCacheTimestamp(storage) {
    if (!storage) return null;
    try {
      var raw = storage.getItem(CACHE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return parsed && parsed.cachedAt ? parsed.cachedAt : null;
    } catch (err) {
      return null;
    }
  }

  // ── 取树：网络优先，失败落缓存，两者都没有就如实返回空（R9）──────────
  // 返回 { tree, source: "network"|"cache"|"none", error? }
  function fetchTree(options) {
    options = options || {};
    var fetchImpl = options.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
    var storage = options.storage || (typeof localStorage !== "undefined" ? localStorage : null);
    var includeEphemeral = !!options.includeEphemeral;

    if (!fetchImpl) {
      var cachedNoFetch = readCache(storage);
      return Promise.resolve(cachedNoFetch
        ? { tree: cachedNoFetch, source: "cache", error: "fetch 不可用" }
        : { tree: null, source: "none", error: "fetch 不可用" });
    }

    return fetchImpl(buildTreeUrl(includeEphemeral)).then(function (res) {
      if (!res || !res.ok) {
        throw new Error("http-" + (res ? res.status : "unknown"));
      }
      return res.json();
    }).then(function (tree) {
      writeCache(storage, tree);
      return { tree: tree, source: "network" };
    }).catch(function (err) {
      var cached = readCache(storage);
      var message = (err && err.message) || String(err);
      if (cached) return { tree: cached, source: "cache", error: message };
      return { tree: null, source: "none", error: message };
    });
  }

  // ── 派生展示值（R5：契约故意不提供的字段，前端自己决定）─────────────
  function indexZonesById(zones) {
    var map = {};
    (zones || []).forEach(function (zone) { map[zone.id] = zone; });
    return map;
  }

  function deriveProjectColor(project, zonesById) {
    var zone = project && zonesById ? zonesById[project.zoneId] : null;
    return (zone && zone.color) || DEFAULT_ZONE_COLOR;
  }

  function deriveProjectIcon(project) {
    var name = (project && project.name ? String(project.name) : "").trim();
    return name ? name.charAt(0).toUpperCase() : "•";
  }

  // ── 空态判定（R10）──────────────────────────────────────────────
  function isEmptyTree(tree) {
    if (!tree) return true;
    var zones = tree.zones || [];
    var projects = tree.projects || [];
    return zones.length === 0 && projects.length === 0;
  }

  // ── 写操作：分区/项目/任务增删改（对接 nexus-core.planner.crud.v1）───
  // 设计约定 C1–C10：
  //   - 全部走真实 API，不本地模拟（C1）；写成功后调用方负责重新 fetchTree 刷新（C2，
  //     本文件不做也不能做"乐观更新"，因为这里根本不持有 state）。
  //   - 每个函数一次 PATCH 只带调用方明确给的字段（C6）：改名只传 name、换归属只传
  //     zoneId/projectId、改权重只传 plannedWeight——不在这里"顺手"拼装成一个大对象。
  //   - id 由系统生成，本文件所有 create* 函数都不接受 id 参数（C5）。
  //   - 错误原样透传：4xx 响应体 { detail: "..." }（nexus-core main.py 的
  //     exception handler 统一形状），本层只做「网络层 ok/status/message」的归一化，
  //     不改写、不包装后端给的消息文字（C3/C4）。
  function resolveFetch(options) {
    options = options || {};
    return options.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  }

  // 统一请求：返回 { ok:true, data } 或 { ok:false, status, message }，从不 reject——
  // 调用方（app 层）不用到处套 try/catch 就能拿到可展示的错误文案。
  function request(fetchImpl, method, path, body) {
    if (!fetchImpl) {
      return Promise.resolve({ ok: false, status: 0, message: "fetch 不可用" });
    }
    var init = { method: method };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    return fetchImpl(path, init).then(function (res) {
      if (res.status === 204) return { ok: true, data: null };
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

  // 统一入口 /api/core/planner/{type}（2026-08-01 迁移，R1）。旧十二条端点
  // （/api/core/zones 等）已被后端删除，不再使用。
  function zonePath(id) { return PLANNER_PREFIX + "zones/" + encodeURIComponent(id); }
  function projectPath(id) { return PLANNER_PREFIX + "projects/" + encodeURIComponent(id); }
  function taskPath(id) { return PLANNER_PREFIX + "tasks/" + encodeURIComponent(id); }

  // ── 分区：新建 / 改名 / 删除（C1，无归属可换）────────────────────────
  function createZone(name, options) {
    return request(resolveFetch(options), "POST", PLANNER_PREFIX + "zones", { name: name });
  }
  function renameZone(id, name, options) {
    return request(resolveFetch(options), "PATCH", zonePath(id), { name: name });
  }
  function deleteZone(id, options) {
    return request(resolveFetch(options), "DELETE", zonePath(id));
  }

  // ── 项目：新建 / 改名 / 换分区 / 改计划权重 / 删除（C1）────────────────
  function createProject(zoneId, name, options) {
    options = options || {};
    var payload = { zoneId: zoneId, name: name };
    if (options.plannedWeight !== undefined) payload.plannedWeight = options.plannedWeight;
    return request(resolveFetch(options), "POST", PLANNER_PREFIX + "projects", payload);
  }
  function renameProject(id, name, options) {
    return request(resolveFetch(options), "PATCH", projectPath(id), { name: name });
  }
  function moveProject(id, zoneId, options) {
    return request(resolveFetch(options), "PATCH", projectPath(id), { zoneId: zoneId });
  }
  function deleteProject(id, options) {
    return request(resolveFetch(options), "DELETE", projectPath(id));
  }

  // ── 任务：新建 / 改名 / 换项目 / 改计划权重 / 切换完成 / 删除（C1）───────
  function createTask(projectId, name, options) {
    options = options || {};
    var payload = { projectId: projectId, name: name };
    if (options.plannedWeight !== undefined) payload.plannedWeight = options.plannedWeight;
    return request(resolveFetch(options), "POST", PLANNER_PREFIX + "tasks", payload);
  }
  function renameTask(id, name, options) {
    return request(resolveFetch(options), "PATCH", taskPath(id), { name: name });
  }
  function moveTask(id, projectId, options) {
    return request(resolveFetch(options), "PATCH", taskPath(id), { projectId: projectId });
  }
  function toggleTaskDone(id, done, options) {
    return request(resolveFetch(options), "PATCH", taskPath(id), { done: !!done });
  }
  function deleteTask(id, options) {
    return request(resolveFetch(options), "DELETE", taskPath(id));
  }

  // ── 任务排期 + 前置任务（F-TABLE-3，防御式）───────────────────────
  // **只在 feature-detect 判定为"后端已支持"之后才会被调用**——本文件的
  // 两个函数本身不做检测，检测在 hasScheduleFields()，调用方（crud.js）
  // 负责先查再发。今天（2026-08-08）nexus-core 的 TaskUpdate 还没有
  // plan/dependsOn 字段且 model_config 是 extra="forbid"（读源码确认，见
  // ../../nexus-core/code/backend/app/modules/planner/schemas.py），发了会被
  // pydantic 拒成 422，detail 是数组不是人话字符串——绕开这个坑的办法只有
  // "不发"，不是"发了再兜底解析 422 形状"。
  function updateTaskPlan(id, plan, options) {
    // plan 为 null = 清空排期；{start,end} = 设置/改期。两侧都不做本地拦截，
    // "只填一侧"的拦截在 crud.js（那是决定发什么值的 UI 逻辑，不是数据层职责）。
    return request(resolveFetch(options), "PATCH", taskPath(id), { plan: plan });
  }
  function updateTaskDependsOn(id, dependsOn, options) {
    return request(resolveFetch(options), "PATCH", taskPath(id), { dependsOn: dependsOn || [] });
  }

  // GET 任务响应（这里特指已加载的 views/tree 里的任务对象）有没有 dependsOn 键——
  // 有就是后端已实现 F-API-1，两个控件都显示；没有就都隐藏，页面其余功能不受影响。
  // 只认这一个键：契约草案（PRD F-API-1）里 plan 和 dependsOn 是同一次增量一起加的，
  // 单键判定足够，不需要两个键都查一遍。
  function hasScheduleFields(task) {
    return !!(task && Object.prototype.hasOwnProperty.call(task, "dependsOn"));
  }

  // 前置任务多选框的候选列表：树里除自己以外的全部任务，跨项目（F-GANTT-4 允许
  // 跨项目依赖，table 这里是它的兜底编辑路径，口径要一致）。标签"项目 / 任务"
  // 与既有下拉框（fillProjectSelect）同款措辞，不另造一套。
  function listOtherTasks(tree, excludeTaskId) {
    var projects = (tree && tree.projects) || [];
    var out = [];
    projects.forEach(function (project) {
      (project.tasks || []).forEach(function (task) {
        if (task.id === excludeTaskId) return;
        out.push({ id: task.id, label: project.name + " / " + task.name });
      });
    });
    return out;
  }

  // ── 概览统计：对已有 progress 做展示层聚合，不是重算单项 progress ──
  function computeSummary(tree) {
    var projects = (tree && tree.projects) || [];
    var allTasks = [];
    projects.forEach(function (project) {
      allTasks = allTasks.concat(project.tasks || []);
    });
    var done = allTasks.filter(function (task) { return !!task.done; }).length;
    var avgProgress = projects.length
      ? Math.round(projects.reduce(function (sum, project) {
          return sum + Number(project.progress || 0);
        }, 0) / projects.length)
      : 0;
    return {
      projectCount: projects.length,
      taskDone: done,
      taskTotal: allTasks.length,
      avgProgress: avgProgress
    };
  }

  // ── 计时档案（R5-R10，对接 nexus-core.events.read.v1）─────────────────
  // 只读展示「x年x月x日 x时x分–x时x分 · 项目 / 任务 · N分钟」。
  // 不碰 POST /api/core/events（前端不直接写事实，契约硬边界），
  // 不在这里排序（后端已按 time 倒序返回，R8），不做本地乐观更新。

  function buildArchiveUrl(options) {
    options = options || {};
    var limit = options.limit != null ? options.limit : ARCHIVE_DEFAULT_LIMIT;
    var offset = options.offset != null ? options.offset : 0;
    return EVENTS_PATH + "?type=" + encodeURIComponent(ARCHIVE_TYPE) +
      "&limit=" + encodeURIComponent(limit) + "&offset=" + encodeURIComponent(offset);
  }

  // 复用写操作共享的 request()：GET 同样从不 reject，网络/HTTP 错误都归一化
  // 成 {ok:false,status,message}（C8 同款约定），调用方不用套 try/catch。
  function fetchArchive(options) {
    return request(resolveFetch(options), "GET", buildArchiveUrl(options));
  }

  // ── 甘特读端（F-TABLE-1 双轨的数据源，见上 GANTT_PATH 注释）─────────
  // 不传 from/to：那两个参数只过滤 actual[]，不传 = 不过滤，拿全部历史用来算
  // "计划期内有事实的天数"（rails.js 会自己按 plan.start/end 截窗口，这里不预筛，
  // 预筛一次以后想看窗口外的事实就要多打一次请求，不如一次拿全量在前端切）。
  function fetchGantt(options) {
    return request(resolveFetch(options), "GET", GANTT_PATH);
  }

  // ── 只读全量导出（顶栏「导出数据」按钮）───────────────────────────
  // 复用 request()：404（端点未上线）与网络错误同样归一化成 {ok:false,...}，
  // 调用方（app.js）不需要区分"没上线"和"打不通"，只要给出统一的
  // "导出端点未就绪"文案（硬要求：不许因此报 console 错误、不许崩页面）。
  function fetchExport(options) {
    return request(resolveFetch(options), "GET", EXPORT_PATH);
  }

  // 下载文件名：cockpit-export-<日期>.json，日期取自响应体的 exportedAt
  // （服务端生成，contract.md v1.3 明文），**不用本地 new Date() 拼**——
  // 客户端时钟/时区不准会让文件名的日期与响应内容的 exportedAt 不一致。
  // exportedAt 是带时区偏移的 ISO8601，直接切前 10 个字符拿 YYYY-MM-DD，
  // 不构造 Date 对象再重新格式化（那样会引入本地时区换算，同样会漂）。
  function exportFileName(exportedAt) {
    var datePart = (typeof exportedAt === "string" && exportedAt.length >= 10)
      ? exportedAt.slice(0, 10) : "unknown-date";
    return "cockpit-export-" + datePart + ".json";
  }

  // ── 事实 → 展示行的纯映射（R10 要求单测的部分）──────────────────────
  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  // "2026年8月1日"：年/月/日不补零，和设计示例逐字节一致（"8月1日"不是"08月01日"）。
  function formatArchiveDate(date) {
    return date.getFullYear() + "年" + (date.getMonth() + 1) + "月" + date.getDate() + "日";
  }

  // "21:15"：时分补零。
  function formatArchiveClock(date) {
    return pad2(date.getHours()) + ":" + pad2(date.getMinutes());
  }

  // 用浏览器本地时区展示（与 app.js 现有的系统时钟 toLocaleTimeString 同一约定）——
  // 后端给的是带时区偏移的 ISO8601，本函数不做时区换算之外的加工。
  function formatArchiveWhen(startAtIso, endIso) {
    var start = startAtIso ? new Date(startAtIso) : null;
    var end = endIso ? new Date(endIso) : null;
    var startOk = !!(start && !isNaN(start.getTime()));
    var endOk = !!(end && !isNaN(end.getTime()));
    var dateLabel = endOk ? formatArchiveDate(end) : (startOk ? formatArchiveDate(start) : "");
    var range = (startOk ? formatArchiveClock(start) : "--:--") + "–" + (endOk ? formatArchiveClock(end) : "--:--");
    return dateLabel ? (dateLabel + " " + range) : range;
  }

  // durationSeconds → "32分钟"：四舍五入到分钟；不足一分钟但确有时长时给 "<1分钟"，
  // 不伪造成 "0分钟"（那会看起来像没计时，而不是"计时了但很短"）。
  function formatArchiveDuration(durationSeconds) {
    var seconds = Number(durationSeconds);
    if (!isFinite(seconds) || seconds < 0) seconds = 0;
    var minutes = Math.round(seconds / 60);
    if (minutes < 1) return seconds > 0 ? "<1分钟" : "0分钟";
    return minutes + "分钟";
  }

  // subject.project / subject.task 是 opaque id，本函数拿它们去 tree 里现查名字
  // （R7）。查不到就展示 id 并标注"已删除"，不崩——项目和任务两类 id 用同一套
  // 兜底规则（这个坑不止任务会踩，项目同样可能被删）。
  function findProjectById(tree, projectId) {
    var projects = (tree && tree.projects) || [];
    for (var i = 0; i < projects.length; i += 1) {
      if (projects[i].id === projectId) return projects[i];
    }
    return null;
  }

  function findTaskById(tree, taskId) {
    var projects = (tree && tree.projects) || [];
    for (var i = 0; i < projects.length; i += 1) {
      var tasks = projects[i].tasks || [];
      for (var j = 0; j < tasks.length; j += 1) {
        if (tasks[j].id === taskId) return tasks[j];
      }
    }
    return null;
  }

  // "做家务 / 洗碗"，项目/任务查不到时改成 "id（已删除）"；subject.task
  // 未填（选填字段，见契约 Subject）时只展示项目，不拼 " / "。
  function resolveArchiveLabel(tree, subject) {
    subject = subject || {};
    var projectId = subject.project;
    var project = projectId ? findProjectById(tree, projectId) : null;
    var projectLabel = project ? project.name : (projectId ? projectId + "（项目已删除）" : "未知项目");

    if (!subject.task) return projectLabel;
    var task = findTaskById(tree, subject.task);
    var taskLabel = task ? task.name : subject.task + "（任务已删除）";
    return projectLabel + " / " + taskLabel;
  }

  // 单条事实 → 一行展示文案。tree 传当前已加载的 views/tree（可为 null，
  // 此时按"查不到"处理，不崩）。
  function mapArchiveEvent(event, tree) {
    event = event || {};
    var data = event.data || {};
    var when = formatArchiveWhen(data.startAt, event.time);
    var label = resolveArchiveLabel(tree, event.subject);
    var duration = formatArchiveDuration(data.durationSeconds);
    return {
      id: event.id,
      summary: when + " · " + label + " · " + duration
    };
  }

  return {
    CACHE_KEY: CACHE_KEY,
    buildTreeUrl: buildTreeUrl,
    fetchTree: fetchTree,
    readCache: readCache,
    writeCache: writeCache,
    readCacheTimestamp: readCacheTimestamp,
    indexZonesById: indexZonesById,
    deriveProjectColor: deriveProjectColor,
    deriveProjectIcon: deriveProjectIcon,
    isEmptyTree: isEmptyTree,
    computeSummary: computeSummary,
    createZone: createZone,
    renameZone: renameZone,
    deleteZone: deleteZone,
    createProject: createProject,
    renameProject: renameProject,
    moveProject: moveProject,
    deleteProject: deleteProject,
    createTask: createTask,
    renameTask: renameTask,
    moveTask: moveTask,
    toggleTaskDone: toggleTaskDone,
    deleteTask: deleteTask,
    updateTaskPlan: updateTaskPlan,
    updateTaskDependsOn: updateTaskDependsOn,
    hasScheduleFields: hasScheduleFields,
    listOtherTasks: listOtherTasks,
    fetchGantt: fetchGantt,
    fetchExport: fetchExport,
    exportFileName: exportFileName,
    ARCHIVE_DEFAULT_LIMIT: ARCHIVE_DEFAULT_LIMIT,
    buildArchiveUrl: buildArchiveUrl,
    fetchArchive: fetchArchive,
    formatArchiveDate: formatArchiveDate,
    formatArchiveClock: formatArchiveClock,
    formatArchiveWhen: formatArchiveWhen,
    formatArchiveDuration: formatArchiveDuration,
    findProjectById: findProjectById,
    findTaskById: findTaskById,
    resolveArchiveLabel: resolveArchiveLabel,
    mapArchiveEvent: mapArchiveEvent
  };
});
