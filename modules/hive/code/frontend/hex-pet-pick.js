// hive · hex-pet-pick.js —— 蜂巢新布局的宠物挑选（hive.stage.v1「二」3、4）。纯逻辑，无 DOM。
// 挑格子边至多 4 只 + +N、左上角无去向的代理、按 tool 查宠物包、phase → mood、左上角固定位、动作节奏。
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

  // 左上角固定位：一只一个位，不走动。已有位的保持不动，新来的按 order（开始时间 → runId）占最小的空位
  var SLOT_COLS = 3, SLOT_ROWS = 2;
  function slotPos(i, rect) {
    var cw = rect.w / SLOT_COLS, rh = rect.h / SLOT_ROWS;
    return { x: rect.x + cw * (i % SLOT_COLS) + cw / 2, y: rect.y + rh * (Math.floor(i / SLOT_COLS) % SLOT_ROWS) + rh / 2 };
  }
  // prev: {runId: 位号}；list: [{runId, startAt}]；返回新的 {runId: 位号}
  function assignSlots(prev, list) {
    var out = {}, used = {};
    list.forEach(function (r) { if (prev && prev[r.runId] != null && !used[prev[r.runId]]) { out[r.runId] = prev[r.runId]; used[prev[r.runId]] = 1; } });
    list.filter(function (r) { return out[r.runId] == null; }).sort(function (a, b) {
      return ms(a.startAt) - ms(b.startAt) || (a.runId < b.runId ? -1 : a.runId > b.runId ? 1 : 0);
    }).forEach(function (r) { var i = 0; while (used[i]) i++; used[i] = 1; out[r.runId] = i; });
    return out;
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
  // 临时动作（点击动作）：pack 的 click，没有用 happy，都没有 = 空表
  function tempList(pack) { var m = (pack && pack.moods) || {}; return m.click && m.click.length ? m.click : (m.happy || []); }
  function clickClip(pack, last, rnd) { return pickClip(tempList(pack), last, rnd); }

  // 动作节奏（仓主 2026-11-10 定）：当前 mood 的动作播 15 秒 → 3 秒临时动作 → 新动作（≠ 上一个）15 秒，循环。
  // s 是上一次的状态（首次 null）；moods = pack.moods；返回新状态 {mood, step: "main"|"temp", file, main, until}。纯函数，rnd / now 可注入。
  var MAIN_MS = 15000, TEMP_MS = 3000;
  function moodList(moods, mood) { var m = moods || {}; return (m[mood] && m[mood].length ? m[mood] : m.idle) || []; }
  function rhythm(s, moods, mood, now, rnd) {
    var list = moodList(moods, mood);
    if (!s) s = { mood: mood, step: "main", file: null, main: null, until: now + Math.floor(rnd() * MAIN_MS) };   // 首次随机错开，免得一起换
    else if (s.mood !== mood) s = { mood: mood, step: "main", file: null, main: s.main, until: now + MAIN_MS };       // 换 mood：立刻换动作、重算周期
    else if (now >= s.until) {
      var tmp = s.step === "main" ? tempList({ moods: moods }) : [];
      if (tmp.length) s = { mood: mood, step: "temp", file: pickClip(tmp, null, rnd), main: s.main, until: now + TEMP_MS };
      else s = { mood: mood, step: "main", file: null, main: s.main, until: now + MAIN_MS };
    } else if (s.step === "temp" || s.file) return s;
    if (s.step === "main" && !s.file) {
      var f = pickClip(list, s.main, rnd);
      if (f) { s = { mood: s.mood, step: "main", file: f, main: f, until: s.until }; }
    }
    return s;
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

  // 大六边形（尖顶，宽 w）的中心沿单位方向 (ux, uy) 到边界的距离：六条边的法线在 0° / ±60° / ±120°，
  // d = 边心距 ÷ cos(与最近法线的夹角)。展开时干活的小人站在这个距离再往外 reach 处（hive.stage.v1「十二」4）
  function hexEdgeDistance(w, ux, uy) {
    var fold = ((Math.atan2(uy, ux) * 180 / Math.PI) % 60 + 90) % 60 - 30;
    return (w / 2) / Math.cos(fold * Math.PI / 180);
  }

  var api = { hexEdgeDistance: hexEdgeDistance, MAX_PETS: MAX_PETS, DEFAULT_PACK: DEFAULT_PACK, DEFAULT_PACKS_BY_TOOL: DEFAULT_PACKS_BY_TOOL, moodOf: moodOf, packOf: packOf, pickWorkers: pickWorkers, pickIdle: pickIdle, isLoose: isLoose, slotPos: slotPos, assignSlots: assignSlots, rhythm: rhythm, tempList: tempList, MAIN_MS: MAIN_MS, TEMP_MS: TEMP_MS,
    CLICK_PX: CLICK_PX, STAY_MS: STAY_MS, pressKind: pressKind, clickCounts: clickCounts, pickClip: pickClip, clickClip: clickClip, walkBack: walkBack };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexPetPick = api;
})();
