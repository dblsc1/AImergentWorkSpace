// hive · 蜂巢六边形的**内容生成**（2026-09-08 从 hex-app.js 拆出）
//
// 只做一件事：拿数据吐 HTML 字符串。**零 DOM 操作、零事件、零 state**——
// 谁调它、把字符串塞到哪里，是 hex-app.js 的事。
//
// 拆出来的直接原因是 hex-app.js 又撞了 1000 行硬线（1035）；但这条缝
// 本来就在，上一轮就写好了这个拆法。判据很干净：这一层从来
// 不认识 state，它要的每一样都从参数进来（todos / completions），
// 所以它天生就是可以单测的那一半。
//
// 与 hex-crud.js 的分工：本文件**生成**带 data-hex-action / data-hex-ui 的
// 标记，hex-crud.js **响应**它们。两边靠那几个属性名对齐，不互相 import。
(function () {
  "use strict";

  var H = window.NexusTableHexData;

  // 自己留一份 esc：这一层是纯函数层，不该为了一个四行的转义去够 hex-app 的闭包。
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // 悬停要显示的那条待办**建 DOM 时就填好**，不等鼠标到了再查。数据早就在
  // state.todos 里；现填只会在 hover 那一帧多插一次 innerHTML 重写 —— 正好是
  // 要保 60fps 的那一帧。静置态 .hex-next 是 display:none，不占位、读屏也跳过。
  // todos：这一格项目的待办（调用方从 state.todos 里取好了递进来）。
  function cellInnerHtml(cell, body, todos) {
    var title = cell.project ? cell.project.name : "（空）";
    var next = "";
    if (!cell.project) {
      // 占位格的悬停档只说一件事：这儿能长按。静置档 .hex-next 是 display:none，
      // 87px 宽的格子塞不下也不该塞。
      next = '<p class="hex-next is-none">长按新建项目</p>';
    }
    if (cell.project) {
      // 悬停档给**三条**待办（人类：放大之后"有点空旷"）。取数口径与展开档
      // 完全一致（H.topTodos，后端已排好序，前端不重排），所以悬停看到的三条
      // 就是点开之后「下一步」那三条 —— 两个档次显示的东西不许对不上。
      var top3 = H.topTodos(todos, 3);
      next = top3.length
        ? '<ul class="hex-next">' + top3.map(function (t) {
            // data-task-id 是长按计时的抓手：长按这一条 = 直接对这条任务计时，
            // 不再建新的（人类：「长按子任务也自动开始计时」）。
            return '<li class="hex-todo-card' + (t.state === "waiting" ? " is-waiting" : "") +
                   '" data-task-id="' + esc(t.id) + '">' +
                   '<span class="hex-todo-dot"></span>' +
                   '<span class="hex-todo-name">' + esc(t.name) + "</span></li>";
          }).join("") + "</ul>"
        : '<p class="hex-next is-none">' + esc(H.NO_TODO_TEXT) + "</p>";
    }
    // 标题独占一行、右边挂「＋」（人类 2026-09-08：「把添加新任务做成一个按钮，
    // 放在标题右边，点击自动伸展为一块卡片」）。按钮静置/悬停档都是 display:none，
    // 只有展开卡里才出现 —— 静置格只有 87px 宽，塞不下也不该塞。
    // 标题行右端是**红色的关闭 ×**（人类 2026-09-08：「打开的六边形右上角
    // 放一个 x 关闭用，红色」）。「＋」搬到了「下一步」那一组的标题行右端 ——
    // 它加的是待办，就该长在待办旁边。
    var head = '<div class="hex-head"><h3 class="hex-title">' + esc(title) + "</h3>" +
      '<button type="button" class="hex-close" data-hex-ui="close"' +
      ' aria-label="关闭" title="关闭">×</button>' + "</div>";
    // body 单独包一层 .hex-body。fillCell 只重写**这一层**，不动 .hex-inner ——
    // FLIP 会往 .hex-inner 上挂反向缩放的 inline transform，整块重写会把它冲掉。
    // 上一版是靠"等动画走完再填"绕开这件事，代价是点进去有 300ms 空窗，
    // 人类当场看成"这个六边形没有加号"（真机 20/20 都有，只是那会儿还没渲染）。
    return '<div class="hex-inner">' + head + next +
           '<div class="hex-body">' + body + "</div></div>";
  }

  // ── 展开卡内容：**全卡片化**（人类 2026-09-08）──────────────────
  //
  // 原话：「六边形内部，全卡片化，编辑目录也卡片化，做成 3 条最近待办 +
  // 可伸展的所有待办卡片 + 3 条最近已完成（沿用目前的滚动），以及把添加新任务
  // 做成一个按钮，放在标题右边，点击自动伸展为一块卡片，直接在卡片上写入标题」。
  //
  // 三组内容用**同一种卡片**（.hex-todo-card），只靠圆点和一条状态边区分：
  // 待办实心点 / 等待中虚线框空心点 / 已完成打勾且标题划掉。
  // 不给每组各设计一套外观 —— 那会让"这里有三类东西"变成"这里有三个界面"。
  //
  // 「全部待办」默认收起：一个项目二三十条任务是常态，展开卡再高也装不下，
  // 而人真正天天看的是最上面那三条。收起态只留一行统计，展开在原地长出来，
  // 滚动仍然是 .hex-detail 那一条（人类：「沿用目前的滚动」）。
  // 卡片：三组内容（下一步 / 全部待办 / 最近完成）共用同一种。
  // 工具条（完成/改名/删）**默认收着，单击卡片才露出来**（人类 2026-09-08：
  // 「单击下一步卡片应该出现完成/改名/删」）。一直挂在那里会让一张 100px 宽的
  // 卡片里三个按钮占掉一半，标题反而看不清。
  function taskCard(t, opts) {
    opts = opts || {};
    var cls = "hex-todo-card";
    if (t.state === "waiting") cls += " is-waiting";
    if (opts.done) cls += " is-done-card";
    var flags = [];
    if (t.overdue) flags.push("已过期");
    if (t.dueToday) flags.push("今天到期");
    if (t.state === "waiting") flags.push("被 " + ((t.blockedBy || []).length) + " 条挡住");
    // 最近完成的卡片也能点开，但只给「撤销」：点错了「完成」要能改回来
    // （v0.2.1 实测：之前完成卡没有任何按钮，误点完成就撤不回）。
    // 临时任务（记在「未分类」时间桶上的一段）不是任务：没有 id、没有按钮、点不开
    if (opts.session) {
      return '<li class="' + cls + '"><span class="hex-todo-dot"></span>' +
        '<span class="hex-task-name">' + esc(t.name) + "</span></li>";
    }
    return '<li class="' + cls + '" data-task-id="' + esc(t.id) + '" data-hex-ui="toggle-card">' +
      '<span class="hex-todo-dot"></span>' +
      '<span class="hex-task-name">' + esc(t.name) + "</span>" +
      (flags.length ? '<span class="hex-flag">' + esc(flags.join(" · ")) + "</span>" : "") +
      (opts.stamp ? '<span class="hex-stamp">' + esc(opts.stamp) + "</span>" : "") +
      (opts.done && !opts.session ? '<span class="hex-spent" data-task-id="' + esc(t.id) + '"' + (t.canceled ? ' data-canceled="1"' : "") + "></span>" : "") +
      (opts.done
        ? '<span class="hex-tools"><button type="button" class="hex-mini" data-hex-action="toggle-task">撤销</button></span>'
        : '<span class="hex-tools">' +
          '<button type="button" class="hex-mini" data-hex-action="toggle-task">' +
            (t.done ? "撤销" : "完成") + "</button>" +
          '<button type="button" class="hex-mini" data-hex-action="rename-task">改名</button>' +
          '<button type="button" class="hex-mini is-danger" data-hex-action="delete-task">删</button>' +
        "</span>") + "</li>";
  }

  // 占位格展开 = 建项目的表单（人类 2026-09-08：「长按分区空白区域可以添加
  // 项目……打开详情页面让人创建」）。刻意和「新任务」那张卡长一个样：
  // 同一个 .hex-newcard，只是提交去向不同（hex-crud.js 按 data-hex-action 分派）。
  // zoneId 不写进表单 —— 它已经在格子的 dataset 上，读那一份，不留第二份真相。
  function newProjectHtml(cell) {
    return '<div class="hex-detail">' +
      '<div class="hex-sub">' + esc(cell.zoneName) + "</div>" +
      '<div class="hex-group">' +
        '<div class="hex-group-head"><div class="hex-group-title">新建项目</div></div>' +
        '<form class="hex-newcard is-open" data-hex-action="create-project">' +
          '<input type="text" name="name" placeholder="项目名…" autocomplete="off" maxlength="120">' +
          '<div class="hex-newcard-ops">' +
            '<button type="submit" class="hex-mini">建立</button>' +
            '<button type="button" class="hex-mini is-danger" data-hex-ui="close">取消</button>' +
          "</div>" +
        "</form>" +
      "</div>" +
      '<div class="hex-err" data-hex-error></div>' +
      "</div>";
  }

  // 展开卡内容（hive.stage.v1「十五」）：只有项目名下的面板槽——任务泳道、「＋ 新建任务」、「最近完成」都由舞台页填进 .hex-ruler-slot
  // （旧的「任务」「全部待办」「最近完成」三组随「十二」「十五」去掉了）。
  function detailHtml(project, zoneName) {
    return '<div class="hex-detail">' +
      '<div class="hex-sub">' + esc(zoneName) + "</div>" +
      // pr-ghosts：AI 决策页（hex-decide / hex-minihive）往里放「新建任务」落点和 AI 建议的幽灵行；平时是空的、不占位。
      // 同步生成（不等面板槽被填），它们展开的同一刻就要用
      '<ul class="hex-list hex-next-list pr-ghosts"></ul>' +
      '<div class="hex-ruler-slot" data-project-id="' + esc(project.id) + '"></div>' +
      '<div class="hex-err" data-hex-error></div>' +
      "</div>";
  }

  window.NexusTableHexCards = {
    cellInnerHtml: cellInnerHtml,
    taskCard: taskCard,
    newProjectHtml: newProjectHtml,
    detailHtml: detailHtml
  };
})();
