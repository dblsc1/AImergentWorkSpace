// hive · 蜂巢行动分区 · DOM 渲染 + FLIP 动效（2026-09-07）
//
// 与 hex-data.js（纯逻辑）分层，同 app.js/rails.js 的既有纪律：这个文件碰 DOM，所以不进单测；
// 一切可被单测钉死的判断都已经搬到 hex-data.js / hex-ring.js 去了。
//
// 落点（2026-09-07 人类改判）：**直接取代 index.html 的 #zoneGrid 行动分区**。
// 上一轮先做成独立调试页，后来直接放进行动分区原位取代它；调试页保留成同一套
// 脚本的裸壳。两个落点共用 #hive 这一个容器 id，**脚本不认页面、只认容器**。
//
// 60fps 是怎么保证的（硬指标，不是口号）：
//   · 所有格子 position:absolute + left/top 恒为 0，位置**只由 transform 决定**；
//     布局改变时改的是 transform，浏览器不需要重排文档流。
//   · 重排走 FLIP：先量旧 rect（First）→ 直接落到终态（Last）→ 用
//     transform 抵消差值（Invert）→ 下一帧过渡到终态（Play）。
//     动画期间只有 transform / opacity 在变，合成器就能跑完，主线程不参与。
//   · 放大的那一格用 scale 抵消，内层 .hex-inner 反向 scale，文字不被拉扁；
//     **没有任何一处动画 left/top/width/height**。
//   · hover 浮层有 150ms 进入延迟，鼠标扫过一片格子不会疯狂闪。
//   · prefers-reduced-motion 直接跳终态，一帧动画都不放。
(function () {
  "use strict";
  // 站点前缀（gateway.v1）：网关往页面注入 HONEYCOMB_BASE，整站挂在子路径下时
  // 所有请求与跳转都从它拼。没注入（单测、直接打开文件）就是 "/"。
  var BASE = (typeof self !== "undefined" && self.HONEYCOMB_BASE) || "/";

  var L = window.NexusTableHexLayout;    // 布局 + 推镜（几何常量的唯一事实源）
  var H = window.NexusTableHexData;
  var D = window.NexusTableData;
  var G = window.NexusTableGtdData;
  var Ring = window.NexusTableHexRing;
  var B = window.NexusTableHexBorders;
  var C = window.NexusTableHexCards;      // 展开卡/格子的 HTML 生成（纯函数层）
  var S = window.NexusTableHexSprout;     // 空白处长按 → 长出一格 → 建项目
  var SK = window.NexusTableHexSpark;     // 中心圆环往项目格撒的轻粒子（纯装饰）   // 界线层（只画不改状态，拆在 hex-borders.js）
  var T = window.NexusTableHexTimer;     // 长按计时 + 聚焦灰度（拆在 hex-timer.js）
  var K = window.NexusTableHexCenterCtl; // 中心格的 完成 / 暂停 / 取消不记录（hex-center-ctl.js）
  var Z = window.NexusTableHexZoneEdit;  // 分区名：短按 / 长按编辑模式（hex-zone-edit.js）
  var ZP = window.NexusTableHexZonePlan; // 短按分区名打开的分区规划面板（hex-zone-plan.js）

  // 几何：SIZE 是**晶格步长**对应的外接圆半径 —— 相邻格中心距恒为 √3·SIZE
  // （hex-data.js 的轴向晶格），这是"咬合"的定义。
  //
  // 缝怎么留：从 SIZE **等比缩**到 DRAW_S = SIZE − GAP/2，缩的是**格子**、
  // 不是晶格。上一版是 `√3·SIZE − GAP` / `2·SIZE − GAP` 两个方向减同一个 GAP，
  // 宽高比被压成 0.854（正六边形是 0.866），六个角就不再是 120°，
  // 咬合上了也会看出来"歪"。等比缩之后宽高比恒等于 √3:2。
  // GAP = 0：**同区的格子严丝合缝粘在一起**（人类：「把没有分割的所有六边形中间
  // 仍有的分割线取消，粘起来」）。GAP 是全局的，留任何值都会在同区内部留下缝。
  //
  // ⚠️ 分界处那条宽白槽**不靠 GAP**，靠 BOUNDARY_INSET：只把朝向别的分区的
  // 那几条边往里收（逐边内缩，几何在 hex-data.js::insetPolygon），
  // 同区的边一点不动、格子中心也不动。这两件事必须分开走，混在一起就是
  // "想让分界宽一点，结果同区也散了"。
  // 几何常量住在 hex-layout.js，这里只取本文件真正要用的几个 ——
  // clipFor 要 DRAW_S，动画时长/缓动要和布局那边完全同步。
  var SIZE = L.SIZE, DRAW_S = L.DRAW_S;
  var ANIM_MS = L.ANIM_MS, HOVER_MS = L.HOVER_MS, EASE = L.EASE;
  var HOVER_DELAY_MS = 90;   // 悬停进入延迟：鼠标扫过一片格子不该疯狂闪。
                             // 这条归**事件**不归几何，所以留在本文件。

  // 朝向别的分区的边额外往里收多少像素（单侧）。分界那条槽 = 2×它。
  // **留在这里不跟着线宽搬走**：它改的是格子的 clip-path（clipFor），
  // 是格子的事；线宽改的是 SVG 描边，是界线层的事。人类第五轮判过一次
  // 「我要的不是每个六边形格子和格子之间间距变大，我要的是只有分割线的那一条变粗」
  // —— 这两个数分家，正是那条判词的物理落点，别为了"看着相关"再并回去。
  var BOUNDARY_INSET = 1.8;
  var RING_HREF = BASE + "ring/";

  var AUDIT_PATH = BASE + "api/core/planner/audit?limit=300";
  var CURRENT_PATH = BASE + "api/core/views/current";

  // 分区绕圈顺序的存储键。这是**第三版语义**了，每次换 key 且不迁移旧值：
  //   v1 labelOffsets —— 只挪标签（人类否掉：「不只是改变分区名位置」）
  //   v1 zoneOffsets  —— 平移整个分区（人类又否掉：「不是机械的移动六边形，
  //                     而是根据分区名位置改变各分区的相对角度」）
  //   v1 zoneOrder    —— 现在这版：拖标签 = **给这个分区换一个绕圈的角度**，
  //                     整张蜂巢按新顺序重排扇区。存的是一串分区 id 的先后。
  // 前两版存的是像素偏移，和"顺序"根本不是一种东西，迁移无从谈起。
  var ZONE_ORDER_KEY = "nexus.hex.zoneOrder.v1";

  var state = {
    tree: null, hive: null, todos: {}, completions: [],
    current: null, expandedId: null, hoverItem: null,
    cells: [], byId: {}, labels: [], borderLayer: null,
    zoneOrder: []
  };

  // 分区名标签的手动位置，**按分区 id 存在本地**。
  // 这是"每个人自己看着顺眼"的偏好，不是事实：不进后端、不进 events、
  // 换台机器没有也不算错（读不到就是默认位置）。localStorage 在隐私窗口/
  // 禁站点数据时连读都会抛，所以读写都包 try。
  function loadZoneOrder() {
    try {
      var raw = window.localStorage.getItem(ZONE_ORDER_KEY);
      var arr = raw ? JSON.parse(raw) : null;
      return Array.isArray(arr) ? arr.filter(function (x) { return typeof x === "string"; }) : [];
    } catch (e) { return []; }
  }
  function saveZoneOrder() {
    try { window.localStorage.setItem(ZONE_ORDER_KEY, JSON.stringify(state.zoneOrder)); }
    catch (e) { /* 存不下就算了，位置这一轮还在，刷新回默认 */ }
  }

  var ZERO_OFF = { x: 0, y: 0 };
  function $(sel) { return document.querySelector(sel); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function reduceMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }
  function setStatus(text) { var el = $("#hexStatus"); if (el) el.textContent = text || ""; }

  // ── 取数：四个现成读端，后端一行不改 ─────────────────────────
  function getJson(path) {
    return fetch(path).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (body) {
        return res.ok ? { ok: true, data: body }
                      : { ok: false, status: res.status, message: (body && body.detail) || ("HTTP " + res.status) };
      });
    }).catch(function (err) { return { ok: false, status: 0, message: String((err && err.message) || err) }; });
  }

  function postJson(path, body) {
    var init = { method: "POST" };
    if (body !== undefined) {
      init.headers = { "Content-Type": "application/json" };
      init.body = JSON.stringify(body);
    }
    return fetch(path, init).then(function (res) {
      if (res.status === 204) return { ok: true, data: null };
      return res.json().catch(function () { return null; }).then(function (body2) {
        return res.ok ? { ok: true, data: body2 }
                      : { ok: false, status: res.status, message: (body2 && body2.detail) || ("HTTP " + res.status) };
      });
    }).catch(function (err) { return { ok: false, status: 0, message: String((err && err.message) || err) }; });
  }

  function loadAll() {
    setStatus("加载中…");
    return Promise.all([
      D.fetchTree(),
      G.fetchNextActions(),
      getJson(AUDIT_PATH),
      getJson(CURRENT_PATH)
    ]).then(function (r) {
      // ⚠️ data.js::fetchTree 的返回形状是 {tree, source, error}，**不是**其余
      // 函数那套 {ok, data}（读端与写端两套约定，本仓既有事实）。照抄 .ok 会
      // 永远判失败——本轮真机联调第一发就撞在这里。
      var treeRes = r[0];
      if (!treeRes.tree) { setStatus("读取项目树失败：" + (treeRes.error || "")); return false; }
      state.tree = treeRes.tree;
      state.todos = r[1].ok ? H.nextActionsByProject(r[1].data) : {};
      state.completions = r[2].ok ? H.recentCompletions((r[2].data || {}).items, state.tree) : [];
      // 热度：按半衰期加权的最近完成次数（人类：「经常有任务被完成的排得更靠近中心」）。
      state.heat = r[2].ok ? H.completionHeat((r[2].data || {}).items, state.tree) : {};
      state.current = r[3].ok ? r[3].data : null;
      state.hive = H.buildHoneycomb(state.tree, { order: state.zoneOrder, heat: state.heat });
      setStatus(state.hive.zones.length + " 个分区 · " +
        ((state.tree.projects || []).length) + " 个项目" +
        (treeRes.source === "cache" ? " · 用的是本地缓存" : "") +
        (r[1].ok ? "" : " · 待办读取失败") + (r[2].ok ? "" : " · 流水读取失败"));
      return true;
    });
  }

  // ── 建 DOM：每次数据变化重建一次，之后只改 transform ───────────
  function cellDomId(cell) { return "c" + cell.q + "_" + cell.r; }

  // 逐边内缩后的 clip-path。顶点由 hex-data 算（六个半平面求交，角还是 120°），
  // 这里只做「S 为单位 → 盒子百分比」换算：盒宽 √3·DRAW_S、盒高 2·DRAW_S。
  //
  // 写成 CSS 变量而不是直接写 clip-path：展开态要换成卡片形状，那条规则在 CSS 里，
  // 而内联 clip-path 会压过它 —— 卡片就永远是六边形了。
  // 变量内联给、clip-path 由 CSS 读，展开态那条规则照样能赢。
  function clipFor(dirs) {
    var pts = H.insetPolygon(dirs, BOUNDARY_INSET / DRAW_S);
    return "polygon(" + pts.map(function (p) {
      return (50 + p[0] / Math.sqrt(3) * 100).toFixed(3) + "% " +
             (50 + p[1] * 50).toFixed(3) + "%";
    }).join(", ") + ")";
  }


  // 把一个已有元素改写成"这一格"。buildCell 和复用路径共用，避免两处漂移。
  function applyCell(el, cell) {
    el.className = "hex-cell" + (cell.placeholder ? " is-placeholder" : "");
    // ⚠️ 格子写的是 **--zone-raw**，不是 --zone-color。
    // 自定义属性写成内联样式就**压过一切类规则**（连 !important 都得靠层叠特例），
    // 于是 .is-dimmed / .is-mate 这种"把这一格调灰一点"的分级就没地方落。
    // 多一层间接（CSS 里 .hex-cell{--zone-color: var(--zone-raw)}）之后，
    // 类规则可以正常改 --zone-color，而原色始终留在 --zone-raw 里可被引用。
    // 这也是"分级灰度不用 filter"的前提 —— 见 hex.css 的 .is-dimmed。
    el.style.setProperty("--zone-raw", cell.color);
    // 深浅：外浅内深，按本分区圈数等比例落在固定区间（hex-data.js::zoneMix）
    el.style.setProperty("--zone-mix", (cell.mix == null ? H.DEPTH_MIX.max : cell.mix) + "%");
    el.dataset.zoneId = cell.zoneId;
    el.style.setProperty("--hex-clip", clipFor(cell.boundaryDirs));
    if (cell.project) el.dataset.projectId = cell.project.id;
    else delete el.dataset.projectId;
    el.innerHTML = C.cellInnerHtml(cell, "", cell.project ? state.todos[cell.project.id] : null);
    return el;
  }
  function buildCell(cell) {
    return applyCell(document.createElement("article"), cell);
  }

  function buildCenter() {
    var el = document.createElement("article");
    el.className = "hex-cell hex-center";
    el.style.setProperty("--zone-raw", "var(--line)");
    el.style.setProperty("--hex-clip", clipFor(state.hive.centerBoundaryDirs));
    el.setAttribute("role", "link");
    el.setAttribute("tabindex", "0");
    el.innerHTML = '<div class="hex-inner">' +
      '<div class="hex-ring-slot">' + Ring.ringMarkup() + "</div>" +
      '<div class="hex-center-title" id="hexCenterTitle"></div>' +
      '<div class="hex-center-meta" id="hexCenterMeta"></div>' +
      // 「进入计时台 →」提示行退役（人类 2026-09-12：「可以去掉了，放上三个按钮」）；
      // 点上半部分照样进计时台，aria-label 里说着。
      K.markup() + "</div>";
    // 标题（开始时间 + 日期）住进圆环正中、读数下面（人类 2026-09-12：「把圆环扩大到底，然后把开始时间
    // 和日期放到圆环中间」）。放进 .tring-read 里才能用圆环的容器单位（cqi）跟着圆环等比缩放。
    // 静置档照旧整个藏起来（hex-center-ctl.css）。
    el.querySelector(".tring-read").appendChild(el.querySelector("#hexCenterTitle"));
    return el;
  }

  function buildLabel(zone) {
    var el = document.createElement("div");
    el.className = "hex-zone-label";
    el.style.setProperty("--zone-color", zone.color);
    el.dataset.zoneId = zone.id;
    el.title = "拖动可以挪位置，双击回到默认位置";
    // 两层：外层只管定位（布局层往它的 inline transform 写 translate3d），
    // 气泡外观与编辑模式的摇晃 / 放大都在里面这层 —— 旋转缩放的中心才是气泡自己的中心。
    // 2026-09-12 人类报「摇晃圆心不在分区中心」「长按进入拖动状态会跑远」都出在单层上：
    // 独立的 rotate / scale 属性叠在定位 transform 外面，中心落在蜂巢原点附近。
    var pill = document.createElement("span");
    pill.className = "hex-zone-pill";
    pill.textContent = zone.name + (zone.projectCount ? "" : " · 空");
    el.appendChild(pill);
    return el;
  }

  // ── 分区名：短按 / 长按进编辑模式拖角度 —— 整族在 hex-zone-edit.js ─────
  // （2026-09-12 拆出：拖角度的算法原样搬过去，加了 iOS 式编辑模式。）

  // 顺序变了要重算整张蜂巢。**复用现有 DOM**（按项目 id 找回原来那个元素），
  // 否则新元素没有 __geom，FLIP 一帧都不动 —— 而"整片地转过去"这个动画
  // 正是这个功能唯一的说服力来源。
  function rebuildHive(animate) {
    var keep = state.expandedId;
    state.expandedId = null;                    // 元素要被 applyCell 重写，展开态先摘干净
    state.hive = H.buildHoneycomb(state.tree, { order: state.zoneOrder, heat: state.heat });
    mount(true);
    L.apply(!!animate);
    if (keep && state.byId[keep]) { state.expandedId = null; expand(keep); }
  }

  function mount(reuse) {
    var hive = $("#hive");
    // 复用：按**项目 id** 找回原来那个元素。分区换角度之后，同一个项目会落到
    // 另一个晶格位上；复用元素 = 它身上的 __geom 还在 = FLIP 知道"它从哪来"，
    // 于是能动画出"整片地转过去"。新建的元素没有 __geom，第一次不动 ——
    // 这是对的，它本来就没有来处。
    var reusable = {};
    var keepCenter = null;
    if (reuse) {
      state.cells.forEach(function (it) {
        if (it.isCenter) { keepCenter = it.el; return; }
        if (it.cell.project) reusable["p:" + it.cell.project.id] = it.el;
      });
      Object.keys(reusable).forEach(function (k) {
        var e = reusable[k]; if (e.parentNode) e.parentNode.removeChild(e);
      });
      if (keepCenter && keepCenter.parentNode) keepCenter.parentNode.removeChild(keepCenter);
    }
    hive.innerHTML = "";
    // 推镜要缩放的是**内容**，而 #hive 得留着当占位盒（它的 width/height 决定
    // .hive-wrap 的滚动区）。一个元素做不了两件事：transform 不影响布局，
    // 把 scale 挂在 #hive 上，滚动区还是原尺寸，放大出去的部分永远滚不到。
    // 所以多一层 .hive-cam：cam 负责 scale，#hive 负责占位（= plan 尺寸 × cam）。
    var camEl = document.createElement("div");
    camEl.className = "hive-cam";
    hive.appendChild(camEl);
    state.camEl = camEl;
    state.cells = []; state.byId = {}; state.labels = [];

    state.hoverItem = null;
    state.hive.cells.forEach(function (cell) {
      var old = cell.project ? reusable["p:" + cell.project.id] : null;
      var el = old ? applyCell(old, cell) : buildCell(cell);
      camEl.appendChild(el);
      var item = { el: el, cell: cell, id: cellDomId(cell) };
      el.__hexItem = item;                       // el → item 反查，hover 用
      state.cells.push(item);
      // 双索引：项目 id（跨 refresh 稳定，展开态靠它续上）与坐标键
      // （占位格唯一的身份 —— 它没有项目 id）。expand/fillCell 一律走 byId，
      // 于是"展开一个空位来建项目"和"展开一个项目"是同一条路径。
      state.byId[item.id] = item;
      if (cell.project) state.byId[cell.project.id] = item;
    });

    var center = keepCenter || buildCenter();
    // 复用的中心格身上可能还挂着上一轮的状态类。**唯独 is-catch 要留** ——
    // 那是"刚接住一段计时、圆环正在膨胀"的 3 秒动画，而 refresh 恰好就发生在
    // 这 3 秒里（长按建完任务要重拉树）。清掉它，人类刚长按完就什么都看不到，
    // 正是本轮要修的那个反馈（真机实测：缩放 1、is-catch 已丢）。
    center.classList.remove("is-hover", "is-expanded", "is-mate", "is-dimmed");
    camEl.appendChild(center);
    state.centerItem = { el: center, cell: state.hive.center, id: "center", isCenter: true };
    center.__hexItem = state.centerItem;
    state.cells.push(state.centerItem);
    // 同一个坑的第二半：「接住计时」时中心格处在**悬停态**（hex-timer.js::swellCenter），
    // 而 refresh 正好发生在那 3 秒里 —— 上面刚把 hoverItem 清成 null、is-hover 摘掉。
    // 不在这儿接回去，中心格会在长按后 620ms 左右突然缩回原尺寸。
    if (state.catchHover) {
      state.hoverItem = state.centerItem;
      center.classList.add("is-hover");
    }

    state.borderLayer = B.mount(camEl, state.hive.zones);

    state.hive.zones.forEach(function (zone) {
      var el = buildLabel(zone);
      Z.bind(el);
      camEl.appendChild(el);
      state.labels.push({ el: el, zone: zone });
    });

    // 粒子层住在相机层里，而相机层每次 mount 重建 —— 这里补挂一次（纯装饰，失败也不影响）。
    if (SK) SK.attach();

    paintCenter();
    T.applyFocus();
  }

  function projectById(id) {
    return ((state.tree && state.tree.projects) || []).filter(function (p) { return p.id === id; })[0] || null;
  }


  // 发布版去掉了「拖卡片改优先级」（人类 2026-09-14：v0.1 不要权重功能）。
  // 开发版里这里会把拖出来的顺序写进后端的 plannedWeight；发布版只留本地排序视觉，
  // 不发任何权重请求，commitPriority 保留成空壳，调用点不用动。
  function commitPriority() { /* v0.1：不写权重 */ }

  // 拖动本体：只在 [data-reorder] 的列表里生效，指针从哪张卡按下就拖哪张。
  // 与长按计时天然共存 —— 长按那边挪超过 10px 就撤销，正好是这里开始接管的点。
  var CARD_DRAG_SLOP = 8;
  var cardDrag = null;
  function onCardPointerDown(ev) {
    if (ev.button !== undefined && ev.button !== 0) return;
    var li = ev.target.closest && ev.target.closest(".hex-todo-card");
    if (!li || ev.target.closest("[data-hex-action]")) return;
    var ul = li.parentNode;
    if (!ul || !ul.dataset || ul.dataset.reorder !== "1") return;
    cardDrag = { li: li, ul: ul, y0: ev.clientY, x0: ev.clientX, active: false };
  }
  function onCardPointerMove(ev) {
    if (!cardDrag) return;
    if (!cardDrag.active) {
      if (Math.abs(ev.clientY - cardDrag.y0) < CARD_DRAG_SLOP &&
          Math.abs(ev.clientX - cardDrag.x0) < CARD_DRAG_SLOP) return;
      cardDrag.active = true;
      cardDrag.li.classList.add("is-carding");
      state.cardDragged = true;                 // 用来吞掉随后那次 click
    }
    // 找指针正下方的同级卡片，越过它一半就换位。用 DOM 顺序本身当模型，
    // 不另存一份索引 —— 两份状态迟早对不上。
    var sibs = [].slice.call(cardDrag.ul.children);
    for (var i = 0; i < sibs.length; i++) {
      var el = sibs[i];
      if (el === cardDrag.li) continue;
      var r = el.getBoundingClientRect();
      if (ev.clientY < r.top || ev.clientY > r.bottom) continue;
      var before = ev.clientY < r.top + r.height / 2;
      cardDrag.ul.insertBefore(cardDrag.li, before ? el : el.nextSibling);
      break;
    }
  }
  function onCardPointerUp() {
    if (!cardDrag) return;
    var d = cardDrag; cardDrag = null;
    if (!d.active) return;
    d.li.classList.remove("is-carding");
    var cell = d.li.closest(".hex-cell");
    if (cell) commitPriority(cell);
  }



  function fillCell(item) {
    var cell = item.cell;
    var body = "";
    // 判据是"byId 指的是不是我"，不是"我的项目 id 等不等" —— 后者把占位格
    // 整个排除在外了，而占位格展开之后正是建项目的表单。
    if (state.expandedId && state.byId[state.expandedId] === item) {
      body = cell.project
        ? C.detailHtml(projectById(cell.project.id) || cell.project, cell.zoneName,
                       { todos: state.todos[cell.project.id], completions: state.completions })
        : C.newProjectHtml(cell);
    }
    // 占位格展开成建项目表单时，标题从「（空）」换成「新建项目」——
    // 静置档那个「（空）」是说"这儿没东西"，展开档要说的是"你在建什么"。
    var titleEl = item.el.querySelector(".hex-title");
    if (titleEl) {
      titleEl.textContent = cell.project ? cell.project.name : (body ? "新建项目" : "（空）");
      // 标题＝改名入口，但**只在展开档**（人类 2026-09-14：「六边形鼠标点击大视角
      // 加一个改名入口，点击标题可以改名」）。收起时不能挂 data-hex-action ——
      // 上面那条 click 委托见到它就 return 交给 hex-crud.js，于是整格点不开了。
      var editable = !!(body && cell.project);
      if (editable) {
        titleEl.setAttribute("data-hex-action", "rename-project");
        titleEl.setAttribute("role", "button");
        titleEl.setAttribute("tabindex", "0");
        titleEl.title = "点一下改名";
      } else {
        titleEl.removeAttribute("data-hex-action");
        titleEl.removeAttribute("role");
        titleEl.removeAttribute("tabindex");
        titleEl.removeAttribute("title");
      }
      titleEl.classList.toggle("is-editable", editable);
    }
    var slot = item.el.querySelector(".hex-body");
    if (slot) slot.innerHTML = body;              // 常路：只换内容，FLIP 的反向缩放不受影响
    else item.el.innerHTML = C.cellInnerHtml(cell, body,   // 兜底：结构还没建好
      cell.project ? state.todos[cell.project.id] : null);
  }

  // key 是项目 id 或坐标键（占位格），两者都在 byId 里。
  function expand(key) {
    if (state.expandedId === key) return;
    var prev = state.expandedId ? state.byId[state.expandedId] : null;
    state.expandedId = key;
    if (prev) { prev.el.classList.remove("is-expanded"); fillCell(prev); }
    var next = key ? state.byId[key] : null;
    if (next) { next.el.classList.add("is-expanded"); fillCell(next); }
    clearHover(true);
    T.applyFocus();
    L.apply(true);
  }
  function collapse() {
    S.removeSprout();
    // 临时长出来的那一格只活到"收起"为止：它没有项目，留着就是个空位。
    // 建成功的话走的是 collapseAndReload，refresh 会把整棵树重拉，同样不留。
    var hive = state.hive;
    if (hive && hive.cells.some(function (c) { return c.__sprout; })) {
      hive.cells = hive.cells.filter(function (c) { return !c.__sprout; });
      expand(null);
      mount(true);
      return;
    }
    expand(null);
  }




  // ── hover：格子**原地稍微长大**，多显示最近的一条待办 ────────────
  //
  // 上一版是跟着鼠标走的浮层（.hex-tip）。人类判词：「鼠标放上去，六边形稍微
  // 扩大显示最近待办，点击再扩大」—— 要的是**同一个六边形的三档尺寸**，
  // 不是六边形旁边冒出来一个框。浮层整块退役（#hexTip 及其样式一并删）。
  //
  // 长大走的是和展开同一套 FLIP：改的是 width/height，动的只有 transform。
  // 格子全是 position:absolute，改一格的尺寸不会把别的格子挤走 —— 这是
  // "悬停不重排"能成立的前提，不是运气。
  var hoverTimer = null;
  function setHover(item) {
    if (state.hoverItem === item) return;
    if (state.hoverItem) state.hoverItem.el.classList.remove("is-hover");
    state.hoverItem = item || null;
    if (item) item.el.classList.add("is-hover");
    L.apply(true, HOVER_MS);
  }
  function queueHover(item) {
    // 长按开火之后的一小段时间里不接受新的悬停：人类原话「不应该是看到六边形
    // 放大」。这段正是替身在飞、圆环在膨胀的时候，眼睛该跟着它们走，
    // 而不是同时有一格在原地长大。锁一过，正常悬停照旧（鼠标本来就还在那）。
    if (item && state.hoverLock && Date.now() < state.hoverLock) return;
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
    if (!item) { setHover(null); return; }
    if (state.hoverItem) { setHover(item); return; }   // 已在悬停态，格间切换不再等
    hoverTimer = setTimeout(function () { hoverTimer = null; setHover(item); }, HOVER_DELAY_MS);
  }
  // silent = 调用方紧接着自己会 applyLayout，别让这里先动一次（展开时用）。
  function clearHover(silent) {
    if (hoverTimer) { clearTimeout(hoverTimer); hoverTimer = null; }
    if (!state.hoverItem) return;
    if (silent) {
      state.hoverItem.el.classList.remove("is-hover");
      state.hoverItem = null;
      return;
    }
    setHover(null);
  }

  // ── 中心格：当前在做的事（timer-ring/v1）──────────────────────
  var tickHandle = null;
  function paintCenter() {
    var el = state.centerItem && state.centerItem.el;
    if (!el) return;
    var svg = el.querySelector(".tring");
    // 暂停是本机状态（后端此刻就是空闲），所以要在这里把"停的是谁"补回标题 ——
    // 否则人类点完暂停，中心格只剩一个「空闲」，看不出还有一件事等着继续。
    var paused = K.paint(el, state.current);
    // 分针 / 秒针 / 圆心数字都按"继续之前累计的 + 本段"走（人类：「暂停后继续需要继续之前时间」）。
    // 只是显示，入账仍按段。
    Ring.paint(svg, state.current, Date.now(), K.carried(state.current));
    var running = !!(state.current && state.current.running);
    // 中心格底色 = 正在计的任务所在分区的颜色（人类 2026-09-12：「闪烁结束后，六边形底色还是黑色的，
    // 需要有任务分区的对应颜色……闪烁结束的颜色需要刚好对应分区颜色」）。
    // 浓度取该分区**最深那一档再加一档**（DEPTH_MIX.center；人类 2026-09-12 第四次改）：比紧挨着它的格子深一档，
    // 一眼看得出谁是中心。hex.css 里 --center-bg 就用这两个变量拼，闪烁的收尾关键帧也落在 --center-bg 上。
    var zid = running ? (state.current.zone && state.current.zone.id) : (paused && paused.zoneId);
    var zone = zid && state.hive && state.hive.zones.filter(function (z) { return z.id === zid; })[0];
    el.classList.toggle("is-zoned", !!zone);
    el.style.setProperty("--zone-raw", zone ? zone.color : "var(--line)");
    el.style.setProperty("--zone-mix", H.DEPTH_MIX.center + "%");
    var title = $("#hexCenterTitle"), meta = $("#hexCenterMeta");
    var name = running ? ((state.current.task && state.current.task.name) || "计时中")
                       : (paused ? paused.taskName : "");
    // 圆心下半部分（人类 2026-09-12 末轮）：「01:06 在偏上部位置，时分文字去掉，下方是简要标题，
    // 再下方是开始时间以及右下方加一个开始（灰色小字），日期就不要了」。
    //   · 简要标题：长按建的任务名就是「YYYY-MM-DD HH:MM[ 备注]」（hex-data.js::stampTaskName），
    //     这种名字本身只是个时间戳 —— 有备注用备注，没有就退到项目名；别的任务名照原样。
    //   · 开始时间：这件事**最初**几点开始（继续出来的那段沿用暂停前的起点，K.startedAt / 暂停记忆的 startedAt），
    //     不再从任务名里抠。
    // 静置档整块藏起来，只剩圆环和读数（CSS：.has-ctl:not(.is-hover)）。
    var stamp = /^(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}:\d{2})\s*(.*)$/.exec(name);
    var projectName = running ? (state.current.project && state.current.project.name) : (paused && paused.projectName);
    var brief = stamp ? (stamp[5] || projectName || "") : name;
    var t0 = Date.parse(running ? K.startedAt(state.current) : (paused && (paused.startedAt || paused.pausedAt)));
    var hhmm = t0 ? new Date(t0).toTimeString().slice(0, 5) : "";
    title.innerHTML = (running || paused)
      ? (brief ? '<span class="hc-name">' + esc(brief) + "</span>" : "") +
        (hhmm ? '<span class="hc-start"><b class="hc-time">' + esc(hhmm) + '</b><small class="hc-tag">开始</small></span>' : "")
      : "";
    if (paused) {
      var c = Ring.formatClock(paused.carriedSeconds || 0);
      Ring.setFlip(el.querySelector(".tring-num"), c.text);
      el.querySelector(".tring-unit").textContent = "已暂停";
    }
    // 这一行只剩空闲态的「空闲」；暂停中靠读数压暗 + 暂停键变「继续」表达（中心格不显示单位小字）。
    meta.innerHTML = (running || paused) ? "" : '<span class="hex-center-idle">空闲</span>';
    el.classList.toggle("is-running", running);
    // 2026-09-07 人类改判：中心圆环也要悬停/点击两档，点击直接进计时台。
    // 上一轮的「空闲态不可点」随之作废 —— 空闲态点进去正是要去开始计时。
    // 规范 contracts/timer-ring-visual-v1.md 已同步改（规范先于实现）。
    el.setAttribute("aria-label", running
      ? ("正在计时：" + ((state.current.task && state.current.task.name) || "") + "，点击进入计时台")
      : "当前空闲，点击进入计时台");
  }
  function startTicking() {
    if (tickHandle) clearInterval(tickHandle);
    tickHandle = setInterval(function () {
      if (state.current && state.current.running) paintCenter();
    }, 1000);
    setInterval(function () {
      getJson(CURRENT_PATH).then(function (r) { if (r.ok) { state.current = r.data; paintCenter(); } });
    }, 5000);
  }


  // 展开卡里的纯 UI 开合。不碰后端，不改 state.hive —— 所以也**不重排布局**：
  // 卡片是 overflow:auto 的，内容长出来只影响它自己那条滚动，蜂巢一格不动。
  function onUiAction(el) {
    var act = el.dataset.hexUi;
    var cell = el.closest(".hex-cell");
    if (!cell) return;
    if (act === "close") { collapse(); return; }
    if (act === "toggle-card") {
      // 单击卡片露出「完成/改名/删」（人类点名）。同一时刻只开一张 ——
      // 全开等于回到"按钮一直挂着"，那正是这次要治的拥挤。
      var already = el.classList.contains("is-open");
      [].forEach.call(cell.querySelectorAll(".hex-todo-card.is-open"), function (o) {
        o.classList.remove("is-open");
      });
      if (!already) el.classList.add("is-open");
      return;
    }
    if (act === "toggle-all") {
      var list = cell.querySelector(".hex-all");
      var open = el.getAttribute("aria-expanded") === "true";
      el.setAttribute("aria-expanded", String(!open));
      el.classList.toggle("is-open", !open);
      if (list) list.hidden = open;
      var n = list ? list.children.length : 0;
      el.innerHTML = '<span class="hex-chev" aria-hidden="true">' + (open ? "▸" : "▾") + "</span>" +
                     (open ? "展开全部 <b>" + n + "</b> 条" : "收起");
      return;
    }
    if (act === "new-task") {
      var form = cell.querySelector(".hex-newcard");
      if (!form) return;
      form.hidden = false;
      cell.classList.add("is-adding");
      var input = form.querySelector('input[name="name"]');
      if (input) { input.focus(); input.select(); }
      return;
    }
    if (act === "cancel-new") {
      var f2 = cell.querySelector(".hex-newcard");
      if (f2) { f2.hidden = true; f2.reset(); }
      cell.classList.remove("is-adding");
    }
  }

  // ── 事件 ────────────────────────────────────────────────
  function bind() {
    var hive = $("#hive");
    // 纯展示态的开合归这里（data-hex-ui），写操作归 hex-crud.js（data-hex-action）。
    // 两套属性名分开，是为了让"点它不该收起卡片"这条规则只写一次：
    // 下面的 click 处理器和 document 上那个"点空白收起"都只需认这两个属性。
    hive.addEventListener("click", function (ev) {
      // 刚拖完的那一次 click 不算点击（否则一拖完就把工具条开了）。
      if (state.cardDragged) { state.cardDragged = false; ev.stopPropagation(); return; }
      // 中心格上的三个按钮先认领：点它们不是"点中心格进计时台"。
      if (K.onClick(ev)) { state.handledClickAt = ev.timeStamp; return; }
      // ⚠️ 两套属性会**互相嵌套**，所以不能按固定顺序判，要看**谁离 target 更近**：
      //   · 「删除」按钮 data-hex-ui 长在 form[data-hex-action] 里 → ui 更近
      //   · 「完成」按钮 data-hex-action 长在卡片 data-hex-ui 里   → action 更近
      // 上一版写死"先查 action 就 return"，于是「删除」永远点不动（人类报的）。
      // 判据：action 是 ui 的**祖先** ⇒ ui 更近 ⇒ 归这里；否则归 hex-crud.js。
      var actEl = ev.target.closest("[data-hex-action]");
      var ui = ev.target.closest("[data-hex-ui]");
      if (actEl && !(ui && actEl.contains(ui) && actEl !== ui)) return;   // 写操作归 hex-crud.js
      if (ui) {
        ev.preventDefault();
        ev.stopPropagation();
        state.handledClickAt = ev.timeStamp;
        onUiAction(ui);
        return;
      }
      var cell = ev.target.closest(".hex-cell");
      if (!cell) return;
      // 中心格：第二档就是**离开这一页**（人类：点击直接进入到圆环界面）。
      if (cell.classList.contains("hex-center")) {
        // 触摸没有悬停档，而三个按钮只在悬停档露出来 —— 所以有按钮可按时
        // （计时中 / 暂停中），第一下先把按钮亮出来，第二下（点上半部分）才进计时台。
        if (state.lastPointerType && state.lastPointerType !== "mouse" &&
            cell.classList.contains("has-ctl") && state.hoverItem !== cell.__hexItem) {
          state.handledClickAt = ev.timeStamp;
          setHover(cell.__hexItem);
          return;
        }
        window.location.href = RING_HREF;
        return;
      }
      // 占位格**单击不展开**：入口是长按（人类定的），单击展开会让"想点开
      // 旁边那格却点偏了"变成"莫名其妙弹出建项目表单"。
      if (cell.classList.contains("is-placeholder") &&
          !cell.classList.contains("is-expanded")) return;
      if (cell.classList.contains("is-expanded")) return;
      // 长按刚开过火：这一次 click 是它的尾巴，别再展开。
      if (T.swallowClickAfterPress()) { state.handledClickAt = ev.timeStamp; return; }
      // 平板/手机没有 hover 这一档。第一下点出"悬停档"（标题上移 + 三条待办
      // 卡片），第二下才展开 —— 直接一步到展开会让触摸用户永远见不到中间那档，
      // 而那一档正是长按待办计时的入口。
      if (state.lastPointerType && state.lastPointerType !== "mouse" &&
          state.hoverItem !== cell.__hexItem) {
        state.handledClickAt = ev.timeStamp;
        setHover(cell.__hexItem);
        return;
      }
      // 这一次 click 已经由本处理器消化掉了，下面那个"点空白处收起"的
      // document 监听必须放行它 —— 见下面注释里的真 bug。
      state.handledClickAt = ev.timeStamp;
      expand(cell.dataset.projectId);   // 占位格走长按，不会走到这里
    });
    // 长按：pointer 一套覆盖鼠标与触摸。move/up/cancel 挂 window，
    // 因为手指经常在元素外面抬起来。
    S.bind(hive);                          // 空白处长按 → 长出一格 → 建项目
    hive.addEventListener("pointerdown", function (ev) {
      state.lastPointerType = ev.pointerType || "mouse";
      onCardPointerDown(ev);                 // 拖卡片改优先级
      T.onPressDown(ev);                     // 长按开始计时
    });
    window.addEventListener("pointermove", function (ev) {
      onCardPointerMove(ev);
      T.onPressMove(ev);
    }, { passive: true });
    window.addEventListener("pointerup", function (ev) { onCardPointerUp(ev); T.onPressUp(ev); });
    window.addEventListener("pointercancel", function (ev) { onCardPointerUp(ev); T.onPressUp(ev); });
    // 触摸长按在多数浏览器上会弹系统菜单/选中文本，把这一层挡掉。
    // 中心格例外：它是 role=link，右键菜单有用。
    hive.addEventListener("contextmenu", function (ev) {
      var cell = ev.target.closest(".hex-cell");
      if (cell && !cell.classList.contains("hex-center")) ev.preventDefault();
    });
    hive.addEventListener("mouseover", function (ev) {
      // 聚焦态**不进悬停档**（人类 2026-09-08：「禁用放大视图下鼠标放上去放大，
      // 不是禁用点击」）。拦在这里而不是 CSS 的 pointer-events —— 后者会把
      // 点击一起掐死，隔壁的卡片就点不进去了。点击照常：点隔壁 = 换它展开。
      if (state.expandedId) return;
      var cell = ev.target.closest(".hex-cell");
      // 占位格（分区里一个项目都没有）不长大：它没有待办可显示，长大了是个空壳。
      var ok = cell && !cell.classList.contains("is-expanded") &&
               (cell.classList.contains("hex-center") || !!cell.dataset.projectId);
      queueHover(ok ? cell.__hexItem : null);
    });
    hive.addEventListener("mouseleave", function () { clearHover(); });
    // ⚠️ 真 bug（2026-09-07 真机联调测出，纸面审不出来）：展开时
    // fillCell() 会重写那一格的 innerHTML，于是**刚才被点中的那个
    // .hex-title 已经从文档树上掉下来了**——等这个冒泡到 document 的
    // 同一次 click 跑到这里时，`ev.target.closest(".hex-cell")` 走的是一条
    // 已经脱离文档的父链，返回 null，于是"点在格子外面"判定成立，
    // 刚展开的卡片当场又被收起来。表现是"点了没反应"。
    // 用鼠标点标题会复现，用 JS 直接 cell.click()（target 就是 .hex-cell
    // 本身、不会被替换）反而正常——所以只有真的用鼠标点才暴露。
    // 修法：本次 click 已被上面的处理器消化过就直接放行；再加一条
    // isConnected 兜底，覆盖将来任何"处理完就重写 DOM"的同类形状。
    document.addEventListener("click", function (ev) {
      if (!state.expandedId) return;
      if (ev.timeStamp === state.handledClickAt) return;
      if (!ev.target.isConnected) return;
      if (ev.target.closest(".hex-cell")) return;
      // ⚠️ 长按的尾巴要放行。人类 2026-09-08 报的「长按之后……又回到了六边形
      // 放大视图」是它的孪生兄弟，这一条是它的另一半：
      // 长按占位格展开成建项目表单之后，那一格**放大并重排**了，鼠标原地
      // 没动，底下却已经不是格子而是 .hive-cam —— 于是紧跟着的那次 click
      // 落在"空白处"，刚展开的表单当场被收掉。
      // 实测流水：601ms 展开 → 816ms click(target=hive-cam) → 863ms 收起。
      if (T.swallowClickAfterPress()) return;
      collapse();
    });
    // Esc 在"正在写新任务"时先收输入卡，不要一下把整张卡片关掉。
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") return;
      var adding = $(".hex-cell.is-adding");
      if (!adding) return;
      ev.stopPropagation();
      var f = adding.querySelector(".hex-newcard");
      if (f) { f.hidden = true; f.reset(); }
      adding.classList.remove("is-adding");
    }, true);
    // 触摸档的"悬停"没有 mouseleave 可依赖，点到蜂巢外面就退掉。
    document.addEventListener("click", function (ev) {
      if (!state.hoverItem) return;
      if (ev.target.closest(".hex-cell")) return;
      clearHover();
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && state.expandedId) collapse();
    });
    // 键盘也能进计时台（中心格是 role=link tabindex=0）。
    hive.addEventListener("keydown", function (ev) {
      var cell = ev.target.closest && ev.target.closest(".hex-center");
      if (!cell || ev.target.closest("button")) return;   // 焦点在按钮上：Enter 归按钮自己
      if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); window.location.href = RING_HREF; }
    });
    window.addEventListener("resize", function () { clearHover(); });
    // 刷新按钮是可选的：嵌进 index.html 时没有，缺了不算错。
    var refreshBtn = $("#hexRefresh");
    if (refreshBtn) refreshBtn.addEventListener("click", function () { refresh(); });

    // 「经典列表」开关连同旧 #zoneGrid 列表一起去掉（2026-09-14）。前提是先把
    // 列表**独有**的写路径搬进蜂巢：分区改名/删分区、项目改名/换区/删项目现在
    // 都在分区规划面板里（hex-zone-plan.js）。
  }

  function refresh(keepExpanded) {
    S.removeSprout();                      // 重建蜂巢前把临时长出来的那一格收掉
    var want = keepExpanded ? state.expandedId : null;
    return loadAll().then(function (ok) {
      if (!ok) return false;
      state.expandedId = null;
      // 复用 DOM：长按建完任务会在飞行动画的尾巴上触发一次 refresh，
      // 重建整棵树会把正在膨胀的中心格连同它的动画一起换掉。
      // 复用还顺带让"数据刷新"也能走 FLIP，而不是整张图闪一下。
      mount(true);
      L.apply(false);
      if (want && state.byId[want]) { expand(want); }
      return true;
    });
  }

  window.NexusTableHexApp = {
    refresh: refresh,
    collapse: collapse,
    getState: function () { return state; },
    fillCell: fillCell,
    setStatus: setStatus,
    reloadExpanded: function () { return refresh(true); },
    // 建完项目用这条：那一格的身份刚从"占位格"变成"某个项目"，展开态却挂在
    // 坐标键上，重建之后同一个键指向的可能已经是别的格子（分区重排、热度
    // 换位都会动坐标）。所以先收起再重拉，不试图"保持展开"。
    collapseAndReload: function () { collapse(); return refresh(false); },
    // 从别的 tab 切回蜂巢时必须调一次：藏着的时候 getBoundingClientRect 全是 0，
    // 不重算 FLIP 会拿这堆 0 去算位移，切回来整张图会飞一下。
    relayout: function () { clearHover(true); L.apply(false); }
  };

  function boot() {
    if (!$("#hive")) return;          // 这一页没有蜂巢容器，静默不启动
    state.zoneOrder = loadZoneOrder();
    L.init(state);                       // 布局层拿到同一份 state（几何全在那边算）
    // 长按计时那一层要的运行时上下文一次注进去（它够不着本闭包里的函数）。
    S.init({ state: state, reduceMotion: reduceMotion,
             // 空白处长按长满之后：把那一格真的插进蜂巢再展开，
             // 于是聚焦、放大、推镜全都走和别的格子一样的那条路。
             addSproutCell: function (cell) {
               state.hive.cells.push(cell);
               mount(true);
               expand(cellDomId(cell));
             } });
    Z.init({ state: state, L: L, saveZoneOrder: saveZoneOrder, rebuildHive: rebuildHive,
             longPressMs: T.LONGPRESS_MS,
             // 短按 = 分区规划面板（人类 2026-09-12）。
             onShortPress: function (zoneId) { ZP.open(zoneId); } });
    ZP.init({ state: state, fetchGantt: D.fetchGantt, createProject: D.createProject,
              halfLifeDays: H.HEAT_HALFLIFE_DAYS,
              // 分区/项目的写路径（2026-09-14）：经典列表去掉之后，它独有的这几条
              // 搬进分区规划面板。**原样递 data.js 的函数**，不在这儿包一层。
              renameZone: D.renameZone, deleteZone: D.deleteZone,
              renameProject: D.renameProject, moveProject: D.moveProject,
              deleteProject: D.deleteProject,
              // 点面板里的一行 = 在蜂巢里展开那个项目（和点格子是同一条 expand）
              openProject: function (projectId) {
                if (state.byId[projectId]) { clearHover(true); expand(projectId); }
              },
              // 新建完：和 hex-crud 建项目同一个收尾 —— 先收起再重拉（格子身份变了）
              reload: function () { collapse(); return refresh(false); } });
    K.init({ state: state, setStatus: setStatus, postJson: postJson, getJson: getJson,
             currentPath: CURRENT_PATH, paintCenter: paintCenter });
    // 轻粒子：纯装饰，只读 state（cells / centerItem / current），不改任何状态。
    if (SK) SK.init({ state: state });
    T.init({ state: state, setStatus: setStatus, reduceMotion: reduceMotion,
             // 中心格「接住计时」= 直接进悬停态（人类 2026-09-11：「自动放大 3 秒，
             // 效果和鼠标放上去一样」）。走 setHover 这条**同一段代码**，
             // 所以尺寸、类名、圆环比例都和真的悬停一字不差，不是仿一个出来。
             // 这 3 秒里 hoverLock 正锁着，鼠标扫过别的格子不会把它抢走。
             hoverCenter: function (on) {
               state.catchHover = !!on;
               setHover(on ? state.centerItem : null);
             },
             // 缩小的过渡时长——"颜色从计划色淡回常态"要和缩小同一段时间，
             // 只能从布局层那一份取，不许 hex-timer 自己再写一个 460。
             animMs: ANIM_MS,
             postJson: postJson, getJson: getJson, currentPath: CURRENT_PATH,
             paintCenter: paintCenter, refresh: refresh, ease: EASE,
             clearHover: function (lockMs) {
               if (lockMs) state.hoverLock = Date.now() + lockMs;
               clearHover();
             },
             // 长按占位格开火之后走这条：把那一格展开成建项目的表单。
             // （占位格现在只剩"零项目的分区"那一种；有项目的分区走空白长按。）
             // 焦点直接进输入框——长按的手抬起来就能打字，不用再点一次。
             openNewProject: function (cellEl) {
               var item = cellEl && cellEl.__hexItem;
               if (!item || item.cell.project) return;
               expand(item.id);
               window.setTimeout(function () {
                 var input = item.el.querySelector('.hex-newcard input[name="name"]');
                 if (input) input.focus();
               }, ANIM_MS);
             } });
    bind();
    // 同一页里另外几条写路径（顶栏「＋新建分区」、回顾里点开的编辑弹窗）写完会
    // 广播新的 tree（app.js 的 onTreeChange，crud.js 每次写完都调 App.refresh()）。
    // 蜂巢订上去就不用人自己按 F5 ——
    // 人类 2026-09-08：「新建分区后不会自动刷新出现新建的。」
    // ⚠️ 第一次回调是**补发当前值**（订阅即发），boot 自己马上要拉一次，
    // 不跳过就会连着打两次 views/tree。
    // 独立调试页没有 app.js，所以这段必须判空。
    var A = window.NexusTableApp;
    if (A && A.onTreeChange) {
      var primed = false;
      A.onTreeChange(function () {
        if (!primed) { primed = true; return; }
        refresh(true);
      });
    }
    refresh().then(function () { startTicking(); });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();                           // 脚本放在 </body> 前时 DOMContentLoaded 可能已经过了
  }
})();
