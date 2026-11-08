// hive · hex-onboard.js —— 新手引导（onboarding.v1「七」，2026-11-08 起非阻断卡片）：建分区 → 建项目与任务 → AI 放窗口 → 结果。
// 上半是纯函数（显示条件 / 硬限制 / 自动 claim 的挑选，给 hex-onboard.test.js 用），下半是 DOM（文字一律 textContent）。
// 建分区 / 项目 / 任务走 data.js 现有的 createZone / createProject / createTask；任何一步都能「跳过」，跳过与走完都调 onboarding/done。
(function () {
  "use strict";

  var MAX_ZONES = 2, MAX_PROJECTS = 2, AUTO_SECONDS = 600;

  // ── 纯逻辑 ──
  // 老账号（已有分区）不出；设置 / 树读不到（null）不出；onboardingDoneAt 必须是 null（缺键不算）
  function shouldShowOnboarding(settings, tree) {
    return !!settings && settings.onboardingDoneAt === null && !!tree && Array.isArray(tree.zones) && tree.zones.length === 0;
  }
  function newDraft() { return { step: 1, zones: [] }; }
  function clone(d) { return JSON.parse(JSON.stringify(d)); }
  function zoneIn(d, id) { return d.zones.filter(function (z) { return z.id === id; })[0] || null; }
  function projectIn(d, id) {
    var hit = null;
    d.zones.forEach(function (z) { z.projects.forEach(function (p) { if (p.id === id) hit = p; }); });
    return hit;
  }
  function canAddZone(d) { return d.zones.length < MAX_ZONES; }
  function canAddProject(d, zoneId) { var z = zoneIn(d, zoneId); return !!z && z.projects.length < MAX_PROJECTS; }
  function addZone(d, z) {
    if (!canAddZone(d)) return d;
    var n = clone(d); n.zones.push({ id: z.id, name: z.name, projects: [] }); return n;
  }
  function addProject(d, zoneId, p) {
    if (!canAddProject(d, zoneId)) return d;
    var n = clone(d); zoneIn(n, zoneId).projects.push({ id: p.id, name: p.name, tasks: [] }); return n;
  }
  function addTask(d, projectId, t) {
    if (!projectIn(d, projectId)) return d;
    var n = clone(d); projectIn(n, projectId).tasks.push({ id: t.id, name: t.name }); return n;
  }
  // 能不能进下一步：1 要有分区；2 每个分区至少一个项目、每个项目至少一个任务
  function canNext(d) {
    if (d.step === 1) return d.zones.length > 0;
    if (d.step === 2) {
      return d.zones.length > 0 && d.zones.every(function (z) {
        return z.projects.length > 0 && z.projects.every(function (p) { return p.tasks.length > 0; });
      });
    }
    return true;
  }
  function targetsOf(d) {
    var out = [];
    d.zones.forEach(function (z) { z.projects.forEach(function (p) { out.push({ projectId: p.id, taskIds: p.tasks.map(function (t) { return t.id; }) }); }); });
    return out;
  }
  // results: [{target:{taskId|projectId}, items:[候选]}]；match 为 agent / name 且 seconds ≥ 600 的块，同一块名只给先到的目标
  function pickAutoClaims(results) {
    var seen = {}, groups = [];
    (results || []).forEach(function (r) {
      if (!r || !r.target || !Array.isArray(r.items)) return;
      var g = null;
      r.items.forEach(function (i) {
        if (!i || seen[i.blockKey] || (i.match !== "agent" && i.match !== "name") || !(i.seconds >= AUTO_SECONDS)) return;
        seen[i.blockKey] = true;
        if (!g) {
          g = r.target.taskId ? { taskId: r.target.taskId, blockKeys: [], names: {} } : { projectId: r.target.projectId, blockKeys: [], names: {} };
          groups.push(g);
        }
        g.blockKeys.push(i.blockKey);
        g.names[i.blockKey] = i.displayName || i.blockKey;
      });
    });
    return groups;
  }
  // direct-log 条目：引导开始后（at ≥ startedMs）AI 直接施加、未被撤回的块动作 / 块规则
  function pickDirect(items, startedMs) {
    return (items || []).filter(function (i) {
      return i && (i.kind === "blockAction" || i.kind === "blockRule") && !i.undoneAt && Date.parse(i.at) >= startedMs && i.payload && i.payload.blockKey;
    });
  }
  // blockAction 带 date；blockRule 没有，取 at 的本地日期
  function directDate(i) {
    if (i.payload.date) return i.payload.date;
    var d = new Date(i.at), p = function (n) { return (n < 10 ? "0" : "") + n; };
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }
  // blocksByDate: {日期: activity/blocks 的 items}；读不到块就显示块名、0 秒。同一天同一块名的多条合成一行，秒数只算一次
  function directRows(items, blocksByDate) {
    var rows = [], by = {};
    items.slice().sort(function (a, b) { return Date.parse(b.at) - Date.parse(a.at); }).forEach(function (i) {
      var date = directDate(i), k = date + "\n" + i.payload.blockKey;
      if (by[k]) { by[k].directIds.push(i.id); return; }
      var b = ((blocksByDate || {})[date] || []).filter(function (x) { return x.blockKey === i.payload.blockKey; })[0];
      by[k] = { directIds: [i.id], blockKey: i.payload.blockKey, displayName: (b && b.displayName) || i.payload.blockKey, seconds: b && b.seconds > 0 ? b.seconds : 0 };
      rows.push(by[k]);
    });
    return rows;
  }
  function totalSeconds(rows) { return rows.reduce(function (s, r) { return s + r.seconds; }, 0); }
  function hoursText(seconds) {
    if (seconds < 360) return "不到 6 分钟";
    return String(Math.round(seconds / 360) / 10).replace(/\.0$/, "") + " 小时";
  }

  var H = { shouldShowOnboarding: shouldShowOnboarding, newDraft: newDraft, canAddZone: canAddZone, canAddProject: canAddProject,
    addZone: addZone, addProject: addProject, addTask: addTask, canNext: canNext, targetsOf: targetsOf, pickAutoClaims: pickAutoClaims, pickDirect: pickDirect, directDate: directDate, directRows: directRows, totalSeconds: totalSeconds, hoursText: hoursText };
  if (typeof module !== "undefined" && module.exports) { module.exports = H; return; }

  // ── DOM ──
  var BASE = window.HONEYCOMB_BASE || "/", ACT = BASE + "api/core/activity/";
  var D = window.NexusTableData;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function button(text, cls, onClick) {
    var b = el("button", "hob-btn" + (cls ? " " + cls : ""), text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function open(ctx, serverDate) {
    var draft = newDraft(), result = [], direct = [], startedMs = Date.parse(serverDate) || Date.now(), timer = null, loading = false, undoing = 0, doneIds = {}, blockCache = {}, busy = false, error = "";
    // 非阻断：右下角一张卡，页面照常能用；× 只收起（不记完成），页面左下角留一个小入口再打开
    var root = el("div", "hob"), rendered = false;
    root.setAttribute("role", "region");
    root.setAttribute("aria-label", "新手引导");
    var card = el("div", "hob-card");
    var closeBtn = button("×", "hob-close", function () { reopen.hidden = false; root.remove(); });
    closeBtn.setAttribute("aria-label", "收起新手引导");
    var reopen = button("新手引导", "hob-reopen", function () { reopen.hidden = true; document.body.appendChild(root); });
    reopen.hidden = true;
    root.appendChild(closeBtn);
    root.appendChild(card);
    document.body.appendChild(root);
    document.body.appendChild(reopen);

    function finish() {
      if (busy) return;
      busy = true;
      clearInterval(timer);
      ctx.postJson(ACT + "onboarding/done").then(function () {
        root.remove(); reopen.remove();
        if (ctx.refresh) ctx.refresh();
      });
    }

    function adder(placeholder, canAdd, make) {
      var row = el("div", "hob-row");
      var input = el("input", "hob-input");
      input.type = "text"; input.placeholder = placeholder; input.maxLength = 80;
      var add = button("添加", "", submit);
      add.disabled = busy || !canAdd;
      input.disabled = !canAdd;
      input.addEventListener("keydown", function (ev) { if (ev.key === "Enter") { ev.preventDefault(); submit(); } });
      function submit() {
        var name = input.value.trim();
        if (busy || !canAdd || !name) return;
        busy = true; error = "";
        make(name).then(function (r) {
          busy = false;
          if (!r.ok) error = r.message || "写入失败";
          render();
        });
      }
      row.appendChild(input); row.appendChild(add);
      return row;
    }
    function made(apply) {
      return function (r) { if (r.ok && r.data && r.data.id) draft = apply(r.data); return r; };
    }

    function step1() {
      card.appendChild(el("h2", "hob-title", "建分区"));
      draft.zones.forEach(function (z) { card.appendChild(el("div", "hob-line", z.name)); });
      card.appendChild(adder("分区名", canAddZone(draft), function (name) {
        return D.createZone(name).then(made(function (z) { return addZone(draft, { id: z.id, name: name }); }));
      }));
    }
    function step2() {
      card.appendChild(el("h2", "hob-title", "建项目和任务"));
      draft.zones.forEach(function (z) {
        var box = el("div", "hob-zone");
        box.appendChild(el("div", "hob-zname", z.name));
        z.projects.forEach(function (p) {
          var pb = el("div", "hob-proj");
          pb.appendChild(el("div", "hob-line", p.name));
          p.tasks.forEach(function (t) { pb.appendChild(el("div", "hob-task", t.name)); });
          pb.appendChild(adder("任务名", true, function (name) {
            return D.createTask(p.id, name).then(made(function (t) { return addTask(draft, p.id, { id: t.id, name: name }); }));
          }));
          box.appendChild(pb);
        });
        box.appendChild(adder("项目名", canAddProject(draft, z.id), function (name) {
          return D.createProject(z.id, name).then(made(function (p) { return addProject(draft, z.id, { id: p.id, name: name }); }));
        }));
        card.appendChild(box);
      });
    }
    function step3() { card.appendChild(el("h2", "hob-title", "AI 放窗口")); }
    function step4() {
      card.appendChild(el("h2", "hob-title", "结果"));
      var claimed = {};
      result.forEach(function (c) { claimed[c.blockKey] = true; });   // 同块已 claim：以 claim 行为主，直接施加的不再单列
      var rows = result.concat(direct.filter(function (d) { return !claimed[d.blockKey]; })), total = totalSeconds(rows);
      rows.forEach(function (r) {
        var row = el("div", "hob-row hob-result");
        row.dataset.block = r.blockKey;
        row.appendChild(el("span", "hob-name", r.displayName));
        row.appendChild(el("span", "hob-dur", hoursText(r.seconds)));
        var undo = button("撤销", "", function () {
          undo.disabled = true;
          undoing++;
          if (r.directIds) {   // 新到旧逐条撤；任一条失败就停，行保留
            var step = function () {
              if (!r.directIds.length) { direct = direct.filter(function (x) { return x !== r; }); error = ""; undoing--; return render(); }
              ctx.postJson(ACT + "direct-log/" + encodeURIComponent(r.directIds[0]) + "/undo").then(function (res) {
                if (res.ok) { doneIds[r.directIds.shift()] = true; step(); }
                else { error = res.message || "撤销失败"; undoing--; render(); }
              });
            };
            return step();
          }
          ctx.postJson(ACT + "blocks/claim/undo", { blockKeys: [r.blockKey] }).then(function (res) {
            undoing--;
            if (res.ok) { result = result.filter(function (x) { return x !== r; }); error = ""; }
            else { error = res.message || "撤销失败"; }
            render();
          });
        });
        row.appendChild(undo);
        card.appendChild(row);
      });
      if (result.length) card.appendChild(el("div", "hob-total", hoursText(total)));
    }

    // 对每个项目及其任务调候选，再按目标 claim；候选 / claim 失败的当作没有，不拦引导
    function placeWindows() {
      var calls = [];
      targetsOf(draft).forEach(function (t) {
        t.taskIds.forEach(function (id) { calls.push({ taskId: id }); });   // 任务在前：同一块先归任务
        calls.push({ projectId: t.projectId });
      });
      return Promise.all(calls.map(function (target) {
        var q = target.taskId ? "taskId=" + encodeURIComponent(target.taskId) : "projectId=" + encodeURIComponent(target.projectId);
        return ctx.getJson(ACT + "unclassified/candidates?" + q + "&days=35").then(function (r) {
          return { target: target, items: r.ok && r.data ? r.data.items : [] };
        });
      })).then(function (results) {
        var groups = pickAutoClaims(results);
        return Promise.all(groups.map(function (g) {
          var body = { blockKeys: g.blockKeys, remember: true };
          if (g.taskId) body.taskId = g.taskId; else body.projectId = g.projectId;
          return ctx.postJson(ACT + "blocks/claim", body).then(function (r) {
            ((r.ok && r.data && r.data.claimed) || []).forEach(function (c) {
              result.push({ blockKey: c.blockKey, displayName: g.names[c.blockKey] || c.blockKey, seconds: c.seconds });
            });
          });
        }));
      });
    }

    // 读 direct-log，再按日期读块补显示名和秒数；读不到当作没有
    // 有撤销在进行或上一轮没回来就不读；读回来时若撤销已开始则丢弃；刚撤掉的 id 不再回来；块按日期缓存
    function loadDirect() {
      if (loading || undoing) return Promise.resolve(false);
      loading = true;
      return ctx.getJson(ACT + "direct-log?limit=200").then(function (r) {
        var items = pickDirect(r.ok && r.data ? r.data.items : [], startedMs).filter(function (i) { return !doneIds[i.id]; }), dates = {};
        items.forEach(function (i) { dates[directDate(i)] = true; });
        var days = Object.keys(dates);
        days.forEach(function (d) { if (!blockCache[d]) blockCache[d] = ctx.getJson(ACT + "blocks?date=" + d + "&limit=1000").then(function (b) { if (!b.ok) delete blockCache[d]; return b; }); });
        return Promise.all(days.map(function (d) { return blockCache[d]; })).then(function (rs) {
          var by = {};
          days.forEach(function (d, k) { by[d] = rs[k].ok && rs[k].data ? rs[k].data.items : []; });
          loading = false;
          if (undoing) return false;
          direct = directRows(items, by);
          return true;
        });
      }, function () { loading = false; return false; });
    }

    function next() {
      if (busy || !canNext(draft)) return;
      draft.step += 1;
      if (draft.step === 3) {
        busy = true; render();
        placeWindows().then(loadDirect).then(function () {
          busy = false; draft.step = 4; render();
          clearInterval(timer);
          timer = setInterval(function () { loadDirect().then(function (changed) { if (changed && draft.step === 4 && !busy) render(); }); }, 10000);
        });
        return;
      }
      render();
    }

    function render() {
      card.textContent = "";
      [null, step1, step2, step3, step4][draft.step]();
      if (error) card.appendChild(el("div", "hob-err", error));
      var foot = el("div", "hob-foot");
      if (draft.step < 4) {
        var skip = foot.appendChild(button("跳过", "", finish));
        skip.disabled = busy;
        var nx = button("下一步", "is-primary", next);
        nx.disabled = busy || !canNext(draft);
        if (draft.step < 3) foot.appendChild(nx);
      } else {
        foot.appendChild(button("完成", "is-primary", finish));
      }
      card.appendChild(foot);
      var first = card.querySelector("input:not(:disabled)");
      if (first && !busy && rendered) first.focus();   // 第一次出现不抢焦点；用户操作后的重画才聚焦
      rendered = true;
    }
    render();
  }

  // 首屏读完树后调一次。树里已有分区就不再多读一次设置。
  function maybeStart(ctx) {
    if (!ctx || !ctx.tree || !Array.isArray(ctx.tree.zones) || ctx.tree.zones.length) return;
    ctx.getJson(ACT + "settings").then(function (r) {
      if (r.ok && shouldShowOnboarding(r.data, ctx.tree)) open(ctx, r.date);
    });
  }

  window.NexusTableHexOnboard = Object.assign({ maybeStart: maybeStart }, H);
})();
