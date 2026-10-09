/* 此刻的焦点 —— 顶栏芯片、计时页（圆环中心 + 泳道里人那张卡）、蜂巢中心格共用的那一句话。
 *
 * nexus-core v2.16 在 views/current 与 views/lanes 的 human 上给出 focus（人此刻在哪个窗口、从什么时候起、
 * 多半属于哪个项目 / 任务），v2.14 的 auto 是「自动跟踪正跟着的目标」。项目 / 任务是服务端认的，
 * 这里**只把它们说成字**：三处的字都从 describe() 出，谁也不自己拼（modules/nginx-docker 契约「此刻的焦点」节）。
 *
 * 纯函数：不发请求、不碰 DOM、不读时钟。网关在 navbar.js 之前注入本文件；计时页另外直接引一次（重复加载是空操作）。
 * 对外只挂 window.HoneycombFocus（node 里 require 得到同一个对象，给单测用）：
 *   describe(src)   src = views/current 的响应或 views/lanes 的 human；有没有手动计时由调用方先判
 *   clock(seconds)  走秒的字：MM:SS，满一小时 H:MM:SS
 * describe() 的结果里 dwell（nexus-core v2.17 的 focus.dwellSeconds）= 「近 2 小时在这上面 N 分」：走秒的钟数的是这一次
 * 待了多久（切走再回来就从头数——人的注意力是串行的），窗口切得快时它总是几秒；这一句小字说的是同一个窗口累计待了多久。
 * 仍然只说当前这一个窗口，不是几个窗口的合计。离开 / 老后端没有这个键 → 空串。
 */
(function (root) {
  'use strict';
  if (root.HoneycombFocus) { return; }

  var SOURCE_WORD = { rules: '规则', choice: '你选的', ai: 'AI 认的', 'agent-session': '来自会话', history: '按以往' };
  var WORD_AFK = '离开', WORD_USING = '正在用', LEAD_FOCUS = '正在：', LEAD_AUTO = '自动 · ', WORD_SOMEWHERE = '电脑';

  var DWELL_LEAD = '近 2 小时在这上面 ';          // 服务端的在场时间线只留 2 小时，所以不说「今天」

  function dwellText(seconds) {
    if (typeof seconds !== 'number' || !(seconds >= 0)) { return ''; }
    var m = Math.floor(seconds / 60);
    return DWELL_LEAD + (m < 1 ? '不到 1 分' : m < 60 ? m + ' 分' : Math.floor(m / 60) + ' 小时' + (m % 60 ? ' ' + m % 60 + ' 分' : ''));
  }

  var join = function (parts, sep) { return parts.filter(function (x) { return x; }).join(sep); };
  var pad2 = function (n) { return (n < 10 ? '0' : '') + n; };

  function clock(seconds) {
    var sec = Math.max(0, Math.floor(Number(seconds) || 0));
    var h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60);
    return (h ? h + ':' + pad2(m) : pad2(m)) + ':' + pad2(sec % 60);
  }

  function describe(src) {
    var auto = (src && src.auto) || null, focus = (src && src.focus) || null;
    var from = auto || focus;                       // 自动跟踪正跟着：字与起点都以它为准（老后端只有 auto 也走这里）
    var since = from ? Date.parse(from.since) : NaN;
    if (isNaN(since)) { return null; }
    var afk = !auto && focus.state === 'afk';
    var target = afk ? '' : join([from.projectName, from.taskName], ' / ');
    var win = join([from.app, from.title], ' · ');
    var out = {
      state: afk ? 'afk' : 'present', auto: !!auto, since: since, target: target, window: win,
      hint: (!afk && SOURCE_WORD[from.source]) || '',
      dwell: (!afk && focus) ? dwellText(focus.dwellSeconds) : '',
      projectId: (!afk && from.projectId) || null, projectName: (!afk && from.projectName) || null,
      taskId: (!afk && from.taskId) || null, taskName: (!afk && from.taskName) || null
    };
    if (afk) {
      out.lead = out.chip = WORD_AFK;
    } else if (auto) {
      out.lead = out.chip = LEAD_AUTO + target;
    } else {
      out.lead = target ? LEAD_FOCUS + target : WORD_USING;
      out.chip = LEAD_FOCUS + (from.projectName || win || WORD_SOMEWHERE);
    }
    return out;
  }

  root.HoneycombFocus = { describe: describe, clock: clock, SOURCE_WORD: SOURCE_WORD };
  if (typeof module !== 'undefined' && module.exports) { module.exports = root.HoneycombFocus; }
})(typeof window !== 'undefined' ? window : globalThis);
