/**
 * ignores.js —— 「被忽略任务」面板（nexus-core 契约 v2.22「忽略并记住」）
 *
 * 仓主 2026-10-09：「记住：忽略 xx 记录」，不进圆环、不进泳道，再放一个可展开的「被忽略任务」菜单。
 * 规则在服务端：待确认建议里点「忽略并记住」（suggestions.js 调 window.assistantIgnores.add）→ 服务端存一条
 * {程序, 可选的标题片段}，以后匹配的窗口不产生建议、不成为焦点 / 自动跟踪目标、不对会话（也就不长「你在看」）。
 * 本面板列这些规则：匹配什么、从什么时候起、已忽略多少条 / 多少分钟（只是计数；被忽略窗口的标题不再保存，规则里存的是你填的匹配文字，只有登录的人看得到），
 * 「取消忽略」= 删规则（以后的窗口照常；已经丢掉的不会回来）。
 *
 * - 端点读取失败 / 404（后端早于 v2.22）→ 整块不出现，「忽略并记住」按钮也不出现；一条规则都没有 → 同样不出现。
 * - 程序名、标题片段是人填的 / 从屏幕抓来的，一律 textContent。不轮询：打开页面、每次操作后、标签页重新可见时拉。
 * 对外只挂 window.assistantIgnores（add / 纯函数 describe，给 suggestions.js 与单测用）。
 */
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/core/activity/ignores";

  // 「chrome」/「chrome · 标题含“银行”」
  function describe(rule) {
    return rule.app + (rule.titleContains ? " · 标题含“" + rule.titleContains + "”" : "（全部窗口）");
  }

  function pad2(n) { return String(n).padStart(2, "0"); }
  function since(iso) {
    var d = new Date(iso);
    return isNaN(d.getTime()) ? "" : pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) + " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }
  function minutes(seconds) {
    var m = Math.round((Number(seconds) || 0) / 60);
    return m < 1 ? "不到 1 分" : m + " 分";
  }

  var api = { describe: describe, supported: undefined };   // undefined = 还没探过；false = 后端没有这个端点
  window.assistantIgnores = api;

  var panel = document.getElementById("ignores-panel");
  var listEl = document.getElementById("ignores-list");
  var countEl = document.getElementById("ignores-count");
  var msgEl = document.getElementById("ignores-message");
  if (!panel || !listEl) return;

  function say(text, isError) {
    msgEl.textContent = text || "";
    msgEl.hidden = !text;
    msgEl.classList.toggle("is-error", Boolean(isError));
  }

  async function send(method, url, body) {
    try {
      var init = { method: method, credentials: "same-origin", headers: { Accept: "application/json" } };
      if (body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(body); }
      var res = await fetch(url, init);
      var data = await res.json().catch(function () { return null; });
      return { ok: res.ok, status: res.status, data: data, detail: (data && data.detail) || "" };
    } catch (e) {
      return { ok: false, status: 0, data: null, detail: "" };
    }
  }

  function render(items) {
    listEl.textContent = "";
    countEl.textContent = items.length ? "(" + items.length + ")" : "";
    items.forEach(function (rule) {
      var li = document.createElement("li");
      li.className = "unc-row ign-row";
      var what = document.createElement("span");
      what.className = "ign-what";
      what.textContent = describe(rule);
      var meta = document.createElement("span");
      meta.className = "ign-meta mono";
      meta.textContent = "自 " + since(rule.createdAt) + " 起 · 已忽略 " + rule.hits + " 条 · " + minutes(rule.seconds) +
        (rule.purgeFailed ? " · 旧记录清理多次失败，可能还留着；再点一次「忽略并记住」会重清" : "");
      var undo = document.createElement("button");
      undo.type = "button";
      undo.className = "btn btn-ghost ign-undo";
      undo.textContent = "取消忽略";
      undo.setAttribute("aria-label", "取消忽略：" + describe(rule));
      undo.addEventListener("click", async function () {
        undo.disabled = true;
        var r = await send("DELETE", API + "/" + encodeURIComponent(rule.id));
        say(r.ok ? "已取消忽略 " + describe(rule) + "：以后这样的窗口照常记录。" : "没有取消成功：" + (r.detail || "请稍后再试"), !r.ok);
        await load();
      });
      li.appendChild(what);
      li.appendChild(meta);
      li.appendChild(undo);
      listEl.appendChild(li);
    });
    panel.hidden = items.length === 0;
  }

  async function load() {
    var r = await send("GET", API);
    if (!r.ok || !r.data || !Array.isArray(r.data.items)) { panel.hidden = true; api.supported = false; return; }
    api.supported = true;
    render(r.data.items);
  }

  // 建一条规则（suggestions.js 用）：返回 {ok, detail, removed, created}；成功后面板跟着刷新。
  api.add = async function (app, titleContains) {
    var r = await send("POST", API, titleContains ? { app: app, titleContains: titleContains } : { app: app });
    if (r.ok) { await load(); }
    return { ok: r.ok, status: r.status, detail: r.detail, removed: r.ok ? r.data.removed : 0, created: r.ok ? r.data.created : false };
  };

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") load();
  });
  // 先探一次端点，suggestions.js 才知道要不要出「忽略并记住」；它在按钮点击时才读 supported
  api.ready = load();
})();
