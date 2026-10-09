/* 泳道渲染（nexus-core.views.lanes.v1）—— 计时页的全部泳道与顶栏芯片的精简预览共用这一份。
 *
 * 网关把它和 navbar.js 放在同一个目录（<前缀>__cockpit/lanes.js，不设门、不含数据）：
 * 顶栏在预览第一次打开时才加载它；计时页（ring-lanes.js）同样按 HONEYCOMB_BASE 加载。
 * 本文件只画，不发请求；配套样式 lanes.css 由本文件在加载时自己挂上（同目录）。
 *
 * 对外只挂 window.HoneycombLanes：
 *   query(prev, hours)             下一次该带的查询串（实时窗口跨零点时读两天）
 *   segments(run, nowMs)           相位观测 → 段（读时合并同相位，第一个转入点之前按 working）
 *   currentPhase(run)              按 at 排最后的那条相位，null → working
 *   pickPreview(agents, v0, max)   顶栏预览的挑法与排序（契约「泳道预览」）
 *   activeSeconds(run, v0, v1, now)  运行在视窗里不空闲的秒数
 *   sortByActivity(agents, v0, v1, now)  计时页卡片的排序（档位：在等你 → 干活 → 出错 → 空闲 → 已结束；同档按活跃秒数、最近转入）
 *   recentRuns(agents, now)        计时页只留在跑的 + 结束不到 3 小时的运行（2026-10-08）
 *   attentionSeconds(run, v0, v1)  人的注意力在这条运行上、落在视窗里的秒数（nexus-core v2.17 的 agents[].attention）
 *   humanStatus(human, now)        人此刻：在电脑前 / 离开 / 不在线（+ 在计时 / 前台程序；v2.14 + 自动跟踪 auto {text, since}；
 *                                  v2.16 + focus：共享件 focus.js 的 describe() 结果，auto 的字也出自它）
 *   render(root, data, opts)       画一张图；全部 textContent，不用 innerHTML
 *                                  同一个 root 第二次起的重画带换位动效（2026-10-08，见 motion()）
 *   optimistic(data, op)           泳道偏好（nexus-core v2.22）的乐观预演：返回改过的副本（置顶 / 不再显示 / 恢复 / 手动排位），
 *                                  调用方先画它、请求失败就退回原来的 data
 *   identKey(agent, label)         代理身份的键（服务端同一个口径：折叠空白、不分大小写）
 */
(function () {
  'use strict';
  if (window.HoneycombLanes) { return; }

  // 样式与本文件同目录；两个使用方都不用自己记得挂。
  var me = document.currentScript;
  if (me && me.src && !document.querySelector('link[data-hcl-css]')) {
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = me.src.replace(/lanes\.js(\?.*)?$/, 'lanes.css');
    link.setAttribute('data-hcl-css', '');
    document.head.appendChild(link);
  }

  var MIN = 60000, HOUR = 3600000, DAY = 86400000;
  var PHASE_WORD = {
    working: '在干活', waiting_input: '等你回话', waiting_permission: '等你批准',
    idle: '空闲', error: '出错'
  };
  var PHASE_CLASS = {
    working: 'working', waiting_input: 'waiting', waiting_permission: 'waiting',
    idle: 'idle', error: 'error'
  };
  var MODE_WORD = { 'do': '动手', prompt: '写提示', review: '审阅' };

  var ms = function (iso) { var t = Date.parse(iso); return isNaN(t) ? null : t; };
  var pad2 = function (n) { return (n < 10 ? '0' : '') + n; };
  var hm = function (t) { var d = new Date(t); return pad2(d.getHours()) + ':' + pad2(d.getMinutes()); };
  var dur = function (a, b) {
    var m = Math.round((b - a) / MIN);
    if (m < 1) { return '不到 1 分'; }
    return m < 60 ? m + ' 分' : Math.floor(m / 60) + ' 小时' + (m % 60 ? ' ' + (m % 60) + ' 分' : '');
  };
  // 短于一分钟的段说到秒（v2.17：在场段细到几秒一段）
  var hms = function (t) { return hm(t) + ':' + pad2(new Date(t).getSeconds()); };
  var brief = function (a, b) {
    return b - a < MIN ? hms(a) + '–' + hms(b) + '（' + Math.max(1, Math.round((b - a) / 1000)) + ' 秒）'
      : hm(a) + '–' + hm(b) + '（' + dur(a, b) + '）';
  };
  var mins = function (sec) { var m = Math.round(sec / 60); return (m < 1 ? '不到 1' : m) + ' 分'; };
  var el = function (tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined) { n.textContent = text; }
    return n;
  };

  // 'YYYY-MM-DD' 的前一天，按日历算（与时区无关）。
  function prevDay(s) {
    var d = new Date(s + 'T00:00:00Z');
    d.setUTCDate(d.getUTCDate() - 1);
    return d.toISOString().slice(0, 10);
  }

  // 实时窗口：读今天；最近 hours 小时跨过今天零点时读昨天 + 今天（契约）。第一次（prev 为空）不带参 = 今天。
  // 今天零点取 windowEnd − 24h：?from&to 回来的 windowStart 是昨天零点。ponytail: 夏令时那天差一小时，NEXUS_TZ 默认无夏令时。
  function query(prev, hours) {
    if (!prev || !prev.today) { return ''; }
    var todayStart = ms(prev.windowEnd) - DAY, now = ms(prev.now);
    if (todayStart !== null && now !== null && now - hours * HOUR < todayStart) {
      return '?from=' + prevDay(prev.today) + '&to=' + prev.today;
    }
    return '?date=' + prev.today;
  }

  function sortedPhases(run) {
    return (run.phases || []).slice().sort(function (a, b) { return ms(a.at) - ms(b.at); });
  }

  function currentPhase(run) {
    var p = sortedPhases(run);
    var last = p.length ? p[p.length - 1].phase : null;
    return PHASE_WORD[last] ? last : 'working';
  }

  // [{phase, s, e, detail, last}]；在跑的止于 now，at 晚于 now 的按 now 画。
  // sorted：调用方已排好的 phases（runInfo 只排一次），缺省自己排
  function segments(run, nowMs, sorted) {
    var start = ms(run.startAt);
    var end = run.endAt ? ms(run.endAt) : nowMs;
    if (start === null || end === null || end <= start) { return []; }
    var cuts = [{ at: start, phase: 'working', detail: null }];
    (sorted || sortedPhases(run)).forEach(function (p) {
      var at = Math.min(Math.max(ms(p.at), start), end);
      if (isNaN(at) || !PHASE_WORD[p.phase]) { return; }
      cuts.push({ at: at, phase: p.phase, detail: p.detail || null });
    });
    var out = [];
    cuts.forEach(function (c, i) {
      var e = i + 1 < cuts.length ? cuts[i + 1].at : end;
      if (e <= c.at) { return; }                    // 零长的段不画
      var prev = out[out.length - 1];
      if (prev && prev.phase === c.phase) { prev.e = e; return; }   // 读时合并同相位
      out.push({ phase: c.phase, s: c.at, e: e, detail: c.detail });
    });
    if (out.length) { out[out.length - 1].last = true; }
    return out;
  }

  // 最近一次相位转入的时刻（没报过相位 = 开始时刻）
  function lastAt(r) {
    var p = sortedPhases(r);
    return p.length ? ms(p[p.length - 1].at) : ms(r.startAt);
  }

  function isWaiting(ph) { return ph === 'waiting_input' || ph === 'waiting_permission'; }

  // 一个运行一次重画要用的全部：phases 只排一次，段、当前相位、最近转入、视窗内活跃秒数都从它来。
  // act = 在 [v0, v1] 里不空闲（working / waiting_* / error）的秒数——代理卡片的排序依据，只是代理的，不是人的时间。
  function runInfo(r, v0, v1, nowMs) {
    var p = sortedPhases(r), lastP = p.length ? p[p.length - 1] : null;
    var ph = lastP && PHASE_WORD[lastP.phase] ? lastP.phase : 'working';
    // 失联（nexus-core v2.18）：在跑、会发心跳、30 分钟没信号。段止于最后一次信号，不再往「现在」画
    var lost = !r.endAt && !!r.lost;
    var segs = segments(r, lost ? ms(r.lastSeenAt) || nowMs : nowMs, p), sum = 0;
    segs.forEach(function (g) {
      if (g.phase !== 'idle') { sum += Math.max(0, Math.min(g.e, v1) - Math.max(g.s, v0)); }
    });
    return { r: r, ph: ph, lost: lost, segs: segs, act: sum / 1000, attn: attentionSeconds(r, v0, v1), last: (lastP ? ms(lastP.at) : ms(r.startAt)) || 0,
      tier: r.endAt ? 4 : lost ? 3.5 : isWaiting(ph) ? 0 : ph === 'working' ? 1 : ph === 'error' ? 2 : 3 };
  }

  // 人的注意力在这条运行上的秒数（裁到视窗）。服务端给的 attention 已合并、互不重叠，这里只裁、只加。
  // 只是这一条运行自己的；人同一时刻只看一个窗口，所以各条运行的这个数加起来不会超过墙上的时间。
  function attentionSeconds(run, v0, v1) {
    var sum = 0;
    (run.attention || []).forEach(function (a) {
      var s = ms(a.from), e = ms(a.to);
      if (s !== null && e !== null) { sum += Math.max(0, Math.min(e, v1) - Math.max(s, v0)); }
    });
    return sum / 1000;
  }

  function activeSeconds(run, v0, v1, nowMs) { return runInfo(run, v0, v1, nowMs).act; }

  // 计时页卡片的排序（ring 契约 2026-10-08，取代 10-03 的排法）：档位 在跑且在等你（waiting_input / waiting_permission）
  // → 在跑且干活 → 在跑出错 → 在跑空闲 → 已结束；同档按视窗内活跃秒数倒序，同分按最近一次相位转入倒序。
  // 纯函数，不改入参。返回 runInfo 列表（render 复用）。
  // v2.22：服务端排好的 rank（置顶 → 手动排位 → 活跃 → 已结束）是唯一的先后；老后端没有 rank 才在本地按上面的档位排。
  function rankRuns(agents, v0, v1, nowMs) {
    var infos = (agents || []).map(function (r) { return runInfo(r, v0, v1, nowMs); });
    var served = infos.every(function (k) { return typeof k.r.rank === 'number'; });
    return infos.sort(function (a, b) {
      return served ? a.r.rank - b.r.rank : (a.tier - b.tier) || (b.act - a.act) || (b.last - a.last);
    });
  }

  // ── 泳道偏好（v2.22）：身份键与乐观预演。服务端是唯一事实源，这里只是请求在路上时先让界面动起来。
  function identKey(agent, label, unverified) {
    var norm = function (t) { return String(t || '').split(/\s+/).filter(Boolean).join(' ').toLowerCase(); };
    return norm(agent) + '\n' + norm(label) + (unverified ? '\n未验证' : '');
  }
  function optimistic(data, op) {
    var d = JSON.parse(JSON.stringify(data));
    var key = op.agent !== undefined ? identKey(op.agent, op.label, op.unverified) : null;
    var isTarget = function (r) { return identKey(r.agent, r.label, r.unverified) === key; };
    var live = function (r) { return !r.endAt; };
    var byRank = function () { return (d.agents || []).slice().sort(function (a, b) { return (a.rank || 0) - (b.rank || 0); }); };
    var number = function (list) { list.forEach(function (r, i) { r.rank = i; }); };
    d.hiddenAgents = d.hiddenAgents || [];
    if (op.type === 'pin') {
      d.agents.forEach(function (r) { if (isTarget(r)) { r.pinned = op.on; } });
      var list = byRank(), top = list.filter(function (r) { return live(r) && r.pinned; });
      if (op.on) { top = top.filter(function (r) { return !isTarget(r); }).concat(top.filter(isTarget)); }   // 新置顶的排在已有置顶后面
      number(top.concat(list.filter(function (r) { return top.indexOf(r) < 0; })));
    } else if (op.type === 'hide') {
      var gone = d.agents.filter(isTarget), phases = gone.filter(live).map(currentPhase);
      var waiting = phases.filter(isWaiting).length;
      d.agents = d.agents.filter(function (r) { return !isTarget(r); });
      d.interactions = (d.interactions || []).filter(function (i) { return d.agents.some(function (r) { return r.runId === i.runId; }); });
      d.hiddenAgents.push({ agent: op.agent, label: op.label || '', unverified: !!op.unverified, live: phases.length > 0,
        phase: phases.filter(isWaiting)[0] || phases[0] || null });
      d.hiddenWaiting = (d.hiddenWaiting || 0) + waiting;
      number(byRank());
    } else if (op.type === 'restore') {
      d.hiddenAgents = d.hiddenAgents.filter(function (h) {
        var hit = identKey(h.agent, h.label, h.unverified) === key;
        if (hit && isWaiting(h.phase)) { d.hiddenWaiting = Math.max(0, (d.hiddenWaiting || 0) - 1); }
        return !hit;
      });
    } else if (op.type === 'move') {
      var all = byRank(), mov = all.filter(function (r) { return live(r) && !r.pinned; });
      var slots = mov.map(function (r) { return all.indexOf(r); });
      var me = mov.filter(function (r) { return r.runId === op.runId; })[0];
      if (me) {
        mov.splice(mov.indexOf(me), 1);
        mov.splice(Math.min(op.index, mov.length), 0, me);
        slots.forEach(function (pos, i) { all[pos] = mov[i]; });
        number(all);
        me.manualOrder = op.index;
      }
    }
    return d;
  }

  // 计时页的卡片只留「还开着的」和「刚结束的」（ring 契约 2026-10-08）：在跑的都留；已结束的只留 endAt 距
  // now（服务端的 now）不到 ENDED_KEEP_MS 的。与选的窗口（最近 3 小时 / 今天）无关。纯函数，不改入参。
  var ENDED_KEEP_MS = 3 * HOUR;
  var SHOW_HIDDEN_WAITING = true;   // 「已隐藏的有 N 个在等你」那半句（仓主还没定要不要；关掉这一个常量就没了）
  function recentRuns(agents, nowMs) {
    return (agents || []).filter(function (r) { return !r.endAt || nowMs - ms(r.endAt) < ENDED_KEEP_MS; });
  }

  function sortByActivity(agents, v0, v1, nowMs) {
    return rankRuns(agents, v0, v1, nowMs).map(function (k) { return k.r; });
  }

  // 人此刻的状态（ring / nginx-docker 契约 2026-10-03）：看各设备里 to 离 now ≤ 90 秒的在场段——
  // 有一段不是离开 → 在电脑前（detail = 程序 · 标题，服务端已脱敏）；都是离开 → 离开；没有 → 不在线。
  // 在计时（human.running）时 detail 写计时，优先于前台程序。
  var FRESH_MS = 90000, SKEW_MS = 60000;
  var ATTN_LIVE_MS = 20000;   // 「你在看」的一段止于这么近之内 = 此刻还在看（心跳 5 秒一拍、泳道 15 秒一拉）
  var HUMAN_WORD = { present: '在电脑前', away: '离开', offline: '不在线' };
  function humanStatus(human, nowMs) {
    human = human || {};
    var seen = null, away = false;
    (human.presence || []).forEach(function (p) {
      var to = ms(p.to);
      // 新鲜 = to 落在 [now − 90 秒, now + 60 秒]；再往后的（设备时钟快太多）不算
      if (to === null || nowMs - to > FRESH_MS || to - nowMs > SKEW_MS) { return; }
      if (p.afk) { away = true; return; }
      if (!seen || to > ms(seen.to)) { seen = p; }
    });
    var state = seen ? 'present' : away ? 'away' : 'offline';
    var run = human.running && ms(human.running.startAt);
    var doing = seen ? [seen.app, seen.title].filter(function (s) { return s; }).join(' · ') : '';
    // 自动跟踪（nexus-core v2.14）：没在计时、服务端说「我」当前的窗口对上了项目 / 任务。手动计时永远优先。
    // v2.15：source 是 "ai" = 这个目标是 AI 认的（后面标出来，计时页给一个「不对」）；aiThinking = AI 正在认的窗口。
    // v2.16：字出自共享件 focus.js（顶栏芯片、圆环中心、蜂巢同一份）；focus = 此刻的焦点（自动跟踪 / 在某个窗口 / 离开）。
    var F = window.HoneycombFocus, f = (!run && F) ? F.describe(human) : null;
    var a = f && f.auto ? human.auto : null, ai = !!a && a.source === 'ai';
    var th = !run && human.aiThinking;
    return { state: state, word: HUMAN_WORD[state], running: !!run, focus: f,
      detail: run ? '计时中 · ' + hm(run) + ' 起' : doing,
      thinking: th ? [th.app, th.title].filter(function (x) { return x; }).join(' · ') || '窗口' : null,
      auto: !a ? null : { since: f.since, ai: ai, key: a.key, app: a.app, title: a.title,
        text: f.lead + (ai ? '（AI 认的）' : '') } };
  }

  // 从 since 起走秒的钟（「自动 · …」后面那个）。本文件唯一的定时器：一秒一次，只改这些钟的字。
  // 节点上记的是换到本机时钟上的起点（服务端的 now 与本机的差在画的那一刻扣掉），之后每秒只读 Date.now()。
  var ticking = null;
  function clockText(sec) {
    sec = Math.max(0, Math.floor(sec));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60);
    return (h ? h + ':' + pad2(m) : pad2(m)) + ':' + pad2(sec % 60);
  }
  function tick() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-hcl-since]'), function (n) {
      n.textContent = clockText((Date.now() - Number(n.getAttribute('data-hcl-since'))) / 1000);
    });
  }
  function clock(since, nowMs) {
    var n = el('span', 'hcl-clock', clockText((nowMs - since) / 1000));
    n.setAttribute('data-hcl-since', String(since - (nowMs - Date.now())));
    if (!ticking) { ticking = setInterval(tick, 1000); }
    return n;
  }

  // 顶栏预览：只看与 [v0, now] 有重叠的运行；在跑且在等 → 在跑且干活 → 在跑其他 → 已结束（结束晚的在前），
  // 同档按最近一次相位转入倒序。
  function pickPreview(agents, v0, max) {
    var tier = function (r) {
      if (r.endAt) { return 3; }
      if (r.lost) { return 2.5; }                   // 失联：排在在跑的之后、已结束的之前
      var ph = currentPhase(r);
      return ph === 'waiting_input' || ph === 'waiting_permission' ? 0 : ph === 'working' ? 1 : 2;
    };
    var list = (agents || []).filter(function (r) { return !r.endAt || ms(r.endAt) > v0; });
    list.sort(function (a, b) {
      return (tier(a) - tier(b)) ||
        (tier(a) === 3 ? ms(b.endAt) - ms(a.endAt) : 0) ||
        (lastAt(b) - lastAt(a));
    });
    return { shown: list.slice(0, max), more: Math.max(0, list.length - max) };
  }

  function laneName(r) { return r.label || r.agent || '代理'; }

  function tickStep(span) {
    if (span <= 1.5 * HOUR) { return 15 * MIN; }
    if (span <= 4 * HOUR) { return 30 * MIN; }
    if (span <= 12 * HOUR) { return HOUR; }
    return 3 * HOUR;
  }

  /* 换位动效（2026-10-08）。重画照旧整棵换掉，动效只靠「按 runId 记的旧位置」做 FLIP：
   * before = 重画前各行 / 卡的 {rect（看不见 = null）, ph}，重画后一次读完新位置、再一次写完动画（不来回量）。
   *   往上走：略放大（只卡片式）+ 抬起的阴影 + 压在别的卡上面，ease-out，几张一起上时自上而下各晚 50 毫秒（排队）
   *   往下走：只平移，稍慢稍软
   *   新出现：淡入 + 上浮；从折叠区里出来 / 进去（一头看不见）：只淡入
   *   相位变了：is-phase-changed（胶囊与卡边闪一下，样式在 lanes.css）
   * 只动 transform / opacity；prefers-reduced-motion 时不平移不放大，只留淡入。第一次画、页面不可见时不动。 */
  var MOVE_ID = 'hcl-move';
  // 看得见才有位置。收着的 <details> 里的卡在新浏览器里仍有盒子（content-visibility），所以另看一眼祖先
  function seenRect(n) {
    return n.getClientRects().length && !n.closest('details:not([open])') ? n.getBoundingClientRect() : null;
  }
  function snapshot(root) {
    var map = {};
    Array.prototype.forEach.call(root.querySelectorAll('[data-run-id]'), function (n) {
      var id = n.getAttribute('data-run-id');
      if (id) { map[id] = { rect: seenRect(n), ph: n.getAttribute('data-phase') }; }
    });
    return map;
  }
  function motion(root, before, cards) {
    if (!before || !root.animate || document.visibilityState === 'hidden') { return; }
    var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var jobs = [];
    Array.prototype.forEach.call(root.querySelectorAll('[data-run-id]'), function (n) {      // 先全部读
      var old = before[n.getAttribute('data-run-id')];
      var rect = seenRect(n);
      if (old && old.ph !== n.getAttribute('data-phase')) { n.classList.add('is-phase-changed'); }
      if (!rect) { return; }
      if (!old || !old.rect) { jobs.push({ n: n, top: rect.top, fresh: !old }); return; }
      var dx = old.rect.left - rect.left, dy = old.rect.top - rect.top;
      if (Math.abs(dx) > 1 || Math.abs(dy) > 1) { jobs.push({ n: n, top: rect.top, dx: dx, dy: dy }); }
    });
    var fade = { duration: 280, easing: 'ease-out', id: MOVE_ID }, ups = 0;
    jobs.sort(function (a, b) { return a.top - b.top; }).forEach(function (j) {              // 再全部写
      var n = j.n, at = function (k, sc) {
        return 'translate(' + j.dx * k + 'px,' + j.dy * k + 'px) scale(' + sc + ')';
      };
      if (j.dy === undefined) {
        n.animate(j.fresh && !still ? [{ opacity: 0, transform: 'translateY(8px)' }, { opacity: 1, transform: 'none' }]
          : [{ opacity: 0 }, { opacity: 1 }], fade);
      } else if (still) {
        return;                                        // 减少动态效果：直接到位
      } else if (j.dy > 0) {
        var t = { duration: 360, delay: 50 * ups++, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards', id: MOVE_ID };
        n.classList.add('is-rising');
        n.animate([{ transform: at(1, 1) }, { transform: at(.6, cards ? 1.025 : 1), offset: .4 }, { transform: at(0, 1) }], t)
          .onfinish = function () { n.classList.remove('is-rising'); };
        // 抬起的阴影画在 ::before 上、只动它的 opacity（不动 box-shadow 本身）
        if (cards) { try { n.animate([{ opacity: 0 }, { opacity: 1, offset: .4 }, { opacity: 0 }], { duration: t.duration, delay: t.delay, easing: 'ease-out', pseudoElement: '::before' }); } catch (e) { /* 老浏览器没有 pseudoElement：不要阴影 */ } }
      } else {
        n.animate([{ transform: at(1, 1) }, { transform: at(0, 1) }],
          { duration: 440, easing: 'cubic-bezier(.4,0,.2,1)', id: MOVE_ID });
      }
    });
  }

  /* 卡片上的偏好交互（nexus-core v2.22，只在 opts.prefs 给了时才有；lanes.js 仍然不发请求，动作交给调用方）：
   *   ⋯ 菜单：置顶 / 取消置顶、不再显示、上移 / 下移（键盘能用的手动排位）；方向键 / Home / End 走菜单项，Esc 关并把焦点还给 ⋯
   *   长按（LONG_PRESS_MS，鼠标与触摸）：在跑且没置顶的卡拖着换位，松手按落点给 onMove(run, 在「未置顶的在跑运行」里的下标)；
   *          Esc / 指针取消 = 放弃。普通点击不受影响（没到时间就松手 = 什么都没发生；拖过之后吞掉紧跟着的那一下 click）
   * 菜单开着 / 正在拖时 root.hclBusy 为真：轮询的重画先攒着（调用方看这个标志），收尾时在 root 上发 hcl-idle。 */
  var LONG_PRESS_MS = 400, PRESS_SLOP = 8;
  var gesture = null;     // 进行中的长按 / 拖动；同一时刻只有一个
  var menu = null;        // 开着的菜单 {close()}

  function setBusy(root, on, quiet) {
    if (!!root.hclBusy === on) { return; }
    root.hclBusy = on;
    if (!on && !quiet) { root.dispatchEvent(new Event('hcl-idle')); }
  }

  // quiet：重画开头顺手收菜单，不再喊 hcl-idle（调用方正在画）
  function closeMenu(refocus, quiet) {
    if (menu) { var m = menu; menu = null; m.close(refocus, quiet); }
  }

  function openMenu(root, card, kebab, entries) {
    closeMenu(false);
    var list = el('ul', 'hcl-menu');
    list.setAttribute('role', 'menu');
    var buttons = entries.map(function (e) {
      var li = el('li'), b = el('button', 'hcl-menu-item', e[0]);
      b.type = 'button';
      b.setAttribute('role', 'menuitem');
      b.tabIndex = -1;
      b.addEventListener('click', function (ev) { ev.stopPropagation(); closeMenu(true); e[1](); });
      li.appendChild(b);
      list.appendChild(li);
      return b;
    });
    card.appendChild(list);
    kebab.setAttribute('aria-expanded', 'true');
    setBusy(root, true);
    var onKey = function (ev) {
      var i = buttons.indexOf(document.activeElement), n = buttons.length;
      if (ev.key === 'Escape') { ev.preventDefault(); closeMenu(true); }
      else if (ev.key === 'Tab') { closeMenu(false); }
      else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp' || ev.key === 'Home' || ev.key === 'End') {
        ev.preventDefault();
        buttons[ev.key === 'Home' ? 0 : ev.key === 'End' ? n - 1 : (i + (ev.key === 'ArrowDown' ? 1 : n - 1)) % n].focus();
      }
    };
    var onDown = function (ev) { if (!list.contains(ev.target) && ev.target !== kebab) { closeMenu(false); } };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onDown, true);
    menu = {
      owner: kebab,
      close: function (refocus, quiet) {
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('pointerdown', onDown, true);
        kebab.setAttribute('aria-expanded', 'false');
        if (list.parentNode) { list.parentNode.removeChild(list); }
        if (refocus && kebab.isConnected) { kebab.focus(); }
        setBusy(root, false, quiet);
      }
    };
    buttons[0].focus();
  }

  // 一张卡的 ⋯ 按钮与菜单。pos / total：这张卡在「未置顶的在跑运行」里的位置（只有它们能手动排位；pos < 0 = 不能）
  function addKebab(root, P, card, head, r, pos, total) {
    var kebab = el('button', 'hcl-kebab', '⋯');
    kebab.type = 'button';
    kebab.setAttribute('aria-haspopup', 'menu');
    kebab.setAttribute('aria-expanded', 'false');
    kebab.setAttribute('aria-label', '更多操作：' + laneName(r));
    kebab.title = '更多操作';
    kebab.addEventListener('click', function (ev) {
      ev.stopPropagation();
      if (kebab.getAttribute('aria-expanded') === 'true') { closeMenu(true); return; }
      // 未验证（匿名）的运行不能置顶、不能手动排位（服务端也拒），只能「不再显示」
      var entries = (r.unverified ? [] : [[r.pinned ? '取消置顶' : '置顶', function () { P.onPin(r, !r.pinned); }]])
        .concat([['不再显示', function () { P.onHide(r); }]]);
      if (pos > 0) { entries.push(['上移', function () { P.onMove(r, pos - 1); }]); }
      if (pos >= 0 && pos < total - 1) { entries.push(['下移', function () { P.onMove(r, pos + 1); }]); }
      openMenu(root, card, kebab, entries);
    });
    head.appendChild(kebab);
  }

  // 长按拖动。movable = [{node, r}]（在跑且没置顶的卡，按显示顺序）；只有看得见的卡参与量位置。
  function addDrag(root, P, card, r, movable) {
    card.classList.add('is-movable');
    card.addEventListener('pointerdown', function (ev) {
      if ((ev.pointerType === 'mouse' && ev.button !== 0) || gesture || ev.target.closest('button, a, summary, input, select, .hcl-menu')) { return; }
      closeMenu(false);
      var g = gesture = { x: ev.clientX, y: ev.clientY, active: false };
      var visible = movable.filter(function (m) { return m.node && seenRect(m.node); });
      var from = visible.map(function (m) { return m.r; }).indexOf(r);

      function shift(dy) {                                  // 被拖的卡跟手，别的卡让位
        var mid = g.mid + dy, to = 0;
        visible.forEach(function (m, i) { if (i !== from && g.mids[i] < mid) { to += 1; } });
        g.to = to;
        visible.forEach(function (m, i) {
          if (i === from) { m.node.style.transform = 'translateY(' + dy + 'px)'; return; }
          var by = (from < to && i > from && i <= to) ? -g.step : (to < from && i >= to && i < from) ? g.step : 0;
          m.node.style.transform = by ? 'translateY(' + by + 'px)' : '';
        });
      }
      function clear() { visible.forEach(function (m) { m.node.style.transform = ''; m.node.classList.remove('is-dragging', 'is-yielding'); }); }
      function finish(drop) {
        clearTimeout(g.timer);
        document.removeEventListener('pointermove', onMove, true);
        document.removeEventListener('pointerup', onUp, true);
        document.removeEventListener('pointercancel', onCancel, true);
        document.removeEventListener('keydown', onKey, true);
        document.removeEventListener('contextmenu', stop, true);
        document.removeEventListener('touchmove', stopScroll, true);
        gesture = null;
        if (g.active) {
          clear();
          document.body.classList.remove('hcl-dragging');
          var swallow = function (e2) { e2.stopPropagation(); e2.preventDefault(); };
          document.addEventListener('click', swallow, true);          // 拖完松手会补一个 click：吞掉
          setTimeout(function () { document.removeEventListener('click', swallow, true); }, 0);
          setBusy(root, false);
          if (drop && g.to !== from) { P.onMove(r, g.to); }
        }
      }
      function onMove(e2) {
        if (!g.active) {
          if (Math.abs(e2.clientX - g.x) > PRESS_SLOP || Math.abs(e2.clientY - g.y) > PRESS_SLOP) { finish(false); }   // 是在滚动 / 选字
          return;
        }
        e2.preventDefault();
        shift(e2.clientY - g.y);
      }
      function onUp() { finish(true); }
      function onCancel() { finish(false); }
      function onKey(e2) { if (e2.key === 'Escape') { e2.preventDefault(); finish(false); } }
      function stop(e2) { e2.preventDefault(); }                      // 触摸长按会弹系统菜单
      function stopScroll(e2) { if (g.active && e2.cancelable) { e2.preventDefault(); } }
      document.addEventListener('pointermove', onMove, true);
      document.addEventListener('pointerup', onUp, true);
      document.addEventListener('pointercancel', onCancel, true);
      document.addEventListener('keydown', onKey, true);
      document.addEventListener('contextmenu', stop, true);
      document.addEventListener('touchmove', stopScroll, { capture: true, passive: false });
      g.timer = setTimeout(function () {
        if (from < 0) { finish(false); return; }
        g.active = true;
        setBusy(root, true);
        var rects = visible.map(function (m) { return m.node.getBoundingClientRect(); });
        g.mids = rects.map(function (b) { return b.top + b.height / 2; });
        g.mid = g.mids[from];
        g.step = rects[from].height + (rects.length > 1 ? Math.max(0, (rects[1].top - rects[0].bottom)) : 0);
        g.to = from;
        visible.forEach(function (m, i) { m.node.classList.add(i === from ? 'is-dragging' : 'is-yielding'); });
        document.body.classList.add('hcl-dragging');
      }, LONG_PRESS_MS);
    });
  }

  /* render(root, data, opts)
   *   opts.viewStart / viewEnd  ms，画的时间范围（段裁到这里）
   *   opts.agents               要画的运行（缺省 data.agents 里与视窗有重叠的）
   *   opts.presence             画人的在场带（计时页与顶栏预览都画，2026-10-03 起）
   *   opts.compact              顶栏预览的紧凑尺寸
   *   opts.cards / top          计时页的卡片布局（只画 recentRuns：结束超过 3 小时的不画）：人一张卡钉在最前，代理按 sortByActivity 排，
   *                             前 top 张（缺省 5；在等你 / 干活的卡永不折叠，多于 top 就全展开）展开，其余收进 <details>「还有 N 个」
   *   opts.lead                 卡片式：调用方的一张置顶卡（计时页的「你在 X，记到哪？」），放在人那张卡之前。同一个节点
   *                             跨重画搬过来（表单状态不丢、焦点还回去）；换位动效把它当一张卡，第一次出现淡入上浮
   *   opts.onAutoWrong(auto)    卡片式：人那张卡上「自动 · …（AI 认的）」后面给一个「不对」按钮，点了调它（auto 带 key / app / title）。
   *                             不给就没有按钮（顶栏预览不给）
   *   opts.prefs                卡片式：泳道偏好动作 {onPin(run, on), onHide(run), onMove(run, index), onRestore(hidden)}（nexus-core v2.22）。
   *                             给了：每张代理卡有 ⋯ 菜单与置顶标记、在跑的卡能长按拖动，区尾多一行可折叠的「已隐藏 (N)」
   *                             （data.hiddenAgents；N 个在等你的提示来自 data.hiddenWaiting）。不给（顶栏预览）就一概没有
   *   opts.more / moreHref      区尾一行（「还有更多」/「还有 N 个 → 计时页」）
   *   opts.focusFallback        焦点在区尾链接上、重画后链接没了时，焦点交给它
   * 返回画了的代理运行的 runInfo 列表（按画的顺序；r / ph / act / last …），调用方拿来数状态，不必再排一遍 phases。
   */
  function render(root, data, opts) {
    var v0 = opts.viewStart, v1 = opts.viewEnd, span = v1 - v0;
    var now = ms(data.now) || Date.now();
    var cards = !!opts.cards, P = cards ? opts.prefs : null;
    var pct = function (t) { return ((Math.min(Math.max(t, v0), v1) - v0) / span * 100) + '%'; };
    var place = function (node, s, e) {
      node.style.left = pct(s);
      node.style.width = 'calc(' + pct(e) + ' - ' + pct(s) + ')';
    };
    var inView = function (s, e) { return e > v0 && s < v1; };

    // 轮询重画会换掉整棵树：焦点正在区尾链接 / 折叠开关上就还给新的那个（键盘用户不该每 15 秒丢一次焦点）；
    // 折叠区开着就还开着
    var a = document.activeElement;
    var refocus = a && root.contains(a) && a.classList.contains('hcl-more');
    // 开合记在 root 上（同一个 root 跨重画）：折叠区这次没了、下次又出现时照旧开着
    if (menu && root.contains(menu.owner)) { closeMenu(false, true); }
    var oldHidden = root.querySelector('details.hcl-hidden');
    if (oldHidden) { root.hclHiddenOpen = oldHidden.open; }
    var hiddenFocus = !!(oldHidden && a && oldHidden.contains(a));
    var oldFold = root.querySelector('details.hcl-fold');
    if (oldFold) { root.hclFoldOpen = oldFold.open; }
    var foldFocus = !!(oldFold && a && oldFold.contains(a) && a.tagName === 'SUMMARY');
    var lead = cards ? opts.lead : null, leadFocus = lead && a && lead.contains(a) ? a : null;
    // 换位动效要的旧位置（第一次画没有 → 不动）；同一个 root 换了布局（卡片 ↔ 列表）也当第一次
    var before = root.hclDrawn === cards ? snapshot(root) : null;
    root.hclDrawn = cards;
    root.textContent = '';
    root.className = 'hcl' + (opts.compact ? ' hcl-compact' : '') + (cards ? ' hcl-cards' : '');

    var summary = el('div', 'hcl-sr');
    summary.setAttribute('data-hcl-summary', '');
    var sayLines = [];
    var human = data.human || {};
    var st = humanStatus(human, now);

    // 列表式（顶栏预览）：人此刻的状态写一行字在最上（卡片式写在人那张卡的卡头）
    if (!cards) {
      var stLine = el('p', 'hcl-status hcl-st-' + st.state, '我：' + st.word + (st.detail ? ' · ' + st.detail : ''));
      stLine.title = stLine.textContent;
      if (st.auto) {                                   // 自动跟踪：换成「我：自动 · 项目 / 任务」+ 走秒的钟
        stLine.textContent = '我：';
        stLine.appendChild(el('span', 'hcl-auto-text', st.auto.text));
        stLine.appendChild(clock(st.auto.since, now));
        stLine.classList.add('hcl-st-auto');
        stLine.title = '我：' + st.auto.text + (st.detail ? '（' + st.detail + '）' : '');
      }
      root.appendChild(stLine);
    }

    // 时间轴（卡片式也只有一条，各卡的轨道与它左右对齐）
    var axisRow = el('div', 'hcl-axis-row');
    axisRow.appendChild(el('div', 'hcl-label-spacer'));
    var axis = el('div', 'hcl-axis');
    axis.setAttribute('aria-hidden', 'true');
    var step = tickStep(span);
    // 刻度对齐本地整点 / 整刻（getTimezoneOffset 是「UTC − 本地」的分钟数）
    var tz = new Date(v0).getTimezoneOffset() * MIN;
    var first = Math.ceil((v0 - tz) / step) * step + tz;
    for (var t = first, k = 0; t <= v1; t += step, k++) {
      var tick = el('span', 'hcl-tick' + (k % 2 ? ' hcl-tick-odd' : ''));
      tick.style.left = pct(t);
      tick.appendChild(el('span', 'hcl-tick-label', hm(t)));
      axis.appendChild(tick);
    }
    axisRow.appendChild(axis);
    root.appendChild(axisRow);

    var rows = el('div', cards ? 'hcl-deck' : 'hcl-rows');
    var overlay = el('div', 'hcl-overlay');
    overlay.setAttribute('aria-hidden', 'true');

    // 一条线：列表式是「标签列 + 轨道」一行，卡片式是「卡头 + 轨道」一张卡。返回轨道；卡头 / 标签是 track.previousSibling
    var addRow = function (parent, cls, name, sub, dotClass) {
      var row = el('div', (cards ? 'hcl-card ' : 'hcl-row ') + cls);
      var label = el('div', cards ? 'hcl-card-head' : 'hcl-label');
      var nameEl = el('span', 'hcl-name');
      if (dotClass) { nameEl.appendChild(el('span', 'hcl-dot hcl-ph-' + dotClass)); }
      nameEl.appendChild(document.createTextNode(name));
      label.appendChild(nameEl);
      if (sub) { label.appendChild(el('span', 'hcl-sub', sub)); }
      label.title = name + (sub ? '（' + sub + '）' : '');
      var track = el('div', 'hcl-track');
      row.appendChild(label);
      row.appendChild(track);
      parent.appendChild(row);
      return track;
    };
    var pill = function (head, cls, text) {
      var p = el('span', 'hcl-pill ' + cls);
      p.appendChild(el('span', 'hcl-dot ' + cls));
      p.appendChild(document.createTextNode(text));
      head.appendChild(p);
      return p;
    };
    var seg = function (track, cls, s, e, tip) {
      var n = el('span', 'hcl-seg ' + cls);
      place(n, s, e);
      n.setAttribute('data-tip', tip);
      track.appendChild(n);
      return n;
    };
    // 不是今天的时刻带上日期（超时挂着的运行，最近一次转入可能在昨晚）
    var when = function (t) {
      var d = new Date(t);
      return (d.toDateString() === new Date(now).toDateString() ? '' : (d.getMonth() + 1) + '/' + d.getDate() + ' ') + hm(t);
    };
    var nowMark = function (track) {
      if (cards && now >= v0 && now <= v1) {
        var n = el('span', 'hcl-now');
        n.style.left = pct(now);
        n.setAttribute('aria-hidden', 'true');
        track.appendChild(n);
      }
    };

    if (lead) {
      lead.classList.add('hcl-card', 'hcl-lead');
      lead.setAttribute('data-run-id', 'lead');
      rows.appendChild(lead);
    }
    // 人那条线（卡片式：钉在最前的一张卡，不算进 top）
    var running = human.running && ms(human.running.startAt);
    var hTrack = cards ? addRow(rows, 'hcl-row-human', '我', '', null)
      : addRow(rows, 'hcl-row-human', '我', running ? '计时中' : st.word, running ? 'human' : null);
    hTrack.classList.add('hcl-track-human');
    if (cards) {
      var hHead = hTrack.previousSibling;
      pill(hHead, 'hcl-st-' + st.state, st.word);
      if (running) { pill(hHead, 'hcl-ph-human', '计时中'); }
      if (st.auto) {                                   // 虚线边 + 走秒的钟：一眼看得出不是手动计时
        var ap = pill(hHead, 'hcl-st-auto', '');
        ap.appendChild(el('span', 'hcl-auto-text', st.auto.text));
        ap.appendChild(clock(st.auto.since, now));
        ap.title = st.auto.text + ' —— 按分类规则自动跟着你当前的窗口，不是手动计时';
        if (st.auto.ai && opts.onAutoWrong) {          // AI 认的：一键「不对」（撤掉 AI 的规则，换人来选）
          var wrong = el('button', 'hcl-auto-wrong', '不对');
          wrong.type = 'button';
          wrong.title = '这个窗口不是 AI 认的那个项目 / 任务：撤掉 AI 写的规则，我自己选';
          wrong.addEventListener('click', function () { wrong.disabled = true; opts.onAutoWrong(st.auto); });
          hHead.appendChild(wrong);
        }
      }
      if (st.thinking) {                               // AI 正在认某个窗口：小字 + 轻微的呼吸，不另起一张卡
        var thinking = el('span', 'hcl-ai-thinking', 'AI 正在认这个窗口…');
        thinking.title = 'AI 正在认：' + st.thinking + '（认不出会请你选）';
        hHead.appendChild(thinking);
      }
      if (st.detail) {
        // 认得出项目 / 任务（且不是自动跟踪——那已经有胶囊了）：「正在：项目 / 任务 · 窗口」，否则照旧「正在用 窗口」
        var known = st.focus && !st.focus.auto && st.focus.target;
        var hStat = el('span', 'hcl-stat', st.running ? st.detail : (known ? st.focus.lead + ' · ' : '正在用 ') + st.detail);
        hStat.title = hStat.textContent;
        hHead.appendChild(hStat);
      }
    }
    (human.sessions || []).forEach(function (x) {
      var s = ms(x.startAt), e = ms(x.endAt);
      if (s === null || e === null || !inView(s, e)) { return; }
      var mode = MODE_WORD[x.mode] ? x.mode : 'do';
      seg(hTrack, 'hcl-human hcl-mode-' + mode, s, e,
        '计时 · ' + MODE_WORD[mode] + ' · ' + hm(s) + '–' + hm(e) + '（' + dur(s, e) + '）');
    });
    if (running && inView(running, now)) {
      seg(hTrack, 'hcl-human hcl-mode-do is-running', running, now,
        '计时中 · ' + hm(running) + '–现在（' + dur(running, now) + '）');
    }
    sayLines.push('我：' + st.word + '，' + (running ? '计时中' : st.auto ? st.auto.text : '没在计时') +
      (st.thinking ? '，AI 正在认窗口 ' + st.thinking : ''));
    if (opts.presence) {
      (human.presence || []).forEach(function (p) {
        var s = ms(p.from), e = ms(p.to);
        if (s === null || e === null || !inView(s, e)) { return; }
        // v2.17：段细到几秒一段、互不重叠，照实画（不再把短段撑到 15 秒）；人在看某条代理的那几段染成注意力的蓝
        // ponytail: 一段一个节点（每设备至多 2000 段）；真嫌多再把落在同一个像素里的相邻段并着画
        var n = seg(hTrack, 'hcl-presence' + (p.afk ? ' is-afk' : '') + (p.runId ? ' is-on-agent' : ''), s, e,
          (p.afk ? '离开' : (p.app || '前台')) + (p.title ? ' · ' + p.title : '') + ' · ' + brief(s, e));
        n.style.minWidth = '2px';
      });
    }
    nowMark(hTrack);

    // 代理线
    var agents = opts.agents || (data.agents || []).filter(function (r) {
      return inView(ms(r.startAt), r.endAt ? ms(r.endAt) : now);
    });
    if (cards) { agents = recentRuns(agents, now); }
    var vEnd = Math.min(v1, now);
    var infos = cards ? rankRuns(agents, v0, vEnd, now)
      : agents.map(function (r) { return runInfo(r, v0, vEnd, now); });
    // ponytail: 折叠区里的卡也整张建好（只是收着）；几十个运行无所谓，真到读端上限 200 个嫌慢再改成展开时才建
    agents = infos.map(function (k) { return k.r; });
    var top = cards ? (opts.top || 5) : agents.length;
    // 在跑且在等你 / 在干活的（档 0–1）不许被折叠：多于 top 个就全展开，折叠从它们之后才开始
    if (cards) { infos.forEach(function (k, i) { if (k.tier <= 1 && i + 1 > top) { top = i + 1; } }); }
    var fold = null, foldList = null;
    if (agents.length > top) {
      fold = el('details', 'hcl-fold');
      fold.open = !!root.hclFoldOpen;
      fold.appendChild(el('summary', 'hcl-fold-toggle', '还有 ' + (agents.length - top) + ' 个'));
      foldList = el('div', 'hcl-deck');
      fold.appendChild(foldList);
    }
    var rowOf = {};
    // 能手动排位的卡：在跑且没置顶的（按显示顺序）；node 在建卡时补上，addDrag 要用到全体
    var movable = [];
    infos.forEach(function (k) { if (P && !k.r.endAt && !k.r.pinned && !k.r.unverified) { movable.push({ r: k.r, node: null }); } });
    infos.forEach(function (info, i) {
      var r = info.r, live = !r.endAt && !info.lost;   // 失联的不按「在跑」画：灰、不闪、不算在等你
      var ph = info.ph;
      var word = info.lost ? '失联' : live ? PHASE_WORD[ph] : r.outcome === 'lost' ? '失联结束' : '已结束';
      var sub = r.agent && r.label ? r.agent : (r.tool || '');
      if (r.overdue) { sub = '超时未收'; }
      var track = addRow(i < top ? rows : foldList, 'hcl-row-agent', laneName(r), sub,
        live && !cards ? PHASE_CLASS[ph] : null);
      var card = track.parentNode;
      track.classList.add('hcl-track-agent');
      card.setAttribute('data-run-id', r.runId || '');
      card.setAttribute('data-phase', live ? ph : info.lost ? 'lost' : 'ended');
      rowOf[r.runId] = cards ? track : i + 1;
      if (cards) {
        var head = track.previousSibling;
        pill(head, live ? 'hcl-ph-' + PHASE_CLASS[ph] : 'is-ended', word);
        if (r.pinned) {
          var pinMark = el('span', 'hcl-pinmark', '置顶');
          pinMark.title = '已置顶';
          head.appendChild(pinMark);
        }
        if (live && isWaiting(ph)) { card.classList.add('is-needs-you'); }
        var act = Math.round(info.act / 60);
        head.appendChild(el('span', 'hcl-stat', '活跃 ' + (act < 1 ? '不到 1' : act) + ' 分 · ' +
          (info.attn > 0 ? '看了 ' + mins(info.attn) + ' · ' : '') +
          (live ? '最近 ' + when(info.last) : info.lost ? '最后信号 ' + when(ms(r.lastSeenAt))
            : when(ms(r.endAt)) + ' 结束')));
        if (P) {
          var pos = movable.map(function (m) { return m.r; }).indexOf(r);
          if (pos >= 0) { movable[pos].node = card; }
          addKebab(root, P, card, head, r, pos, movable.length);
          if (pos >= 0) { addDrag(root, P, card, r, movable); }
        }
      }
      info.segs.forEach(function (g) {
        if (!inView(g.s, g.e)) { return; }
        var isLive = live && g.last;
        var cls = 'hcl-ph-' + PHASE_CLASS[g.phase] + (isLive ? ' is-live' : '') +
          (isLive && r.overdue ? ' is-overdue' : '');
        seg(track, cls, g.s, g.e, laneName(r) + ' · ' + PHASE_WORD[g.phase] +
          (g.detail ? '（' + g.detail + '）' : '') + ' · ' + hm(g.s) + '–' + (isLive ? '现在' : hm(g.e)) +
          '（' + dur(g.s, g.e) + '）');
      });
      // 你在看（v2.17）：相位条正下方自己的一小行，蓝条 = 人的注意力在这条运行上的时间。正在变长的那一段右端有一道活边
      (r.attention || []).forEach(function (a) {
        var s = ms(a.from), e = ms(a.to);
        if (s === null || e === null || !inView(s, Math.max(e, s + 1))) { return; }
        seg(track, 'hcl-attn' + (live && now - e <= ATTN_LIVE_MS ? ' is-live' : ''), s, e,
          laneName(r) + ' · 你在看 · ' + brief(s, e));
      });
      nowMark(track);
      sayLines.push(laneName(r) + '：' + word + (r.overdue ? '（超时未收）' : '') +
        (info.attn > 0 ? '，你看了 ' + mins(info.attn) : ''));
    });

    // 连线：reply 实线竖线。列表式从人那条线落到该代理线；卡片式各卡分开放，画在该代理自己的轨道上
    // （竖线 = 人在这一刻回了它的话）。「人在看它」不再是这里的半透明带子（interactions 的 attend）：
    // v2.17 起画成上面每条线里的蓝条（agents[].attention）。
    (data.interactions || []).forEach(function (x) {
      var r = rowOf[x.runId], at = ms(x.at);
      if (!r || at === null || x.kind !== 'reply' || at < v0 || at > v1) { return; }
      var line = el('span', 'hcl-reply');
      line.style.left = pct(at);
      if (cards) { line.setAttribute('data-tip', '我回了话 · ' + hm(at)); }
      else { line.style.setProperty('--hcl-r', r); }
      (cards ? r : overlay).appendChild(line);
    });
    if (!cards) {
      if (now >= v0 && now <= v1) {
        var nowLine = el('span', 'hcl-now');
        nowLine.style.left = pct(now);
        overlay.appendChild(nowLine);
      }
      rows.appendChild(overlay);
    }
    root.appendChild(rows);
    if (leadFocus) { leadFocus.focus(); }
    if (fold) {
      root.appendChild(fold);
      if (foldFocus) { fold.firstChild.focus(); }
    } else if (foldFocus && opts.focusFallback) {
      opts.focusFallback.focus();                      // 「还有 N 个」没了（≤ top 张）：焦点别掉到 body 上
    }

    if (P && (data.hiddenAgents || []).length) {
      // 已隐藏的代理（v2.22）：默认收着，展开后逐个「恢复显示」。SHOW_HIDDEN_WAITING 关掉就没有「N 个在等你」那半句
      var hd = el('details', 'hcl-hidden'), waitingN = SHOW_HIDDEN_WAITING ? data.hiddenWaiting || 0 : 0;
      hd.open = !!root.hclHiddenOpen;
      var hsum = el('summary', 'hcl-hidden-toggle', '已隐藏 (' + data.hiddenAgents.length + ')' +
        (waitingN ? ' · ' + waitingN + ' 个在等你' : ''));
      hd.appendChild(hsum);
      var hlist = el('ul', 'hcl-hidden-list');
      data.hiddenAgents.forEach(function (h) {
        var li = el('li', 'hcl-hidden-item');
        li.appendChild(el('span', 'hcl-hidden-name', h.label || h.agent));
        if (h.label) { li.appendChild(el('span', 'hcl-sub', h.agent)); }
        li.appendChild(el('span', 'hcl-hidden-state', !h.live ? '没在跑' : isWaiting(h.phase) ? '在等你' : '在跑'));
        var back = el('button', 'hcl-hidden-restore', '恢复显示');
        back.type = 'button';
        back.setAttribute('aria-label', '恢复显示：' + (h.label || h.agent));
        back.addEventListener('click', function () { P.onRestore(h); });
        li.appendChild(back);
        hlist.appendChild(li);
      });
      hd.appendChild(hlist);
      root.appendChild(hd);
      if (hiddenFocus) { hsum.focus(); }
    } else if (hiddenFocus && opts.focusFallback) {
      opts.focusFallback.focus();
    }

    if (!agents.length) { root.appendChild(el('p', 'hcl-empty', '这段时间没有代理在跑。')); }
    if (refocus && !(opts.more && opts.moreHref) && opts.focusFallback) { opts.focusFallback.focus(); }
    if (opts.more) {
      var more = el(opts.moreHref ? 'a' : 'p', 'hcl-more', opts.more);
      if (opts.moreHref) { more.href = opts.moreHref; }
      root.appendChild(more);
      if (refocus && opts.moreHref) { more.focus(); }
    }

    // 图例
    var legend = el('div', 'hcl-legend');
    legend.setAttribute('aria-hidden', 'true');
    var keys = [['hcl-human hcl-mode-do', '我在计时']];
    if (opts.presence) { keys.push(['hcl-presence', '在电脑前'], ['hcl-presence is-afk', '离开']); }
    keys.concat([['hcl-ph-working', '在干活'], ['hcl-ph-waiting', '在等你'],
     ['hcl-ph-idle', '空闲'], ['hcl-ph-error', '出错'], ['hcl-key-reply', '回话'], ['hcl-attn', '你在看']
    ]).forEach(function (p) {
      var item = el('span', 'hcl-key');
      item.appendChild(el('span', 'hcl-swatch ' + p[0]));
      item.appendChild(document.createTextNode(p[1]));
      legend.appendChild(item);
    });
    root.appendChild(legend);

    summary.textContent = sayLines.join('；');
    root.insertBefore(summary, root.firstChild);

    // 悬停提示：一个浮层，按鼠标所在的段填字
    var tip = el('div', 'hcl-tip');
    tip.setAttribute('role', 'tooltip');
    root.appendChild(tip);
    var hideTip = function () { tip.classList.remove('is-on'); };
    root.onmouseover = function (e) {
      var target = e.target.closest ? e.target.closest('[data-tip]') : null;
      if (!target || !root.contains(target)) { hideTip(); return; }
      tip.textContent = target.getAttribute('data-tip');
      tip.classList.add('is-on');
      var box = root.getBoundingClientRect(), r = target.getBoundingClientRect();
      var x = Math.min(Math.max(e.clientX - box.left - tip.offsetWidth / 2, 0),
        Math.max(0, box.width - tip.offsetWidth));
      tip.style.left = x + 'px';
      tip.style.top = (r.bottom - box.top + 4) + 'px';
    };
    root.onmouseleave = hideTip;
    motion(root, before, cards);
    return infos;
  }

  window.HoneycombLanes = {
    query: query, prevDay: prevDay, segments: segments, currentPhase: currentPhase,
    pickPreview: pickPreview, activeSeconds: activeSeconds, attentionSeconds: attentionSeconds, sortByActivity: sortByActivity, recentRuns: recentRuns,
    humanStatus: humanStatus, render: render, PHASE_WORD: PHASE_WORD,
    optimistic: optimistic, identKey: identKey
  };
})();
