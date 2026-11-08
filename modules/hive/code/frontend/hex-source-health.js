// hive · hex-source-health.js —— 信息源体检的提示（hive.stage.v1「信息源体检」，nexus-core v2.47）。
// 上半是纯函数（给 hex-source-health.test.js 用），下半是 DOM（文字一律 textContent）。
(function () {
  "use strict";

  var AW = { label: "ActivityWatch 下载", url: "https://activitywatch.net/downloads/" };
  var ISSUES = {
    aw_down: { text: "连不上 ActivityWatch", fix: "先启动 ActivityWatch，并设成开机自启。", links: [AW] },
    window_missing: { text: "最近 30 分钟没有窗口记录", fix: "确认 ActivityWatch 里的窗口记录器 aw-watcher-window 在运行。", links: [{ label: "记录器说明", url: "https://docs.activitywatch.net/en/latest/watchers.html" }] },
    window_wayland: { text: "Wayland 桌面读不到窗口", fix: "改用 awatcher（替代 aw-watcher-window）；GNOME 还要装扩展 Focused Window D-Bus。", links: [{ label: "awatcher", url: "https://github.com/2e3s/awatcher" }, { label: "Focused Window D-Bus", url: "https://extensions.gnome.org/extension/5592/" }] },
    afk_missing: { text: "没有离开检测", fix: "启动 aw-watcher-afk（awatcher 已包含）。", links: [{ label: "记录器说明", url: "https://docs.activitywatch.net/en/latest/watchers.html" }] },
    web_extension_missing: { text: "看不到浏览器里的网页", fix: "给浏览器装 ActivityWatch 网页记录扩展。", links: [{ label: "Chrome", url: "https://chromewebstore.google.com/detail/activitywatch-web-watcher/nglaklhklhcoonedhgnpgddginnjdadi" }, { label: "Firefox", url: "https://addons.mozilla.org/en-US/firefox/addon/aw-watcher-web/" }] },
    titles_pseudonymized: { text: "窗口标题已换成代号，没法按标题归类", fix: "在「自动记录」的隐私里把标题改成保留，或只按程序归类。", links: [] },
    terminal_title_no_path: { text: "终端标题里没有目录", fix: "用连接脚本的终端标题，让标题带上当前目录。", links: [] }
  };

  // 取最近一次体检的设备；没有任何设备报过 / 全好 = 空。未知编码（新服务端）跳过。
  function problems(devices) {
    var best = null;
    (devices || []).forEach(function (d) {
      var s = d && d.sourceHealth;
      if (s && (!best || s.at > best.at)) best = s;
    });
    if (!best) return [];
    return best.issues.filter(function (c) { return ISSUES[c]; }).map(function (c) { return { code: c, text: ISSUES[c].text, fix: ISSUES[c].fix, links: ISSUES[c].links }; });
  }
  function hintText(list) { return list.length ? "窗口信息不全 · " + list.length + " 项" : ""; }

  var H = { problems: problems, hintText: hintText };
  if (typeof module !== "undefined" && module.exports) { module.exports = H; return; }
  window.HexSourceHealth = H;

  // ── DOM：render(list) 画小提示按钮与点开的清单 ──
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  H.mount = function (host) {
    var btn = el("button", "st-srchint"), pop = el("div", "st-srcpop st-card");
    btn.type = "button"; btn.id = "stSrcHint"; btn.hidden = true;
    pop.id = "stSrcPop";
    btn.addEventListener("click", function () { pop.classList.toggle("open"); });
    host.appendChild(btn); host.appendChild(pop);
    return function render(list) {
      btn.hidden = !list.length;
      if (!list.length) pop.classList.remove("open");
      btn.textContent = hintText(list);
      pop.textContent = "";
      list.forEach(function (p) {
        var row = el("div", "st-src-row");
        row.appendChild(el("b", "", p.text));
        row.appendChild(el("span", "", p.fix));
        p.links.forEach(function (l) {
          var a = el("a", "", l.label);
          a.href = l.url; a.target = "_blank"; a.rel = "noopener noreferrer";
          row.appendChild(a);
        });
        pop.appendChild(row);
      });
    };
  };
})();
