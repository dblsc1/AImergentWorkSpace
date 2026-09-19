/* 驾驶舱共享顶栏 v2 —— 由 nginx sub_filter 注入各页。
 *
 * v0.1 发布版：品牌 + 两页签（任务 / 计时）+ 计时芯片 + 主题面板。无登录层，故无退出。
 * （PRD F-NAV-1..5）。对外不变量与 v0.4/v0.5 相同，逐条延续：
 *
 *   · 根元素仍是 nav.ckpt-nav[data-ckpt-nav]，全部 class 仍以 ckpt- 起头；
 *   · 只对 /__cockpit/current 发 GET（约 10s 一次），先判 degraded 再读 running，
 *     退出发 POST /api/auth/logout 后跳 /login/，不发任何其它非 GET 请求；
 *   · 顶栏自己的网络失败不写 console.error——这条由网关侧 /__cockpit/current
 *     的恒 200 兑现，不是靠这里的 .catch()（契约 v0.4/v0.5 已把因果讲透）。
 *
 * 新增：主题面板（明/暗/跟随 + 三色预设），写 localStorage["cockpit-theme"] /
 * ["cockpit-accent"]，监听 storage 事件做多窗即时同步（PRD F-THEME-3）。
 * `</head>` 处另有一段独立的内联 boot 脚本（见 cockpit.conf.template）负责首帧
 * 防白闪；本文件只负责「用户在这一页手动切换时」的后续更新，两者分工不重叠：
 * boot 脚本只在页面加载时跑一次，本文件在页面存活期间持续响应交互与跨窗事件。
 */
(function () {
  'use strict';

  var STOPS = [
    { href: '/hive/', label: '任务' },
    { href: '/ring/',  label: '计时' }
  ];

  var POLL_MS = 10000;             // 拉计时状态：views 本身聚合周期就有这么长
  var TICK_MS = 1000;              // 本地走秒：用时每秒更新，不依赖网络往返
  var CURRENT_URL = '/__cockpit/current';   // 恒 200 的网关端点，见契约 v0.5/v0.6

  var THEME_KEY = 'cockpit-theme';     // localStorage：light|dark|auto
  var ACCENT_KEY = 'cockpit-accent';   // localStorage：teal|violet|amber
  var ACCENTS = ['teal', 'violet', 'amber'];

  var IDLE = 'idle', RUNNING = 'running', DEGRADED = 'degraded';
  var WORD_IDLE = '未在计时';
  var WORD_DEGRADED = '状态未知';
  var HINT_DEGRADED = '后端暂时联系不上，计时状态未知 —— 这不表示你没在计时';

  // 注入点已由 nginx 限定在三个页面，这里再挡一道：还没进门就给导航是错的（A1），
  // 登录页出现「退出」与主题面板更荒唐。
  if (document.body === null) { return; }
  if (window.__ckptNavMounted) { return; }        // sub_filter 只注一次，这条是防御
  if (/^\/login\//.test(location.pathname)) { return; }

  window.__ckptNavMounted = true;

  var el = function (tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) { n.className = cls; }
    if (text !== undefined) { n.textContent = text; }
    return n;
  };

  // 图标一律用 SVG DOM 方法拼，不用 innerHTML——两个图标的字节都是本文件里的
  // 字面常量，本没有注入风险，但「拼 HTML 字符串再塞进去」这个形状本身值得
  // 避免：以后有人在同一个函数里手滑加一段带用户数据的字符串，就会在无意间
  // 把安全的模式改成不安全的模式。用 DOM 方法从根上不给这个手滑的机会。
  var SVGNS = 'http://www.w3.org/2000/svg';
  var svgEl = function (tag, attrs) {
    var n = document.createElementNS(SVGNS, tag);
    for (var k in attrs) { if (attrs.hasOwnProperty(k)) { n.setAttribute(k, attrs[k]); } }
    return n;
  };
  var svg = function (attrs, children) {
    var s = svgEl('svg', attrs);
    for (var i = 0; i < children.length; i++) { s.appendChild(children[i]); }
    return s;
  };

  /* ── 主题：解析、应用、持久化 ─────────────────────────────────────
   * 与 </head> 的内联 boot 脚本用同一套 key 与同一套 auto 解析规则，
   * 两处逻辑分别维护是有意的——boot 脚本必须自成一段可内联的最小体量（≤10 行
   * 等价逻辑），不能依赖本文件（本文件在 boot 脚本跑的那一刻还没加载）。 */
  var resolveTheme = function (raw) {
    if (raw === 'light' || raw === 'dark') { return raw; }
    return (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
      ? 'dark' : 'light';
  };

  var applyTheme = function () {
    var raw = localStorage.getItem(THEME_KEY) || 'auto';
    document.documentElement.setAttribute('data-theme', resolveTheme(raw));
    var accent = localStorage.getItem(ACCENT_KEY);
    if (accent) { document.documentElement.setAttribute('data-accent', accent); }
    return raw;
  };

  var setTheme = function (mode) {
    // mode: light|dark|auto
    localStorage.setItem(THEME_KEY, mode);
    applyTheme();
    paintThemePanel();
  };

  var setAccent = function (name) {
    if (ACCENTS.indexOf(name) === -1) { return; }
    localStorage.setItem(ACCENT_KEY, name);
    document.documentElement.setAttribute('data-accent', name);
    paintThemePanel();
  };

  var currentThemeRaw = applyTheme();  // 首帧已由 boot 脚本钉过，这里只是让本文件自己也知道当前值

  // 跟随系统时，系统主题变化要立刻反映——「跟随」意味着一直跟，不是只在加载那一刻问一次。
  if (window.matchMedia) {
    var mq = window.matchMedia('(prefers-color-scheme: dark)');
    var onSchemeChange = function () {
      if ((localStorage.getItem(THEME_KEY) || 'auto') === 'auto') { applyTheme(); }
    };
    if (mq.addEventListener) { mq.addEventListener('change', onSchemeChange); }
    else if (mq.addListener) { mq.addListener(onSchemeChange); }
  }

  // 多窗同步（F-THEME-3）：另一个已开窗口改了主题，本窗口立刻跟，不刷新。
  window.addEventListener('storage', function (e) {
    if (e.key === THEME_KEY || e.key === ACCENT_KEY) {
      applyTheme();
      paintThemePanel();
    }
  });

  /* ── 组装 DOM ──────────────────────────────────────────────────
   * 全程不使用 [hidden] 属性，显隐一律走 class。别处有遍历全部 [hidden]
   * 元素的回归断言，注入带 hidden 的新 DOM 会把它打乱。 */
  var nav = el('nav', 'ckpt-nav');
  nav.setAttribute('aria-label', '驾驶舱导航');
  nav.setAttribute('data-ckpt-nav', '');
  // 首帧取 degraded：第一次 poll 回来之前我们确实还不知道，写 idle 等于在没有依据的
  // 情况下断言「你没在计时」（v0.5 就是这么定的，v2 沿用）。
  nav.setAttribute('data-ckpt-timer', 'degraded');

  // 品牌
  var brand = el('a', 'ckpt-brand');
  brand.href = '/hive/';
  brand.setAttribute('aria-label', 'HoneyComb');
  brand.appendChild(svg(
    { width: '20', height: '20', viewBox: '0 0 20 20', fill: 'none', 'aria-hidden': 'true' },
    [
      svgEl('circle', { cx: '10', cy: '10', r: '8', stroke: 'var(--ink-3)', 'stroke-width': '1.5', 'stroke-dasharray': '1.2 2.2' }),
      svgEl('path', { d: 'M10 2 A8 8 0 0 1 17.4 7.2', stroke: 'var(--accent)', 'stroke-width': '2.5', 'stroke-linecap': 'round' })
    ]
  ));
  brand.appendChild(el('span', 'ckpt-word', 'HoneyComb'));
  nav.appendChild(brand);

  // 页签
  var tabs = el('div', 'ckpt-tabs');
  tabs.setAttribute('aria-label', '页面');
  var here = -1;
  for (var i = 0; i < STOPS.length; i++) {
    if (location.pathname.indexOf(STOPS[i].href) === 0) { here = i; }
  }
  for (var j = 0; j < STOPS.length; j++) {
    var tab = el('a', 'ckpt-tab', STOPS[j].label);
    tab.href = STOPS[j].href;
    tab.setAttribute('data-ckpt-stop', STOPS[j].href);
    if (j === here) { tab.setAttribute('aria-current', 'page'); }
    tabs.appendChild(tab);
  }
  nav.appendChild(tabs);

  // 右侧区
  var right = el('span', 'ckpt-right');

  // 录制胶囊
  var chip = el('a', 'ckpt-chip');
  chip.href = '/ring/';
  chip.setAttribute('data-ckpt-chip', '');
  chip.setAttribute('aria-label', '录制状态，点击前往计时页');
  var chipDot = el('span', 'ckpt-dot');
  chipDot.setAttribute('aria-hidden', 'true');
  chip.appendChild(chipDot);
  var liveWord = el('span', 'ckpt-live-word', WORD_IDLE);
  chip.appendChild(liveWord);
  chip.appendChild(el('span', 'ckpt-sep', '·'));
  var elapsedNode = el('span', 'ckpt-elapsed', '00:00');
  elapsedNode.setAttribute('aria-live', 'off');  // 每秒变化，告诉读屏软件安静更新
  chip.appendChild(elapsedNode);
  right.appendChild(chip);

  // 主题按钮 + 面板
  var themeWrap = el('span');
  themeWrap.style.position = 'relative';
  var themeBtn = el('button', 'ckpt-icon-btn');
  themeBtn.type = 'button';
  themeBtn.setAttribute('aria-label', '切换主题');
  themeBtn.setAttribute('aria-expanded', 'false');
  themeBtn.innerHTML =
    '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">' +
    '<path d="M13.5 9.5A6 6 0 0 1 6.5 2.5a6 6 0 1 0 7 7Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>' +
    '</svg>';

  var panel = el('div', 'ckpt-theme-panel');
  panel.setAttribute('data-ckpt-theme-panel', '');

  panel.appendChild(el('div', 'ckpt-theme-label', '明暗'));
  var modes = el('div', 'ckpt-theme-modes');
  var MODE_LABEL = { light: '亮', dark: '暗', auto: '跟随' };
  var modeButtons = {};
  ['light', 'dark', 'auto'].forEach(function (m) {
    var b = el('button', 'ckpt-mode-btn', MODE_LABEL[m]);
    b.type = 'button';
    b.setAttribute('data-ckpt-mode', m);
    b.addEventListener('click', function (mode) { return function () { setTheme(mode); }; }(m));
    modeButtons[m] = b;
    modes.appendChild(b);
  });
  panel.appendChild(modes);

  panel.appendChild(el('div', 'ckpt-theme-label', '主题色'));
  var dots = el('div', 'ckpt-accent-dots');
  var ACCENT_NAME = { teal: '青', violet: '紫', amber: '琥珀' };
  var accentButtons = {};
  ACCENTS.forEach(function (name) {
    var b = el('button', 'ckpt-accent-dot');
    b.type = 'button';
    b.setAttribute('data-ckpt-accent', name);
    b.setAttribute('aria-label', ACCENT_NAME[name]);
    b.appendChild(el('span', 'ckpt-accent-swatch'));
    b.addEventListener('click', function (accentName) { return function () { setAccent(accentName); }; }(name));
    accentButtons[name] = b;
    dots.appendChild(b);
  });
  panel.appendChild(dots);

  themeWrap.appendChild(themeBtn);
  themeWrap.appendChild(panel);
  right.appendChild(themeWrap);

  var paintThemePanel = function () {
    var raw = localStorage.getItem(THEME_KEY) || 'auto';
    Object.keys(modeButtons).forEach(function (m) {
      modeButtons[m].setAttribute('aria-pressed', String(m === raw));
    });
    var accent = localStorage.getItem(ACCENT_KEY) || 'teal';
    Object.keys(accentButtons).forEach(function (name) {
      accentButtons[name].setAttribute('aria-pressed', String(name === accent));
    });
  };

  var closeThemePanel = function () {
    panel.classList.remove('ckpt-open');
    themeBtn.setAttribute('aria-expanded', 'false');
  };
  var toggleThemePanel = function () {
    var opening = !panel.classList.contains('ckpt-open');
    panel.classList.toggle('ckpt-open', opening);
    themeBtn.setAttribute('aria-expanded', String(opening));
  };
  themeBtn.addEventListener('click', function (e) { e.stopPropagation(); toggleThemePanel(); });
  document.addEventListener('click', function (e) {
    if (!themeWrap.contains(e.target)) { closeThemePanel(); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeThemePanel(); }
  });

  // v0.1 发布版没有登录层，所以顶栏也没有「退出」。
  // （开发版里这里会插一个按钮，POST /api/auth/logout 后回登录页。）

  nav.appendChild(right);

  // 插到 body 最前：Tab 顺序随 DOM 顺序，顶栏理应先于页面内容被走到。
  // 顶栏是 position: fixed，不构成 grid item，所以放最前也不会掉进 ring 的
  // body(display:grid; place-items:center) 布局里。
  document.body.insertBefore(nav, document.body.firstChild);
  paintThemePanel();

  // 顶栏是在页面脚本已经量过尺寸之后才插进来的，body 的 padding-top 因此是一次
  // 事后的高度变化。gantt 用 vis-timeline，它按容器尺寸预先算好几何位置，不重算
  // 就会留下过期坐标。派发一次 resize 是它公开支持的重算触发方式。
  window.dispatchEvent(new Event('resize'));

  /* ── 计时状态 ────────────────────────────────────────────────── */
  var taskNode = null;   // 运行时把 elapsed 前面那段替换成任务名，靠 liveWord 本身承载
  var startMs = null;
  var state = DEGRADED;

  var two = function (n) { return (n < 10 ? '0' : '') + n; };
  var fmt = function (totalSec) {
    if (totalSec < 0) { totalSec = 0; }
    var h = Math.floor(totalSec / 3600);
    var m = Math.floor((totalSec % 3600) / 60);
    var s = totalSec % 60;
    return h > 0 ? h + ':' + two(m) + ':' + two(s) : two(m) + ':' + two(s);
  };

  var paint = function () {
    nav.setAttribute('data-ckpt-timer', state);
    if (state === DEGRADED) {
      liveWord.textContent = WORD_DEGRADED;
      chip.title = HINT_DEGRADED;
      return;
    }
    chip.title = '';
    if (state === IDLE) {
      liveWord.textContent = WORD_IDLE;
      return;
    }
    // running：liveWord 显示任务名（F-NAV-2：running -> 青点脉动 + 任务名 + 时长）
    liveWord.textContent = taskNode || '计时中';
    elapsedNode.textContent = fmt(Math.floor((Date.now() - startMs) / 1000));
  };

  var degrade = function () { startMs = null; state = DEGRADED; paint(); };

  var apply = function (data) {
    // 网关的降级体：恒 200 但明说了自己不可信，必须先判。降级体里 running 恒为
    // false，当成真值读就会把「后端挂了」显示成「没在计时」。
    if (!data || data.degraded) { degrade(); return; }

    var running = !!data.running && data.sessionStartAt;
    if (!running) { startMs = null; state = IDLE; paint(); return; }
    var t = Date.parse(data.sessionStartAt);
    if (isNaN(t)) { degrade(); return; }   // 有 sessionStartAt 但解析不出来 = 数据坏了，同样是「不知道」
    startMs = t;
    state = RUNNING;
    taskNode = (data.task && data.task.name) ? data.task.name : '';
    paint();
  };

  var poll = function () {
    // 同源 + HttpOnly cookie：必须带 credentials，否则网关 auth_request 判未登录。
    // CURRENT_URL 是恒 200 的网关端点，正常情况下 .catch() 不会触发；下面这条兜底
    // 是为「有人把 nginx 改回旧端点」准备的，行为是降级，不是替后端撒谎地回到静止点。
    fetch(CURRENT_URL, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (!r.ok) { throw new Error('HTTP ' + r.status); }
        return r.json();
      })
      .then(apply)
      .catch(degrade);
  };

  poll();
  setInterval(poll, POLL_MS);
  setInterval(paint, TICK_MS);
})();
