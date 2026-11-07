// hive · hex-pet-pick.js —— 蜂巢新布局的宠物挑选（hive.stage.v1「二」3、4）。纯逻辑，无 DOM。
// 挑格子边至多 4 只 + +N、左上角无去向的代理、按 tool 查宠物包、phase → mood、左上角随机走动。
(function () {
  "use strict";

  var MAX_PETS = 4;
  var DEFAULT_PACK = "deepseek";
  var DEFAULT_PACKS_BY_TOOL = { "claude-code": "claude", "codex": "gpt", "gemini-cli": "gemini", "*": DEFAULT_PACK };

  function ms(s) { var t = s ? Date.parse(s) : NaN; return isNaN(t) ? 0 : t; }
  function lastActive(r) { return ms(r.lastSeenAt || r.startAt || r.startedAt); }
  function isRunning(r) { return r.running !== false && !r.endAt && !r.endedAt; }

  function moodOf(phase, lost) {
    if (lost) return "idle";
    if (phase === "working") return "working";
    if (phase === "waiting_input" || phase === "waiting_permission") return "waiting";
    return "idle";
  }

  function packOf(run, packsByTool) {
    var m = packsByTool || DEFAULT_PACKS_BY_TOOL;
    return m[run.tool] || m["*"] || DEFAULT_PACK;
  }

  // 最近活动从新到旧，并列 runId 升序；取 MAX_PETS 只，其余记 plus
  function top(runs, opts, projectOf) {
    var sorted = runs.slice().sort(function (a, b) {
      return lastActive(b) - lastActive(a) || (a.runId < b.runId ? -1 : a.runId > b.runId ? 1 : 0);
    });
    return {
      pets: sorted.slice(0, MAX_PETS).map(function (r) {
        return { runId: r.runId, name: r.label || r.agent, mood: moodOf(r.phase, r.lost), pack: packOf(r, opts && opts.packsByTool), projectId: projectOf(r) };
      }),
      plus: Math.max(0, sorted.length - MAX_PETS)
    };
  }

  function projectOfRun(r, opts) {
    if (r.projectId) return r.projectId;
    return (r.taskId && opts && opts.projectOfTask && opts.projectOfTask(r.taskId)) || null;
  }

  // 格子边：在跑、挂了任务或项目（任务能解出项目）的运行
  function pickWorkers(runs, opts) {
    var hosted = (runs || []).filter(function (r) { return isRunning(r) && projectOfRun(r, opts); });
    return top(hosted, opts, function (r) { return projectOfRun(r, opts); });
  }

  // 左上角：在跑、taskId 与 projectId 都没有
  function pickIdle(runs, opts) {
    var loose = (runs || []).filter(function (r) { return isRunning(r) && !r.projectId && !r.taskId; });
    return top(loose, opts, function () { return null; });
  }

  // 左上角随机走动：朝随机目标点走，每步位移 ≤ speed(px/s) × dt；位置始终在 rect 内
  function pick(rect, rnd) { return { x: rect.x + rnd() * rect.w, y: rect.y + rnd() * rect.h }; }
  function initWander(rect, rnd) { var p = pick(rect, rnd), t = pick(rect, rnd); return { x: p.x, y: p.y, tx: t.x, ty: t.y }; }
  function stepWander(s, rect, rnd, dtMs, speed) {
    var tx = s.tx, ty = s.ty, dx = tx - s.x, dy = ty - s.y, d = Math.hypot(dx, dy), step = speed * dtMs / 1000;
    if (d <= step) {
      var n = pick(rect, rnd);
      return { x: tx, y: ty, tx: n.x, ty: n.y };
    }
    var x = s.x + dx / d * step, y = s.y + dy / d * step;
    return {
      x: Math.min(rect.x + rect.w, Math.max(rect.x, x)),
      y: Math.min(rect.y + rect.h, Math.max(rect.y, y)),
      tx: tx, ty: ty
    };
  }

  var api = { MAX_PETS: MAX_PETS, DEFAULT_PACK: DEFAULT_PACK, DEFAULT_PACKS_BY_TOOL: DEFAULT_PACKS_BY_TOOL, moodOf: moodOf, packOf: packOf, pickWorkers: pickWorkers, pickIdle: pickIdle, initWander: initWander, stepWander: stepWander };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexPetPick = api;
})();
