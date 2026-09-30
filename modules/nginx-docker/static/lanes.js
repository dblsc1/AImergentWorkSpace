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
 *   render(root, data, opts)       画一张图；全部 textContent，不用 innerHTML
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
  function segments(run, nowMs) {
    var start = ms(run.startAt);
    var end = run.endAt ? ms(run.endAt) : nowMs;
    if (start === null || end === null || end <= start) { return []; }
    var cuts = [{ at: start, phase: 'working', detail: null }];
    sortedPhases(run).forEach(function (p) {
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

  // 顶栏预览：只看与 [v0, now] 有重叠的运行；在跑且在等 → 在跑且干活 → 在跑其他 → 已结束（结束晚的在前），
  // 同档按最近一次相位转入倒序。
  function pickPreview(agents, v0, max) {
    var lastAt = function (r) {
      var p = sortedPhases(r);
      return p.length ? ms(p[p.length - 1].at) : ms(r.startAt);
    };
    var tier = function (r) {
      if (r.endAt) { return 3; }
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

  /* render(root, data, opts)
   *   opts.viewStart / viewEnd  ms，画的时间范围（段裁到这里）
   *   opts.agents               要画的运行（缺省 data.agents 里与视窗有重叠的）
   *   opts.presence             画人的在场细带（顶栏预览不画）
   *   opts.compact              顶栏预览的紧凑尺寸
   *   opts.more / moreHref      区尾一行（「还有更多」/「还有 N 个 → 计时页」）
 *   opts.focusFallback        焦点在区尾链接上、重画后链接没了时，焦点交给它
   */
  function render(root, data, opts) {
    var v0 = opts.viewStart, v1 = opts.viewEnd, span = v1 - v0;
    var now = ms(data.now) || Date.now();
    var pct = function (t) { return ((Math.min(Math.max(t, v0), v1) - v0) / span * 100) + '%'; };
    var place = function (node, s, e) {
      node.style.left = pct(s);
      node.style.width = 'calc(' + pct(e) + ' - ' + pct(s) + ')';
    };
    var inView = function (s, e) { return e > v0 && s < v1; };

    // 轮询重画会换掉整棵树：焦点正在区尾链接上就还给新的那个（键盘用户不该每 15 秒丢一次焦点）
    var a = document.activeElement;
    var refocus = a && root.contains(a) && a.classList.contains('hcl-more');
    root.textContent = '';
    root.className = 'hcl' + (opts.compact ? ' hcl-compact' : '');

    var summary = el('div', 'hcl-sr');
    summary.setAttribute('data-hcl-summary', '');
    var sayLines = [];

    // 时间轴
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

    var rows = el('div', 'hcl-rows');
    var overlay = el('div', 'hcl-overlay');
    overlay.setAttribute('aria-hidden', 'true');

    var addRow = function (cls, name, sub, dotClass) {
      var row = el('div', 'hcl-row ' + cls);
      var label = el('div', 'hcl-label');
      var nameEl = el('span', 'hcl-name');
      if (dotClass) { nameEl.appendChild(el('span', 'hcl-dot hcl-ph-' + dotClass)); }
      nameEl.appendChild(document.createTextNode(name));
      label.appendChild(nameEl);
      if (sub) { label.appendChild(el('span', 'hcl-sub', sub)); }
      label.title = name + (sub ? '（' + sub + '）' : '');
      var track = el('div', 'hcl-track');
      row.appendChild(label);
      row.appendChild(track);
      rows.appendChild(row);
      return track;
    };
    var seg = function (track, cls, s, e, tip) {
      var n = el('span', 'hcl-seg ' + cls);
      place(n, s, e);
      n.setAttribute('data-tip', tip);
      track.appendChild(n);
      return n;
    };

    // 人那条线
    var human = data.human || {};
    var running = human.running && ms(human.running.startAt);
    var hTrack = addRow('hcl-row-human', '我', running ? '计时中' : '人', running ? 'human' : null);
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
    sayLines.push('我：' + (running ? '计时中' : '没在计时'));
    if (opts.presence) {
      (human.presence || []).forEach(function (p) {
        var s = ms(p.from), e = ms(p.to);
        if (s === null || e === null || !inView(s, e)) { return; }
        var n = seg(hTrack, 'hcl-presence' + (p.afk ? ' is-afk' : ''), s, Math.max(e, s + MIN / 4),
          (p.afk ? '离开' : (p.app || '前台')) + (p.title ? ' · ' + p.title : '') + ' · ' + hm(s) + '–' + hm(e));
        n.style.minWidth = '2px';
      });
    }

    // 代理线
    var agents = opts.agents || (data.agents || []).filter(function (r) {
      return inView(ms(r.startAt), r.endAt ? ms(r.endAt) : now);
    });
    var rowOf = {};
    agents.forEach(function (r, i) {
      var live = !r.endAt;
      var ph = currentPhase(r);
      var sub = r.agent && r.label ? r.agent : (r.tool || '');
      if (r.overdue) { sub = '超时未收'; }
      var track = addRow('hcl-row-agent', laneName(r), sub, live ? PHASE_CLASS[ph] : null);
      track.parentNode.setAttribute('data-run-id', r.runId || '');
      rowOf[r.runId] = i + 1;
      segments(r, now).forEach(function (g) {
        if (!inView(g.s, g.e)) { return; }
        var isLive = live && g.last;
        var cls = 'hcl-ph-' + PHASE_CLASS[g.phase] + (isLive ? ' is-live' : '') +
          (isLive && r.overdue ? ' is-overdue' : '');
        seg(track, cls, g.s, g.e, laneName(r) + ' · ' + PHASE_WORD[g.phase] +
          (g.detail ? '（' + g.detail + '）' : '') + ' · ' + hm(g.s) + '–' + (isLive ? '现在' : hm(g.e)) +
          '（' + dur(g.s, g.e) + '）');
      });
      sayLines.push(laneName(r) + '：' + (live ? PHASE_WORD[ph] : '已结束') + (r.overdue ? '（超时未收）' : ''));
    });

    // 连线：reply 实线竖线、attend 半透明竖带，都从人那条线落到该代理线
    (data.interactions || []).forEach(function (x) {
      var r = rowOf[x.runId], at = ms(x.at);
      if (!r || at === null) { return; }
      if (x.kind === 'reply' && at >= v0 && at <= v1) {
        var line = el('span', 'hcl-reply');
        line.style.left = pct(at);
        line.style.setProperty('--hcl-r', r);
        overlay.appendChild(line);
      } else if (x.kind === 'attend') {
        var until = ms(x.until) || at;
        if (!inView(at, Math.max(until, at + 1))) { return; }
        var band = el('span', 'hcl-attend');
        place(band, at, Math.max(until, at));
        band.style.setProperty('--hcl-r', r);
        overlay.appendChild(band);
      }
    });
    if (now >= v0 && now <= v1) {
      var nowLine = el('span', 'hcl-now');
      nowLine.style.left = pct(now);
      overlay.appendChild(nowLine);
    }
    rows.appendChild(overlay);
    root.appendChild(rows);

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
    [['hcl-human hcl-mode-do', '我在计时'], ['hcl-ph-working', '在干活'], ['hcl-ph-waiting', '在等你'],
     ['hcl-ph-idle', '空闲'], ['hcl-ph-error', '出错'], ['hcl-key-reply', '回话'], ['hcl-key-attend', '在看']
    ].forEach(function (p) {
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
  }

  window.HoneycombLanes = {
    query: query, prevDay: prevDay, segments: segments, currentPhase: currentPhase,
    pickPreview: pickPreview, render: render, PHASE_WORD: PHASE_WORD
  };
})();
