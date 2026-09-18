// HoneyComb · 回顾视图（v0.1 发布版，2026-09-14 拿回来）
//
// 开发版里回顾和收件箱/待办区/AI 角标缠在一起；
// 发布版没有那三样，所以这里**只重写回顾这一块**，读端仍是 gtd-data.js 的
// fetchReview()（GET /api/core/views/review，只读透传，后端零改动）。
//
// 「跳到对应对象」沿用开发版的判断：点一条不是打开另一个只读详情页，而是切回
// 主视图、打开那个对象平常就有的编辑弹窗（crud.js 导出的 openProjectDialog /
// openTaskDialog），人在同一个弹窗里直接处理，不另造一层信息展示。
(function () {
  "use strict";

  var G = window.NexusTableGtdData;
  var D = window.NexusTableData;
  var App = window.NexusTableApp;
  var TABS = ["board", "review"];
  var state = { loaded: false, error: null, data: null };

  var $ = function (sel) { return document.querySelector(sel); };
  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function hm(seconds) { return D.formatArchiveDuration(seconds); }

  function switchTab(name) {
    if (TABS.indexOf(name) === -1) return;
    TABS.forEach(function (t) {
      var panel = document.querySelector('[data-tab-panel="' + t + '"]');
      if (panel) panel.hidden = t !== name;
      var btn = document.querySelector('[data-tab="' + t + '"]');
      if (btn) btn.classList.toggle("active", t === name);
    });
    // 切回蜂巢：藏着的时候 getBoundingClientRect 全是 0，布局要重算一次，
    // 否则 FLIP 会拿一堆 0 去算位移。
    if (name === "board" && window.NexusTableHexApp && window.NexusTableHexApp.relayout) {
      window.NexusTableHexApp.relayout();
    }
  }

  function row(attrs, name, detail) {
    return "<li class=\"review-row\" " + attrs + ">" +
      '<span class="review-name">' + esc(name) + "</span>" +
      '<span class="review-detail">' + detail + "</span></li>";
  }

  function render() {
    var body = $("#reviewBody");
    if (!body) return;
    if (state.error) { body.innerHTML = '<p class="rv-error">⚠ 回顾加载失败：' + esc(state.error) + "</p>"; return; }
    if (!state.loaded) { body.innerHTML = '<p class="rv-loading">加载中…</p>'; return; }

    var data = state.data || {};
    var plan = data.planVsActual || [];
    var overdue = data.overdueProjects || [];
    var stale = data.staleTasks || [];

    var head = '<p class="rv-today">本周：<span class="mono">' + esc(data.weekStart) + " → " +
      esc(data.weekEnd) + '</span>（今天 <span class="mono">' + esc(data.today) + "</span>）</p>";

    var planHtml = '<section class="review-section"><h3>本周计划 vs 实际</h3>' +
      (plan.length ? '<ul class="review-list">' + plan.map(function (p) {
        var label = p.plan ? (esc(p.plan.start) + " → " + esc(p.plan.end)) : "未定计划";
        var badge = p.scheduledThisWeek ? '<span class="rv-badge today">本周有排期</span>' : "";
        return row('data-rv="project" data-project-id="' + esc(p.projectId) + '"', p.name,
                   label + badge + " · 本周计时 " + hm(p.actualSecondsThisWeek));
      }).join("") + "</ul>" : '<p class="rv-empty">暂无项目</p>') + "</section>";

    var overdueHtml = '<section class="review-section"><h3>过期项目</h3>' +
      (overdue.length ? '<ul class="review-list">' + overdue.map(function (p) {
        return row('data-rv="project" data-project-id="' + esc(p.id) + '"', p.name,
                   '<span class="rv-badge overdue">截止 ' + esc(p.plan.end) + "</span>");
      }).join("") + "</ul>" : '<p class="rv-empty">没有过期项目</p>') + "</section>";

    var staleHtml = '<section class="review-section"><h3>久未动任务</h3>' +
      (stale.length ? '<ul class="review-list">' + stale.map(function (t) {
        return row('data-rv="task" data-task-id="' + esc(t.id) + '"', t.name,
                   t.lastActiveDate ? "最近计时 " + esc(t.lastActiveDate) : "从未计时过");
      }).join("") + "</ul>" : '<p class="rv-empty">没有久未动的任务</p>') + "</section>";

    body.innerHTML = head + planHtml + overdueHtml + staleHtml;
  }

  function load() {
    return G.fetchReview().then(function (r) {
      state.loaded = true;
      state.error = r.ok ? null : r.message;
      state.data = r.ok ? r.data : null;
      render();
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var tabs = $(".view-tabs");
    if (!tabs) return;
    tabs.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-tab]");
      if (btn) switchTab(btn.dataset.tab);
    });
    var body = $("#reviewBody");
    if (body) {
      body.addEventListener("click", function (ev) {
        var hit = ev.target.closest("[data-rv]");
        if (!hit) return;
        switchTab("board");
        if (hit.dataset.rv === "project" && App.openProjectDialog) {
          App.openProjectDialog(hit.dataset.projectId);
        } else if (hit.dataset.rv === "task" && App.openTaskDialog) {
          App.openTaskDialog(hit.dataset.taskId);
        }
      });
    }
    // 「打开即见」：不等人点到回顾那一下才开始拉。
    load();
    // 任何一次写操作之后 tree 会重新广播，回顾的口径也跟着变了，重拉一次。
    if (App && App.onTreeChange) {
      var primed = false;
      App.onTreeChange(function () {
        if (!primed) { primed = true; return; }
        load();
      });
    }
  });

  window.HoneyCombReview = { switchTab: switchTab, reload: load };
})();
