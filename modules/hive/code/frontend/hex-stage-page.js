// hive · hex-stage-page.js —— 蜂巢新布局的页面接线（hive.stage.v1「一」「二」「四」）。
// 选择与计算全在 hex-stage.js / hex-pet-pick.js（纯逻辑）；这里只读数据、摆元素、接 Pointer Events 拖动。
// 不依赖 hex-app.js 的内部：格子位置一律从 DOM（.hex-cell[data-project-id]）量。
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/core/";
  var St = window.NexusTableHexStage, Rec = window.NexusTableHexRecord, Pk = window.NexusTableHexPetPick;
  var shell = document.querySelector(".board-shell");
  if (!St || !Rec || !Pk || !shell) return;

  var POLL_MS = 5000, TICK_MS = 1000, PET_MS = 100, PET_SPEED = 18;
  var WANDER = { x: 24, y: 70, w: 340, h: 170 };
  var DAY_FROM_H = 6, DAY_SPAN_H = 18;
  var STATUS_TEXT = { working: "工作中", waiting: "等你", idle: "空闲" };

  var layer = document.createElement("div");
  layer.className = "st-layer";
  layer.id = "stageLayer";
  layer.innerHTML =
    '<div class="st-ring" id="stRing"></div>' +
    '<div class="st-clock st-card" id="stClock" role="button" tabindex="0" aria-label="计时"></div>' +
    '<div class="st-strips" id="stStrips"></div>' +
    '<div class="st-bubble" id="stBubble" aria-hidden="true"></div>' +
    '<div class="st-more" id="stMore" hidden></div>' +
    '<div class="st-drawer st-card" id="stDrawer"></div>' +
    '<div class="st-bucket" id="stBucket"><button type="button" class="st-bucket-btn" id="stBucketBtn" aria-label="未分类"></button><div class="st-bucket-label" id="stBucketLabel"></div></div>' +
    '<div class="st-toast" id="stToast" role="status" aria-live="polite"></div>' +
    '<div class="st-veil" id="stVeil" hidden><div class="st-panel st-card" id="stPanel" role="dialog" aria-modal="true"></div></div>';
  shell.appendChild(layer);
  document.body.classList.add("stage-on");
  var $ = function (id) { return document.getElementById(id); };

  // ── 数据 ───────────────────────────────────────────────
  var data = { lanes: null, current: null, tree: null, settings: null, blocks: null, unplaced: [] };
  var busy = 0, dragging = 0, panelPid = null;

  function getJson(path) {
    return fetch(API + path).then(function (res) {
      return res.ok ? res.json().catch(function () { return null; }) : null;
    }).catch(function () { return null; });
  }
  function send(req) {
    var init = { method: req.method };
    if (req.body !== undefined) { init.headers = { "Content-Type": "application/json" }; init.body = JSON.stringify(req.body); }
    return fetch(API + req.path, init).then(function (res) {
      if (res.ok && req.path.indexOf("timer/") === 0) window.dispatchEvent(new Event("honeycomb:timer-changed"));
      return res.json().catch(function () { return null; }).then(function (body) { return { status: res.status, body: body }; });
    });
  }

  function projects() { return (data.tree && data.tree.projects) || []; }
  function projectName(id) {
    var p = projects().filter(function (x) { return x.id === id; })[0];
    return p ? p.name : id;
  }
  function projectOfTask(taskId) {
    var ps = projects();
    for (var i = 0; i < ps.length; i++) {
      if ((ps[i].tasks || []).some(function (t) { return t.id === taskId; })) return ps[i].id;
    }
    return null;
  }
  var stage = St.createStage({ send: send, now: function () { return Date.now(); }, projectName: projectName });

  function packsByTool() {
    var p = data.settings && data.settings.pet && data.settings.pet.packsByTool;
    return p || Pk.DEFAULT_PACKS_BY_TOOL;
  }
  // lanes 的运行没有 phase 键：当前相位 = phases 的最后一条
  function runsOf() {
    return ((data.lanes && data.lanes.agents) || []).map(function (r) {
      var ph = r.phases || [];
      return r.phase || !ph.length ? r : Object.assign({}, r, { phase: ph[ph.length - 1].phase });
    });
  }
  function ms(s) { var t = s ? Date.parse(s) : NaN; return isNaN(t) ? 0 : t; }
  function dayStart() { return ms(data.lanes && data.lanes.windowStart); }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function hms(sec) { sec = Math.max(0, Math.floor(sec)); return pad(Math.floor(sec / 3600)) + ":" + pad(Math.floor(sec / 60) % 60) + ":" + pad(sec % 60); }
  function dur(sec) { var m = Math.floor(Math.max(0, sec) / 60), h = Math.floor(m / 60); return h ? h + " 小时 " + (m % 60) + " 分" : m + " 分"; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); }

  function humanTodaySeconds(now) {
    var lanes = data.lanes, human = lanes && lanes.human, from = dayStart(), sum = 0;
    if (!human) return 0;
    (human.sessions || []).forEach(function (s) {
      var a = Math.max(ms(s.startAt), from), b = Math.min(ms(s.endAt), now);
      if (b > a) sum += (b - a) / 1000;
    });
    if (human.running) { var a2 = Math.max(ms(human.running.startAt), from); if (now > a2) sum += (now - a2) / 1000; }
    return Math.floor(sum);
  }

  // ── 几何：全部从 DOM 量，换算到层内坐标（层可能被 zoom 缩小）──
  function zoom() { return parseFloat(shell.style.zoom) || 1; }
  function toLayer(x, y) {
    var r = layer.getBoundingClientRect(), k = zoom();
    return { x: (x - r.left) / k, y: (y - r.top) / k };
  }
  function cellEl(pid) { return document.querySelector('.hex-cell[data-project-id="' + (window.CSS && CSS.escape ? CSS.escape(pid) : pid) + '"]'); }
  function rectCenter(el) {
    var r = el.getBoundingClientRect(), p = toLayer(r.left + r.width / 2, r.top + r.height / 2);
    return { x: p.x, y: p.y, w: r.width / zoom(), h: r.height / zoom() };
  }
  function hiveCenter() {
    var c = document.querySelector(".hex-cell.hex-center");
    return c ? rectCenter(c) : { x: layer.clientWidth / 2, y: layer.clientHeight / 2, w: 0, h: 0 };
  }
  function cellAt(x, y) {
    var a = document.elementsFromPoint(x, y);
    for (var i = 0; i < a.length; i++) {
      var c = a[i].closest && a[i].closest(".hex-cell[data-project-id]");
      if (c) return c;
    }
    return null;
  }
  function place(el, p) { el.style.left = p.x + "px"; el.style.top = p.y + "px"; }

  var swallow = false;
  document.addEventListener("click", function (ev) { if (swallow) { swallow = false; ev.stopPropagation(); ev.preventDefault(); } }, true);

  // ── 拖动（Pointer Events）。item() 返回 null = 不可拖，什么都不发生 ──
  function drag(handle, o) {
    var id = null, sx = 0, sy = 0, moved = false, hot = null, item = null, sprout = null, lx = 0, ly = 0;
    var Sp = window.NexusTableHexSprout;
    function lit(c) { if (c !== hot) { if (hot) hot.classList.remove("st-hot"); hot = c; if (hot) hot.classList.add("st-hot"); } }
    handle.addEventListener("pointerdown", function (ev) {
      if (id != null || (ev.button != null && ev.button > 0)) return;
      item = o.item();
      if (!item && !o.onClick) return;
      id = ev.pointerId; sx = ev.clientX; sy = ev.clientY; moved = false;
      window.addEventListener("keydown", onEsc);
      try { handle.setPointerCapture(id); } catch (e) { /* 合成事件抓不到也照拖 */ }
      ev.preventDefault();
    });
    handle.addEventListener("pointermove", function (ev) {
      if (id !== ev.pointerId || !item) return;
      var dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (!moved) {
        if (Pk.pressKind(dx, dy) === "click") return;
        moved = true; dragging++; o.el.classList.add("st-dragging");
        if (o.onGrab) o.onGrab();
      }
      lx = dx / zoom(); ly = dy / zoom();
      o.el.style.translate = lx + "px " + ly + "px";
      lit(item.kind === "free" ? null : cellAt(ev.clientX, ev.clientY));   // 格子边宠物松手不改数据，不高亮
      if (item.kind === "block" && Sp) sprout = blankSprout(ev.clientX, ev.clientY, sprout);
    });
    function onEsc(ev) { if (ev.key === "Escape" && id != null && moved) end({ pointerId: id }, true); }
    function end(ev, cancel) {
      if (id !== ev.pointerId) return;
      id = null;
      window.removeEventListener("keydown", onEsc);
      lit(null);
      var grown = sprout; sprout = null;
      if (moved) { swallow = true; window.setTimeout(function () { swallow = false; }, 50); }   // 松手那下 click 不能落到空白处把刚开的表单收掉
      if (!moved) { if (!cancel && o.onClick) o.onClick(); return; }
      var target = cancel ? null : cellAt(ev.clientX, ev.clientY);
      o.el.classList.remove("st-dragging"); dragging--;
      if (o.onRelease) { var back = o.onRelease(target, cancel, lx, ly); if (back === "keep") return; }
      var pid = target ? target.dataset.projectId : null;
      // 拖块放在分区空白处 = 建新项目（同长按建项目的那一格与表单），建好后把块记进去
      if (!pid && grown && !cancel && Sp.openCreate({ name: item.block.displayName || "", onCreated: function (p) {
        busy++;
        getJson("views/tree").then(function (t) { if (t) data.tree = t; return stage.drop(item, p.id); }).then(function (r) { busy--; render(); if (r && r.ok) refresh(); });
      } })) { o.el.style.translate = ""; return; }
      if (Sp && item.kind === "block") Sp.removeSprout();
      busy++;
      stage.drop(item, pid).then(function (r) {
        o.el.style.translate = "";
        if (r && r.reverted && pid) o.el.classList.add("st-bounce");
        busy--;
        render();
        if (r && r.ok) refresh();
      });
    }
    handle.addEventListener("pointerup", function (ev) { end(ev, false); });
    handle.addEventListener("pointercancel", function (ev) { end(ev, true); });
  }

  // 指针在分区空白处（不在格子、不在本层元素上，且在 #hive 里）：长出同长按建项目一样的那一格，并同样「升起」
  function blankSprout(x, y, prev) {
    var Sp = window.NexusTableHexSprout, hit = null;
    document.elementsFromPoint(x, y).some(function (e) { if (e.closest("#stageLayer")) return false; hit = e; return true; });
    var blank = hit && hit.closest("#hive") && !hit.closest(".hex-cell"), el = blank ? Sp.preview(x, y) : (Sp.removeSprout(), null);
    if (el && el !== prev && !(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches)) {
      el.classList.add("hd-rise"); void el.offsetWidth; el.classList.add("is-rising");
    }
    return el;
  }

  // ── 宠物素材：pack.json 读不到就用内置占位形象 ─────────
  var packs = {};
  function loadPack(kind) {
    if (kind in packs) return;
    packs[kind] = null;
    fetch(BASE + "hive/pets/" + encodeURIComponent(kind) + "/pack.json").then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (p) {
      if (p && p.moods && typeof p.moods === "object") {
        var ok = function (f) { return typeof f === "string" && f !== "" && f.split("/").every(function (seg) { return seg !== "" && seg !== "." && seg !== ".."; }); };
        var moods = {};
        Object.keys(p.moods).forEach(function (m) { moods[m] = Array.isArray(p.moods[m]) ? p.moods[m].filter(ok) : []; });
        packs[kind] = { moods: moods }; renderPets();
      }
    }).catch(function () { /* 素材还没下：占位形象 */ });
  }
  var PLACEHOLDER = '<svg class="st-art" width="56" height="52" viewBox="0 0 56 52" aria-hidden="true">' +
    '<ellipse cx="28" cy="30" rx="22" ry="18" fill="var(--attn, var(--focus))"/>' +
    '<path d="M10 16 L6 4 L19 11Z M46 16 L50 4 L37 11Z" fill="var(--attn, var(--focus))"/>' +
    '<circle cx="20" cy="27" r="3" fill="var(--panel)"/><circle cx="36" cy="27" r="3" fill="var(--panel)"/>' +
    '<circle cx="20.8" cy="27.4" r="1.4" fill="var(--ink)"/><circle cx="36.8" cy="27.4" r="1.4" fill="var(--ink)"/>' +
    '<path d="M23 36 q5 4 10 0" stroke="var(--ink)" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>';
  function mediaFor(pet) {
    var p = packs[pet.pack], list = p && p.moods && (p.moods[pet.mood] || p.moods.idle);
    if (!list || !list.length) return { key: "svg", html: PLACEHOLDER };
    var file = list[0], url = BASE + "hive/pets/" + encodeURIComponent(pet.pack) + "/" + file.split("/").map(encodeURIComponent).join("/");
    return { key: url, html: '<video class="st-art" src="' + esc(url) + '" autoplay loop muted playsinline></video>' };
  }

  // ── 干活的宠物（格子边）与左上角的宠物 ───────────────────
  var petEls = {}, wander = {};
  function petEl(runId) {
    var e = petEls[runId];
    if (e) return e;
    e = document.createElement("div");
    e.className = "st-pet"; e.dataset.runId = runId;
    e.innerHTML = '<div class="st-pet-in"><div class="st-tag"></div><div class="st-media"></div></div>';
    layer.appendChild(e);
    petEls[runId] = e;
    return e;
  }
  function fillPet(e, pet, run) {
    loadPack(pet.pack);
    var m = mediaFor(pet), slot = e.querySelector(".st-media");
    e.querySelector(".st-tag").textContent = pet.name;
    e.dataset.mood = pet.mood;
    e.__pet = pet;
    if (!slot.dataset.over && slot.dataset.key !== m.key) {
      slot.dataset.key = m.key; slot.innerHTML = m.html;
      var v = slot.querySelector("video");
      if (v) v.addEventListener("error", function () { slot.dataset.key = "svg"; slot.innerHTML = PLACEHOLDER; });
    }
    e.__run = run;
  }

  // ── 点 / 拖 / 走回：每只宠物的交互状态 ix[runId] = {mode: "drag"|"back"|null, from, to, t0, click, lastClick, walkClip, dragEnd}
  // renderPets 每轮只更新 e.__home（格子边的家）与名牌；有交互时不挪位置、不换动作，动作由 syncClip 管。
  var ix = {}, STAY_MS = Pk.STAY_MS, SQUASH_MS = 220;
  function reduced() { return !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches); }
  function ixOf(id) { return ix[id] || (ix[id] = { mode: null, click: null, lastClick: null, walkClip: null, dragEnd: -1e9 }); }
  function packMoods(e) { var p = e.__pet && packs[e.__pet.pack]; return (p && p.moods) || {}; }
  function wantClip(e) {
    var s = ix[e.dataset.runId], m = packMoods(e);
    if (!s || !e.__pet) return null;
    if (s.click) return { file: s.click, loop: false };
    if (s.mode === "drag" && m.drag && m.drag.length) return { file: m.drag[0], loop: true };
    if (s.mode === "back" && s.walking && s.walkClip) return { file: s.walkClip, loop: true };
    return null;
  }
  function syncClip(e) {
    var slot = e.querySelector(".st-media"), w = wantClip(e);
    if (!w) {
      if (slot.dataset.over) { delete slot.dataset.over; slot.dataset.key = ""; fillPet(e, e.__pet, e.__run); }
      return;
    }
    var url = BASE + "hive/pets/" + encodeURIComponent(e.__pet.pack) + "/" + w.file.split("/").map(encodeURIComponent).join("/");
    var key = "ix:" + (w.loop ? "L:" : "1:") + url;
    if (slot.dataset.key === key) return;
    slot.dataset.key = key; slot.dataset.over = "1";
    slot.innerHTML = '<video class="st-art" src="' + esc(url) + '" autoplay ' + (w.loop ? "loop " : "") + "muted playsinline></video>";
    var v = slot.querySelector("video"), s = ix[e.dataset.runId];
    v.addEventListener("error", function () {
      delete slot.dataset.over; slot.dataset.key = "svg"; slot.innerHTML = PLACEHOLDER;
      if (s) s.click = null;
    });
    if (!w.loop) v.addEventListener("ended", function () { if (s) s.click = null; syncClip(e); });
  }
  function squash(e) {
    if (reduced()) return;
    e.classList.remove("st-squash"); void e.offsetWidth; e.classList.add("st-squash");
    window.setTimeout(function () { e.classList.remove("st-squash"); }, SQUASH_MS);
  }
  function poke(e) {
    var id = e.dataset.runId, s = ixOf(id);
    if (!Pk.clickCounts(Date.now() - s.dragEnd)) return;
    var file = e.__pet && Pk.clickClip(packs[e.__pet.pack], s.lastClick, Math.random);
    squash(e);
    if (file) { s.click = s.lastClick = file; syncClip(e); }
  }
  function curPos(e) { return { x: parseFloat(e.style.left) || 0, y: parseFloat(e.style.top) || 0 }; }
  function setFlip(e, f) { e.classList.toggle("flip", f); }
  function startBack(e, from, afterMs) {
    var s = ixOf(e.dataset.runId);
    s.mode = "back"; s.from = from; s.to = e.__home; s.t0 = Date.now() - (afterMs || 0); s.walking = false;
    s.walkClip = (packMoods(e).walk || [])[0] || null;
    e.classList.add("st-away"); place(e, from); syncClip(e);
  }
  function endBack(e) {
    var s = ix[e.dataset.runId];
    if (s) { s.mode = null; s.walking = false; }
    e.classList.remove("st-away"); setFlip(e, false);
    if (e.__home) place(e, e.__home);
    syncClip(e);
  }
  function stepBack(now) {
    Object.keys(ix).forEach(function (id) {
      var e = petEls[id], s = ix[id];
      if (!e || s.mode !== "back" || !e.__home) return;
      if (e.__home.x !== s.to.x || e.__home.y !== s.to.y) {      // 家挪了：从当前位置走向新家，已在走就接着走
        var wasWalking = s.walking;
        s.from = curPos(e); s.to = e.__home; s.t0 = now - (wasWalking ? STAY_MS : now - s.t0);
      }
      if (reduced()) { if (now - s.t0 >= STAY_MS) endBack(e); return; }
      var w = Pk.walkBack(s.from, s.to, now - s.t0);
      if (w.phase === "done") { endBack(e); return; }
      var walking = w.phase === "walk";
      if (walking !== !!s.walking) { s.walking = walking; syncClip(e); }
      if (walking) { place(e, w); setFlip(e, w.flip); }
    });
  }
  function renderPets() {
    var runs = runsOf().map(function (r) {
      var dock = stage.petDock(r.runId);
      return dock && Pk.isLoose(r, { projectOfTask: projectOfTask }) ? Object.assign({}, r, { projectId: dock, taskId: null }) : r;
    });
    var opts = { packsByTool: packsByTool(), projectOfTask: projectOfTask };
    var work = Pk.pickWorkers(runs, opts), idle = Pk.pickIdle(runs, opts), keep = {};
    var hc = hiveCenter(), byCell = {};
    work.pets.forEach(function (p) { (byCell[p.projectId] = byCell[p.projectId] || []).push(p); });
    Object.keys(byCell).forEach(function (pid) {
      var el = cellEl(pid); if (!el) return;
      var c = rectCenter(el), dx = c.x - hc.x, dy = c.y - hc.y, l = Math.hypot(dx, dy) || 1, ux = dx / l, uy = dy / l;
      if (l === 1 && !dx && !dy) { ux = 0; uy = -1; }
      var n = byCell[pid].length, r = Math.max(c.w, c.h) / 2 * 0.9 + 26;
      byCell[pid].forEach(function (p, i) {
        var o = (i - (n - 1) / 2) * 58, e = petEl(p.runId);
        keep[p.runId] = 1;
        if (!e.classList.contains("is-work")) { e.classList.remove("is-loose"); e.classList.add("is-work"); }
        fillPet(e, p, runById(p.runId));
        e.__home = { x: c.x + ux * r - uy * o, y: c.y + uy * r + ux * o };
        if (!ix[p.runId] || !ix[p.runId].mode) place(e, e.__home);
      });
    });
    idle.pets.forEach(function (p) {
      var e = petEl(p.runId); keep[p.runId] = 1;
      var w = wander[p.runId] || (wander[p.runId] = Pk.initWander(WANDER, Math.random));
      if (!e.classList.contains("is-loose")) {
        delete ix[p.runId]; e.classList.remove("is-work", "st-away", "flip"); e.classList.add("is-loose"); place(e, w);
        var sl = e.querySelector(".st-media"); if (sl.dataset.over) { delete sl.dataset.over; sl.dataset.key = ""; }
      }
      fillPet(e, p, runById(p.runId));
    });
    Object.keys(petEls).forEach(function (id) {
      if (keep[id]) return;
      if (petEls[id].classList.contains("st-dragging")) dragging--;   // 拖着时被移除：收不到 pointerup
      petEls[id].remove(); delete petEls[id]; delete wander[id]; delete ix[id];
    });
    var more = $("stMore"), plus = work.plus;
    more.hidden = !plus; more.textContent = plus ? "+" + plus : "";
    if (plus) {
      var cells = [].slice.call(document.querySelectorAll(".hex-cell[data-project-id]")), maxX = hc.x, maxY = hc.y;
      cells.forEach(function (el) { var c = rectCenter(el); maxX = Math.max(maxX, c.x + c.w / 2); maxY = Math.max(maxY, c.y + c.h / 2); });
      place(more, { x: maxX + 14, y: maxY });
    }
  }
  function runById(id) { return runsOf().filter(function (r) { return r.runId === id; })[0] || null; }
  function bindPetDrag(e) {
    if (e.__bound) return; e.__bound = 1;
    drag(e, {
      el: e,
      item: function () {
        var r = e.__run;
        if (!r) return null;
        return e.classList.contains("is-loose") ? { kind: "pet", runId: r.runId, agent: r.agent } : { kind: "free" };
      },
      onClick: function () { poke(e); },
      onGrab: function () {
        var s = ixOf(e.dataset.runId); s.mode = "drag"; s.walking = false; s.click = null;
        e.classList.remove("st-away"); syncClip(e);
      },
      onRelease: function (target, cancel, dx, dy) {
        var s = ixOf(e.dataset.runId), at = curPos(e);
        s.dragEnd = Date.now(); s.click = null;
        var from = { x: at.x + dx, y: at.y + dy };
        if (!e.classList.contains("is-loose")) {            // 格子边：不改数据，停 3 秒再走回
          e.style.translate = ""; s.mode = null;
          startBack(e, from, cancel ? STAY_MS : 0);
          return "keep";
        }
        s.mode = null; syncClip(e);
        if (target) return;                                  // 落在格子上：照原样建去向规则
        e.style.translate = "";
        if (!cancel) {                                       // 空白处：从落点接着随机走动
          var w = wander[e.dataset.runId], q = Pk.initWander(WANDER, Math.random);
          if (w) wander[e.dataset.runId] = { x: Math.min(WANDER.x + WANDER.w, Math.max(WANDER.x, from.x)), y: Math.min(WANDER.y + WANDER.h, Math.max(WANDER.y, from.y)), tx: q.tx, ty: q.ty };
          place(e, wander[e.dataset.runId]);
        }
        return "keep";
      }
    });
  }
  function stepPets() {
    stepBack(Date.now());
    if (dragging) return;
    Object.keys(wander).forEach(function (id) {
      var e = petEls[id]; if (!e || !e.classList.contains("is-loose") || e.classList.contains("st-dragging")) return;
      var w = wander[id] = Pk.stepWander(wander[id], WANDER, Math.random, PET_MS, PET_SPEED);
      e.classList.toggle("flip", w.tx < w.x);
      place(e, w);
    });
  }

  // ── 中心环 ────────────────────────────────────────────
  var ringKey = null;
  function renderRing(now) {
    var el = $("stRing"), hc = hiveCenter();
    place(el, hc);
    var lanes = data.lanes || {}, hit = St.pickRing(runsOf(), lanes.interactions, now, dayStart() || null);
    var run = hit && hit.agent !== undefined ? hit : null, sec = 0, name = "";
    if (run) {
      name = run.label || run.agent;
      sec = run.endAt ? (ms(run.endAt) - ms(run.startAt)) / 1000 : (now - ms(run.startAt)) / 1000;
      if (run.lost && run.lastSeenAt) sec = (ms(run.lastSeenAt) - ms(run.startAt)) / 1000;
    }
    var key = run ? run.runId : "";
    if (!el.firstChild) {
      el.innerHTML = '<svg viewBox="0 0 200 200" aria-hidden="true"><circle cx="100" cy="100" r="96" fill="var(--panel)"/>' +
        '<circle cx="100" cy="100" r="86" fill="none" stroke="var(--panel-2)" stroke-width="12"/>' +
        '<circle class="st-arc" cx="100" cy="100" r="86" fill="none" stroke="var(--fact-s1)" stroke-width="12" pathLength="100" stroke-dasharray="0 100" transform="rotate(-90 100 100)"/></svg>' +
        '<div class="st-face"><div class="st-ring-agent"></div><div class="st-ring-time"></div></div>';
    }
    if (key !== ringKey) {
      ringKey = key;
      var face = el.querySelector(".st-face");
      face.classList.remove("swap"); void face.offsetWidth; face.classList.add("swap");
    }
    el.dataset.runId = key;
    el.querySelector(".st-ring-agent").textContent = name;
    el.querySelector(".st-ring-time").textContent = run ? hms(sec) : "--:--:--";
    el.querySelector(".st-arc").setAttribute("stroke-dasharray", (run ? (sec % 3600) / 36 : 0) + " 100");
  }

  // ── 右上角：人的时钟、气泡、白条 ─────────────────────────
  var CLOCK_FACE = '<svg viewBox="0 0 100 100" class="st-dial" aria-hidden="true"><circle cx="50" cy="50" r="46" fill="var(--panel-2)" stroke="var(--line)" stroke-width="3"/>' +
    '<line class="hh" x1="50" y1="50" x2="50" y2="28" stroke="var(--ink)" stroke-width="4" stroke-linecap="round"/>' +
    '<line class="hm" x1="50" y1="50" x2="50" y2="16" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>' +
    '<line class="hs" x1="50" y1="56" x2="50" y2="14" stroke="var(--fact)" stroke-width="1.5"/><circle cx="50" cy="50" r="3" fill="var(--fact)"/></svg>';
  function renderClock(now) {
    var el = $("stClock"), human = (data.lanes && data.lanes.human) || {}, running = stage.timerRunning();
    if (!el.firstChild) {
      el.innerHTML = CLOCK_FACE + '<div class="st-clock-tx"><div class="st-clock-today"></div><div class="st-clock-proj"></div></div>';
    }
    el.classList.toggle("is-timing", running);
    el.setAttribute("aria-pressed", running ? "true" : "false");
    el.querySelector(".st-clock-today").textContent = dur(humanTodaySeconds(now));
    var f = (data.current && data.current.focus) || human.focus, pid = hostProject(), name = "";
    if (human.running && human.running.projectId) name = projectName(human.running.projectId);
    else if (f && f.state !== "afk" && (f.projectName || f.projectId)) name = f.projectName || projectName(f.projectId);
    else if (pid) name = projectName(pid);
    el.querySelector(".st-clock-proj").textContent = name;
    var d = new Date(now), h = d.getHours() % 12, m = d.getMinutes(), s = d.getSeconds();
    el.querySelector(".hh").setAttribute("transform", "rotate(" + (h + m / 60) * 30 + " 50 50)");
    el.querySelector(".hm").setAttribute("transform", "rotate(" + (m * 6 + s / 10) + " 50 50)");
    el.querySelector(".hs").setAttribute("transform", "rotate(" + s * 6 + " 50 50)");
  }
  function hostProject() {
    var run = data.lanes && data.lanes.human && data.lanes.human.running;
    if (run && run.projectId) return run.projectId;
    var h = St.pickBubbleHost(data.current && data.current.focus);
    return h.project || null;
  }
  var bub = { x: 0, y: 0, host: null, raf: 0 };
  function bubbleTarget(pid) {
    var el = pid && cellEl(pid);
    if (el) { var c = rectCenter(el); return { x: c.x + c.w * 0.3, y: c.y - c.h * 0.36 }; }
    var ck = $("stClock").querySelector(".st-dial").getBoundingClientRect(), p = toLayer(ck.left + ck.width / 2, ck.top + ck.height / 2);
    return p;
  }
  function renderBubble() {
    var el = $("stBubble"), pid = hostProject(), t = bubbleTarget(pid), key = pid || "clock";
    if (!el.firstChild) el.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
    el.dataset.host = key;
    el.classList.toggle("is-timing", stage.timerRunning());
    if (bub.host === null) { bub.host = key; bub.x = t.x; bub.y = t.y; place(el, t); return; }
    if (bub.host === key) { if (!bub.raf) { bub.x = t.x; bub.y = t.y; place(el, t); } return; }
    bub.host = key;
    var from = { x: bub.x, y: bub.y }, dx = t.x - from.x, dy = t.y - from.y, l = Math.hypot(dx, dy) || 1, k = Math.min(110, l * 0.35);
    var qx = (from.x + t.x) / 2 - dy / l * k, qy = (from.y + t.y) / 2 + dx / l * k - 20, t0 = performance.now(), DUR = 600;
    cancelAnimationFrame(bub.raf);
    (function step(nowT) {
      var u = Math.min(1, (nowT - t0) / DUR), e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2, a = (1 - e) * (1 - e), b = 2 * (1 - e) * e, c = e * e;
      bub.x = a * from.x + b * qx + c * t.x; bub.y = a * from.y + b * qy + c * t.y;
      place(el, bub);
      if (u < 1) bub.raf = requestAnimationFrame(step); else bub.raf = 0;
    })(t0);
  }
  var stripEls = {};
  function renderStrips() {
    var box = $("stStrips"), s = stage.strips(), keep = {};
    s.items.forEach(function (u, i) {
      var e = stripEls[u.id];
      if (!e) {
        e = stripEls[u.id] = document.createElement("div");
        e.className = "st-strip"; e.dataset.id = u.id; e.style.setProperty("--i", i);
        e.__u = u;
        drag(e, { el: e, item: function () { return { kind: "strip", id: e.__u.id }; } });
      }
      keep[u.id] = 1; e.__u = u;
      e.textContent = dur((ms(u.endAt) - ms(u.startAt)) / 1000);
      box.appendChild(e);
    });
    Object.keys(stripEls).forEach(function (id) { if (!keep[id]) { stripEls[id].remove(); delete stripEls[id]; } });
    var more = box.querySelector(".st-strip-more");
    if (s.plus) {
      if (!more) { more = document.createElement("div"); more.className = "st-strip-more"; }
      more.textContent = "+" + s.plus; box.appendChild(more);
    } else if (more) more.remove();
  }

  // ── 左下角桶与抽屉 ───────────────────────────────────
  var BUCKET = '<svg viewBox="0 0 190 150" aria-hidden="true"><defs><clipPath id="stBclip"><path d="M30 28 L160 28 L146 140 Q95 150 44 140 Z"/></clipPath></defs>' +
    '<path d="M30 28 L160 28 L146 140 Q95 150 44 140 Z" fill="var(--panel-2)" stroke="var(--line)" stroke-width="3"/>' +
    '<g clip-path="url(#stBclip)"><rect class="st-liquid" x="20" width="150" height="150" fill="var(--warn)" opacity=".55"/></g>' +
    '<path d="M30 28 L160 28 L146 140 Q95 150 44 140 Z" fill="none" stroke="var(--ink-3)" stroke-width="3"/>' +
    '<ellipse cx="95" cy="28" rx="65" ry="9" fill="none" stroke="var(--ink-3)" stroke-width="3"/>' +
    '<path d="M30 28 Q95 -30 160 28" fill="none" stroke="var(--ink-3)" stroke-width="3"/></svg>';
  function renderBucket() {
    var btn = $("stBucketBtn"), sec = stage.bucketSeconds();
    if (!btn.firstChild) btn.innerHTML = BUCKET;
    $("stBucketLabel").textContent = St.bucketText(sec);
    btn.querySelector(".st-liquid").setAttribute("y", String(150 - Math.min(1, sec / 7200) * 115));
    if ($("stDrawer").classList.contains("open")) renderDrawer();
  }
  var drawerSig = "";
  function renderDrawer() {
    var box = $("stDrawer"), rows = stage.drawer(), sig = JSON.stringify(rows.map(function (r) { return [r.name, r.seconds, r.draggable]; }));
    if (sig === drawerSig && box.firstChild) return;
    drawerSig = sig;
    box.innerHTML = "";
    rows.forEach(function (r) {
      var d = document.createElement("div");
      d.className = "st-chip" + (r.draggable ? "" : " nodrag");
      d.innerHTML = "<b></b><span></span>";
      d.firstChild.textContent = r.name; d.lastChild.textContent = dur(r.seconds);
      box.appendChild(d);
      if (r.draggable) drag(d, { el: d, item: function () { return { kind: "block", block: r.block }; } });
    });
  }
  $("stBucketBtn").addEventListener("click", function () {
    var d = $("stDrawer"); d.classList.toggle("open"); if (d.classList.contains("open")) { drawerSig = ""; renderDrawer(); }
  });

  // ── 提示 ───────────────────────────────────────────
  function renderToast() {
    var t = $("stToast"), n = stage.notice();
    t.textContent = n || ""; t.classList.toggle("show", !!n);
  }

  // ── 完成记录面板 ─────────────────────────────────────
  function lineHtml(spans, color, from) {
    var span = DAY_SPAN_H * 3600000, base = from + DAY_FROM_H * 3600000;
    return spans.map(function (s) {
      var a = Math.max(s.from, base), b = Math.min(s.to, base + span);
      if (b <= a) return "";
      return '<i style="left:' + (a - base) / span * 100 + "%;width:" + (b - a) / span * 100 + "%;background:" + color + '"></i>';
    }).join("");
  }
  function openPanel(pid) {
    panelPid = pid; renderPanel(); $("stVeil").hidden = false;
    var x = $("stPanel").querySelector(".st-x"); if (x) x.focus();
  }
  var panelHtml = "";
  function closePanel() { panelPid = null; panelHtml = ""; $("stVeil").hidden = true; }
  function renderPanel() {
    if (!panelPid) return;
    var now = Date.now(), from = dayStart(), pid = panelPid;
    var rec = Rec.projectRecord(data.lanes, runsOf(), pid, projectOfTask, now);
    var timer = { html: lineHtml(rec.timer.spans, "var(--fact-s1)", from), total: rec.timer.total };
    var talk = { html: lineHtml(rec.attend.spans, "var(--attn, var(--focus))", from), total: rec.attend.total };
    var axis = '<div class="st-axis"><span>06</span><span>09</span><span>12</span><span>15</span><span>18</span><span>21</span><span>24</span></div>';
    var rows = rec.runs.map(function (r) {
      var mood = Pk.moodOf(r.phase, r.lost);
      return '<div class="st-agrow"><b>' + esc(r.name) + '</b><span class="st-d">' + dur(r.workingSeconds) +
        '</span><span class="st-st is-' + mood + '">' + STATUS_TEXT[mood] + "</span></div>";
    }).join("");
    var html = '<div class="st-hd"><h2>' + esc(projectName(pid)) + '</h2><button type="button" class="st-expand">展开</button><button type="button" class="st-x" aria-label="关闭">✕</button></div>' +
      '<div class="st-cols"><div class="st-col-human"><h3>你</h3>' +
      '<div class="st-kind" data-kind="timer"><div class="st-row"><b>计时</b><span>' + dur(timer.total) + '</span></div><div class="st-track">' + timer.html + "</div>" + axis + "</div>" +
      '<div class="st-kind" data-kind="attend"><div class="st-row"><b>对话</b><span>' + dur(talk.total) + '</span></div><div class="st-track">' + talk.html + "</div>" + axis + "</div></div>" +
      '<div class="st-col-agent"><h3>代理</h3>' + rows + '<div class="st-token"><b>token 花费</b><span>—</span></div></div></div>';
    if (html !== panelHtml) { panelHtml = html; $("stPanel").innerHTML = html; }   // 没变就不重设，免得丢关闭按钮的焦点
  }
  $("stPanel").addEventListener("click", function (ev) {
    if (ev.target.closest(".st-x")) closePanel();
    else if (ev.target.closest(".st-expand")) {   // 老的格子展开（新建任务、改任务都在里面）
      var pid = panelPid, App = window.NexusTableHexApp;
      ev.stopPropagation();   // 不让 hex-app 的「点空白处收起」把刚展开的格子当场收掉
      closePanel();
      if (App && pid) App.expand(pid);
    }
  });
  $("stVeil").addEventListener("pointerdown", function (ev) { if (ev.target === $("stVeil")) closePanel(); });
  document.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && panelPid) closePanel(); });
  // 点格子 = 完成记录（拦在老的「展开格子」之前；占位格与已展开的格子不拦）
  document.addEventListener("click", function (ev) {
    var c = ev.target.closest && ev.target.closest(".hex-cell[data-project-id]");
    if (!c || c.classList.contains("is-expanded") || c.classList.contains("hex-center") || ev.target.closest("#stageLayer")) return;
    ev.stopPropagation(); ev.preventDefault();
    openPanel(c.dataset.projectId);
  }, true);

  // 点时钟：开始 / 停；拖时钟或气泡（计时中）到格子 = 记到该项目
  (function () {
    var el = $("stClock");
    var clockItem = function () { return { kind: "clock" }; };
    var clockClick = function () {
      busy++;
      stage.clickClock().then(function () { busy--; render(); refresh(); });
    };
    drag($("stBubble"), { el: $("stBubble"), item: function () { return stage.timerRunning() ? clockItem() : null; }, onClick: clockClick });   // 气泡 = 时钟的延伸：拖、点都同时钟
    drag(el, { el: el, item: clockItem, onClick: clockClick });
    el.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); stage.clickClock().then(function () { render(); refresh(); }); }
    });
  })();

  // ── 汇总 ───────────────────────────────────────────
  function render() {
    var now = Date.now();
    renderRing(now); renderClock(now); renderBubble(); renderStrips(); renderBucket(); renderPets();
    [].forEach.call(layer.querySelectorAll(".st-pet"), bindPetDrag);
    renderToast();
    if (panelPid) renderPanel();
  }
  function applyData() {
    var lanes = data.lanes, running = lanes && lanes.human && lanes.human.running;
    stage.setTimer(running ? { projectId: running.projectId || null, startAt: ms(running.startAt) } : null);
    var b = data.blocks, date = b && b.date, items = ((b && b.items) || []).filter(function (x) { return x.status ? x.status === "pending" : true; })
      .map(function (x) { return Object.assign({ date: date, legible: false, kind: "other" }, x); });
    stage.setBucket(items.reduce(function (s, x) { return s + (x.seconds || 0); }, 0), items);
    stage.setStrips(data.unplaced || []);
  }

  var polls = 0;
  function refresh() {
    var slow = polls++ % 6 === 0;
    // 三级分类关着时不读窗口块、也不多读账号设置（决策栏启动后会在 .hd-panel 上挂 data-tiers）
    var tiers = !!document.querySelector(".hd-panel[data-tiers]");
    return (tiers && (slow || !data.settings) ? getJson("activity/settings") : Promise.resolve(null)).then(function (set) {
      if (set) data.settings = set;
      return Promise.all([
        getJson("views/lanes"), getJson("views/current"), slow ? getJson("views/tree") : null,
        tiers ? getJson("activity/blocks?status=pending&limit=1000") : { date: null, items: [] }, getJson("timer/unplaced")
      ]);
    }).then(function (r) {
      if (dragging || busy) return;
      if (r[0]) data.lanes = r[0];
      if (r[1]) data.current = r[1];
      if (r[2]) data.tree = r[2];
      if (r[3]) data.blocks = r[3];
      data.unplaced = (r[4] && r[4].items) || [];
      applyData(); render();
    });
  }

  function sizeToWindow() {
    var w = window.innerWidth, k = w >= 1280 || w < 1024 ? 1 : w / 1280;
    shell.style.zoom = k === 1 ? "" : String(k);
    render();
  }
  window.addEventListener("resize", sizeToWindow);

  refresh().then(function () { sizeToWindow(); });
  setInterval(refresh, POLL_MS);
  setInterval(function () { var n = Date.now(); renderRing(n); renderClock(n); renderToast(); }, TICK_MS);
  setInterval(stepPets, PET_MS);
  setInterval(function () { renderBubble(); renderPets(); }, 400);
})();
