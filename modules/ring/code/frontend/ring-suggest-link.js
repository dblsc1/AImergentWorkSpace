/**
 * ring-suggest-link.js —— 计时页上的「N 条待确认 → AI助理」小链接（nexus-core v2.2 活动建议）
 *
 * 待确认面板本身 2026-09-30 搬去了「AI助理」页（modules/assistant）；计时页只报个数、给个入口。
 * GET suggestions?status=pending&limit=1 只为拿 total。0 条、404（后端早于 v2.2）、出错、
 * 或网关注入的页签里没有「AI助理」（没装那一页，链过去是 404）→ 不出现。
 * 打开页面、标签页重新可见时各拉一次，不轮询。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var linkEl = document.getElementById("suggest-link");
  if (!linkEl) return;
  var nav = window.HONEYCOMB_NAV;
  if (nav && Array.isArray(nav.tabs) &&
      !nav.tabs.some(function (t) { return t.href === BASE + "assistant/"; })) return;

  async function load() {
    var total = 0;
    try {
      var res = await fetch(BASE + "api/core/activity/suggestions?status=pending&limit=1");
      var body = res.ok ? await res.json() : null;
      total = body && typeof body.total === "number" ? body.total : 0;
    } catch (err) { total = 0; }
    linkEl.textContent = total + " 条待确认 → AI助理";
    linkEl.hidden = total <= 0;
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") load();
  });
  load();
})();
