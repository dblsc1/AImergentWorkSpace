// hive · hex-claim-card.js —— 新建任务 / 项目后的补记卡（onboarding.v1「五」「八」3）。
// 上半是纯函数（排序 / 默认勾选 / 文案 / 请求组装，给 hex-claim-card.test.js 用），下半是 DOM（文字一律 textContent，只显示 displayName）。
(function () {
  "use strict";

  var CANDIDATES = "api/core/activity/unclassified/candidates?";
  var CLAIM = "api/core/activity/blocks/claim";

  function sortCandidates(items) {
    return (items || []).map(function (x, i) { return { x: x, i: i }; })
      .sort(function (a, b) { return b.x.seconds - a.x.seconds || a.i - b.i; })
      .map(function (e) { return e.x; });
  }
  function defaultChecked(item) { return item.match === "agent" || item.match === "name"; }
  function defaultCheckedKeys(items) { return items.filter(defaultChecked).map(function (x) { return x.blockKey; }); }

  function hoursText(seconds) {
    if (!(seconds >= 360)) return "不到 6 分钟";
    return String(Math.round(seconds / 360) / 10) + " 小时";
  }
  function titleText(items) {
    var total = items.reduce(function (s, x) { return s + x.seconds; }, 0), h = hoursText(total);
    return "找到 " + items.length + " 个窗口块，共" + (total >= 360 ? " " : "") + h + "，可能属于它";
  }
  function resultText(totalSeconds) { return "已记进 " + hoursText(totalSeconds); }

  function candidatesPath(target) {
    return CANDIDATES + (target.taskId ? "taskId=" + encodeURIComponent(target.taskId) : "projectId=" + encodeURIComponent(target.projectId));
  }
  function claimBody(target, blockKeys) {
    var b = target.taskId ? { taskId: target.taskId } : { projectId: target.projectId };
    b.blockKeys = blockKeys; b.remember = true;
    return b;
  }

  var api = { sortCandidates: sortCandidates, defaultChecked: defaultChecked, defaultCheckedKeys: defaultCheckedKeys, hoursText: hoursText,
    titleText: titleText, resultText: resultText, candidatesPath: candidatesPath, claimBody: claimBody };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (typeof document === "undefined" || typeof window === "undefined") return;
  window.NexusTableHexClaimCard = api;

  // ── DOM ──
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function json(res) { return res.json().catch(function () { return null; }); }

  function toast(text) {
    var t = el("div", "hcc-toast", text);
    t.setAttribute("role", "status");
    document.body.appendChild(t);
    window.setTimeout(function () { t.remove(); }, 4000);
  }

  function open(target, items) {
    var rows = sortCandidates(items), on = {};
    rows.forEach(function (x) { on[x.blockKey] = defaultChecked(x); });
    var wrap = el("div", "hcc-wrap"), card = el("div", "hcc-card");
    card.setAttribute("role", "dialog"); card.setAttribute("aria-label", "补记");
    var title = el("div", "hcc-title", titleText(rows)), list = el("div", "hcc-list"), err = el("div", "hcc-err");
    err.setAttribute("role", "alert");
    var go = el("button", "hcc-go", "记进去"), no = el("button", "hcc-no", "不用"), acts = el("div", "hcc-acts");
    go.type = no.type = "button"; go.dataset.hcc = "claim"; no.dataset.hcc = "close";
    function sync() { go.disabled = !rows.some(function (x) { return on[x.blockKey]; }); }
    rows.forEach(function (x) {
      var lab = el("label", "hcc-row"), cb = el("input"), nm = el("span", "hcc-name", x.displayName), tm = el("span", "hcc-time", hoursText(x.seconds));
      cb.type = "checkbox"; cb.checked = on[x.blockKey];
      cb.addEventListener("change", function () { on[x.blockKey] = cb.checked; sync(); });
      lab.appendChild(cb); lab.appendChild(nm); lab.appendChild(tm); list.appendChild(lab);
    });
    function close() { wrap.remove(); }
    no.addEventListener("click", close);
    go.addEventListener("click", function () {
      var keys = rows.filter(function (x) { return on[x.blockKey]; }).map(function (x) { return x.blockKey; });
      if (!keys.length) return;
      go.disabled = true; err.textContent = "";
      fetch(BASE + CLAIM, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(claimBody(target, keys)) })
        .then(function (res) {
          return json(res).then(function (b) {
            if (!res.ok) { var d = b && b.detail; throw new Error(d ? (typeof d === "string" ? d : JSON.stringify(d)) : "HTTP " + res.status); }
            close(); toast(resultText(b && b.totalSeconds || 0));
          });
        })
        .catch(function (e) { err.textContent = "没记成：" + ((e && e.message) || e); sync(); });
    });
    acts.appendChild(no); acts.appendChild(go);
    card.appendChild(title); card.appendChild(list); card.appendChild(err); card.appendChild(acts);
    wrap.appendChild(card); document.body.appendChild(wrap);
    sync(); go.focus();
  }

  // target = {taskId} 或 {projectId}；读不到 / 没有候选就什么都不弹
  function offer(target) {
    if (!target || !(target.taskId || target.projectId)) return Promise.resolve();
    return fetch(BASE + candidatesPath(target)).then(function (res) { return res.ok ? json(res) : null; })
      .then(function (b) { if (b && b.items && b.items.length) open(target, b.items); })
      .catch(function () { /* 补记是锦上添花，读不到不打扰 */ });
  }
  api.offer = offer;
})();
