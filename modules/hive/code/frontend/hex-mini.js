// hive · hex-mini.js —— /hive/mini（桌宠浮窗里的蜂巢缩略页，desktop.pet.v1「四」）。
// 上半是纯函数（给 hex-mini.test.js 用），下半是 DOM（文字一律 textContent）。
// 人登录后的会话，不带 Authorization；蜂巢几何复用 hex-minihive.js，拖放请求复用 hex-stage.js 的 dropRequest。
(function () {
  "use strict";

  var node = typeof module !== "undefined" && module.exports;
  var Stage = node ? require("./hex-stage.js") : window.NexusTableHexStage;
  var GONE = "这个窗口已经离开";
  var REASONS = { 409: "这个窗口刚有变化，请再试一次", 404: "找不到这个窗口了" };

  function minutesText(sec) { return Math.max(0, Math.floor((sec || 0) / 60)) + " 分钟"; }
  function cardView(ask, left) {
    if (!ask || left) return { kind: "gone", text: GONE };
    return { kind: "ask", name: ask.displayName || "未命名窗口", dwell: minutesText(ask.focusedSeconds) };
  }
  function assignRequest(ask, projectId) {
    return Stage.dropRequest({ kind: "block", block: { date: ask.date, blockKey: ask.blockKey, seq: ask.seq } }, projectId);
  }
  function answerRequest(ask) { return { method: "POST", path: "activity/live-ask/answer", body: { blockKey: ask.blockKey, answer: "unmanaged" } }; }
  // assign 409 后拿新 live-ask 比：块还在且 seq 变了就重试；块没了 = 离开；seq 没变重试也没用
  function retryPlan(old, fresh) {
    if (!fresh || fresh.blockKey !== old.blockKey) return { action: "gone" };
    return fresh.seq !== old.seq ? { action: "retry", ask: fresh } : { action: "fail" };
  }
  function reasonText(status) { return REASONS[status] || "没成功，请再试一次"; }
  function shortName(name, n) {
    var s = String(name || "").trim(), max = n || 4;
    return !s ? "未命名" : s.length > max ? s.slice(0, max) + "…" : s;
  }

  var api = { minutesText: minutesText, cardView: cardView, assignRequest: assignRequest, answerRequest: answerRequest, retryPlan: retryPlan, reasonText: reasonText, shortName: shortName };
  if (node) { module.exports = api; return; }
  window.NexusTableHexMiniPage = api;
  var root = document.getElementById("miniRoot");
  if (!root) return;

  // ── DOM ───────────────────────────────────────────────────────────
  var NS = "http://www.w3.org/2000/svg", BASE = window.HONEYCOMB_BASE || "/", SIZE = 50, PX = 340;
  var Mini = window.NexusTableHexMini, Data = window.NexusTableHexData;
  var ask = null, left = false, finished = false, busy = false, hive = null;
  var cardBox = document.getElementById("miniCard"), hiveBox = document.getElementById("miniHive"), noteBox = document.getElementById("miniNote");

  function el(tag, cls, text) { var n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; }
  function sv(tag, attrs, cls) {
    var n = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
    if (cls) n.setAttribute("class", cls);
    return n;
  }
  function call(req) {
    var init = { method: req.method, credentials: "same-origin", headers: { Accept: "application/json" } };
    if (req.body) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(req.body); }
    return fetch(BASE + "api/core/" + req.path, init).then(function (r) {
      return r.json().catch(function () { return null; }).then(function (data) { return { status: r.status, ok: r.ok, data: data }; });
    }, function () { return { status: 0, ok: false, data: null }; });
  }
  function note(text) { noteBox.textContent = text || ""; noteBox.hidden = !text; }
  function showLogin() {
    root.textContent = "";
    var a = el("a", "hmi-login", "登录");
    a.href = BASE + "login/";
    root.appendChild(a);
  }
  function finish() {
    finished = true;
    window.postMessage({ type: "hive-mini-done" }, "*");
    renderCard();
  }

  function renderCard() {
    var v = cardView(ask, left);
    cardBox.textContent = "";
    cardBox.className = "hmi-card is-" + v.kind;
    if (v.kind === "gone") { cardBox.appendChild(el("div", "hmi-gone", v.text)); return; }
    cardBox.appendChild(el("div", "hmi-name", v.name));
    cardBox.appendChild(el("div", "hmi-dwell", v.dwell));
    var skip = el("button", "btn hmi-skip", "不管它");
    skip.type = "button";
    skip.disabled = finished;
    skip.addEventListener("click", onSkip);
    cardBox.appendChild(skip);
  }
  function renderHive(tree) {
    hive = Data.buildHoneycomb(tree, {});
    var names = {};
    (tree.projects || []).forEach(function (p) { names[p.id] = p.name; });
    var g = Mini.geom(hive.cells, SIZE, PX), svg = sv("svg", { viewBox: g.vb.map(function (n) { return n.toFixed(1); }).join(" "), role: "img" }, "hmi-svg");
    g.cells.forEach(function (c) {
      var grp = sv("g", {}, "hmi-cell" + (c.empty ? " is-empty" : ""));
      var poly = sv("polygon", { points: Mini.hexPoints(c.cx, c.cy, SIZE) }, "hm-cell" + (c.empty ? " is-empty" : ""));
      if (c.color) poly.style.setProperty("--zone-raw", c.color);
      poly.style.setProperty("--zone-mix", (c.mix == null ? 40 : c.mix) + "%");
      grp.appendChild(poly);
      if (c.projectId) {
        grp.setAttribute("data-project-id", c.projectId);
        var t = sv("text", { x: c.cx.toFixed(1), y: c.cy.toFixed(1), "text-anchor": "middle", "dominant-baseline": "central" }, "hmi-label");
        t.textContent = shortName(names[c.projectId]);
        grp.appendChild(t);
      }
      svg.appendChild(grp);
    });
    hiveBox.textContent = "";
    hiveBox.appendChild(svg);
  }

  // ── 动作 ──
  function fail(status) { note(reasonText(status)); }
  function onSkip() {
    if (busy || finished || !ask) return;
    busy = true; note("");
    call(answerRequest(ask)).then(function (r) {
      busy = false;
      if (r.status === 401) return showLogin();
      if (r.ok) finish(); else fail(r.status);
    });
  }
  function assign(projectId, again) {
    var req = assignRequest(ask, projectId);
    if (!req || busy || finished) return;
    busy = true; note("");
    call(req).then(function (r) {
      if (r.status === 401) { busy = false; return showLogin(); }
      if (r.ok) { busy = false; return finish(); }
      if (r.status !== 409 || again) { busy = false; return fail(r.status); }
      call({ method: "GET", path: "activity/live-ask" }).then(function (f) {
        busy = false;
        var plan = retryPlan(ask, f.ok && f.data ? f.data.ask : null);
        if (plan.action === "gone") { left = true; renderCard(); finish(); }
        else if (plan.action === "retry") { ask = plan.ask; assign(projectId, true); }
        else fail(409);
      });
    });
  }

  // ── 拖：Pointer Events，卡跟着指针走，放在项目格上 = assign；放在别处卡回原位 ──
  function cellUnder(x, y) {
    var prev = cardBox.style.pointerEvents;
    cardBox.style.pointerEvents = "none";
    var n = document.elementFromPoint(x, y);
    cardBox.style.pointerEvents = prev;
    return n && n.closest ? n.closest("[data-project-id]") : null;
  }
  function mark(cell) {
    [].forEach.call(hiveBox.querySelectorAll(".is-target"), function (n) { n.classList.remove("is-target"); });
    if (cell) cell.classList.add("is-target");
  }
  cardBox.addEventListener("pointerdown", function (e) {
    if (!ask || left || finished || busy || e.button > 0 || (e.target.closest && e.target.closest("button"))) return;
    var x0 = e.clientX, y0 = e.clientY, moved = false, id = e.pointerId;
    cardBox.setPointerCapture(id);
    function move(ev) {
      if (ev.pointerId !== id) return;
      var dx = ev.clientX - x0, dy = ev.clientY - y0;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 4) return;
      moved = true; cardBox.classList.add("is-dragging");
      cardBox.style.transform = "translate(" + dx + "px," + dy + "px)";
      mark(cellUnder(ev.clientX, ev.clientY));
    }
    function end(ev) {
      if (ev.pointerId !== id) return;
      cardBox.removeEventListener("pointermove", move); cardBox.removeEventListener("pointerup", end); cardBox.removeEventListener("pointercancel", end);
      var cell = ev.type === "pointerup" && moved ? cellUnder(ev.clientX, ev.clientY) : null;
      mark(null); cardBox.classList.remove("is-dragging"); cardBox.style.transform = "";
      if (cell) assign(cell.getAttribute("data-project-id"), false);
    }
    cardBox.addEventListener("pointermove", move); cardBox.addEventListener("pointerup", end); cardBox.addEventListener("pointercancel", end);
  });

  window.addEventListener("message", function (e) {
    if (e.data && e.data.type === "hive-mini-card" && e.data.state === "left") { left = true; renderCard(); }
  });

  Promise.all([call({ method: "GET", path: "views/tree" }), call({ method: "GET", path: "activity/live-ask" })]).then(function (r) {
    if (r[0].status === 401 || r[1].status === 401) return showLogin();
    if (r[0].ok && r[0].data) renderHive(r[0].data); else fail(r[0].status);
    if (r[1].ok && r[1].data) ask = r[1].data.ask || null;
    else if (!r[1].ok) fail(r[1].status);
    renderCard();
  });
})();
