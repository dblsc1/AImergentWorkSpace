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

  // 状态 → 动作（hive.stage.v1「三十二」1）。mood 键：working / chatting / worried / waiting / idle / idle-bored / idle-slacking / idle-sleeping
  //   chatting = 人此刻正跟它对话；worried = 出错或等授权；idle 按「从最后一次干活结束算起」分档：
  //   < 1 分钟 idle；1–5 分钟 idle-bored（扶额等待）；5–30 分钟 idle-slacking（开小差）；30 分钟起 idle-sleeping；满 1 小时 home = 飞回左上角睡
  var BORED_MS = 60000, SLACK_MS = 300000, SLEEP_MS = 1800000, HOME_MS = 3600000;
  function byAt(a, b) { return ms(a.at) - ms(b.at); }
  // 最后一次干活的结束时刻（ms）：最后一个 working 相位之后第一个相位的时刻；最后一相还是 working = null（正在干）；
  // 没干过 = run.lastWorkAt（views/lanes 折叠行给的），再没有 = 运行开始
  function lastWorkedAt(run) {
    var ph = (run.phases || []).slice().sort(byAt), i = ph.length - 1;
    while (i >= 0 && ph[i].phase !== "working") i--;
    if (i < 0) return ms(run.lastWorkAt) || ms(run.startAt || run.startedAt);
    return i + 1 < ph.length ? ms(ph[i + 1].at) : null;
  }
  // 这一拍的状态 {mood, home, idleMs}。watchedId = 人此刻正看着的那条运行的 runId（hive.stage.v1「二十一」的判据）
  function stateOf(run, now, watchedId) {
    var ph = run.phase;
    if (run.lost) return { mood: "idle", home: false, idleMs: 0 };
    if (ph === "error" || ph === "waiting_permission") return { mood: "worried", home: false, idleMs: 0 };
    if (watchedId && run.runId === watchedId) return { mood: "chatting", home: false, idleMs: 0 };
    if (ph === "working") return { mood: "working", home: false, idleMs: 0 };
    if (ph === "waiting_input") return { mood: "waiting", home: false, idleMs: 0 };
    var since = lastWorkedAt(run), idle = since == null ? 0 : Math.max(0, now - since);
    return { mood: idle >= SLEEP_MS ? "idle-sleeping" : idle >= SLACK_MS ? "idle-slacking" : idle >= BORED_MS ? "idle-bored" : "idle", home: idle >= HOME_MS, idleMs: idle };
  }
  // views/lanes 把空闲久了 / 出错的折叠进 inactiveAgents（没有 runId）；views/current 的 agents 仍列着所有在跑的。
  // 把 lanes 里没有的在跑运行补上，最后干活时刻按 agent + label 从折叠行取
  function withCurrentRuns(runs, currentAgents, inactiveAgents) {
    var seen = {};
    (runs || []).forEach(function (r) { seen[r.runId] = 1; });
    var extra = (currentAgents || []).filter(function (a) { return a && !seen[a.runId]; }).map(function (a) {
      var row = (inactiveAgents || []).filter(function (x) { return x.agent === a.agent && (x.label || null) === (a.label || null); })[0];
      return { runId: a.runId, agent: a.agent, tool: a.tool, model: a.model, label: a.label, taskId: a.taskId, projectId: null, startAt: a.startedAt, endAt: null,
        lost: false, phase: a.phase || "idle", phases: [], attention: [], elapsedSeconds: a.elapsedSeconds, lastWorkAt: row ? row.lastWorkAt : null, lastSeenAt: row ? row.lastWorkAt : null };
    });
    return (runs || []).concat(extra);
  }
  // 满 1 小时的睡觉小人不再站格子里：飞回左上角（同没有去向的那排位置）
  function stateFor(r, opts) { return opts && opts.now != null ? stateOf(r, opts.now, opts.watchedId) : { mood: moodOf(r.phase, r.lost), home: false }; }

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
        return { runId: r.runId, name: r.label || r.agent, mood: stateFor(r, opts).mood, pack: packOf(r, opts && opts.packsByTool), projectId: projectOf(r) };
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
    var hosted = (runs || []).filter(function (r) { return isRunning(r) && projectOfRun(r, opts) && !stateFor(r, opts).home; });
    return top(hosted, opts, function (r) { return projectOfRun(r, opts); });
  }

  // 左上角：在跑、没有任务也没有项目（或只挂在收件箱）
  function pickIdle(runs, opts) {
    var loose = (runs || []).filter(function (r) { return isRunning(r) && (isLoose(r, opts) || stateFor(r, opts).home); });
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

  // 动作节奏（仓主 2026-11-12 改，hive.stage.v1「二十六」）：当前 mood 的动作里随机挑一个当「主动作」播 15 秒 → 3 秒随机临时动作 →
  // **回到原来那个主动作** 15 秒，如此循环；主动作每 5 分钟换一个（≠ 上一个），换 mood 立刻换。
  // s 是上一次的状态（首次 null）；moods = pack.moods；返回新状态 {mood, step: "main"|"temp", file, main, until, keepUntil}。纯函数，rnd / now 可注入。
  var MAIN_MS = 15000, TEMP_MS = 3000, KEEP_MS = 300000;
  // 包里没有这个 mood 键时的回退（「三十二」1）：chatting → happy / click，idle-bored → waiting，idle-slacking → idle，idle-sleeping → idle 里文件名含 小憩 / 沉眠 / 睡 的，最后都回 idle
  var MOOD_FALLBACK = { "chatting": ["happy", "click"], "idle-bored": ["waiting"], "idle-slacking": ["idle"], "idle-sleeping": ["idle"], worried: ["waiting"] };
  var SLEEP_FILE = /小憩|沉眠|睡/;
  function moodList(moods, mood) {
    var m = moods || {};
    if (m[mood] && m[mood].length) return m[mood];
    var chain = MOOD_FALLBACK[mood] || [];
    if (mood === "idle-sleeping") { var z = (m.idle || []).filter(function (f) { return SLEEP_FILE.test(f); }); if (z.length) return z; }
    for (var i = 0; i < chain.length; i++) if (m[chain[i]] && m[chain[i]].length) return m[chain[i]];
    return m.idle || [];
  }
  function rhythm(s, moods, mood, now, rnd) {
    var list = moodList(moods, mood);
    function fresh(last, until) { var f = pickClip(list, last, rnd); return { mood: mood, step: "main", file: f, main: f || last, until: until, keepUntil: now + KEEP_MS }; }
    if (!s) return fresh(null, now + Math.floor(rnd() * MAIN_MS));   // 首次随机错开，免得一起换
    if (s.mood !== mood) return fresh(s.main, now + MAIN_MS);          // 换 mood：立刻换动作、重算周期
    if (s.step === "main" && !s.file) { var f0 = pickClip(list, s.main, rnd); return f0 ? { mood: mood, step: "main", file: f0, main: f0, until: s.until, keepUntil: now + KEEP_MS } : s; }   // 包刚下完：补上
    if (now < s.until) return s;
    if (s.step === "main" && mood !== "idle-sleeping") {   // 睡着了不插点击动作
      var tmp = tempList({ moods: moods });
      if (tmp.length) return { mood: mood, step: "temp", file: pickClip(tmp, null, rnd), main: s.main, until: now + TEMP_MS, keepUntil: s.keepUntil };
    }
    if (now >= (s.keepUntil || 0)) return fresh(s.main, now + MAIN_MS);   // 满 5 分钟：换一个主动作
    return { mood: mood, step: "main", file: s.main, main: s.main, until: now + MAIN_MS, keepUntil: s.keepUntil };   // 回到原来的主动作
  }

  // 人看着某个 agent 时的互动（hive.stage.v1「二十一」）。动作名各包相同（mood-map.json 一份）：
  // 人挥手（点击回应-元气挥手）、小人跃动（点击回应-开心跃动）；名字对不上取 happy 第一段，再取 click 第一段；都没有 = null（用 CSS 小跳）
  var MEET_KEY = { human: /挥手/, pet: /跃动/ };
  function meetClip(moods, role) {
    var m = moods || {}, pool = (m.happy || []).concat(m.click || []);
    return pool.filter(function (f) { return MEET_KEY[role] && MEET_KEY[role].test(f); })[0] || (m.happy && m.happy[0]) || (m.click && m.click[0]) || null;
  }
  // 人站在小人旁边：人来的那一侧，比名字牌低（名字牌最宽 110px，会伸到小人身体两侧）。petK = 小人此刻的个头倍数；
  // 素材默认朝左：人在右边 → 人不翻、小人翻；人在左边反之
  function meetSpot(petPos, eyePos, petK) {
    var k = petK || 1, side = eyePos.x < petPos.x ? -1 : 1;
    return { x: petPos.x + side * (30 * k + 33), y: petPos.y + 30 * k, side: side, eyeFlip: side < 0, petFlip: side > 0 };
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

  // 干活的小人平时站在格子里：围着格心一圈，右上 → 右下 → 左下 → 左上；多于 4 个的接着同样四个方位、离格心一半距离
  // （hive.stage.v1「二十二」1）。返回相对格心的偏移
  var REST_SIDES = [[1, -1], [1, 1], [-1, 1], [-1, -1]];
  function restSlot(i, w, h) {
    var s = REST_SIDES[i % 4], k = i < 4 ? 0.2 : 0.1;
    return { x: s[0] * w * k, y: s[1] * h * k };
  }

  var api = { stateOf: stateOf, lastWorkedAt: lastWorkedAt, withCurrentRuns: withCurrentRuns, moodList: moodList, BORED_MS: BORED_MS, SLACK_MS: SLACK_MS, SLEEP_MS: SLEEP_MS, HOME_MS: HOME_MS, restSlot: restSlot, hexEdgeDistance: hexEdgeDistance, MAX_PETS: MAX_PETS, DEFAULT_PACK: DEFAULT_PACK, DEFAULT_PACKS_BY_TOOL: DEFAULT_PACKS_BY_TOOL, moodOf: moodOf, packOf: packOf, pickWorkers: pickWorkers, pickIdle: pickIdle, isLoose: isLoose, slotPos: slotPos, assignSlots: assignSlots, rhythm: rhythm, tempList: tempList, MAIN_MS: MAIN_MS, KEEP_MS: KEEP_MS, TEMP_MS: TEMP_MS,
    CLICK_PX: CLICK_PX, STAY_MS: STAY_MS, pressKind: pressKind, clickCounts: clickCounts, pickClip: pickClip, clickClip: clickClip, walkBack: walkBack, meetClip: meetClip, meetSpot: meetSpot };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.NexusTableHexPetPick = api;
})();
