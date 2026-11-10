// hive · hex-stage-page.js —— 蜂巢新布局的页面接线（hive.stage.v1「一」「二」「四」）。
// 选择与计算全在 hex-stage.js / hex-pet-pick.js（纯逻辑）；这里只读数据、摆元素、接 Pointer Events 拖动。
// 不依赖 hex-app.js 的内部：格子位置一律从 DOM（.hex-cell[data-project-id]）量。
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/core/";
  var St = window.NexusTableHexStage, Rec = window.NexusTableHexRecord, Pk = window.NexusTableHexPetPick, Pr = window.NexusTableHexProjectRuler;
  var shell = document.querySelector(".board-shell");
  if (!St || !Rec || !Pk || !Pr || !shell) return;

  var POLL_MS = 5000, TICK_MS = 1000, PET_MS = 100;
  var SLOT_AREA = { x: 24, y: 70, w: 340, h: 170 };
  var DAY_FROM_H = 6, DAY_SPAN_H = 18;
  var STATUS_TEXT = { working: "工作中", waiting: "等你", idle: "空闲", error: "出错" };
  var LANE_MS = 3 * 3600000;

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
    '<div class="st-greypop st-card" id="stGreyPop"></div>' +
    '<div class="st-bucket" id="stBucket"><button type="button" class="st-bucket-btn" id="stBucketBtn" aria-label="未分类"></button><div class="st-bucket-label" id="stBucketLabel"></div></div>' +
    '<div class="st-toast" id="stToast" role="status" aria-live="polite"></div>' +
    '<div class="st-veil" id="stVeil" hidden><div class="st-panel st-card" id="stPanel" role="dialog" aria-modal="true"></div></div>';
  shell.appendChild(layer);
  var renderSourceHint = window.HexSourceHealth ? window.HexSourceHealth.mount(layer) : function () {};   // 信息源体检：没数据 / 全好时不显示
  document.body.appendChild(document.getElementById("stVeil"));   // 舞台层自带层级 5，面板要盖住整个蜂巢（含分区描边）
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
      var cell = cellAt(ev.clientX, ev.clientY);
      if (fileable(item) && PP.dragOver(ev.clientX, ev.clientY, cell)) cell = null;   // 项目面板（弹簧展开 / 任务条 / 空白新建）：指针在面板里，亮的是任务条，不是后面的格子
      lit(cell);
      if (Sp && fileable(item)) sprout = blankSprout(ev.clientX, ev.clientY, sprout);
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
      var on = cancel ? null : PP.dropAt(ev.clientX, ev.clientY);   // 落在项目面板的任务条上 / 新建任务的输入上
      var target = cancel ? null : on ? cellEl(on.projectId) : PP.overPanel(ev.clientX, ev.clientY) ? null : cellAt(ev.clientX, ev.clientY);
      PP.dragEnd();
      o.el.classList.remove("st-dragging"); dragging--;
      // 时间元素停在分区空白处、那一格亮满后松手 = 建新项目（同长按建项目的表单），建好后把它记进去（hive.stage.v1「二」8）
      var made = !target && !cancel && grown && fileable(item) && Sp.openCreate({ name: createName(item), onCreated: function (p) {
        busy++;
        getJson("views/tree").then(function (t) { if (t) data.tree = t; return stage.drop(item, p.id); })
          .then(function (r) { busy--; render(); return Promise.resolve(r && r.ok ? refresh() : null).then(function () { if (o.afterCreate) o.afterCreate(); }); });
      }, onCancel: function () { if (o.afterCreate) o.afterCreate(); } });
      if (!made && Sp) Sp.removeSprout();
      if (o.onRelease) { var back = o.onRelease(target, cancel, lx, ly, made, on); if (back === "keep" || made) return; }
      if (made) { o.el.style.translate = ""; return; }
      var pid = on ? on.projectId : target ? target.dataset.projectId : null;
      function file(taskId) {
        busy++;
        stage.drop(item, pid, { taskId: taskId }).then(function (r) {
          o.el.style.translate = "";
          if (r && r.reverted && pid) o.el.classList.add("st-bounce");
          busy--;
          if (r && r.ok && !taskId) fileBubble(item, pid, r);
          if (r && r.ok) PP.invalidate();
          render();
          if (r && r.ok) refresh();
        });
      }
      if (on && on.create) {   // 在面板空白处停够 1 秒长出了新任务输入：等人打名字，建好再把时间元素记进去
        PP.awaitName(function (taskId) { getJson("views/tree").then(function (t) { if (t) data.tree = t; file(taskId); }); }, function () { o.el.style.translate = ""; });
        return;
      }
      file(on && on.taskId);
    }
    handle.addEventListener("pointerup", function (ev) { end(ev, false); });
    handle.addEventListener("pointercancel", function (ev) { end(ev, true); });
  }

  // 能「拖到空白处建项目」的元素：块、宠物（含格子边的）、计时中的时钟 / 气泡、白条
  function fileable(item) { return item.kind !== "clock" || stage.timerRunning(); }
  function createName(item) { return item.kind === "block" ? item.block.displayName || "" : item.kind === "pet" ? item.agent || "" : ""; }

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


  // ── 宠物名牌与详情卡、归档气泡（hive.stage.v1「二」10，2026-11-08）──
  function taskOf(id) {
    var ps = projects();
    for (var i = 0; i < ps.length; i++) {
      var t = (ps[i].tasks || []).filter(function (x) { return x.id === id; })[0];
      if (t) return t;
    }
    return null;
  }
  function filedUnder(run) {
    var t = run.taskId && taskOf(run.taskId), pid = run.projectId || (run.taskId && projectOfTask(run.taskId)) || stage.petDock(run.runId);
    if (pid === "inbox" || pid === "p_inbox") pid = null;
    if (t && projectOfTask(run.taskId) === "p_inbox") t = null;
    return { task: t ? t.name : null, project: pid ? projectName(pid) : null };
  }
  function titleOf(run) {
    var f = filedUnder(run);
    return St.petTitle(run, f.task || f.project);
  }

  var card = null;
  function closeCard() {
    if (!card) return;
    card.remove(); card = null;
    document.removeEventListener("pointerdown", onCardOutside, true);
    document.removeEventListener("keydown", onCardKey, true);
  }
  function onCardOutside(ev) {
    if (!card || card.contains(ev.target)) return;
    if (card.__anchor && card.__anchor.contains(ev.target)) return;   // 点同一只宠物 = 由它的点击切换
    closeCard();
  }
  function onCardKey(ev) { if (ev.key === "Escape") { ev.stopPropagation(); if (card && card.__cancelEdit && card.__cancelEdit()) return; closeCard(); } }
  function toggleCard(who, anchor) {
    if (card && card.__key === (who.runId || who.blockKey)) closeCard(); else openCard(who, anchor);
  }
  function placeCard(el, anchor) {            // 挂在 body 上用视口坐标：盖得住分区描边
    var a = (anchor.querySelector(".st-pet-in") || anchor).getBoundingClientRect(), w = el.offsetWidth, x = a.right + 12;
    if (x + w > window.innerWidth - 8) x = a.left - w - 12;
    el.style.left = Math.max(8, x) + "px"; el.style.top = Math.max(8, Math.min(a.top - 10, window.innerHeight - el.offsetHeight - 8)) + "px";
  }
  // 泳道横条：近 3 小时，按状态上色；悬停段 = 「HH:MM–HH:MM 状态」
  function hm(t) { var d = new Date(t); return pad(d.getHours()) + ":" + pad(d.getMinutes()); }
  function laneHtml(lane) {
    var span = lane.to - lane.from;
    var segs = lane.segments.map(function (g) {
      return '<i class="is-' + g.status + '" style="left:' + (g.from - lane.from) / span * 100 + "%;width:" + (g.to - g.from) / span * 100 + '%" title="' + hm(g.from) + "–" + hm(g.to) + " " + STATUS_TEXT[g.status] + '"></i>';
    }).join("");
    var ticks = [0, 1, 2, 3].map(function (k) { return "<span>" + hm(lane.from + k * span / 3) + "</span>"; }).join("");
    return '<div class="st-lane" aria-label="近 3 小时">' + segs + '</div><div class="st-lane-ticks">' + ticks + "</div>";
  }
  function openCard(who, anchor) {
    closeCard();
    var el = document.createElement("div");
    el.className = "st-detail st-card"; el.setAttribute("role", "dialog"); el.__anchor = anchor; el.__key = who.runId || who.blockKey;
    var now = Date.now();
    if (who.runId) {
      if (!runById(who.runId)) return;
      el.innerHTML = '<h3 class="st-d-title" role="button" tabindex="0"></h3><div class="st-d-body"></div>';
      var body = el.querySelector(".st-d-body"), ttl = el.querySelector(".st-d-title"), editing = false;
      el.__cancelEdit = function () { if (!editing) return false; editing = false; ttl.textContent = titleOf(runById(who.runId) || {}); return true; };
      el.__fill = function () {       // 轮询后重画正文与泳道；改名中的标题不动，输入和焦点都留着
        var run = runById(who.runId); if (!run) { closeCard(); return; }
        var f = filedUnder(run), mood = Pk.moodOf(run.phase, run.lost), t = Date.now(), lane = Rec.laneSegments(run, t, LANE_MS);
        if (!editing) ttl.textContent = titleOf(run);
        body.innerHTML = '<div class="st-d-line st-d-tool">' + esc([run.tool || run.agent, run.model].filter(Boolean).join(" · ")) + "</div>" +
          '<div class="st-d-line st-d-filed">' + (f.project ? "归在 " + esc((f.task ? f.project + " / " + f.task : f.project)) : "还没归到项目") + "</div>" +
          '<div class="st-d-line st-d-phase">' + STATUS_TEXT[mood] + " · 近 3 小时工作 " + dur(lane.workingSeconds) + "</div>" +
          laneHtml(lane) +
          (run.match ? '<div class="st-d-line st-d-win">窗口：' + esc(run.match) + "</div>" : "");
      };
      el.__fill();
      var startEdit = function () {
        var run = runById(who.runId); if (editing || !run) return;
        editing = true;
        var inp = document.createElement("input"), saving = false, orig = run.humanName || titleOf(run);
        inp.className = "st-d-name"; inp.maxLength = 64; inp.setAttribute("aria-label", "改名字"); inp.value = orig;
        ttl.textContent = ""; ttl.appendChild(inp); inp.focus(); inp.select();
        inp.addEventListener("keydown", function (ev) {
          if (ev.key !== "Enter") return;
          ev.preventDefault();
          var v = inp.value.trim(), name = v === "" ? null : v;
          if (name === orig && !run.humanName) { el.__cancelEdit(); return; }
          saving = true;
          send({ method: "PUT", path: "agents/" + encodeURIComponent(who.runId) + "/name", body: { name: name } }).then(function (res) {
            saving = false;
            if (res.status >= 200 && res.status < 300) {
              var r0 = ((data.lanes && data.lanes.agents) || []).filter(function (x) { return x.runId === who.runId; })[0];
              if (r0) r0.humanName = name;
              editing = false; render(); refresh();
            } else inp.classList.add("is-bad");
          }, function () { saving = false; inp.classList.add("is-bad"); });
        });
        inp.addEventListener("blur", function () { if (!saving) el.__cancelEdit(); });
      };
      ttl.addEventListener("click", startEdit);
      ttl.addEventListener("keydown", function (ev) { if ((ev.key === "Enter" || ev.key === " ") && ev.target === ttl) { ev.preventDefault(); startEdit(); } });
    } else {
      var b = who.block;
      el.innerHTML = '<h3 class="st-d-title" title="' + esc(b.displayName) + '">' + esc(b.displayName) + "</h3>" +
        '<div class="st-d-line">窗口 · ' + dur(b.seconds) + '</div><ul class="st-d-hist st-d-wins"></ul>';
      getJson("activity/blocks/records?date=" + encodeURIComponent(b.date) + "&blockKey=" + encodeURIComponent(b.blockKey)).then(function (r) {
        var by = {}; ((r && r.items) || []).forEach(function (x) { var t = x.title || x.app || "—"; by[t] = (by[t] || 0) + Math.max(0, (ms(x.endAt) - ms(x.startAt)) / 1000); });
        var ul = el.querySelector(".st-d-wins");
        if (ul) ul.innerHTML = Object.keys(by).sort(function (x, y) { return by[y] - by[x]; }).slice(0, 8).map(function (t) { return "<li><span>" + dur(by[t]) + "</span>" + esc(t) + "</li>"; }).join("");
      });
    }
    document.body.appendChild(el);
    placeCard(el, anchor);
    card = el;
    document.addEventListener("pointerdown", onCardOutside, true);
    document.addEventListener("keydown", onCardKey, true);
  }

  // 拖进格子后的小气泡：转圈 5 秒、勾「以后都算」、点气泡开详情；悬停 / 聚焦暂停
  var FILE_MS = 5000, FILE_TICK = 100, RING_C = 62.83;
  var fileB = null;
  function closeFileBubble() { if (!fileB) return; clearInterval(fileB.timer); fileB.el.remove(); fileB = null; }
  function fileBubble(item, pid, res) {
    if (item.kind !== "block") return;
    closeFileBubble();
    var cell = cellEl(pid); if (!cell) return;
    var c = rectCenter(cell), name = projectName(pid);
    var el = document.createElement("div");
    el.className = "st-file st-card"; el.setAttribute("role", "status");
    el.innerHTML = '<svg class="st-file-ring" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="10" fill="none" stroke="var(--panel-2)" stroke-width="3"/>' +
      '<circle class="st-file-arc" cx="12" cy="12" r="10" fill="none" stroke="var(--fact-s1)" stroke-width="3" stroke-linecap="round" transform="rotate(-90 12 12)" stroke-dasharray="' + RING_C + '" stroke-dashoffset="0"/></svg>' +
      '<input class="st-file-box" type="checkbox" aria-label="以后都算"><span class="st-file-tx"></span>';
    el.querySelector(".st-file-tx").textContent = "以后这个窗口都算 " + name;
    layer.appendChild(el);
    place(el, { x: c.x + c.w * 0.1, y: c.y - c.h / 2 - 22 });
    var st = { el: el, left: FILE_MS, seq: res && res.seq, timer: 0 }, box = el.querySelector("input"), arc = el.querySelector(".st-file-arc"), still = reduced();
    fileB = st;
    el.addEventListener("pointerenter", function () { st.hover = true; });
    el.addEventListener("pointerleave", function () { st.hover = false; });
    el.addEventListener("focusin", function () { st.focus = true; });
    el.addEventListener("focusout", function () { st.focus = false; });
    st.timer = setInterval(function () {
      if (st.hover || st.focus) return;
      st.left -= FILE_TICK;
      if (!still) arc.setAttribute("stroke-dashoffset", String(RING_C * (1 - st.left / FILE_MS)));
      if (st.left > 0) return;
      clearInterval(st.timer); el.classList.add("is-leaving");
      setTimeout(function () { if (fileB === st) closeFileBubble(); }, 250);
    }, FILE_TICK);
    box.addEventListener("change", function () {
      if (!box.checked) return;
      box.disabled = true;
      stage.remember(item, pid, st.seq).then(function (r) {
        if (r && r.ok) { box.blur(); refresh(); } else { box.checked = false; box.disabled = false; renderToast(); }
      });
    });
    el.addEventListener("click", function (ev) {
      if (ev.target.closest(".st-file-box")) return;
      toggleCard({ blockKey: item.block.blockKey, block: item.block }, el);
    });
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
  var PLACEHOLDER = '<svg class="st-art" width="42" height="39" viewBox="0 0 56 52" aria-hidden="true">' +
    '<ellipse cx="28" cy="30" rx="22" ry="18" fill="var(--attn, var(--focus))"/>' +
    '<path d="M10 16 L6 4 L19 11Z M46 16 L50 4 L37 11Z" fill="var(--attn, var(--focus))"/>' +
    '<circle cx="20" cy="27" r="3" fill="var(--panel)"/><circle cx="36" cy="27" r="3" fill="var(--panel)"/>' +
    '<circle cx="20.8" cy="27.4" r="1.4" fill="var(--ink)"/><circle cx="36.8" cy="27.4" r="1.4" fill="var(--ink)"/>' +
    '<path d="M23 36 q5 4 10 0" stroke="var(--ink)" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>';
  function mediaFor(pet, file) {
    if (!file) return { key: "svg", html: PLACEHOLDER };
    var url = BASE + "hive/pets/" + encodeURIComponent(pet.pack) + "/" + file.split("/").map(encodeURIComponent).join("/");
    return { key: url, html: '<video class="st-art" src="' + esc(url) + '" autoplay loop muted playsinline></video>' };
  }

  // ── 干活的宠物（格子边）与左上角的宠物 ───────────────────
  var petEls = {}, slotOf = {};
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
    var pk = packs[pet.pack];
    e.__rhythm = Pk.rhythm(e.__rhythm, pk && pk.moods, pet.mood, Date.now(), Math.random);   // 15 秒动作 → 3 秒临时动作 → 新动作
    var m = mediaFor(pet, e.__rhythm.file), slot = e.querySelector(".st-media");
    var tg = e.querySelector(".st-tag"), ttl = run ? titleOf(run) : pet.name;
    if (tg.textContent !== ttl) { tg.textContent = ttl; tg.title = ttl; }
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
    slotOf = Pk.assignSlots(slotOf, idle.pets.map(function (p) { var r = runById(p.runId); return { runId: p.runId, startAt: r && r.startAt }; }));
    idle.pets.forEach(function (p) {
      var e = petEl(p.runId); keep[p.runId] = 1;
      var home = Pk.slotPos(slotOf[p.runId], SLOT_AREA);
      e.__home = home;
      if (!e.classList.contains("is-loose")) {
        delete ix[p.runId]; e.classList.remove("is-work", "st-away", "flip"); e.classList.add("is-loose"); place(e, home);
        var sl = e.querySelector(".st-media"); if (sl.dataset.over) { delete sl.dataset.over; sl.dataset.key = ""; }
      } else if (!ix[p.runId] || !ix[p.runId].mode) place(e, home);
      fillPet(e, p, runById(p.runId));
    });
    Object.keys(petEls).forEach(function (id) {
      if (keep[id]) return;
      if (petEls[id].classList.contains("st-dragging")) dragging--;   // 拖着时被移除：收不到 pointerup
      petEls[id].remove(); delete petEls[id]; delete ix[id];
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
        return { kind: "pet", runId: r.runId, agent: r.agent };
      },
      onClick: function () { poke(e); toggleCard({ runId: e.dataset.runId }, e); },
      onGrab: function () {
        var s = ixOf(e.dataset.runId); s.mode = "drag"; s.walking = false; s.click = null;
        e.classList.remove("st-away"); syncClip(e);
      },
      afterCreate: function () {                            // 表单走完（建成 / 取消 / 失败）：格子边的宠物这时才走回 / 走向新格子
        var s = ix[e.dataset.runId];
        if (s && s.mode === "hold" && e.__home) startBack(e, curPos(e), 0);
      },
      onRelease: function (target, cancel, dx, dy, made, on) {
        var s = ixOf(e.dataset.runId), at = curPos(e);
        s.dragEnd = Date.now(); s.click = null;
        var from = { x: at.x + dx, y: at.y + dy };
        var pid = target && !cancel ? target.dataset.projectId : null;
        if (made) {                                          // 表单开着：原地等，取消 / 建完再走
          e.style.translate = ""; s.mode = "hold"; place(e, from); syncClip(e);
          return "keep";
        }
        var tid = on && on.taskId || null, run = e.__run;
        if (on && on.create) {                               // 项目面板空白处新建任务：宠物原地等，建好再问
          e.style.translate = ""; s.mode = "hold"; place(e, from); syncClip(e);
          PP.awaitName(function (taskId) { getJson("views/tree").then(function (t) { if (t) data.tree = t; openConfirm(e, on.projectId, taskId); }); }, function () { backHome(e.dataset.runId); });
          return "keep";
        }
        if (pid && (pid !== e.__pet.projectId || (tid && run && tid !== run.taskId))) {   // 落在另一个项目的格子 / 项目面板的任务条上：问清楚再挂；宠物在原地等
          e.style.translate = ""; s.mode = "hold"; place(e, from); syncClip(e);
          openConfirm(e, pid, tid);
          return "keep";
        }
        e.style.translate = ""; s.mode = null;               // 空白处 / 自己的格子 / 取消：不改数据，停 3 秒再走回
        startBack(e, from, cancel ? STAY_MS : 0);
        return "keep";
      }
    });
  }
  // 宠物拖到另一个项目：确认卡，一次落下一张、各答各的。同一格的卡在该格上方自上而下叠着（放不下就滚动），不同格的卡各在各的格子。
  // 「之前的时间也算过去」= 整条运行；「只算以后的时间」= timeFrom "now"；勾「以后 X 都算」= remember。Esc 只取消最新一张，点卡外不取消
  var confirms = [];
  function onConfirmKey(ev) { if (ev.key === "Escape" && confirms.length) { ev.stopPropagation(); confirms[confirms.length - 1].__done(null); } }
  function dropConfirm(el) {
    var i = confirms.indexOf(el); if (i < 0) return;
    confirms.splice(i, 1);
    var stack = el.parentNode; el.remove();
    if (stack && !stack.children.length) stack.remove();
    if (!confirms.length) document.removeEventListener("keydown", onConfirmKey, true);
  }
  function confirmStack(pid, c) {
    var st = [].slice.call(layer.querySelectorAll(".st-cf-stack")).filter(function (x) { return x.dataset.projectId === pid; })[0];
    if (st) return st;
    st = document.createElement("div"); st.className = "st-cf-stack"; st.dataset.projectId = pid;
    layer.appendChild(st);
    var top = Math.max(8, c.y - c.h / 2 - 22);
    place(st, { x: c.x + c.w * 0.1, y: top });
    st.style.maxHeight = Math.max(120, layer.clientHeight - top - 8) + "px";
    return st;
  }
  function openConfirm(e, pid, taskId) {
    closeFileBubble();
    var cell = cellEl(pid), id = e.dataset.runId, run = runById(id);
    if (!cell || !run) { backHome(id); return; }
    confirms.filter(function (x) { return x.dataset.runId === id; }).forEach(dropConfirm);   // 同一只再拖一次：只留新的
    var c = rectCenter(cell), tk = taskId && taskOf(taskId), name = tk ? tk.name : projectName(pid), who = titleOf(run), el = document.createElement("div");
    el.className = "st-confirm st-card"; el.setAttribute("role", "dialog"); el.dataset.runId = id;
    el.innerHTML = '<button type="button" class="st-cf-x" aria-label="取消">✕</button><div class="st-cf-title"></div>' +
      '<div class="st-cf-btns"><button type="button" data-from="start">之前的时间也算过去</button><button type="button" data-from="now">只算以后的时间</button></div>' +
      '<label class="st-cf-rem"><input type="checkbox" class="st-cf-box"><span></span></label>';
    el.querySelector(".st-cf-title").textContent = "把 " + who + " 挂到 " + name;
    el.querySelector(".st-cf-rem span").textContent = "以后 " + who + " 都算 " + name;
    confirmStack(pid, c).appendChild(el);
    if (!confirms.length) document.addEventListener("keydown", onConfirmKey, true);
    confirms.push(el);
    el.__done = function (timeFrom) {
      var remember = el.querySelector(".st-cf-box").checked;
      dropConfirm(el);
      if (!timeFrom) { backHome(id); return; }
      busy++;
      stage.drop({ kind: "pet", runId: id, agent: run.agent }, pid, { remember: remember, timeFrom: timeFrom, taskId: taskId }).then(function (r) {
        busy--; if (r && r.ok) PP.invalidate(); render();
        return Promise.resolve(r && r.ok ? refresh() : null).then(function () { backHome(id); });
      });
    };
    el.querySelector(".st-cf-x").addEventListener("click", function () { el.__done(null); });
    el.querySelector(".st-cf-btns").addEventListener("click", function (ev) { var b = ev.target.closest("button"); if (b) el.__done(b.dataset.from); });
  }
  function backHome(id) {            // 卡关了（取消 / 挂完 / 失败）：宠物从当前位置走回（新）家
    var e = petEls[id], s = ix[id];
    if (e && s && s.mode === "hold" && e.__home) startBack(e, curPos(e), STAY_MS);
  }
  function stepPets() {
    stepBack(Date.now());
    Object.keys(petEls).forEach(function (id) { var e = petEls[id]; if (e.__pet) fillPet(e, e.__pet, e.__run); });   // 动作节奏到点就换
  }

  // ── 中心环 ────────────────────────────────────────────
  var ringKey = null;
  function renderRing(now) {
    var el = $("stRing"), hc = hiveCenter();
    place(el, hc);
    // 计时环正好放进中心格（设计稿 140px，按中心格宽度等比缩放；展开推镜时中心格变大也不超过原大）
    var k = hc.w ? Math.min(1, hc.w / 140) : 1;
    el.style.transform = k < 1 ? "translate(-50%, -50%) scale(" + k.toFixed(3) + ")" : "";
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
      el.addEventListener("click", function () { if (el.dataset.runId) toggleCard({ runId: el.dataset.runId }, el); });   // 点中心 = 这个代理的详情卡
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
  // 白条标签：「手动计时 · [标题 | HH:MM 开始] · X 分钟」（hive.stage.v1「二」9）
  function stripLabel(u) {
    var a = new Date(ms(u.startAt)), title = u.title || u.name;
    return "手动计时 · " + (title || pad(a.getHours()) + ":" + pad(a.getMinutes()) + " 开始") + " · " + Math.max(1, Math.round((ms(u.endAt) - ms(u.startAt)) / 60000)) + " 分钟";
  }
  // 点白条：在白条旁列出分区 → 项目；选中 = 同拖到那一格
  var stripPop = null;
  function closeStripList() { if (stripPop) { stripPop.remove(); stripPop = null; document.removeEventListener("pointerdown", onPopOutside, true); } }
  function onPopOutside(ev) { if (stripPop && !stripPop.contains(ev.target) && ev.target.closest && ev.target.closest(".st-strip") !== stripPop.__strip) closeStripList(); }
  function openStripList(strip) {
    if (stripPop && stripPop.__strip === strip) { closeStripList(); return; }   // 再点同一条 = 收起
    closeStripList();
    var id = strip.__u.id, zones = (data.tree && data.tree.zones) || [], pop = document.createElement("div"), r = strip.getBoundingClientRect();
    pop.className = "st-pop st-card"; pop.tabIndex = -1; pop.setAttribute("role", "listbox"); pop.setAttribute("aria-label", "记到哪个项目");
    pop.innerHTML = (projects().length > 8 ? '<input class="st-pop-q" type="search" placeholder="搜索项目" aria-label="搜索项目">' : "") + '<div class="st-pop-list"></div>';
    var list = pop.querySelector(".st-pop-list"), q = pop.querySelector("input"), cur = -1;
    function items() { return [].slice.call(list.querySelectorAll(".st-pop-item")); }
    function mark(i) { var a = items(); if (!a.length) return; cur = (i + a.length) % a.length; a.forEach(function (x, k) { x.classList.toggle("is-cur", k === cur); }); a[cur].scrollIntoView({ block: "nearest" }); }
    function pick(pid) {
      closeStripList(); busy++;
      stage.drop({ kind: "strip", id: id }, pid).then(function (res) { busy--; render(); if (res && res.ok) refresh(); });
    }
    function fill() {
      var kw = q ? q.value.trim().toLowerCase() : "", html = "";
      zones.concat([{ id: null, name: "其他" }]).forEach(function (z) {
        var ps = projects().filter(function (p) { return (p.zoneId || null) === z.id && (!kw || p.name.toLowerCase().indexOf(kw) >= 0); });
        if (ps.length) html += '<div class="st-pop-zone">' + esc(z.name) + "</div>" + ps.map(function (p) { return '<button type="button" class="st-pop-item" role="option" data-pid="' + esc(p.id) + '">' + esc(p.name) + "</button>"; }).join("");
      });
      list.innerHTML = html || '<div class="st-pop-zone">没有项目</div>'; cur = -1; mark(0);
    }
    pop.addEventListener("click", function (ev) { var b = ev.target.closest(".st-pop-item"); if (b) pick(b.dataset.pid); });
    pop.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape") { ev.stopPropagation(); closeStripList(); strip.focus && strip.focus(); }
      else if (ev.key === "ArrowDown") { ev.preventDefault(); mark(cur + 1); }
      else if (ev.key === "ArrowUp") { ev.preventDefault(); mark(cur - 1); }
      else if (ev.key === "Enter") { ev.preventDefault(); var a = items()[cur]; if (a) pick(a.dataset.pid); }
    });
    if (q) q.addEventListener("input", fill);
    fill();
    document.body.appendChild(pop);
    var w = pop.offsetWidth;
    pop.style.left = Math.max(8, r.left - w - 8) + "px"; pop.style.top = Math.max(8, Math.min(r.top, window.innerHeight - pop.offsetHeight - 8)) + "px";
    stripPop = pop; pop.__strip = strip;
    document.addEventListener("pointerdown", onPopOutside, true);
    (q || pop.querySelector(".st-pop-item") || pop).focus();
  }
  var stripEls = {};
  function renderStrips() {
    var box = $("stStrips"), s = stage.strips(), keep = {};
    s.items.forEach(function (u, i) {
      var e = stripEls[u.id];
      if (!e) {
        e = stripEls[u.id] = document.createElement("div");
        e.className = "st-strip"; e.tabIndex = 0; e.setAttribute("role", "button"); e.dataset.id = u.id; e.style.setProperty("--i", i);
        e.__u = u;
        drag(e, { el: e, item: function () { return { kind: "strip", id: e.__u.id }; }, onClick: function () { openStripList(e); } });
        e.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openStripList(e); } });
      }
      keep[u.id] = 1; e.__u = u;
      e.textContent = stripLabel(u);
      box.appendChild(e);
    });
    Object.keys(stripEls).forEach(function (id) { if (!keep[id]) { stripEls[id].remove(); delete stripEls[id]; } });
    if (stripPop && !keep[stripPop.__strip.__u.id]) closeStripList();
    var more = box.querySelector(".st-strip-more");
    if (s.plus) {
      if (!more) { more = document.createElement("div"); more.className = "st-strip-more"; }
      more.textContent = "+" + s.plus; box.appendChild(more);
    } else if (more) more.remove();
  }

  // ── 左下角桶与抽屉 ───────────────────────────────────
  // 托盘图形：Phosphor「tray」duotone（MIT，见 third_party/phosphor-icons/LICENSE）
  var BUCKET = '<svg viewBox="0 0 140 120" aria-hidden="true"><g class="st-tray-chips"></g><g transform="translate(6 -2) scale(.5)" style="color:var(--ink-3)"><path d="M216,48V160H179.31a8,8,0,0,0-5.66,2.34l-19.31,19.32a8,8,0,0,1-5.66,2.34H107.31a8,8,0,0,1-5.66-2.34L82.34,162.34A8,8,0,0,0,76.68,160H40V48a8,8,0,0,1,8-8H208A8,8,0,0,1,216,48Z" fill="var(--plan)" opacity="0.14"/><path fill="currentColor" d="M208,32H48A16,16,0,0,0,32,48V208a16,16,0,0,0,16,16H208a16,16,0,0,0,16-16V48A16,16,0,0,0,208,32Zm0,16V152h-28.7A15.86,15.86,0,0,0,168,156.69L148.69,176H107.31L88,156.68A15.89,15.89,0,0,0,76.69,152H48V48Zm0,160H48V168H76.69L96,187.32A15.89,15.89,0,0,0,107.31,192h41.38A15.86,15.86,0,0,0,160,187.31L179.31,168H208v40Z"/></g></svg>';
  // 12 个堆位：底行 5、中行 4、上行 3，位置固定，重画不跳
  var TRAY_SLOTS = [[38, 64], [54, 64], [70, 64], [86, 64], [102, 64], [46, 50], [62, 50], [78, 50], [94, 50], [54, 36], [70, 36], [86, 36]];
  var TRAY_HUES = ["--plan", "--focus", "--warn", "--fact"];
  var HEX_PATH = "M0 -10 L8.66 -5 L8.66 5 L0 10 L-8.66 5 L-8.66 -5 Z", SVGNS = "http://www.w3.org/2000/svg";
  var traySlot = {}, trayChips = {}, trayArmed = false, trayLoaded = false;   // blockKey → 堆位 / <g>
  function trayChip(slot) {
    var hue = "var(" + TRAY_HUES[slot % 4] + ")", g = document.createElementNS(SVGNS, "g"), p = document.createElementNS(SVGNS, "path"), inner = document.createElementNS(SVGNS, "g");
    g.setAttribute("class", "st-tray-chip");
    g.setAttribute("transform", "translate(" + TRAY_SLOTS[slot][0] + " " + TRAY_SLOTS[slot][1] + ") rotate(" + ((slot * 37) % 7 - 3) + ") scale(.85)");
    p.setAttribute("d", HEX_PATH); p.setAttribute("fill", "color-mix(in srgb, " + hue + " 30%, var(--panel))");
    p.setAttribute("stroke", hue); p.setAttribute("stroke-width", "1.5"); p.setAttribute("stroke-linejoin", "round");
    inner.setAttribute("class", "st-hexchip"); inner.appendChild(p); g.appendChild(inner);
    return g;
  }
  function renderTrayChips(keys) {
    var host = $("stBucketBtn").querySelector(".st-tray-chips"), live = {}, used = {};
    keys.forEach(function (k) { live[k] = 1; });
    Object.keys(traySlot).forEach(function (k) {
      if (live[k]) { used[traySlot[k]] = 1; return; }
      var g = trayChips[k]; delete traySlot[k]; delete trayChips[k];
      g.firstChild.classList.add("st-leave"); setTimeout(function () { g.remove(); }, 300);
    });
    keys.forEach(function (k) {
      if (k in traySlot) return;
      for (var i = 0; i < TRAY_SLOTS.length && used[i]; i++);
      if (i === TRAY_SLOTS.length) return;
      used[i] = 1; traySlot[k] = i; trayChips[k] = trayChip(i);
      if (trayArmed) trayChips[k].firstChild.classList.add("st-drop");
      host.appendChild(trayChips[k]);
    });
    trayArmed = trayLoaded;   // 首批数据那一次不掉落
  }
  function renderBucket() {
    var btn = $("stBucketBtn"), sec = stage.bucketSeconds();
    if (!btn.firstChild) btn.innerHTML = BUCKET;
    $("stBucketLabel").textContent = St.bucketText(sec);
    renderTrayChips(stage.bucketKeys());
    if ($("stDrawer").classList.contains("open")) renderDrawer();
  }
  var drawerSig = "";
  function renderDrawer() {
    var box = $("stDrawer"), rows = stage.drawer(), sig = JSON.stringify(rows.map(function (r) { return [r.name, r.seconds, r.draggable]; }));
    renderGreyPop(rows);
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
      else if (r.members) {
        d.classList.add("grey"); d.setAttribute("role", "button"); d.tabIndex = 0;
        var open = function () { greyKind = greyKind === r.kind ? null : r.kind; renderGreyPop(stage.drawer()); };
        d.addEventListener("click", open);
        d.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); open(); } });
      }
    });
  }
  // 灰行点开：这一类里每块（同名窗口）一行，可拖进格子 / 空白处（同可拖块）
  var greyKind = null, greySig = "";
  function renderGreyPop(rows) {
    var box = $("stGreyPop"), row = rows.filter(function (r) { return r.kind === greyKind && r.members; })[0];
    if (!row || !$("stDrawer").classList.contains("open")) { greyKind = row ? greyKind : null; box.classList.remove("open"); greySig = ""; return; }
    var sig = JSON.stringify(row.members.map(function (m) { return [m.block.blockKey, m.seconds]; }));
    box.classList.add("open");
    if (sig === greySig) return;
    greySig = sig; box.innerHTML = "";
    var h = document.createElement("div"); h.className = "st-greypop-h"; h.textContent = row.name + " · 拖到项目里"; box.appendChild(h);
    row.members.forEach(function (m) {
      var d = document.createElement("div");
      d.className = "st-chip"; d.innerHTML = "<b></b><span></span>";
      var shown = m.hidden ? "标题已隐去" : m.title;
      d.firstChild.textContent = shown; d.firstChild.title = shown; d.lastChild.textContent = dur(m.seconds);
      box.appendChild(d);
      drag(d, { el: d, item: function () { return { kind: "block", block: m.block }; } });
    });
  }
  $("stBucketBtn").addEventListener("click", function () {
    var d = $("stDrawer"); d.classList.toggle("open"); if (d.classList.contains("open")) { drawerSig = ""; renderDrawer(); } else renderGreyPop(stage.drawer());
  });

  // ── 提示 ───────────────────────────────────────────
  function renderToast() {
    var t = $("stToast"), n = stage.notice();
    t.textContent = n || ""; t.classList.toggle("show", !!n);
  }

  // ── 完成记录面板 ─────────────────────────────────────
  function openPanel(pid) {
    panelPid = pid; renderPanel(); $("stVeil").hidden = false;
    var x = $("stPanel").querySelector(".st-x"); if (x) x.focus();
  }
  var panelHtml = "";
  function closePanel() { panelPid = null; panelHtml = ""; $("stVeil").hidden = true; PP.reset(); }
  // 项目面板（任务条 + 人 / 代理时间标尺）在 hex-project-ruler.js；这里只接标题栏与重画
  var PP = Pr.createPanel({ tree: function () { return data.tree; }, getJson: getJson, dur: dur, open: openPanel, taskApi: function () { return window.NexusTableData; },
    changed: function () { renderPanel(); },
    cellColor: function (pid) { var c = cellEl(pid); return c ? getComputedStyle(c).getPropertyValue("--zone-raw").trim() : ""; } });
  function renderPanel() {
    if (!panelPid || PP.frozen()) return;
    var html = '<div class="st-hd"><h2>' + esc(projectName(panelPid)) + '</h2><button type="button" class="st-expand">展开</button><button type="button" class="st-x" aria-label="关闭">✕</button></div>' + PP.body(panelPid, Date.now());
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
    if (card && card.__fill) card.__fill();
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
        tiers ? getJson("activity/blocks?status=pending&limit=1000") : { date: null, items: [] }, getJson("timer/unplaced"),
        slow ? getJson("detector/devices") : null
      ]);
    }).then(function (r) {
      if (dragging || busy) return;
      if (r[0]) data.lanes = r[0];
      if (r[1]) data.current = r[1];
      if (r[2]) data.tree = r[2];
      if (r[3]) data.blocks = r[3];
      if (tiers) trayLoaded = true;
      data.unplaced = (r[4] && r[4].items) || [];
      if (r[5] && window.HexSourceHealth) renderSourceHint(window.HexSourceHealth.problems(r[5].devices));
      applyData(); render();
    });
  }

  function sizeToWindow() {
    var w = window.innerWidth, k = w >= 1280 || w < 1024 ? 1 : w / 1280;
    shell.style.zoom = k === 1 ? "" : String(k);
    render();
  }
  window.addEventListener("resize", sizeToWindow);
  new MutationObserver(function () { if ($("stClock").querySelector(".st-dial")) renderBubble(); }).observe(shell, { attributes: true, attributeFilter: ["hidden"], subtree: true });   // 决策栏出现 / 消失 → 时钟挪位，气泡立刻跟过去

  refresh().then(function () { sizeToWindow(); });
  setInterval(refresh, POLL_MS);
  setInterval(function () { var n = Date.now(); renderRing(n); renderClock(n); renderToast(); }, TICK_MS);
  setInterval(stepPets, PET_MS);
  setInterval(function () { renderBubble(); renderPets(); }, 400);
})();
