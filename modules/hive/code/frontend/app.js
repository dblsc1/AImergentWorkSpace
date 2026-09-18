// table · 渲染层（DOM 编排）
//
// 派生自上游 NEXUS 控制台原型。本文件只负责**只读渲染**。
//
// 2026-09-14 起只剩三件事：离线状态条、顶部概览统计、计时档案。分区/项目/任务的
// 展示整块搬去了蜂巢（hex-app.js 自己拉、自己渲染、自己订 onTreeChange），旧的
// 「经典列表」#zoneGrid 连同分页、项目卡双轨一起删掉（人类判：「经典列表问题
// 有点多，直接去掉，以后搁置不开发了，只要这个蜂巢视图」）。数据来自 data.js
// 的 fetchTree()/fetchArchive()。增删改的弹窗/表单交互在同目录 crud.js
// （2026-07-31 定：table 恢复编辑能力，四前端里唯一例外，见
// module_docs/contract.md v0.2）
// ——拆成两个文件是为了单文件不超 500 行，也让"读"与"写+表单
// 状态机"的关注点不混在一起。本文件只做两件事让 crud.js 能接得上：渲染时
// 打上 data-action/data-*-id 属性（供 crud.js 做事件委托），以及在 load()
// 之后通过 notifyTreeListeners() 把最新 tree 广播出去（供 crud.js 填充下拉框
// /做改前改后对比，见 C6）。
//
// 计时档案（2026-08-01 起）：只读展示 GET /api/core/events 返回的
// session.completed 事实，**不加计时按钮**（那是 ring 模块的活）。
// 双轨（F-TABLE-1）现在只在分区规划面板里（hex-zone-plan.js 打开时自己拉一次
// views/gantt，拿不到就那一栏写"读取失败"），本文件不再拉 gantt。
(function () {
  "use strict";

  var D = window.NexusTableData;

  var state = { tree: null, source: "none", error: null };
  var archiveState = { rawItems: [], total: 0, offset: 0, error: null, loaded: false };

  var $ = function (selector) { return document.querySelector(selector); };

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (char) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char];
    });
  }

  function renderStatusBanner() {
    var el = $("#statusBanner");
    if (state.source === "cache") {
      el.textContent = "⚠ 离线/缓存数据 — 网络请求失败，正在展示上次成功获取的数据（" +
        (D.readCacheTimestamp(window.localStorage) || "时间未知") + "）";
      el.classList.add("show");
    } else if (state.source === "none" && state.error) {
      el.textContent = "⚠ 无法连接服务，且没有可用的缓存数据";
      el.classList.add("show");
    } else {
      el.classList.remove("show");
      el.textContent = "";
    }
  }

  function render() {
    renderStatusBanner();
    var tree = state.tree || { zones: [], projects: [] };

    // 2026-09-14：分区/项目/任务的渲染整块删掉 —— 旧的「经典列表」(#zoneGrid)
    // 连同它的分页一起去掉了，这一屏的分区展示只剩蜂巢（hex-app.js 自己渲染，
    // 自己订 onTreeChange）。本文件现在只留三件事：离线状态条、顶部概览统计、
    // 计时档案。写路径见 crud.js（弹窗）和 hex-crud.js / hex-zone-plan.js（蜂巢）。
    var summary = D.computeSummary(tree);
    // v0.1 发布版的首页没有那一排指标卡（人类：「这一栏去掉」），
    // 所以这里按"元素不在就跳过"写 —— 开发版有卡片时照旧更新，两边共用同一段代码。
    var mProj = $("#projectCount"), mRatio = $("#taskRatio"), mProg = $("#averageProgress");
    if (mProj) mProj.innerHTML = summary.projectCount + " <small>项</small>";
    if (mRatio) mRatio.textContent = summary.taskDone + "/" + summary.taskTotal;
    if (mProg) mProg.textContent = summary.avgProgress + "%";

    renderArchive();
  }

  // ── 计时档案（R5-R10）──────────────────────────────────────────────
  // 名字解析用 state.tree（可能比档案条目更新，也可能还没加载完——
  // D.mapArchiveEvent 对 tree=null 或查不到都有兜底，见 data.js R7 注释）。
  function renderArchive() {
    var list = $("#archiveList");
    var moreBtn = $("#archiveLoadMore");
    if (!list || !moreBtn) return; // 防御：极端情况下 DOM 未就绪

    if (!archiveState.loaded) {
      // 首次请求还没返回：保留 HTML 里的初始占位文案，不提前判定成"空档案"。
      list.innerHTML = '<li class="archive-empty">加载中…</li>';
      moreBtn.hidden = true;
      return;
    }
    if (archiveState.error) {
      list.innerHTML = '<li class="archive-empty">⚠ 档案加载失败：' + escapeHtml(archiveState.error) + "</li>";
      moreBtn.hidden = true;
      return;
    }
    if (!archiveState.rawItems.length) {
      // R9：空档案是友好空态，不是错误
      list.innerHTML = '<li class="archive-empty">暂无计时档案，完成一段计时后会出现在这里。</li>';
      moreBtn.hidden = true;
      return;
    }
    list.innerHTML = archiveState.rawItems.map(function (event) {
      var entry = D.mapArchiveEvent(event, state.tree);
      return '<li class="archive-entry">' + escapeHtml(entry.summary) + "</li>";
    }).join("");
    moreBtn.hidden = archiveState.rawItems.length >= archiveState.total;
  }

  // reset=true：从头拉（初次加载/点刷新）；reset=false：追加下一页（R8 limit/offset）。
  function loadArchive(reset) {
    if (reset) {
      archiveState.offset = 0;
      archiveState.rawItems = [];
      archiveState.total = 0;
    }
    return D.fetchArchive({ limit: D.ARCHIVE_DEFAULT_LIMIT, offset: archiveState.offset }).then(function (result) {
      archiveState.loaded = true;
      if (!result.ok) {
        archiveState.error = result.message;
        renderArchive();
        return;
      }
      archiveState.error = null;
      var payload = result.data || {};
      var items = payload.items || [];
      archiveState.rawItems = archiveState.rawItems.concat(items);
      archiveState.total = payload.total || 0;
      archiveState.offset += items.length;
      renderArchive();
    });
  }

  // ── 导出数据（顶栏「导出数据」按钮，只读，contract.md v1.3）─────────
  // 惰性：点了才拉，不在 load() 里跟着首屏一起请求——导出是偶发动作，不该
  // 让每次刷新都多打一条全量请求。404/未上线统一归一成「导出端点未就绪」文案，
  // 不 throw、不 console：D.fetchExport 走跟写操作同款的 request()，从不 reject。
  function downloadExport(payload) {
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = D.exportFileName(payload && payload.exportedAt);
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function loadExport() {
    var shell = $("#exportShell");
    var status = $("#exportStatus");
    var pre = $("#exportPre");
    var downloadBtn = $("#exportDownload");
    if (!shell || !status || !pre || !downloadBtn) return; // 防御：DOM 未就绪

    shell.hidden = false;
    status.textContent = "正在导出…";
    pre.hidden = true;
    downloadBtn.hidden = true;

    return D.fetchExport().then(function (result) {
      if (!result.ok) {
        // 404（端点不存在）与 0（连不上，同样视为"未就绪"）给统一提示；
        // 其余状态码（如 500）如实展示后端消息，不伪装成"未就绪"。
        status.textContent = (result.status === 404 || result.status === 0)
          ? "⚠ 导出端点未就绪"
          : "⚠ 导出失败：" + result.message;
        return;
      }
      var payload = result.data || {};
      status.textContent = "导出于 " + (payload.exportedAt || "未知时间");
      pre.hidden = false;
      pre.textContent = JSON.stringify(payload, null, 2);
      downloadBtn.hidden = false;
      downloadBtn.onclick = function () { downloadExport(payload); };
    });
  }

  // crud.js 监听最新 tree（填下拉框、算改前改后 diff，见 C6），不重复发请求。
  var treeListeners = [];
  function notifyTreeListeners() {
    treeListeners.forEach(function (fn) { fn(state.tree); });
  }

  function load() {
    // R6：includeEphemeral 默认 false，上游没有对应开关，本任务不补（升级条款允许跳过）
    // C2：写操作完成后 crud.js 调用本函数重新拉取 views/tree，不做本地乐观更新。
    return D.fetchTree({ includeEphemeral: false }).then(function (result) {
      state.tree = result.tree;
      state.source = result.source;
      state.error = result.error || null;
      render();
      notifyTreeListeners();
    });
  }

  function updateClock() {
    var now = new Date();
    var clockEl = $("#clock");
    var todayEl = $("#today");
    if (clockEl) clockEl.textContent = now.toLocaleTimeString("zh-CN", { hour12: false });
    if (todayEl) {
      todayEl.textContent = now.toLocaleDateString("zh-CN", {
        year: "numeric", month: "2-digit", day: "2-digit", weekday: "short"
      });
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    $("#refreshTree").addEventListener("click", function () { load(); loadArchive(true); });
    $("#archiveLoadMore").addEventListener("click", function () { loadArchive(false); });
    $("#exportData").addEventListener("click", function () { loadExport(); });

    updateClock();
    window.setInterval(updateClock, 1000);
    // 先拉树再拉档案：mapArchiveEvent 现查任务/项目名字要用 state.tree（R7），
    // 树先到位能让档案首次渲染就带上正确名字，不用等第二次 render() 才补上。
    // 双轨/gantt 不在这儿拉了：只有旧列表的项目卡用它，现在要看排期是在分区
    // 规划面板里（hex-zone-plan.js 打开时自己拉一次 views/gantt）。
    load().then(function () { return loadArchive(true); });
  });

  // crud.js 通过这个小接口接入：refresh() 供写操作成功后调用（C2），
  // onTreeChange() 订阅最新 tree（立即补发一次当前值，晚注册也不丢）。
  // rerender()（gtd-v1 波2-B 新增）：不重新拉网络，只用当前已有的 state 重画一次——
  // 供 gtd-app.js 在 lastWriter 角标缓存刷新后触发一次纯本地重渲染（同 loadGantt
  // 拿到数据后调 render() 补一次渲染同一个模式，只是搬到跨文件接口上）。
  window.NexusTableApp = {
    refresh: load,
    rerender: render,
    onTreeChange: function (fn) {
      treeListeners.push(fn);
      if (state.tree) fn(state.tree);
    }
  };
}());
