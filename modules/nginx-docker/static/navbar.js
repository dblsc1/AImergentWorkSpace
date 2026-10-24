/* 共享顶栏 —— 由网关（nginx sub_filter）注入每个设了门的前端页面。
 *
 * 品牌 + 页签（按已装前端） + 录制胶囊 + 主题面板 + 退出。对外不变量：
 *
 *   · 根元素是 nav.ckpt-nav[data-ckpt-nav]，全部 class 以 ckpt- 起头；
 *   · 对 /__cockpit/current 发 GET（约 10s 一次），先判 degraded 再读 running，
 *     退出发 POST /api/auth/logout 后跳 /login/，不发任何其它非 GET 请求；
 *   · 自动跟踪（nexus-core v2.14）：没在计时、同一份响应带 auto 时芯片显示「自动 · 项目 / 任务」+ 走秒
 *     （data-ckpt-auto）；带 needsChoice 时芯片上多一个小点（data-ckpt-choice，点芯片去计时页选）。不多发请求；
 *   · 此刻的焦点（nexus-core v2.16）：没在计时、没有 auto、同一份响应带 focus 时芯片显示「正在：项目或窗口」+ 走秒
 *     （data-ckpt-focus="present"），离开时「离开」（"afk"）。字出自共享件 focus.js（网关先于本文件注入），
 *     与 auto 是同一段代码；不多发请求；
 *   · 泳道预览（v0.3，契约「泳道预览」节）：只在预览打开、页面可见时约 15s GET 一次
 *     api/core/views/lanes，并在第一次打开时加载同目录的 lanes.js（计时页上整段不启用）；
 *   · 顶栏自己的网络失败不写 console.error——这条由网关侧 /__cockpit/current
 *     的恒 200 兑现，不是靠这里的 .catch()（见 contract.md「HTML 响应体注入」节）。
 *
 * 主题面板（明/暗/跟随 + 三色预设），写 localStorage["cockpit-theme"] /
 * ["cockpit-accent"]，监听 storage 事件做多窗即时同步。
 * `</head>` 处另有一段独立的内联 boot 脚本（见 cockpit.conf.template）负责首帧
 * 防白闪；本文件只负责「用户在这一页手动切换时」的后续更新，两者分工不重叠：
 * boot 脚本只在页面加载时跑一次，本文件在页面存活期间持续响应交互与跨窗事件。
 */
(function () {
  'use strict';

  // 页签不写死：网关按「装了哪些前端」在 </head> 前注入 window.HONEYCOMB_NAV
  // （各模块 module.yaml 的 nav 字段）。没装的前端就没有页签，不出死链。
  //   { home: '/hive/', timer: '/ring/', tabs: [{ href, label }, ...] }
  // 站点前缀（gateway.v1）：整站挂子路径时网关注入 HONEYCOMB_BASE，页签 href 已含前缀。
  var BASE = window.HONEYCOMB_BASE || '/';
  var NAV = window.HONEYCOMB_NAV || { home: BASE, timer: null, tabs: [] };
  var STOPS = NAV.tabs || [];

  // 拉计时状态与此刻的焦点。2026-10-09（nexus-core v2.17）：检测程序 5 秒一拍，这里跟到 5 秒——每 3 秒切一次窗口的人，
  // 10 秒一拉总是晚一两个窗口。页面不可见时退到 60 秒（没人看），回到前台立刻拉一次。
  var POLL_MS = 5000;
  var POLL_HIDDEN_MS = 60000;
  var TICK_MS = 1000;              // 本地走秒：用时每秒更新，不依赖网络往返
  var CURRENT_URL = BASE + '__cockpit/current';   // 恒 200 的网关端点，见契约 v0.5/v0.6
  var LOGOUT_URL = BASE + 'api/auth/logout';
  var LOGIN_URL = BASE + 'login/';

  var THEME_KEY = 'cockpit-theme';     // localStorage：light|dark|auto
  var ACCENT_KEY = 'cockpit-accent';   // localStorage：teal|violet|amber
  var ACCENTS = ['teal', 'violet', 'amber'];

  var IDLE = 'idle', RUNNING = 'running', DEGRADED = 'degraded';
  var WORD_IDLE = '未在计时';
  var WORD_PAUSED = '已暂停';
  // 蜂巢 / 计时台共用的两个本机键（contracts/timer-ring-visual-v1.md「暂停记忆」「累计记忆」）。
  // 后端没有暂停：暂停时 views/current 是空闲，只有这两个键知道「停的是谁、之前累计多少」。
  var PAUSED_KEY = 'nexus.timer.paused.v1';
  var CARRY_KEY = 'nexus.timer.carry.v1';
  // 形状不对（不是对象、没有 taskId）一律当不存在；秒数只认有限非负数，
  // 否则负数会倒扣本段、怪对象在转数字时抛（Codex 审核）。
  var readKey = function (k) {
    try {
      var v = JSON.parse(localStorage.getItem(k) || 'null');
      return (v && typeof v === 'object' && typeof v.taskId === 'string') ? v : null;
    } catch (e) { return null; }
  };
  var secsOf = function (v) {
    return (typeof v === 'number' && isFinite(v) && v >= 0) ? Math.floor(v) : 0;
  };
  var WORD_DEGRADED = '状态未知';
  var HINT_DEGRADED = '后端暂时联系不上，计时状态未知 —— 这不表示你没在计时';

  // 注入点已由网关限定在设了门的前端页面，这里再挡一道：还没进门就给导航是错的（A1），
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
   * 全程不使用 [hidden] 属性，显隐一律走 class。gantt 仓有一条遍历全部 [hidden]
   * 元素的回归断言，注入带 hidden 的新 DOM 会把它打乱，而我们无权改别人仓里的测试。 */
  var nav = el('nav', 'ckpt-nav');
  nav.setAttribute('aria-label', 'HoneyComb 导航');
  nav.setAttribute('data-ckpt-nav', '');
  // 首帧取 degraded：第一次 poll 回来之前我们确实还不知道，写 idle 等于在没有依据的
  // 情况下断言「你没在计时」（v0.5 就是这么定的，v2 沿用）。
  nav.setAttribute('data-ckpt-timer', 'degraded');

  // 品牌
  var brand = el('a', 'ckpt-brand');
  brand.href = NAV.home || BASE;
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
  chip.href = NAV.timer || NAV.home || BASE;
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
  var needDot = el('span', 'ckpt-need');         // 有个窗口等人选记到哪（data-ckpt-choice 时才显示）
  needDot.setAttribute('aria-hidden', 'true');
  chip.appendChild(needDot);
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

  // 退出
  var exit = el('button', 'ckpt-exit');
  exit.type = 'button';
  exit.setAttribute('data-ckpt-exit', '');
  exit.setAttribute('aria-label', '退出');
  // 窄屏只留这个图标（文字藏掉）。v0.2.1 之前窄屏下文字藏了又没有图标，是个空框。
  exit.appendChild(svg(
    { width: '16', height: '16', viewBox: '0 0 20 20', fill: 'none', 'aria-hidden': 'true', class: 'ckpt-exit-icon' },
    [
      svgEl('path', { d: 'M8 3H4v14h4', stroke: 'currentColor', 'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }),
      svgEl('path', { d: 'M12 6l4 4-4 4M16 10H8', stroke: 'currentColor', 'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })
    ]
  ));
  exit.appendChild(el('span', 'ckpt-word', '退出'));
  right.appendChild(exit);

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
  var taskId = null;     // 在计的任务：每秒对一次累计记忆（见 paint）
  var state = DEGRADED;
  // 自动跟踪（nexus-core v2.14 的 auto / needsChoice）与此刻的焦点（v2.16 的 focus）：只在没在计时时用。
  // focus = 共享件 HoneycombFocus.describe() 的结果（自动跟踪 / 在某个窗口 / 离开），没有为 null
  var focus = null;
  var needsChoice = false;
  var CHIP_LABEL = chip.getAttribute('aria-label');
  var HINT_CHOICE = '有个窗口不知道记到哪 —— 去计时页选';

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
    nav.removeAttribute('data-ckpt-paused');
    nav.removeAttribute('data-ckpt-auto');
    nav.removeAttribute('data-ckpt-focus');
    var asking = state === IDLE && needsChoice;
    if (asking) { nav.setAttribute('data-ckpt-choice', ''); } else { nav.removeAttribute('data-ckpt-choice'); }
    chip.setAttribute('aria-label', CHIP_LABEL + (asking ? '；' + HINT_CHOICE : ''));
    if (state === DEGRADED) {
      liveWord.textContent = WORD_DEGRADED;
      chip.title = HINT_DEGRADED;
      return;
    }
    chip.title = asking ? HINT_CHOICE : '';
    if (state === IDLE) {
      // 以前空闲时读数停在上一段的最后一秒（「未在计时 · 03:47」，Windows 验收）。
      // 已知天花板：暂停记忆按契约只在本机。在别的浏览器继续并结束了，这里仍显示
      // 「已暂停」—— 与 hive 中心格 / 计时台同一语义，要根治得把暂停搬到后端。
      var paused = readKey(PAUSED_KEY);
      if (focus && !paused) {
        // 没在计时、也没暂停着：「我」此刻在做的事顶上来——自动跟踪（虚线边 + 空心点）、在某个窗口（「正在：…」）、
        // 离开（压暗）。三种都和手动计时的实线边分得开。
        if (focus.auto) { nav.setAttribute('data-ckpt-auto', ''); } else { nav.setAttribute('data-ckpt-focus', focus.state); }
        liveWord.textContent = focus.chip;
        elapsedNode.textContent = window.HoneycombFocus.clock((Date.now() - focus.since) / 1000);
        // 字截断了，全文放这里；v2.17 再带一句「近 2 小时在这上面 N 分」（芯片上放不下，只进悬停）
        var full = focus.lead + (focus.window ? ' · ' + focus.window : '') + (focus.dwell ? '（' + focus.dwell + '）' : '');
        chip.title = asking ? full + ' —— ' + HINT_CHOICE : full;
        return;
      }
      liveWord.textContent = paused ? WORD_PAUSED : WORD_IDLE;
      // 窄屏把字藏了，只剩圆点 + 读数：暂停和空闲得靠点本身分开（Windows 验收）
      if (paused) nav.setAttribute('data-ckpt-paused', '');
      elapsedNode.textContent = fmt(paused ? secsOf(paused.carriedSeconds) : 0);
      return;
    }
    // running：liveWord 显示任务名（F-NAV-2：running -> 青点脉动 + 任务名 + 时长）
    liveWord.textContent = taskNode || '计时中';
    // 暂停后继续：读数 = 之前各段累计 + 本段（只影响显示，同 hive / ring）。每次画都现读：
    // 「继续」时 start 一成功顶栏就收到刷新事件，计时台写累计记忆在那之后，只在 apply
    // 读一次会错过（实测继续后仍从 00:00 走）。
    var carry = readKey(CARRY_KEY);
    var carrySec = (carry && carry.taskId === taskId) ? secsOf(carry.carriedSeconds) : 0;
    elapsedNode.textContent = fmt(Math.floor((Date.now() - startMs) / 1000) + carrySec);
  };

  var degrade = function () { startMs = null; focus = null; needsChoice = false; state = DEGRADED; paint(); };

  var apply = function (data) {
    // 网关的降级体：恒 200 但明说了自己不可信，必须先判。降级体里 running 恒为
    // false，当成真值读就会把「后端挂了」显示成「没在计时」。
    if (!data || data.degraded) { degrade(); return; }

    var running = !!data.running && data.sessionStartAt;
    // 手动计时永远优先：在计时就不看 auto / focus / needsChoice
    focus = (!running && window.HoneycombFocus) ? window.HoneycombFocus.describe(data) : null;
    needsChoice = !running && !!data.needsChoice;
    if (!running) { startMs = null; state = IDLE; paint(); return; }
    var t = Date.parse(data.sessionStartAt);
    if (isNaN(t)) { degrade(); return; }   // 有 sessionStartAt 但解析不出来 = 数据坏了，同样是「不知道」
    startMs = t;
    state = RUNNING;
    // 计的是项目的「未分类」时间桶（nexus-core v2.9）：只显示项目名
    var bucket = data.task && data.task.kind === 'unclassified';
    taskNode = (bucket ? (data.project && data.project.name) : (data.task && data.task.name)) || '';
    taskId = (data.task && data.task.id) || null;
    paint();
  };

  var pollTimer = null;
  var poll = function () {
    // 自己排下一次：可见 5 秒、不可见 60 秒（setInterval 改不了间隔）
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, document.visibilityState === 'hidden' ? POLL_HIDDEN_MS : POLL_MS);
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
  // 切到后台：下一次改按 60 秒排（不发请求）；回到前台：立刻拉一次
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') {
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, POLL_HIDDEN_MS);
    } else { poll(); }
  });
  // 页面里开始 / 停止 / 取消计时后立刻重拉，不等下一个 5 秒（v0.2.1 实测：
  // 蜂巢里刚开始计时，顶栏还显示「未在计时」好几秒）。页面发这个事件即可，
  // 顶栏不关心是谁发的（契约 modules/nginx-docker/module_docs/contract.md）。
  window.addEventListener('honeycomb:timer-changed', poll);
  setInterval(paint, TICK_MS);

  /* ── 退出 ─────────────────────────────────────────────────────
   * POST /api/auth/logout -> 204 + 清 cookie，然后回登录页。失败也回登录页：
   * cookie 可能已经被清掉了，把人留在一个已经登出的页面更糟。 */
  exit.addEventListener('click', function () {
    exit.disabled = true;
    fetch(LOGOUT_URL, { method: 'POST', credentials: 'same-origin' })
      .catch(function () { /* 网络失败照样回登录页，见上 */ })
      .then(function () { location.assign(LOGIN_URL); });
  });

  /* ── 泳道预览（v0.3，nexus-core views.lanes.v1；契约「泳道预览」节）────────────
   * 悬停 / 聚焦 / 触屏点一下计时芯片，弹出人一条线 + 至多 4 条代理线的最近 1 小时。
   * 计时页上不弹（那一页画了全部泳道）。画图交给共享的 lanes.js（计时页同一份），
   * 第一次打开时才加载。非 2xx（含 404 老后端、401）→ 不弹，芯片照旧，不跳登录页。 */
  if (!(NAV.timer && location.pathname.indexOf(NAV.timer) === 0)) {
    var LANES_URL = BASE + 'api/core/views/lanes';
    var LANES_JS = BASE + '__cockpit/lanes.js';
    var LANES_POLL_MS = 15000;
    var HOUR_MS = 3600000;

    var pop = el('div', 'ckpt-lanes-pop');
    pop.id = 'ckpt-lanes-pop';
    pop.setAttribute('data-ckpt-lanes', '');
    pop.setAttribute('role', 'region');
    pop.setAttribute('aria-label', '泳道预览');
    pop.appendChild(el('div', 'ckpt-lanes-head', '最近 1 小时'));
    var popBody = el('div', 'ckpt-lanes-body');
    pop.appendChild(popBody);
    var popGo = el('a', 'ckpt-lanes-go', '去计时页');
    popGo.href = chip.getAttribute('href');
    pop.appendChild(popGo);
    right.insertBefore(pop, chip.nextSibling);      // 紧跟芯片：Tab 从芯片直接走进预览
    chip.setAttribute('aria-controls', pop.id);
    chip.setAttribute('aria-expanded', 'false');

    var want = false, lanesPoll = null, lanesLast = null, lanesGone = false;
    var gen = 0;   // 每发一次、每关一次都加一：只认最新那次请求的回应（Codex 审核）
    var openT = null, closeT = null, lastTouch = 0, quietFocus = false;

    var placePop = function () {
      var vw = document.documentElement.clientWidth;
      var w = Math.min(360, vw - 32);
      var r = chip.getBoundingClientRect();
      pop.style.width = w + 'px';
      pop.style.left = Math.min(Math.max(r.right - w, 16), vw - 16 - w) + 'px';
    };
    var showPop = function () {
      placePop();
      pop.classList.add('ckpt-open');
      chip.setAttribute('aria-expanded', 'true');
    };
    var closePreview = function () {
      want = false;
      gen += 1;
      clearTimeout(openT);
      clearInterval(lanesPoll);
      lanesPoll = null;
      pop.classList.remove('ckpt-open');
      chip.setAttribute('aria-expanded', 'false');
    };
    var paintLanes = function () {
      var L = window.HoneycombLanes, now = Date.parse(lanesLast.now), v0 = now - HOUR_MS;
      var pick = L.pickPreview(lanesLast.agents, v0, 4);
      L.render(popBody, lanesLast, {
        viewStart: v0, viewEnd: now + HOUR_MS * 0.03, agents: pick.shown, compact: true, presence: true,
        more: pick.more ? '还有 ' + pick.more + ' 个 → 计时页' : '', moreHref: chip.getAttribute('href'),
        focusFallback: popGo
      });
    };
    var fetchLanes = function () {
      if (!want || document.visibilityState === 'hidden') { return; }
      var q = window.HoneycombLanes.query(lanesLast, 1), mine = ++gen;
      fetch(LANES_URL + q, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
        .then(function (r) {
          if (r.status === 404) { lanesGone = true; }
          if (!r.ok) { throw new Error('HTTP ' + r.status); }
          return r.json();
        })
        .then(function (d) {
          if (mine !== gen) { return; }
          if (!d || !d.now) { throw new Error('bad body'); }
          lanesLast = d;
          var next = window.HoneycombLanes.query(d, 1);   // 1 小时跨了零点：补拉昨天 + 今天
          if (next !== q && next.indexOf('from=') !== -1) { fetchLanes(); return; }
          paintLanes();
          showPop();
        })
        .catch(function () { if (mine === gen) { closePreview(); } });
    };
    var withLib = function (cb) {
      if (window.HoneycombLanes) { cb(); return; }
      var s = document.querySelector('script[data-ckpt-lanes-js]');
      if (!s) {
        s = document.createElement('script');
        s.src = LANES_JS;
        s.setAttribute('data-ckpt-lanes-js', '');
        document.head.appendChild(s);
      }
      s.addEventListener('load', cb);
    };
    var openPreview = function () {
      if (want || lanesGone) { return; }
      want = true;
      withLib(function () {
        if (!want || lanesPoll) { return; }
        fetchLanes();
        lanesPoll = setInterval(fetchLanes, LANES_POLL_MS);
      });
    };

    // 鼠标：停 150 ms 才开，离开芯片与预览 300 ms 才关（防闪）；点芯片照旧去计时页。
    var hoverIn = function (e) {
      if (e.pointerType !== 'mouse') { return; }
      clearTimeout(closeT);
      if (!want) { clearTimeout(openT); openT = setTimeout(openPreview, 150); }
    };
    var hoverOut = function (e) {
      if (e.pointerType !== 'mouse') { return; }
      clearTimeout(openT);
      closeT = setTimeout(function () {
        // 焦点还在芯片或预览里（键盘打开的）就不因鼠标离开而关
        var a = document.activeElement;
        if (a && (chip.contains(a) || pop.contains(a))) { return; }
        closePreview();
      }, 300);
    };
    [chip, pop].forEach(function (n) {
      n.addEventListener('pointerenter', hoverIn);
      n.addEventListener('pointerleave', hoverOut);
    });

    // 键盘：芯片获得焦点即开；焦点离开芯片与预览就关；Esc 关并把焦点留在芯片。
    chip.addEventListener('focus', function () {
      clearTimeout(closeT);
      if (quietFocus || Date.now() - lastTouch < 1000) { return; }
      openPreview();
    });
    var focusOut = function (e) {
      // 只在焦点真的走到别的控件上时关；点到空白处（relatedTarget 为空）交给下面的「点外面」
      var to = e.relatedTarget;
      if (!to || chip.contains(to) || pop.contains(to)) { return; }
      closePreview();
    };
    chip.addEventListener('blur', focusOut);
    pop.addEventListener('focusout', focusOut);
    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape' || !want) { return; }
      closePreview();
      quietFocus = true;
      chip.focus();
      quietFocus = false;
    });

    // 触屏（没有悬停）：第一下点芯片只切换预览，预览里的「去计时页」才走；点外面关。
    document.addEventListener('pointerdown', function (e) {
      if (e.pointerType === 'touch' || e.pointerType === 'pen') { lastTouch = Date.now(); }
    }, true);
    chip.addEventListener('click', function (e) {
      if (Date.now() - lastTouch > 1000) { return; }
      e.preventDefault();
      if (want) { closePreview(); } else { openPreview(); }
    });
    document.addEventListener('click', function (e) {
      if (want && !chip.contains(e.target) && !pop.contains(e.target)) { closePreview(); }
    });

    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') { fetchLanes(); }   // 关着时 fetchLanes 直接返回
    });
    window.addEventListener('resize', function () { if (want) { placePop(); } });
  }
})();
