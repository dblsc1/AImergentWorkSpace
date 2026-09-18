// table · 蜂巢中心格计时圆环 —— 实现的是 `timer-ring/v1`
// 规范：contracts/timer-ring-visual-v1.md（框架根仓，**只读**，不复制进本仓）。
//
// 「圆环全站一个样，但不做进静态件」（2026-09-07 定）＝ 统一靠规范 + 共享
// 设计变量，不靠共享代码。本文件是 ring 之外的第二个实现者，与 ring 各写各的
// （模块只经契约依赖，跨仓共享文件要另造分发机制）。
//
// 规范里被本文件逐条落实的硬要求：
//   · viewBox 0 0 200 200，圆心 (100,100)；刻度圈 r=92 / 进度弧 r=86 / 贡献环 r=78
//   · stroke-width 12，stroke-linecap butt（不是 round）
//   · pathLength="100" —— 之后 dasharray 单位直接是百分数，**不算周长 2πr**
//   · 颜色只用设计变量（--panel-2 / --line / --fact-s1..s3 / --fact），一个色值不写死
//   · 走秒 = Date.now() - Date.parse(sessionStartAt)，单调差值，不累加本地计数器
//   · 空闲态 = 只有轨道与刻度的空圆环，无进度弧，不可点
//
// 2026-09-12 人类改表盘（蜂巢中心格变体，规范「中心格变体」一节）：
//   「计时数字放在圆环中心，少于 1 小时显示分钟和秒，多于 1 小时显示时和分」
//   「白色短指针浮动在内环作为秒针。全圆环浅青色但是再浅一点，深青色作为分针显示。
//    作出一个记满一小时圆环青色走满一圈的效果」
// 于是：底圈整圈浅青（轨道）、进度弧 = 分针（60 分走满一圈）、内圈一根短秒针、
// 圆心放数字。r=78 的贡献环在中心格里退役 —— 那一圈正是秒针要浮的地方，
// 而且中心格只有 60px 大，两层含义叠在一起读不出来（ring 页主仪表照旧用它）。
//
// 纯函数（走秒/百分比/文案）在 Node 里可直接 require() 单测；DOM 函数只在浏览器用。
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.NexusTableHexRing = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var R_TICKS = 92, R_PROGRESS = 86, R_CONTRIB = 78;
  var TICK_COUNT = 60;

  // 走秒：单调差值。sessionStartAt 解析不出来就返回 null（不是 0——0 是"刚开始"
  // 的真实值，两者必须能区分开）。
  function elapsedSeconds(sessionStartAt, nowMs) {
    var start = Date.parse(sessionStartAt || "");
    if (isNaN(start)) return null;
    var now = (nowMs == null) ? Date.now() : Number(nowMs);
    return Math.max(0, Math.floor((now - start) / 1000));
  }

  // 进度弧扫过一整圈 = 60 分钟（和钟面分针同构，不需要用户先设一个目标时长）。
  // 超过一小时就再绕一圈，不封顶——封顶会让"跑了 90 分钟"看起来和"跑了 60 分钟"
  // 一样，那是撒谎。
  function progressPercent(seconds) {
    if (seconds == null || !isFinite(seconds) || seconds < 0) return 0;
    return (seconds % 3600) / 36;
  }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }

  function formatElapsed(seconds) {
    if (seconds == null || !isFinite(seconds) || seconds < 0) return "--:--";
    var s = Math.floor(seconds);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h > 0 ? (h + ":" + pad2(m) + ":" + pad2(sec)) : (pad2(m) + ":" + pad2(sec));
  }

  // 圆心的数字（人类：少于 1 小时 分:秒，多于 1 小时 时:分）。unit 是数字下面那行小字，
  // 不然「1:05」到底是一分五秒还是一小时五分读不出来。
  function formatClock(seconds) {
    if (seconds == null || !isFinite(seconds) || seconds < 0) return { text: "--:--", unit: "" };
    var s = Math.floor(seconds);
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h > 0 ? { text: h + ":" + pad2(m), unit: "时 · 分" }
                 : { text: pad2(m) + ":" + pad2(sec), unit: "分 · 秒" };
  }

  // 秒针角度：**累计**角度（秒 × 6°），不取模。取模的话 59 秒 → 0 秒那一下，
  // CSS 过渡会让指针倒着转一整圈回去。
  function secondAngle(seconds) {
    return (seconds == null || !isFinite(seconds)) ? 0 : Math.floor(seconds) * 6;
  }

  // 贡献环两段：当前任务在本项目里的占比（--fact-s1）+ 项目其余（--fact-s2）。
  // shareOfProject 契约是 0–100 百分数（不是 0–1 小数，contract-schemas 钉死过）。
  // 拿不到就返回 null，调用方据此不画贡献环——**不硬凑一段 0 分**（规范：
  // --fact-s3 是装饰档，任务不够多别硬凑；同理两段也不该编）。
  function contributionSegments(current) {
    var task = current && current.task;
    var share = task && task.shareOfProject;
    if (share == null || !isFinite(share)) return null;
    var s1 = Math.max(0, Math.min(100, Number(share)));
    return { current: s1, others: 100 - s1 };
  }

  // pathLength=100 之后 dasharray 单位直接是百分数（规范硬要求，不算 2πr）。
  function setSegment(el, startPercent, lengthPercent) {
    if (!el) return;
    var len = Math.max(0, Math.min(100, lengthPercent));
    el.setAttribute("stroke-dasharray", len + " " + (100 - len));
    el.setAttribute("stroke-dashoffset", String(-startPercent));
  }

  // 运行指示点落在进度弧末端。0% 在 12 点方向（弧组整体 rotate(-90)）。
  function dotPosition(percent) {
    var rad = (percent * 3.6 - 90) * Math.PI / 180;
    return { x: 100 + R_PROGRESS * Math.cos(rad), y: 100 + R_PROGRESS * Math.sin(rad) };
  }

  function tickMarkup() {
    var out = [];
    for (var i = 0; i < TICK_COUNT; i++) {
      var long = (i % 5 === 0);
      var a = (i / TICK_COUNT) * 2 * Math.PI - Math.PI / 2;
      var r2 = R_TICKS, r1 = R_TICKS - (long ? 7 : 3);
      out.push('<line x1="' + (100 + r1 * Math.cos(a)).toFixed(2) +
        '" y1="' + (100 + r1 * Math.sin(a)).toFixed(2) +
        '" x2="' + (100 + r2 * Math.cos(a)).toFixed(2) +
        '" y2="' + (100 + r2 * Math.sin(a)).toFixed(2) + '"/>');
    }
    return out.join("");
  }

  // 秒针浮在内圈：从 r=74 到 r=60 的一小截，不连到圆心 —— 圆心是数字的地方。
  var SEC_OUTER = 74, SEC_INNER = 60;

  // 圆环 SVG 骨架 + 圆心数字。颜色全部由 hex.css 用设计变量上色，本文件不写任何色值。
  function ringMarkup() {
    return '' +
      '<svg class="tring" viewBox="0 0 200 200" role="img" aria-label="计时圆环">' +
      '<g class="tring-ticks">' + tickMarkup() + '</g>' +
      '<circle class="tring-track" cx="100" cy="100" r="' + R_PROGRESS + '" pathLength="100"/>' +
      '<g transform="rotate(-90 100 100)">' +
      '<circle class="tring-progress" cx="100" cy="100" r="' + R_PROGRESS +
        '" pathLength="100" stroke-dasharray="0 100"/>' +
      '</g>' +
      '<line class="tring-sec" x1="100" y1="' + (100 - SEC_OUTER) + '" x2="100" y2="' + (100 - SEC_INNER) + '"/>' +
      '</svg>' +
      '<div class="tring-read"><b class="tring-num"></b><span class="tring-unit"></span></div>';
  }

  // 把 views/current 画上去。running=false → 空圆环（只有轨道与刻度）。
  // 满一小时的那一下：分针弧先走满整圈、亮一下，再从 0 重新走（人类：「记满一小时
  // 圆环青色走满一圈的效果」）。取模直接回 0 的话，满圈那一刻根本看不见。
  var HOUR_FULL_MS = 1600;

  // 圆心读数：记分牌翻牌（人类 2026-09-12：「数字能做个记分牌翻牌出现效果不？」）。
  // 每一位是一个 span，只有**变了的那几位**重播翻牌动画（CSS .tring-d.is-flip）；
  // 第一次出现 / 位数变了（空 → 有、59:59 → 1:00:00 那种）整串重建，每位错开一点依次翻出来。
  // 读数只有数字和冒号（formatClock），直接拼进 span 没有注入面；还是走 textContent 稳妥。
  var FLIP_STAGGER_MS = 45;
  function setFlip(el, text) {
    if (!el) return;
    text = text || "";
    if (el.__text === text) return;
    var prev = el.__text;
    el.__text = text;
    var i, d;
    if (prev == null || prev.length !== text.length || el.children.length !== text.length) {
      el.textContent = "";
      for (i = 0; i < text.length; i++) {
        d = document.createElement("span");
        d.className = "tring-d is-flip";
        d.textContent = text[i];
        d.style.animationDelay = (i * FLIP_STAGGER_MS) + "ms";
        el.appendChild(d);
      }
      return;
    }
    for (i = 0; i < text.length; i++) {
      if (prev[i] === text[i]) continue;
      d = el.children[i];
      d.textContent = text[i];
      d.style.animationDelay = "0ms";
      d.classList.remove("is-flip");
      void d.offsetWidth;                  // 同一个类摘了再挂，动画才会重播
      d.classList.add("is-flip");
    }
  }

  // 把 views/current 画上去。running=false → 空圆环（只有轨道与刻度）。
  // carrySeconds：暂停后继续时之前累计的秒数（hex-center-ctl.js 记的），
  // 分针、秒针、数字都按"累计 + 本段"走 —— 人类要的是接着之前的时间。
  // 返回的仍是**本段**秒数，调用方需要累计就自己加（与旧接口一致）。
  function paint(svg, current, nowMs, carrySeconds) {
    if (!svg) return null;
    var running = !!(current && current.running);
    var progress = svg.querySelector(".tring-progress");
    var sec = svg.querySelector(".tring-sec");
    var slot = svg.parentNode;
    var num = slot && slot.querySelector(".tring-num");
    var unit = slot && slot.querySelector(".tring-unit");
    svg.classList.toggle("is-running", running);
    if (!running) {
      setSegment(progress, 0, 0);
      setFlip(num, "");
      if (unit) unit.textContent = "";
      svg.__hour = null;
      return null;
    }
    var secs = elapsedSeconds(current.sessionStartAt, nowMs);
    var total = secs == null ? null : secs + (carrySeconds || 0);
    var hour = total == null ? null : Math.floor(total / 3600);
    // 跨过整点：只在"上一次画的时候还在前一个小时"时触发，刷新页面落在 1:20 不会乱闪。
    if (svg.__hour != null && hour != null && hour > svg.__hour) {
      svg.classList.add("is-hour-full");
      if (svg.__hourT) clearTimeout(svg.__hourT);
      svg.__hourT = setTimeout(function () {
        // 满圈 → 0 要**瞬间**回去：带着 350ms 的弧长过渡回去，看着是倒着转了一圈。
        svg.classList.add("is-hour-reset");
        svg.classList.remove("is-hour-full");
        setSegment(progress, 0, 0);
        void svg.getBoundingClientRect();
        svg.classList.remove("is-hour-reset");
        svg.__hourT = null;
      }, HOUR_FULL_MS);
    }
    svg.__hour = hour;
    setSegment(progress, 0, svg.classList.contains("is-hour-full") ? 100 : progressPercent(total));
    if (sec) sec.style.transform = "rotate(" + secondAngle(total) + "deg)";
    var c = formatClock(total);
    setFlip(num, c.text);
    if (unit) unit.textContent = c.unit;
    return secs;
  }

  return {
    R_TICKS: R_TICKS, R_PROGRESS: R_PROGRESS, R_CONTRIB: R_CONTRIB,
    elapsedSeconds: elapsedSeconds,
    progressPercent: progressPercent,
    formatElapsed: formatElapsed,
    formatClock: formatClock,
    secondAngle: secondAngle,
    contributionSegments: contributionSegments,
    dotPosition: dotPosition,
    setSegment: setSegment,
    setFlip: setFlip,
    ringMarkup: ringMarkup,
    paint: paint
  };
});
