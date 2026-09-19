// hive · 蜂巢 · 审计流派生（无 DOM，2026-09-14 从 hex-data.js 拆出）
//
// 拆出来的原因是 hex-data.js 撞了 1000 行硬线（1066）。缝本来就在：
// 这一半只认 planner/audit 流水和 next-actions 两份**数据**，一行几何都不碰
// （实测交叉引用：它不用坐标层的任何函数，坐标层也只在注释里提过它一次）。
//
// 两件事：
//   1. 「最近完成」：从 audit 流水里筛 done 相关动作，**按任务去重只看最后一次
//      动作** —— 实测数据里有人勾了又取消（12:08:32 done=true、12:08:34 done=false），
//      漏了去重就是给人类显示假成绩。热度（completionHeat）建在同一份去重结果上。
//   2. 待办取数：next-actions 已按分区分好组排好序，本文件只做 projectId 归并，
//      **不重新分类、不重新排序**（同 gtd-data.js 既有口径）。
//
// 调用方不需要认识本文件：hex-data.js 原样再导出了这里的每一个名字，
// `H.topTodos` / `H.completionHeat` 这些写法一个字都没变。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.NexusTableHexAudit = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ── 3. 「最近完成」：按任务去重，只认最后一次 done 动作 ──────────
  // ⚠️ 本节是本文件最容易做错、也最贵的一条：实测流水里同一个任务
  // 12:08:32 done=true、12:08:34 done=false（勾了又取消）。不去重就会把
  // 一个没做完的任务当成"刚完成"报给人类——假成绩比没有成绩糟得多。
  function isDoneChange(item) {
    return !!(item && item.objectType === "tasks" && item.changes &&
      Object.prototype.hasOwnProperty.call(item.changes, "done"));
  }

  // 排序键：优先用后端单调递增的 seq，缺了才退回时间戳。
  function auditOrder(item) {
    if (item && typeof item.seq === "number") return item.seq;
    var t = Date.parse((item && item.at) || "");
    return isNaN(t) ? 0 : t;
  }

  // 每个 objectId 只留**最后一次** done 相关动作，再筛 done===true。
  function lastDoneActions(items) {
    var sorted = ((items || []).filter(isDoneChange)).slice().sort(function (a, b) {
      return auditOrder(a) - auditOrder(b);
    });
    var last = {};
    sorted.forEach(function (item) { last[item.objectId] = item; });
    return Object.keys(last).map(function (k) { return last[k]; })
      .filter(function (item) { return item.changes.done === true; })
      .sort(function (a, b) { return auditOrder(b) - auditOrder(a); });
  }

  // tree → taskId 的归属索引（任务名 / 项目 / 分区）。
  function indexTasks(tree) {
    var zonesById = {};
    ((tree && tree.zones) || []).forEach(function (z) { zonesById[z.id] = z; });
    var idx = {};
    ((tree && tree.projects) || []).forEach(function (p) {
      (p.tasks || []).forEach(function (t) {
        idx[t.id] = {
          taskId: t.id, taskName: t.name,
          projectId: p.id, projectName: p.name,
          zoneId: p.zoneId,
          zoneName: (zonesById[p.zoneId] && zonesById[p.zoneId].name) || "?"
        };
      });
    });
    return idx;
  }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  // ISO 时间戳 → "YYYY-MM-DD HH:MM"。offsetMinutes 东正西负（UTC+8 传 480）；
  // 不传就用运行环境的真实本地偏移。显式传入是为了单测不依赖跑测机器的时区
  // （沿用既有先例）。
  function formatStamp(iso, offsetMinutes) {
    var ms = Date.parse(iso || "");
    if (isNaN(ms)) return "";
    var off = (offsetMinutes == null) ? -(new Date(ms)).getTimezoneOffset() : Number(offsetMinutes);
    var d = new Date(ms + off * 60000);
    return d.getUTCFullYear() + "-" + pad2(d.getUTCMonth() + 1) + "-" + pad2(d.getUTCDate()) +
      " " + pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes());
  }

  // ── 最近完成热度（人类 2026-09-08：「一个项目和分区如果经常有任务被完成，
  // 就把它排的更靠近中心」）─────────────────────────────────────
  //
  // 不是数总次数，是**按半衰期加权**：7 天前完成的一条只算半条。
  // 数总次数的话，一个三个月前疯狂产出、现在已经停了的项目会永远霸着中心 ——
  // 人类要的是"最近"热度，不是历史总量。
  // 半衰期做成参数是为了能被单测钉死（喂固定的 now，结果是确定值）。
  var HEAT_HALFLIFE_DAYS = 7;
  function completionHeat(auditItems, tree, options) {
    options = options || {};
    var now = options.now == null ? Date.now() : options.now;
    var half = options.halfLifeDays || HEAT_HALFLIFE_DAYS;
    var idx = indexTasks(tree);
    var out = {};
    lastDoneActions(auditItems).forEach(function (item) {
      var hit = idx[item.objectId];
      if (!hit || !hit.projectId) return;          // 已删除的任务不计热度
      var t = Date.parse(item.at);
      var w = 1;
      if (!isNaN(t)) {
        var days = (now - t) / 86400000;
        if (days < 0) days = 0;                    // 时钟偏差不给未来加成
        w = Math.pow(0.5, days / half);
      }
      out[hit.projectId] = (out[hit.projectId] || 0) + w;
    });
    return out;
  }

  function recentCompletions(auditItems, tree, options) {
    options = options || {};
    var idx = indexTasks(tree);
    var rows = lastDoneActions(auditItems).map(function (item) {
      var hit = idx[item.objectId];
      return {
        taskId: item.objectId,
        at: item.at,
        stamp: formatStamp(item.at, options.offsetMinutes),
        taskName: hit ? hit.taskName : null,
        projectId: hit ? hit.projectId : null,
        projectName: hit ? hit.projectName : null,
        zoneName: hit ? hit.zoneName : null,
        missing: !hit,
        path: hit ? (hit.zoneName + "/" + hit.projectName + "/" + hit.taskName)
                  : ("已删除的任务 " + item.objectId)
      };
    });
    if (options.limit != null) return rows.slice(0, options.limit);
    return rows;
  }

  // 某个项目最近完成的一条；没有就 null（调用方给「暂无完成记录」文案）。
  function latestCompletionForProject(completions, projectId) {
    var hit = (completions || []).filter(function (c) { return c.projectId === projectId; });
    return hit.length ? hit[0] : null;
  }

  // ── 4. 待办取数：只归并，不重排（后端已排好序）───────────────────
  function nextActionsByProject(nextActions) {
    var map = {};
    function push(item, state) {
      var list = map[item.projectId] || (map[item.projectId] = []);
      list.push({
        id: item.id, name: item.name, state: state,
        projectId: item.projectId, projectName: item.projectName,
        overdue: !!item.overdue, dueToday: !!item.dueToday,
        // 优先级信号（契约 ai-planner-guide-v1：plannedWeight 就是它）。
        // ⚠️ **只有 next-actions 这个读端给**：views/tree 的任务对象里
        // 根本没有这个字段（真机实测全是 undefined）。所以"全部待办"这一栏
        // 必须走 next-actions，不能从 tree 的 tasks 里筛 —— 那边排不了序。
        plannedWeight: item.plannedWeight == null ? 0 : item.plannedWeight,
        blockedBy: item.blockedBy || []
      });
    }
    ((nextActions && nextActions.zones) || []).forEach(function (zone) {
      (zone.actionable || []).forEach(function (t) { push(t, "actionable"); });
    });
    ((nextActions && nextActions.zones) || []).forEach(function (zone) {
      (zone.waiting || []).forEach(function (t) { push(t, "waiting"); });
    });
    return map;
  }

  var NO_TODO_TEXT = "无待办";

  // 长按项目格自动建的子任务用什么名字（人类：「标题就弄个日期+时间任务」）。
  // 放在这一层是因为它是**纯函数**，DOM 层那边一律不做能被钉死的判断。
  // 带年份不是啰嗦：这条名字会一直躺在计时档案里，跨年之后只有「09-08 00:43」
  // 就分不清是哪一年的了。
  function stampTaskName(when) {
    var d = when || new Date();
    function p(n) { return n < 10 ? "0" + n : String(n); }
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
           " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }

  // hover 只列**最近的一条**待办；一条都没有时给明确文案，不留空白。
  function hoverTodoText(list) {
    if (!list || !list.length) return NO_TODO_TEXT;
    var first = list[0];
    return first.state === "waiting" ? ("等待中 · " + first.name) : first.name;
  }

  function topTodos(list, n) {
    return (list || []).slice(0, n == null ? 3 : n);
  }

  return {
    NO_TODO_TEXT: NO_TODO_TEXT,
    isDoneChange: isDoneChange,
    auditOrder: auditOrder,
    lastDoneActions: lastDoneActions,
    indexTasks: indexTasks,
    formatStamp: formatStamp,
    recentCompletions: recentCompletions,
    latestCompletionForProject: latestCompletionForProject,
    nextActionsByProject: nextActionsByProject,
    hoverTodoText: hoverTodoText,
    topTodos: topTodos,
    stampTaskName: stampTaskName,
    completionHeat: completionHeat,
    HEAT_HALFLIFE_DAYS: HEAT_HALFLIFE_DAYS
  };
});
