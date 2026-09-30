/**
 * review.js —— 「回顾」块的入口链接：网关注入了页签配置（window.HONEYCOMB_NAV）时，没装的那一页的链接藏起来，
 * 不出点了 404 的死链。没注入（单测、直接打开文件）就都留着。
 */
(function () {
  "use strict";
  var nav = window.HONEYCOMB_NAV;
  if (!nav || !Array.isArray(nav.tabs)) return;
  var base = window.HONEYCOMB_BASE || "/";
  document.querySelectorAll("[data-needs]").forEach(function (li) {
    var want = base + li.getAttribute("data-needs");
    li.hidden = !nav.tabs.some(function (t) { return t.href === want; });
  });
})();
