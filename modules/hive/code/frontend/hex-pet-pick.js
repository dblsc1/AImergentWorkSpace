// hive · hex-pet-pick.js —— 蜂巢新布局的宠物挑选（hive.stage.v1「二」3、4）。纯逻辑，无 DOM。
// 挑格子边至多 4 只 + +N、左上角无去向的代理、按 tool 查宠物包、phase → mood、左上角随机走动。
(function () {
  "use strict";

  var MAX_PETS = 4;
  var INBOX = "p_inbox";  // 收件箱不是格子：挂在这里的代理算没去向
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
    var p = r.projectId || (r.taskId && opts && opts.projectOfTask && opts.projectOfTask(r.taskId)) || null;
    return p === INBOX ? null : p;
  }
  function isLoose(r, opts) {
    return (!r.projectId || r.projectId === INBOX) &&
      (!r.taskId || !!(opts && opts.projectOfTask && opts.projectOfTask(r.taskId) === INBOX));
  }

  // 格子边：在跑、挂了任务或项目（任务能解出项目）的运行
  function pickWorkers(runs, opts) {
    var hosted = (runs || []).filter(function (r) { return isRunning(r) && projectOfRun(r, opts); });
    return top(hosted, opts, function (r) { return projectOfRun(r, opts); });
  }

  // 左上角：在跑、没有任务也没有项目（或只挂在收件箱）
  function pickIdle(runs, opts) {
    var loose = (runs || []).filter(function (r) { return isRunning(r) && isLoose(r, opts); });
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

  // 点宠物、拖宠物（hive.stage.v1「二」7）：判定、挑点击动画、走回
  var CLICK_PX = 5, AFTER_DRAG_MS = 100, STAY_MS = 3000, WALK_SPEED = 45, WALK_MAX_MS = 8000;
  function pressKind(dx, dy) { return Math.hypot(dx, dy) < CLICK_PX ? "click" : "drag"; }
  function clickCounts(sinceDragEndMs) { return !(sinceDragEndMs >= 0 && sinceDragEndMs < AFTER_DRAG_MS); }
  // 从列表随机挑一段，不挑 last；只有一段就是它；空 = null
  function pickClip(list, last, rnd) {
    if (!list || !list.length) return null;
    var pool = list.length > 1 ? list.filter(function (f) { return f !== last; }) : list;
    return pool[Math.min(pool.length - 1, Math.floor(rnd() * pool.length))];
  }
  function clickClip(pack, last, rnd) {
    var m = (pack && pack.moods) || {};
    return pickClip(m.click && m.click.length ? m.click : m.happy, last, rnd);
  }
  // 松手后 elapsedMs：先停 STAY_MS，再以 45px/s 走，单程至多 8 秒（远了加快）；往右走 = flip
  function walkBack(from, to, elapsedMs) {
    var dx = to.x - from.x, dy = to.y - from.y, d = Math.hypot(dx, dy), flip = dx > 0;
    var dur = Math.min(WALK_MAX_MS, d / WALK_SPEED * 1000);
    if (d === 0 || elapsedMs >= STAY_MS + dur) return { x: to.x, y: to.y, phase: "done", flip: flip };
    if (elapsedMs < STAY_MS) return { x: from.x, y: from.y, phase: "stay", flip: flip };
    var u = (elapsedMs - STAY_MS) / dur;
    return { x: from.x + dx * u, y: from.y + dy * u, phase: "walk", flip: flip };
  }

  var api = { MAX_PETS: MAX_PETS, DEFAULT_PACK: DEFAULT_PACK, DEFAULT_PACKS_BY_TOOL: DEFAULT_PACKS_BY_TOOL, moodOf: moodOf, packOf: packOf, pickWorkers: pickWorkers, pickIdle: pickIdle, isLoose: isLoose, initWander: initWander, stepWander: stepWander,
    CLICK_PX: CLICK_PX, STAY_MS: STAY_MS, pressKind: pressKind, clickCounts: clickCounts, pickClip: pickClip, clickClip: clickClip, walkBack: walkBack };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexPetPick = api;
})();
