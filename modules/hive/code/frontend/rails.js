// table · 双轨计算（纯逻辑，无 DOM 依赖，F-TABLE-1）
//
// 项目卡的紫轨（计划期已走比例）+ 青轨（有事实天数比例），数据来自
// GET /api/core/views/gantt（data.js 的 fetchGantt，见那里的注释——这是既有
// 投影，不是新加的后端能力）。拆成独立文件的理由与 crud.js/app.js 当初拆分
// 同一个（单文件 500 行上限 + 关注点分开）：data.js 已经很满，且"读/写
// 网络请求"与"把一个 {plan,actual[]} 换算成两条百分比"是两类不同的活，
// 后者是纯数学、天然适合单独测（rails.test.js）。
//
// 口径（PRD F-TABLE-1 + 设计稿 §4 示例核对过）：
//   - 无 plan（未排期）：两轨都空；caption 右侧仍显示"事实 N 天"——
//     N 是 actual[] 里 seconds>0 的天数，不受任何窗口限制（没有计划期就没有
//     "计划期内"这个概念，只能报"总共有过事实的天数"）。
//   - 有 plan：
//       totalDays   = plan 区间的天数（含首尾两天）
//       elapsedDays = 今天（服务端 today，不用本地时钟——见 nexus-core 契约
//                     「甘特读端」的理由：客户端时区/时钟不准会让红线飘）
//                     落在区间内的第几天；今天早于 start 记 0，晚于 end 记满
//       factDays    = actual[] 里落在 [start,end] 区间内且 seconds>0 的天数
//       ended       = today > end（挂琥珀"已结束"章，不标红——PRD 原文）
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.NexusTableRails = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // "YYYY-MM-DD" → 当天 UTC 零点的毫秒数。用 UTC 而不是本地时区解析，避免
  // 不同浏览器时区把同一个日期字符串解析成不同的"那一刻"，天数差就会跟着漂。
  function parseDateUTC(s) {
    var parts = String(s).split("-");
    return Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  }

  function daysBetween(a, b) {
    return Math.round((parseDateUTC(b) - parseDateUTC(a)) / 86400000);
  }

  function clamp(n, lo, hi) {
    return Math.max(lo, Math.min(hi, n));
  }

  // "2026-08-01" → "08-01"（与设计稿 §4 的"计划 07-30 → 08-06"逐字节一致的格式）。
  function formatMonthDay(dateStr) {
    return String(dateStr).slice(5);
  }

  // ganttProject：views/gantt 返回的单个项目对象 { plan, actual[] }（plan 可 null）。
  // todayIso：GanttOut.today（服务端给的"今天"，"YYYY-MM-DD"）。
  function computeProjectRail(ganttProject, todayIso) {
    var plan = ganttProject && ganttProject.plan;
    var actual = (ganttProject && ganttProject.actual) || [];

    if (!plan || !plan.start || !plan.end) {
      var factDaysTotal = actual.filter(function (d) { return Number(d.seconds) > 0; }).length;
      return { hasPlan: false, factDaysTotal: factDaysTotal };
    }

    var start = plan.start;
    var end = plan.end;
    // 计划本身首尾颠倒（后端目前不校验，F-API-3 明文允许排期不校验）——
    // 不崩，总天数按 1 天兜底，好过除零或负宽度的轨。
    var totalDays = Math.max(1, daysBetween(start, end) + 1);

    var elapsedDays;
    if (todayIso < start) {
      elapsedDays = 0;
    } else if (todayIso > end) {
      elapsedDays = totalDays;
    } else {
      elapsedDays = daysBetween(start, todayIso) + 1;
    }
    elapsedDays = clamp(elapsedDays, 0, totalDays);

    var factDaysInWindow = actual.filter(function (d) {
      return Number(d.seconds) > 0 && d.date >= start && d.date <= end;
    }).length;

    var planRatio = clamp(Math.round((elapsedDays / totalDays) * 100), 0, 100);
    var factRatio = clamp(Math.round((factDaysInWindow / totalDays) * 100), 0, 100);
    var ended = todayIso > end;

    return {
      hasPlan: true,
      start: start,
      end: end,
      totalDays: totalDays,
      elapsedDays: elapsedDays,
      factDaysInWindow: factDaysInWindow,
      planRatio: planRatio,
      factRatio: factRatio,
      ended: ended
    };
  }

  // rail → "计划 N 天 · 已走完/进行中/未开始"（左侧 caption，仅 hasPlan 时调用）。
  function formatPlanCaption(rail) {
    var status = rail.ended ? "已走完" : (rail.elapsedDays === 0 ? "未开始" : "进行中");
    return "计划 " + rail.totalDays + " 天 · " + status;
  }

  // rail → "事实 M / N 天"（右侧 caption，仅 hasPlan 时调用；与设计稿 §4 逐字节一致）。
  function formatFactCaption(rail) {
    return "事实 " + rail.factDaysInWindow + " / " + rail.totalDays + " 天";
  }

  function indexGanttByProjectId(ganttProjects) {
    var map = {};
    (ganttProjects || []).forEach(function (p) { map[p.id] = p; });
    return map;
  }

  return {
    computeProjectRail: computeProjectRail,
    formatMonthDay: formatMonthDay,
    formatPlanCaption: formatPlanCaption,
    formatFactCaption: formatFactCaption,
    indexGanttByProjectId: indexGanttByProjectId
  };
});
