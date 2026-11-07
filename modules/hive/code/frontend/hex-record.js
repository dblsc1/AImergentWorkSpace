// hive · hex-record.js —— 完成记录面板的选择与计算（hive.stage.v1「四」）。纯逻辑，无 DOM。
// 输入 = views/lanes 的响应；两条时间线 + 合计、该项目下每条运行一行。
(function () {
  "use strict";
  function ms(s) { var t = s ? Date.parse(s) : NaN; return isNaN(t) ? 0 : t; }

  // 合计只算今天（lanes.windowStart 起 24 小时）
  function total(spans, dayFrom) {
    var sum = 0;
    spans.forEach(function (s) {
      var a = Math.max(s.from, dayFrom), b = Math.min(s.to, dayFrom + 86400000);
      if (b > a) sum += (b - a) / 1000;
    });
    return Math.floor(sum);
  }

  // 运行的 working 秒数：phases 里 working 段的总和，段到下一条 phase 为止，最后一段到 endAt（没有 = 此刻）
  function workingSeconds(run, now) {
    var ph = run.phases || [], end = run.endAt ? ms(run.endAt) : now, sum = 0;
    ph.forEach(function (p, i) {
      if (p.phase !== "working") return;
      var b = i + 1 < ph.length ? ms(ph[i + 1].at) : end;
      sum += Math.max(0, b - ms(p.at));
    });
    return Math.floor(sum / 1000);
  }

  // runs：已带 phase 的运行（页面的 runsOf）；projectOfTask(taskId) → 项目 id | null
  function projectRecord(lanes, runs, pid, projectOfTask, now) {
    lanes = lanes || {};
    var mine = (runs || []).filter(function (r) { return r.projectId === pid || (r.taskId && projectOfTask(r.taskId) === pid); });
    var ids = {};
    mine.forEach(function (r) { ids[r.runId] = 1; });
    var from = ms(lanes.windowStart);
    var timer = ((lanes.human && lanes.human.sessions) || []).filter(function (s) { return s.projectId === pid; })
      .map(function (s) { return { from: ms(s.startAt), to: ms(s.endAt), kind: "timer" }; });
    var attend = (lanes.interactions || []).filter(function (a) { return a.kind === "attend" && ids[a.runId]; })
      .map(function (a) { return { from: ms(a.at), to: ms(a.until), kind: "attend" }; });
    return {
      timer: { spans: timer, total: total(timer, from) },
      attend: { spans: attend, total: total(attend, from) },
      runs: mine.map(function (r) { return { runId: r.runId, name: r.label || r.agent, workingSeconds: workingSeconds(r, now), phase: r.phase, lost: !!r.lost }; })
    };
  }

  var api = { projectRecord: projectRecord, workingSeconds: workingSeconds };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexRecord = api;
})();
