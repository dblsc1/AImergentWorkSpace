// hive · hex-stage-page.js —— 蜂巢新布局的页面接线（hive.stage.v1「一」「二」「四」）。
// 选择与计算全在 hex-stage.js / hex-pet-pick.js（纯逻辑）；这里只读数据、摆元素、接 Pointer Events 拖动。
// 不依赖 hex-app.js 的内部：格子位置一律从 DOM（.hex-cell[data-project-id]）量。
(function () {
  "use strict";
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";
  var API = BASE + "api/core/";
  var CM = window.HexClockMenu, St = window.NexusTableHexStage, Rec = window.NexusTableHexRecord, Pk = window.NexusTableHexPetPick, Pr = window.NexusTableHexProjectRuler, CS = window.NexusTableHexCentreStats, FP = window.HexFigurePick;
  var shell = document.querySelector(".board-shell");
  if (!St || !Rec || !Pk || !Pr || !CS || !CM || !window.HexHumanPage || !shell) return;

  var POLL_MS = 5000, TICK_MS = 1000;
  var SLOT_AREA = { x: 24, y: 142, w: 340, h: 160 };   // 左上角 y < 142 留给 72px 的头像
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
    '<div class="st-eye" id="stEye" role="button" aria-label="你的聚焦"></div>' +
    '<div class="st-more" id="stMore" hidden></div>' +
    '<div class="st-drawer st-card" id="stDrawer"></div>' +
    '<div class="st-greypop st-card" id="stGreyPop"></div>' +
    '<div class="st-bucket" id="stBucket"><button type="button" class="st-bucket-btn" id="stBucketBtn" aria-label="未分类"></button><div class="st-bucket-label" id="stBucketLabel"></div></div>' +
    '<div class="st-toast" id="stToast" role="status" aria-live="polite"></div>';
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
  // 人自己的形象用哪个包（hive.stage.v1「十一」）；没选过用缺省
  var HUMAN_PACK = "kimi";
  function humanPack() { var p = data.settings && data.settings.pet; return (p && p.humanPack) || HUMAN_PACK; }
  // 改形象：读最新的账号设置，改 pet 这一块，带版本号写回
  function savePet(change) {
    return getJson("activity/settings").then(function (set) {
      if (!set) return null;
      var pet = Object.assign({}, set.pet); pet.packsByTool = Object.assign({}, pet.packsByTool || Pk.DEFAULT_PACKS_BY_TOOL); change(pet);
      return fetch(API + "activity/settings", { method: "PUT", headers: { "Content-Type": "application/json", "If-Match": '"' + set.version + '"' },
        body: JSON.stringify({ tiers: set.tiers, agentDirect: set.agentDirect, pet: pet }) }).then(function (r) { return r.ok ? r.json() : null; });
    }).catch(function () { return null; }).then(function (set) {
      if (set) data.settings = set; else stage.say("失败：形象没存上");
      render();
    });
  }
  function pickFigure(o) {
    FP.open({ title: o.title, base: BASE + "hive/pets/", current: o.current, near: o.near, placeholder: PLACEHOLDER, onPick: function (kind) { savePet(function (pet) { o.set(pet, kind); }); } });
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
      lx = dx / (o.unzoomed ? 1 : zoom()); ly = dy / (o.unzoomed ? 1 : zoom());   // unzoomed：元素在 body 上（不在被缩放的 .board-shell 里）
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
        getJson("views/tree").then(function (t) { if (t) data.tree = t; return dropItem(item, p.id); })
          .then(function (r) { busy--; render(); return Promise.resolve(r && r.ok ? refresh() : null).then(function () { if (o.afterCreate) o.afterCreate(); }); });
      }, onCancel: function () { if (o.afterCreate) o.afterCreate(); } });
      if (!made && Sp) Sp.removeSprout();
      if (o.onRelease) { var back = o.onRelease(target, cancel, lx, ly, made, on); if (back === "keep" || made) return; }
      if (made) { o.el.style.translate = ""; return; }
      var pid = on ? on.projectId : target ? target.dataset.projectId : null;
      function file(taskId) {
        busy++;
        dropItem(item, pid, { taskId: taskId }).then(function (r) {
          o.el.style.translate = "";
          if (r && r.reverted && pid) o.el.classList.add("st-bounce");
          busy--;
          if (r && r.ok && !taskId) fileBubble(item, pid, r);
          if (r && r.ok) PP.invalidate();
          if (r && r.ok && o.afterFile) o.afterFile();
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

  // 能「拖到空白处建项目」的元素：块、宠物（含格子边的）、计时中的时钟、眼睛、白条
  function fileable(item) { return item.kind !== "clock" || stage.timerRunning(); }
  function createName(item) { return item.kind === "block" ? item.block.displayName || "" : item.kind === "pet" ? item.agent || "" : item.kind === "focus" ? item.name : ""; }
  // 眼睛拖的是「此刻这个窗口」（kind "focus"）：落定时才去读它今天的块（要 date 与 seq）；还没有记录的块按 seq 0 发
  function dropItem(item, pid, opts) {
    if (item.kind !== "focus") return stage.drop(item, pid, opts);
    return getJson("activity/blocks?status=all&limit=1000").then(function (r) {
      if (!r || !r.date) { stage.say("失败：读不到窗口记录"); return { ok: false, reverted: true }; }
      var hit = (r.items || []).filter(function (b) { return b.blockKey === item.blockKey; })[0] || {};
      return stage.drop({ kind: "block", block: { date: r.date, blockKey: item.blockKey, seq: hit.seq || 0, seconds: hit.status === "pending" ? hit.seconds || 0 : 0, displayName: item.name } }, pid, opts);
    });
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
        packs[kind] = { moods: moods, image: ok(p.image) ? p.image : null, backdrop: p.backdrop }; renderPets(); renderEye(); if (centre) centre.redraw();
      }
    }).catch(function () { /* 素材还没下：占位形象 */ });
  }
  var PLACEHOLDER = '<svg class="st-art" width="42" height="39" viewBox="0 0 56 52" aria-hidden="true">' +
    '<ellipse cx="28" cy="30" rx="22" ry="18" fill="var(--attn, var(--focus))"/>' +
    '<path d="M10 16 L6 4 L19 11Z M46 16 L50 4 L37 11Z" fill="var(--attn, var(--focus))"/>' +
    '<circle cx="20" cy="27" r="3" fill="var(--panel)"/><circle cx="36" cy="27" r="3" fill="var(--panel)"/>' +
    '<circle cx="20.8" cy="27.4" r="1.4" fill="var(--ink)"/><circle cx="36.8" cy="27.4" r="1.4" fill="var(--ink)"/>' +
    '<path d="M23 36 q5 4 10 0" stroke="var(--ink)" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>';
  // 静态形象包（pack.json 的 image）：抠好底的图，没好之前先用占位
  var stills = {};
  function stillOf(kind) {
    if (kind in stills) return stills[kind];
    stills[kind] = null;
    FP.cutout(BASE + "hive/pets/" + encodeURIComponent(kind) + "/" + packs[kind].image.split("/").map(encodeURIComponent).join("/"), packs[kind].backdrop).then(function (u) {
      if (!u) { delete stills[kind]; return; }
      // 全身立绘（抠完底又高又窄）比 Q 版高一点（「二十四」）：量一次图的高宽比
      var im = new Image();
      im.onload = im.onerror = function () { stillTall[kind] = im.naturalHeight > im.naturalWidth * 1.5; stills[kind] = u; renderPets(); renderEye(); if (centre) centre.redraw(); };
      im.src = u;
    });
    return null;
  }
  var stillTall = {};
  function mediaFor(pet, file) {
    var still = !file && packs[pet.pack] && packs[pet.pack].image && stillOf(pet.pack);
    if (still) return { key: still, html: '<img class="st-art st-still' + (stillTall[pet.pack] ? " st-tall" : "") + '" alt="" src="' + esc(still) + '">' };
    if (!file) return { key: "svg", html: PLACEHOLDER };
    var url = BASE + "hive/pets/" + encodeURIComponent(pet.pack) + "/" + file.split("/").map(encodeURIComponent).join("/");
    return { key: url, video: url, loop: true, html: '<video class="st-art" src="' + esc(url) + '" autoplay loop muted playsinline></video>' };
  }
  // 宠物的画面只经这里换。已有 <video> 就换它的 src，不重建元素；换成别的之前先把播放器放掉。
  // 每换一段动作就新建一个 <video>，旧播放器要等回收才释放——开上几分钟越积越多，页面越来越卡（仓主 2026-11-12）。
  function releaseVideos(root) {
    [].forEach.call(root.querySelectorAll("video"), function (v) { try { v.pause(); v.removeAttribute("src"); v.load(); } catch (e) { /* 已经放掉了 */ } });
  }
  window.HexReleaseVideos = releaseVideos;
  // m = {video, loop, html}（mediaFor 的结果，或只有 html）；onError / onEnded 记在槽上，换画面时一起换
  function showMedia(slot, m, onError, onEnded) {
    slot.__onError = onError || null; slot.__onEnded = onEnded || null;
    var v = slot.firstElementChild;
    if (m.video && v && v.tagName === "VIDEO") {
      v.loop = m.loop !== false;
      if (v.getAttribute("src") !== m.video) { v.setAttribute("src", m.video); v.load(); var p = v.play(); if (p && p.catch) p.catch(function () { /* 还没能播：autoplay 会接着试 */ }); }
      return v;
    }
    releaseVideos(slot);
    slot.innerHTML = m.html;
    v = slot.querySelector("video");
    if (v) {
      v.addEventListener("error", function () { if (v.parentNode === slot && v.getAttribute("src") && slot.__onError) slot.__onError(); });
      v.addEventListener("ended", function () { if (v.parentNode === slot && slot.__onEnded) slot.__onEnded(); });
    }
    return v;
  }
  function showPlaceholder(slot) { slot.dataset.key = "svg"; showMedia(slot, { html: PLACEHOLDER }); }

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
      slot.dataset.key = m.key; showMedia(slot, m, function () { showPlaceholder(slot); });
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
    var s = ix[e.dataset.runId];
    showMedia(slot, { video: url, loop: !!w.loop, html: '<video class="st-art" src="' + esc(url) + '" autoplay ' + (w.loop ? "loop " : "") + "muted playsinline></video>" },
      function () { delete slot.dataset.over; showPlaceholder(slot); if (s) s.click = null; },
      w.loop ? null : function () { if (s) s.click = null; syncClip(e); });
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
    s.mode = "back"; s.from = from; s.to = e.__home; s.t0 = Date.now() - (afterMs || 0); s.walking = false; kickWalk();
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
  var PET_BIG_K = 1.7;
  // 鼠标在哪个项目格（或它的小人）上：那一格的小人飞到格子旁边，离开 0.5 秒飞回格子里（「二十二」2）
  var outPid = null, outTimer = 0;
  document.addEventListener("pointerover", function (ev) {
    if (dragging || !ev.target.closest) return;
    var pet = ev.target.closest(".st-pet.is-work"), cell = pet ? null : ev.target.closest(".hex-cell[data-project-id]");
    var pid = pet ? pet.dataset.cell : cell ? cell.dataset.projectId : null;
    clearTimeout(outTimer);
    if (pid) { if (pid !== outPid) { outPid = pid; renderPets(); } }
    else if (outPid) outTimer = setTimeout(function () { outPid = null; renderPets(); }, 500);
  });
  function layerPetK() { return parseFloat(layer.style.getPropertyValue("--pet-k")) || 1; }
  function renderPets() {
    if (layoutBusy()) return;   // 放大 / 缩小动画里格子还在路上：量到的是半路的位置
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
      var big = el.classList.contains("is-expanded");   // 展开成大六边形：小人放大、站到斜边外侧（「十二」4）
      var n = byCell[pid].length, bigK = big ? PET_BIG_K : 1;
      var r = big ? Pk.hexEdgeDistance(c.w, ux, uy) + 26 * bigK : Math.max(c.w, c.h) / 2 * 0.9 + 26;
      byCell[pid].forEach(function (p, i) {
        var o = (i - (n - 1) / 2) * 58 * bigK, e = petEl(p.runId), out = big || outPid === pid, rest = Pk.restSlot(i, c.w, c.h);
        e.classList.toggle("is-big", big); e.classList.toggle("is-in", !out); e.dataset.cell = pid;
        e.style.setProperty("--pet-k", big ? (layerPetK() * PET_BIG_K).toFixed(3) : "");   // 空 = 回到舞台层的值
        keep[p.runId] = 1;
        if (!e.classList.contains("is-work")) { e.classList.remove("is-loose"); e.classList.add("is-work"); }
        fillPet(e, p, runById(p.runId));
        e.__home = out ? { x: c.x + ux * r - uy * o, y: c.y + uy * r + ux * o } : { x: c.x + rest.x, y: c.y + rest.y };
        if (!ix[p.runId] || !ix[p.runId].mode) place(e, e.__home);
      });
    });
    slotOf = Pk.assignSlots(slotOf, idle.pets.map(function (p) { var r = runById(p.runId); return { runId: p.runId, startAt: r && r.startAt }; }));
    idle.pets.forEach(function (p) {
      var e = petEl(p.runId); keep[p.runId] = 1;
      var home = Pk.slotPos(slotOf[p.runId], SLOT_AREA);
      e.__home = home; e.style.removeProperty("--pet-k"); e.classList.remove("is-big");
      if (!e.classList.contains("is-loose")) {
        delete ix[p.runId]; e.classList.remove("is-work", "is-in", "st-away", "flip"); e.classList.add("is-loose"); place(e, home);
        var sl = e.querySelector(".st-media"); if (sl.dataset.over) { delete sl.dataset.over; sl.dataset.key = ""; }
      } else if (!ix[p.runId] || !ix[p.runId].mode) place(e, home);
      fillPet(e, p, runById(p.runId));
    });
    Object.keys(petEls).forEach(function (id) {
      if (keep[id]) return;
      if (petEls[id].classList.contains("st-dragging")) dragging--;   // 拖着时被移除：收不到 pointerup
      releaseVideos(petEls[id]); petEls[id].remove(); delete petEls[id]; delete ix[id];
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
    // 长按（不挪）= 挑这个 AI 的形象：按它的 tool 记进宠物包对照；随后那下松手不算点击
    var hold = { t: 0, x: 0, y: 0, fired: false };
    function unhold() { clearTimeout(hold.t); }
    e.addEventListener("pointerdown", function (ev) {
      if (ev.button > 0) return;
      unhold(); hold.fired = false; hold.x = ev.clientX; hold.y = ev.clientY;
      hold.t = setTimeout(function () {
        var r = e.__run; if (!r || e.classList.contains("st-dragging")) return;
        hold.fired = true;
        var key = r.tool || "*";
        pickFigure({ title: (r.tool || r.agent || "AI") + " 的形象", current: e.__pet.pack, near: e.querySelector(".st-pet-in"), set: function (pet, kind) { pet.packsByTool[key] = kind; } });
      }, CM.HOLD_MS);
    });
    e.addEventListener("pointermove", function (ev) { if (Pk.pressKind(ev.clientX - hold.x, ev.clientY - hold.y) !== "click") unhold(); });
    e.addEventListener("pointerup", unhold); e.addEventListener("pointercancel", unhold);
    drag(e, {
      el: e,
      item: function () {
        var r = e.__run;
        if (!r) return null;
        return { kind: "pet", runId: r.runId, agent: r.agent };
      },
      onClick: function () { if (hold.fired) { hold.fired = false; return; } poke(e); toggleCard({ runId: e.dataset.runId }, e); },
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
  function stepPets() {   // 动作节奏到点就换：每秒看一次就够（一段动作 15 秒 / 3 秒）
    Object.keys(petEls).forEach(function (id) { var e = petEls[id]; if (e.__pet) fillPet(e, e.__pet, e.__run); });
  }
  // 走回家：只在有宠物在走的时候逐帧推（以前每 100ms 挪一格，看着一顿一顿的）
  var walkRaf = 0;
  function walkFrame() {
    stepBack(Date.now());
    walkRaf = Object.keys(ix).some(function (id) { return ix[id].mode === "back"; }) ? requestAnimationFrame(walkFrame) : 0;
  }
  function kickWalk() { if (!walkRaf) walkRaf = requestAnimationFrame(walkFrame); }

  // ── 中心环 ────────────────────────────────────────────
  // 中心环 = 人 + 代理今天的总计时，内容与点开的统计页在 hex-centre-stats.js（hive.stage.v1「八」）
  function ringSnapshot(now) {
    var lanes = data.lanes || {}, human = lanes.human || {}, f = (data.current && data.current.focus) || human.focus, running = human.running, humanNow = null;
    if (running) humanNow = "你在做 " + (running.projectId ? projectName(running.projectId) : "手动计时");
    else if (f && f.state === "afk") humanNow = "你离开了";
    else if (f && (f.projectName || f.projectId)) humanNow = "你在做 " + (f.projectName || projectName(f.projectId));
    var hit = St.pickRing(runsOf(), lanes.interactions, now, dayStart() || null), run = hit && hit.agent !== undefined ? hit : null, focus = null;
    if (run) {   // 人此刻在看的代理：一句轮播
      var sec = run.endAt ? (ms(run.endAt) - ms(run.startAt)) / 1000 : (now - ms(run.startAt)) / 1000;
      if (run.lost && run.lastSeenAt) sec = (ms(run.lastSeenAt) - ms(run.startAt)) / 1000;
      focus = { name: run.label || run.agent, text: hms(sec) };
    }
    return { humanNow: humanNow, humanSec: humanTodaySeconds(now), focus: focus,
      working: runsOf().filter(function (r) { return !r.endAt && !r.lost && Pk.moodOf(r.phase, r.lost) === "working"; }).length };
  }
  // Agent 页卡片里的小人：打招呼（happy）→ 点击（click）→ 待机（idle）的第一段
  function figureOf(kind) {
    loadPack(kind);
    var m = (packs[kind] && packs[kind].moods) || {}, f = (m.happy && m.happy[0]) || (m.click && m.click[0]) || (m.idle && m.idle[0]) || null, o = mediaFor({ pack: kind }, f);
    return { key: o.key, html: '<div class="st-media">' + o.html + "</div>" };
  }
  var centre = CS.mount({ packOfTool: function (tool) { return Pk.packOf({ tool: tool }, packsByTool()); }, figureOf: figureOf, petEl: function (id) { return petEls[id] || null; }, layer: layer, ring: $("stRing"), clock: $("stClock"), getJson: getJson, getLanes: function () { return data.lanes; }, snapshot: ringSnapshot, base: BASE, placeholder: PLACEHOLDER,
    projectName: projectName, cellColor: function (pid) { var c = cellEl(pid); return c ? getComputedStyle(c).getPropertyValue("--zone-raw").trim() : ""; } });
  function renderRing(now) {
    var el = $("stRing"), hc = hiveCenter();
    place(el, hc);
    // 计时环正好放进中心格（设计稿 140px，按中心格宽度等比缩放；展开推镜时中心格变大也不超过原大）
    var k = hc.w ? Math.min(1, hc.w / 140) : 1;
    el.style.transform = k < 1 ? "translate(-50%, -50%) scale(" + k.toFixed(3) + ")" : "";
    el.dataset.k = k.toFixed(3);
    // 宠物跟蜂巢一起缩（蜂巢按屏幕等比缩小的倍数；只缩不放）
    var cam = document.querySelector(".hive-cam"), m = cam && /scale\(([\d.]+)/.exec(cam.style.transform || "");
    layer.style.setProperty("--pet-k", (m ? Math.min(1, +m[1]) : 1).toFixed(3));
    centre.renderRing(now);
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
    var tm = stage.timerState(), today = el.querySelector(".st-clock-today");
    if (tm && tm.deadlineAt) {
      today.textContent = CM.formatRemaining(CM.remainingSeconds(tm.deadlineAt, now)); today.classList.add("is-countdown");
      if (now >= tm.deadlineAt && countdownDone !== tm.startAt) {   // 页面开着时到点：先提醒，再读一次（服务端已按 deadlineAt 记下）
        countdownDone = tm.startAt; clockMenu.remind(CM.endedText(tm.startAt, tm.deadlineAt)); stage.setTimer(null); refresh();
      }
    } else { today.textContent = dur(humanTodaySeconds(now)); today.classList.remove("is-countdown"); }
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
  // ── 眼睛：人此刻的聚焦（hive.stage.v1「十」）。时钟不动，飞来飞去的是它 ──
  // 形象：人自己挑的那个宠物包，右上角一滴汗 = 这是人
  var EYE = '<div class="st-media"></div><svg class="st-eye-drop" viewBox="0 0 12 16" aria-hidden="true"><path d="M6 1C8.6 5 10.5 7.6 10.5 10.2a4.5 4.5 0 0 1-9 0C1.5 7.6 3.4 5 6 1Z"/></svg><span class="st-eye-win"></span>';
  function focusNow() { return (data.current && data.current.focus) || (data.lanes && data.lanes.human && data.lanes.human.focus) || null; }
  function eyeNow() { return St.eyeHost(focusNow(), stage.timerState()); }
  function hostProject() { var h = eyeNow(); return h.at === "project" ? h.project : null; }
  // 拖眼睛拖的是什么：计时中 = 这次计时（同拖时钟）；否则 = 此刻这个窗口；离开 / 没有心跳 = 拖不动
  function eyeItem() {
    if (stage.timerRunning()) return { kind: "clock" };
    var f = focusNow();
    return f && f.state !== "afk" && f.blockKey ? { kind: "focus", blockKey: f.blockKey, label: St.focusLabel(f), name: f.displayName || "" } : null;
  }
  var eye = { x: 0, y: 0, host: null, raf: 0, hold: false, back: false, filed: false };
  function eyeTarget(h) {
    var el = h.at === "project" && cellEl(h.project), k = zoom();
    if (el) { var c = rectCenter(el); return { x: c.x + c.w * 0.3, y: c.y - c.h * 0.36 }; }
    if (h.at === "tray") { var b = $("stBucketBtn").getBoundingClientRect(); return toLayer(b.right + 26 * k, b.top + b.height * 0.62); }
    var ck = $("stClock").getBoundingClientRect();
    return toLayer(ck.left + 2 * k, ck.top + 2 * k);   // 时钟卡左上角：像别在时钟上
  }
  function renderEye() {
    var el = $("stEye"), h = eyeNow(), key = h.at === "project" ? h.project : h.at, f = focusNow();
    if (!el.firstChild) el.innerHTML = EYE;
    el.dataset.host = key;
    el.classList.toggle("is-closed", !!h.closed);
    el.classList.toggle("is-timing", h.by === "timer");
    el.classList.toggle("is-live", !!eyeItem());
    el.querySelector(".st-eye-win").textContent = h.at === "tray" && f ? St.focusLabel(f) : "";
    var kind = humanPack(), slot = el.querySelector(".st-media");
    loadPack(kind);
    eye.rhythm = Pk.rhythm(eye.pack === kind ? eye.rhythm : null, packs[kind] && packs[kind].moods, h.closed ? "idle" : h.by === "timer" ? "working" : "waiting", Date.now(), Math.random);
    eye.pack = kind;
    var m = mediaFor({ pack: kind }, eye.rhythm.file);
    if (slot.dataset.key !== m.key) {
      slot.dataset.key = m.key; showMedia(slot, m, function () { showPlaceholder(slot); });
    }
    if (eye.hold || el.classList.contains("st-dragging") || layoutBusy()) return;   // 拖着 / 等确认卡 / 格子还在动画里：留在原地
    var t = eyeTarget(h);
    if (eye.host === null) { eye.host = key; eye.x = t.x; eye.y = t.y; place(el, t); return; }
    if (eye.host === key && !eye.back) { if (!eye.raf) { eye.x = t.x; eye.y = t.y; place(el, t); } return; }
    // 离开未分类窗口：托盘先掉一块占位（真块到了认领这一格）；刚把这个窗口归了类的不掉
    if (eye.host === "tray" && key !== "tray" && !eye.filed) { ghost = "\u0000pending:" + Date.now(); ghostUntil = Date.now() + 15000; renderBucket(); }
    eye.host = key; eye.back = eye.filed = false;
    cancelAnimationFrame(eye.raf); eye.raf = 0;
    if (reduced()) { eye.x = t.x; eye.y = t.y; place(el, t); return; }
    var from = { x: eye.x, y: eye.y }, dx = t.x - from.x, dy = t.y - from.y, l = Math.hypot(dx, dy) || 1, k = Math.min(110, l * 0.35);
    var qx = (from.x + t.x) / 2 - dy / l * k, qy = (from.y + t.y) / 2 + dx / l * k - 20, t0 = performance.now(), DUR = 600;
    (function step(nowT) {
      var u = Math.min(1, (nowT - t0) / DUR), e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2, a = (1 - e) * (1 - e), b = 2 * (1 - e) * e, c = e * e;
      eye.x = a * from.x + b * qx + c * t.x; eye.y = a * from.y + b * qy + c * t.y;
      place(el, eye);
      if (u < 1) eye.raf = requestAnimationFrame(step); else eye.raf = 0;
    })(t0);
  }
  function eyeRelease() { eye.hold = false; eye.back = true; renderEye(); }   // 确认卡关了 / 放空了：从落点飞回它该在的地方
  // 拖眼睛到项目格 / 任务条：弹确认卡（同宠物那一叠，眼睛只留一张），确认了才改。窗口可勾「以后这个窗口都算」
  function openEyeConfirm(item, pid, taskId, after) {
    after = after || eyeRelease;   // 人的页面里拖计时卡也走这张确认卡，收尾各自管
    closeFileBubble();
    var cell = cellEl(pid);
    if (!cell) { after(); return; }
    confirms.filter(function (x) { return x.dataset.eye; }).forEach(dropConfirm);
    var tk = taskId && taskOf(taskId), name = tk ? tk.name : projectName(pid), timing = item.kind === "clock", el = document.createElement("div");
    el.className = "st-confirm st-card"; el.setAttribute("role", "dialog"); el.dataset.eye = "1";
    el.innerHTML = '<button type="button" class="st-cf-x" aria-label="取消">✕</button><div class="st-cf-title"></div>' +
      '<div class="st-cf-btns"><button type="button" class="st-cf-ok">确认</button></div>' +
      (timing ? "" : '<label class="st-cf-rem"><input type="checkbox" class="st-cf-box"><span></span></label>');
    el.querySelector(".st-cf-title").textContent = timing ? "把这次计时记到 " + name : "把 " + item.label + " 算到 " + name;
    if (!timing) el.querySelector(".st-cf-rem span").textContent = "以后这个窗口都算 " + name;
    confirmStack(pid, rectCenter(cell)).appendChild(el);
    if (!confirms.length) document.addEventListener("keydown", onConfirmKey, true);
    confirms.push(el);
    el.__done = function (ok) {
      var box = el.querySelector(".st-cf-box"), remember = !!(box && box.checked);
      dropConfirm(el);
      if (!ok) { after(); return; }
      busy++;
      dropItem(item, pid, { remember: remember, taskId: taskId }).then(function (r) {
        busy--; eye.filed = !!(r && r.ok); if (r && r.ok) PP.invalidate(); render();
        return Promise.resolve(r && r.ok ? refresh() : null).then(after);
      });
    };
    el.querySelector(".st-cf-x").addEventListener("click", function () { el.__done(null); });
    el.querySelector(".st-cf-ok").addEventListener("click", function () { el.__done(true); });
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
    renderTrayChips(trayKeys(stage.bucketKeys()));
    if ($("stDrawer").classList.contains("open")) renderDrawer();
  }
  var drawerSig = "";
  var BUCKET_MIN_KEY = "hive.unclassifiedMinMinutes";
  function bucketMin() { var v = null; try { v = localStorage.getItem(BUCKET_MIN_KEY); } catch (e) {} return St.bucketMinutes(v); }
  function renderDrawer() {
    var box = $("stDrawer"), rows = stage.drawer(), sig = JSON.stringify(rows.map(function (r) { return [r.name, r.seconds, r.draggable]; }));
    renderGreyPop(rows);
    if ((sig === drawerSig && box.firstChild) || box.contains(document.activeElement)) return;   // 正在改过滤器的数：不重画
    drawerSig = sig;
    box.innerHTML = "";
    // 过滤器：满 x 分钟的窗口才进入（记在这台设备上）
    var f = document.createElement("label"); f.className = "st-d-filter";
    f.innerHTML = '满 <input type="number" min="0" max="120" step="1" inputmode="numeric" aria-label="满几分钟的窗口才进入未分类"> 分钟的窗口才进入';
    f.firstElementChild.value = bucketMin();
    f.firstElementChild.addEventListener("change", function () {
      try { localStorage.setItem(BUCKET_MIN_KEY, String(St.bucketMinutes(this.value))); } catch (e) {}
      drawerSig = ""; applyData(); render();
    });
    box.appendChild(f);
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

  // ── 项目大六边形里的面板（hive.stage.v1「十二」）──────────
  // 点项目格 = hex-app 的老展开；面板（任务条 + 人 / 代理时间标尺）填进展开格里的 .hex-ruler-slot。
  // panelPid = 当前展开的项目；hex-app 重写格子内容（刷新、改名）后槽是空的，由观察器补回。
  function openPanel(pid) {   // 拖着东西在格子上停满 1 秒：展开这一格
    var App = window.NexusTableHexApp;
    if (App && pid) App.expand(pid);
    syncPanel();
  }
  var panelHtml = "";
  // tree 取 hex-app 重读的那棵：完成 / 取消 / 改名 / 删之后它最新
  var PP = Pr.createPanel({ tree: function () { var A = window.NexusTableHexApp, st = A && A.getState && A.getState(); return (st && st.tree) || data.tree; }, getJson: getJson, dur: dur, open: openPanel, taskApi: function () { return window.NexusTableData; },
    changed: function () { renderPanel(); syncFull(); },
    recentCount: function (pid) { return recentOf(pid).length; },
    recentHtml: function (pid) {   // 「最近完成」点开才生成：每条沿用 hex-cards 的卡片（点开只给「撤销」）
      var Cd = window.NexusTableHexCards;
      return recentOf(pid).map(function (c) {
        return Cd.taskCard({ id: c.taskId, name: c.taskName || c.path, done: true, canceled: !!c.canceled }, { done: true, stamp: c.stamp, session: c.session });
      }).join("");
    },
    created: function () { getJson("views/tree").then(function (t) { if (t) data.tree = t; PP.invalidate(); render(); }); },
    cellColor: function (pid) { var c = cellEl(pid); return c ? getComputedStyle(c).getPropertyValue("--zone-raw").trim() : ""; } });
  function recentOf(pid) {   // 这个项目最近完成 / 取消的任务（hex-app 读来的流水），最多 10 条
    var App = window.NexusTableHexApp, st = App && App.getState && App.getState();
    return ((st && st.completions) || []).filter(function (c) { return c.projectId === pid; }).slice(0, 10);
  }
  // 「展开全部」开着：大六边形占满蜂巢（「十九」）；收起、换项目、关掉就回平时的大小
  function syncFull() {
    var c = expandedCell(), on = !!c && PP.expandedAll(), L = window.NexusTableHexLayout, resize = false;
    [].forEach.call(document.querySelectorAll(".hex-cell.is-full"), function (el) { if (el !== c || !on) { el.classList.remove("is-full"); resize = resize || el === c; } });
    if (on && !c.classList.contains("is-full")) { c.classList.add("is-full"); resize = true; }
    if (resize && L) L.apply(true);
  }
  function expandedCell() { return document.querySelector(".hex-cell.is-expanded[data-project-id]"); }
  function syncPanel() {
    var c = expandedCell(), pid = c ? c.dataset.projectId : null;
    if (pid !== panelPid) { panelPid = pid; panelHtml = ""; PP.reset(); }
    if (pid) renderPanel();
    syncFull();
    renderPets();   // 展开 / 收起：干活的小人跟着放大 / 缩回
  }
  // AI 决策页（hex-decide / hex-minihive）刚展开项目就要找任务行：面板同步画好；任务被收在「展开全部」里的话先展开
  window.HexProjectPanel = { reveal: function (taskId) {
    syncPanel();
    var sel = '.hex-cell.is-expanded .pr-row[data-task-id="' + (window.CSS && CSS.escape ? CSS.escape(taskId || "") : taskId) + '"]';
    if (taskId && !document.querySelector(sel)) PP.expandAll();
  } };
  function renderPanel() {
    if (!panelPid || PP.frozen()) return;
    var slot = document.querySelector(".hex-cell.is-expanded .hex-ruler-slot");
    if (!slot) return;
    var html = PP.body(panelPid, Date.now());
    if (html !== panelHtml || !slot.firstChild) { panelHtml = html; slot.innerHTML = html; }
    [].forEach.call(document.querySelectorAll(".hex-cell.is-expanded .hex-spent"), function (el) {
      var sp = PP.spent(el.dataset.taskId);
      var tx = sp && Pr.spentText(el.dataset.canceled === "1", sp.human, sp.agent);
      if (tx && el.textContent !== tx) el.textContent = tx;
    });
  }
  window.addEventListener("hive:refreshed", function () { renderPanel(); });   // 完成 / 取消 / 改名 / 删之后 hex-app 重读了树与流水：泳道与最近完成跟着换
  document.addEventListener("click", function (ev) {   // 点「＋ 新建任务」（「十二」）
    var t = ev.target, q = t.closest && t.closest(".hex-ruler-slot");
    if (!q) return;
    if (t.closest(".pr-newslot")) PP.startCreate();
    else if (t.closest(".pr-expand")) PP.toggleExpand();
    else if (t.closest(".pr-donehead")) PP.toggleDone();
    else if (t.closest(".pr-row .pr-name")) PP.toggleMenu(t.closest(".pr-row").dataset.task);   // 点任务名：完成 / 改名 / 取消 / 删
  });
  new MutationObserver(function () {
    var c = expandedCell(), slot = c && c.querySelector(".hex-ruler-slot");
    if (c ? c.dataset.projectId === panelPid && slot && slot.firstChild : !panelPid) return;   // 没变：不重画
    syncPanel();
  }).observe(document.getElementById("hive") || document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });

  // 人的页面（「十四」）：逐任务那一张的名字与归并键；收件箱不算项目，未记到项目的计时叫「手动计时」
  function timeLabelOf(taskId, projectId, live) {
    var pid = projectId || (taskId && projectOfTask(taskId)) || null;
    if (pid === "inbox" || pid === "p_inbox") pid = null;
    var p = pid && projects().filter(function (x) { return x.id === pid; })[0], t = taskId && taskOf(taskId);
    if (t && p && p.unclassifiedTaskId !== taskId) return { key: "t:" + taskId, name: t.name, projectId: pid };
    if (pid) return { key: "p:" + pid, name: projectName(pid), projectId: pid };
    return live ? { key: "running", name: "手动计时", projectId: null } : { key: "none", name: "未分类", projectId: null };
  }
  // 左边的大立绘：同眼睛，画 humanPack 的那个包（静态包 = 抠好底的图）
  var humanFig = { pack: null, rhythm: null };
  function fillHumanFigure(slot) {
    var kind = humanPack();
    loadPack(kind);
    humanFig.rhythm = Pk.rhythm(humanFig.pack === kind ? humanFig.rhythm : null, packs[kind] && packs[kind].moods, "waiting", Date.now(), Math.random);
    humanFig.pack = kind;
    var m = mediaFor({ pack: kind }, humanFig.rhythm.file);
    if (slot.dataset.key === m.key) return;
    slot.dataset.key = m.key; showMedia(slot, m, function () { showPlaceholder(slot); });
  }
  // 人的页面里的卡片拖起来时离开原位置（fixed）免得被卡片栏裁掉，原处留一个同高的空位；home() 放回
  function liftCard(card) {
    var hole = null;
    return {
      grab: function () {
        var r = card.getBoundingClientRect();
        hole = document.createElement("div"); hole.className = "hp-hole"; hole.style.height = r.height + "px"; card.before(hole);
        card.style.position = "fixed"; card.style.left = r.left + "px"; card.style.top = r.top + "px"; card.style.width = r.width + "px"; card.style.height = r.height + "px";
      },
      home: function () { ["position", "left", "top", "width", "height", "translate"].forEach(function (k) { card.style[k] = ""; }); if (hole) { hole.remove(); hole = null; } }
    };
  }
  // 逐任务那一张里正在跑的计时卡：拖到项目格 / 任务条 = 弹确认卡「把这次计时记到 …」；落在别处卡片回原位
  function dragTimerCard(card, pid) {
    var lift = liftCard(card);
    drag(card, { el: card, unzoomed: true, item: function () { return stage.timerRunning() ? { kind: "clock" } : null; },
      onClick: function () { if (pid) { clockMenu.close(); openPanel(pid); } },
      onGrab: lift.grab,
      afterCreate: lift.home,
      onRelease: function (target, cancel, dx, dy, made, on) {
        if (made) { lift.home(); return "keep"; }
        var to = on ? on.projectId : target && !cancel ? target.dataset.projectId : null;
        lift.home();
        if (to) openEyeConfirm({ kind: "clock" }, to, (on && on.taskId) || null, function () {});
        return "keep";
      } });
  }
  // 窗口记录里的卡片：同未分类桶里的窗口块，拖到项目格 / 任务泳道 = 归类（含改归）；落在别处回原位不发请求。归完 done() 让卡片重读颜色
  function dragBlockCard(card, block, done) {
    var lift = liftCard(card);
    drag(card, { el: card, unzoomed: true, item: function () { return { kind: "block", block: block }; },
      onGrab: lift.grab, afterCreate: done, afterFile: done,
      onRelease: function (target, cancel, dx, dy, made) { lift.home(); return made ? "keep" : undefined; } });
  }

  // 时钟：短按 = 人的页面（三张卡），长按 = 计时气泡（正计时 / 倒计时 / 停止）；计时中拖时钟到格子 = 记到该项目。时钟自己不挪位
  var clockMenu;
  (function () {
    var el = $("stClock");
    var clockItem = function () { return { kind: "clock" }; };
    var after = function (p) { busy++; return p.then(function () { busy--; render(); refresh(); }); };
    clockMenu = CM.mount({
      clock: el, now: Date.now, getLanes: function () { return data.lanes; }, projectName: projectName, getJson: getJson,
      projectColor: function (pid) { var c = cellEl(pid); return c ? getComputedStyle(c).getPropertyValue("--zone-raw").trim() : ""; },
      taskLabel: timeLabelOf, fillFigure: fillHumanFigure, dragTimer: dragTimerCard, dragBlock: dragBlockCard,
      openProject: function (pid) { clockMenu.close(); openPanel(pid); },
      fillIssues: function (box, list) { window.HexSourceHealth.fill(box, list); }, timerRunning: function () { return stage.timerRunning(); },
      startClock: function (sec) { after(stage.startClock(sec)); }, stopClock: function () { after(stage.clickClock()); },
      pickHuman: function () { pickFigure({ title: "我的形象", current: humanPack(), near: el, set: function (pet, kind) { pet.humanPack = kind; } }); }
    });
    var tap = function () { if (!clockMenu.consumedHold()) clockMenu.open(); };
    // 眼睛：点 = 同点时钟；拖到项目格 / 任务条 = 弹确认卡再改；拖到空白处亮满 = 建项目（那张表单就是确认）；别处 = 飞回
    drag($("stEye"), { el: $("stEye"), item: eyeItem, onClick: tap, afterCreate: eyeRelease,
      onGrab: function () { cancelAnimationFrame(eye.raf); eye.raf = 0; },
      onRelease: function (target, cancel, dx, dy, made, on) {
        var e = $("stEye"), item = eyeItem(), h = eyeNow();
        e.style.translate = ""; eye.x += dx; eye.y += dy; place(e, eye); eye.hold = true;
        if (made) return "keep";
        var pid = on ? on.projectId : target && !cancel ? target.dataset.projectId : null, tid = (on && on.taskId) || null;
        if (item && on && on.create) {
          PP.awaitName(function (taskId) { getJson("views/tree").then(function (t) { if (t) data.tree = t; openEyeConfirm(item, on.projectId, taskId); }); }, eyeRelease);
        } else if (item && pid && (tid || h.at !== "project" || h.project !== pid)) openEyeConfirm(item, pid, tid);
        else eyeRelease();
        return "keep";
      } });
    drag(el, { el: el, item: clockItem, onClick: tap });
    el.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); clockMenu.open(); }
      else if (ev.key === "ArrowDown") { ev.preventDefault(); clockMenu.showActions(); }
    });
  })();

  var ghost = null, ghostUntil = 0, countdownDone = null;
  // 刚结束的窗口块还没被轮询带来时，先掉一块占位；真块到了就认领占位那一格，不会出两块
  function trayKeys(keys) {
    if (!ghost) return keys;
    var fresh = keys.filter(function (k) { return !(k in traySlot); });
    if (ghost in traySlot) {
      if (fresh.length) {
        traySlot[fresh[0]] = traySlot[ghost]; trayChips[fresh[0]] = trayChips[ghost];
        delete traySlot[ghost]; delete trayChips[ghost]; ghost = null; return keys;
      }
    } else if (fresh.length) { ghost = null; return keys; }
    if (Date.now() > ghostUntil) { ghost = null; return keys; }
    return keys.concat(ghost);
  }

  // ── 汇总 ───────────────────────────────────────────
  function render() {
    var now = Date.now();
    renderRing(now); renderClock(now); renderEye(); renderStrips(); renderBucket(); renderPets();
    [].forEach.call(layer.querySelectorAll(".st-pet"), bindPetDrag);
    renderToast();
    if (panelPid) renderPanel();
    if (card && card.__fill) card.__fill();
  }
  function applyData() {
    var lanes = data.lanes, running = lanes && lanes.human && lanes.human.running;
    var was = stage.timerState();   // 倒计时刚到点、服务端已收尾（轮询赶在秒表前读到）：同样提醒一次
    if (was && was.deadlineAt && !running && Date.now() >= was.deadlineAt - 1500 && countdownDone !== was.startAt) {
      countdownDone = was.startAt; clockMenu.remind(CM.endedText(was.startAt, was.deadlineAt));
    }
    stage.setTimer(running ? { projectId: running.projectId || null, startAt: ms(running.startAt), deadlineAt: ms(running.deadlineAt) || null } : null);
    var b = data.blocks, date = b && b.date, items = ((b && b.items) || []).filter(function (x) { return x.status ? x.status === "pending" : true; })
      .map(function (x) { return Object.assign({ date: date, legible: false, kind: "other" }, x); });
    items = St.bucketFilter(items, bucketMin());
    stage.setBucket(items.reduce(function (s, x) { return s + (x.seconds || 0); }, 0), items);
    stage.setStrips(data.unplaced || []);
  }

  var polls = 0;
  function refresh() {
    var slow = polls++ % 6 === 0;
    // 三级分类关着时不读窗口块、也不多读账号设置（决策栏启动后会在 .hd-panel 上挂 data-tiers）
    var tiers = !!document.querySelector(".hd-panel[data-tiers]");
    // 账号设置：启动读一次（形象要用）；之后慢轮询只在三级分类开着、或还没读到时
    return (slow && (tiers || !data.settings) ? getJson("activity/settings") : Promise.resolve(null)).then(function (set) {
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
      if (r[5] && window.HexSourceHealth) clockMenu.setIssues(window.HexSourceHealth.problems(r[5].devices));
      applyData(); render();
    });
  }

  function sizeToWindow() {
    var w = window.innerWidth, k = w >= 1280 || w < 1024 ? 1 : w / 1280;
    shell.style.zoom = k === 1 ? "" : String(k);
    render();
  }
  window.addEventListener("resize", sizeToWindow);
  // 未分类桶钉在视口上：页面一滚，停在桶边的人的形象要跟着重新落位
  var scrollRaf = 0;
  window.addEventListener("scroll", function () { if (!scrollRaf) scrollRaf = requestAnimationFrame(function () { scrollRaf = 0; if ($("stEye").dataset.host === "tray") renderEye(); }); }, { passive: true });
  new MutationObserver(function () { if ($("stClock").querySelector(".st-dial")) renderEye(); }).observe(shell, { attributes: true, attributeFilter: ["hidden"], subtree: true });   // 决策栏出现 / 消失 → 时钟挪位，眼睛立刻跟过去

  // 蜂巢放大 / 缩小（hex-layout 发 hexlayout 时格子已是终态、动画还没起）：
  // 小人与人的形象按终态摆好，再从原位起一段同时长、同缓动的动画 —— 和格子、镜头同一条时间线
  var following = false, followEnd = 0;
  function layoutBusy() { var L = window.NexusTableHexLayout; return !following && !!(L && L.busy && L.busy()); }
  document.addEventListener("hexlayout", function (ev) {
    if (!data.lanes) return;
    var els = [].slice.call(layer.querySelectorAll(".st-pet")).concat($("stEye"), $("stMore"));
    var was = els.map(function (e) { return { x: parseFloat(e.style.left), y: parseFloat(e.style.top), big: e.classList.contains("is-big") }; });
    // 干活的小人平时挪窝走 900ms 的 left/top 过渡：这一回关掉，改由下面那段同时长的动画带着走
    layer.classList.add("st-following"); clearTimeout(followEnd);
    followEnd = setTimeout(function () { layer.classList.remove("st-following"); }, ev.detail.ms + 60);
    following = true;
    try { renderEye(); renderPets(); } finally { following = false; }
    els.forEach(function (e, i) {
      var dx = was[i].x - parseFloat(e.style.left), dy = was[i].y - parseFloat(e.style.top);
      var big = e.classList.contains("is-big"), k = big === was[i].big ? 1 : big ? 1 / PET_BIG_K : PET_BIG_K;   // 小人变大 / 变回：个头也在这条时间线上长
      if (!e.isConnected || !e.animate || !isFinite(dx) || !isFinite(dy) || (k === 1 && Math.abs(dx) < 1 && Math.abs(dy) < 1)) return;
      e.animate([{ translate: dx.toFixed(1) + "px " + dy.toFixed(1) + "px", scale: String(k) }, { translate: "0px 0px", scale: "1" }], { duration: ev.detail.ms, easing: ev.detail.ease });
    });
  });

  refresh().then(function () { sizeToWindow(); });
  setInterval(refresh, POLL_MS);
  setInterval(function () { var n = Date.now(); renderRing(n); renderClock(n); renderToast(); clockMenu.refresh(); stepPets(); }, TICK_MS);
  setInterval(function () { renderEye(); renderPets(); }, 400);
})();
