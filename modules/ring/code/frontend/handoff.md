# ring/frontend · 一页纸说明（代码侧）

> **每改必核**：改了本目录代码，同一提交要么更新这里，要么在提交说明里
> 写出站得住的「无需改动」理由。超过一页 = 该拆子文件夹了。

## 这个文件夹是什么

ring 模块唯一的前端页面：`project-task-contribution-ring.html`（cockpit-v1
起改名为"仪表圆环"，2026-08-08 随 F-RING-1 把旧的「贡献圆环 + 计时控件」两块
区域合并成一件仪器，**整体重写**，不是旧版的补丁）。纯静态 HTML/CSS/vanilla
JS，零后端逻辑，只读/只调 nexus-core 四条接口：`GET /api/core/views/current`
（计时状态数据源）、`GET /api/core/views/gantt`（"今天"范围的任务级逐日事实
数据源，2026-08-08 后补的一轮裁决直接复用这条既有读端，不新开端点）、
`GET /api/core/views/tree`（两级选择器 + `dependsOn` 前置提示的数据源）、
`POST /api/core/timer/start|stop|cancel|backfill`（计时控制，`cancel` 2026-08
新增按钮，`backfill` 2026-08-19 新增补登入口）。
对外暴露 `window.renderContributionRing`（沿用契约里的旧名，**入参形状已变**
——见下「与旧版的关键差异」）。

**2026-08-09 新增倒计时（番茄钟）**：**不是新接口**——倒计时＝预设了目标时长
的正计时，「开始倒计时」打的还是 `POST /api/core/timer/start`，到点自动打的
还是 `POST /api/core/timer/stop`（与手动「停止并记录」同一条路径），后端零
新增、契约零变化。前端只多了「选时长 + 本地倒数 + 到点自动停 + 到点提醒」
一层，目标时长/剩余时刻只存浏览器 `localStorage`，不落库、不进契约。

**2026-08-19 新增补登入口**：**是真的新接口**——`POST /api/core/timer/backfill`
（nexus-core v1.8，契约见 `../nexus-core/module_docs/contract.md`「## 补登
（规范性 · v1.8，backfill）」）。用于「完成了但没计时」的事后补录，与
`timer_state` 完全独立（不判断、不读当前是否在跑），空闲态/运行态都能点。
见下「补登入口（2026-08-19）」整节。

## 怎么跑 / 怎么测

- 本地看效果：起一个静态文件服务器（如 `python3 -m http.server`）打开本文件；
  没有真实后端时会显示"连接失败"提示，这是预期行为，不是 bug。
- 生产：由 nginx 挂 `/ring/` 静态目录；确认 `code/frontend/` 是活挂载，
  改文件即生效，不需要重启容器。
  **`/ring/` 经 `auth_request` 鉴权**——本机没有 `COCKPIT_TEST_PASSWORD` 时
  无法登录看真实集成效果，只能靠下面的自核套件（不经 nginx、不需要口令）。
- **自核套件（先跑这个）**：`bash code/frontend/tests/run.sh` —— 真浏览器，
  **一个字节都不写库**：页面由套件自己起的本地静态服务器提供（不经 nginx，
  因此不需要口令），三条接口全部 `page.route()` 在浏览器侧桩掉，
  `/api/core/timer/**`（start/stop/cancel）被**显式 abort**；
  `/api/core/planner/**`（2026-09-08 改名新增的写入面）被 fulfill 成功并**逐条
  记账**（`harness.planner_writes`）——改名要验的是"改完 UI 怎么走"，abort 掉
  就走不到，但一样出不了浏览器。约 2 分钟，49 条用例。依赖不全时响亮 skip 并打印装法，不静默通过。
  **它和真网关上的 E2E 是两回事**：那一套打真网关真库、
  要口令、会往库里写不可删的计时记录——没有明确理由不要跑。
- **tokens 兜底块校验（A2/A3，2026-08-08 新增）**：
  `python3 code/frontend/tests/check_tokens_fallback.py`——diff `ring.css`
  的 `tokens-fallback` 哨兵块 vs `contracts/design-tokens-v1.md` §3 全表
  （颜色）+ 设计稿 `tokens.css`（字体/间距/触控/动效，契约 §3.4 明文委托给
  它），另扫兜底块之外的裸 hex。跨仓依赖不可达（ring 被单独 clone 出来测）
  时响亮 skip、exit 0。
- 截"两级选择器展开"证据图：`python code/frontend/tests/screenshot_dropdown.py
  [输出路径]`（树数据优先只读现拉线上后端）。原生 `<select>` 的下拉是操作系统
  级弹窗，截不到，脚本把两个 `<select>` 都临时设成 `size=N` 原地铺开。
- 无构建链、无 JS lint/单测框架——纯静态 vanilla JS，验收靠上面的 Playwright
  自核套件（行为级、可重放）。

## 当前结构

- `project-task-contribution-ring.html`（182 行）—— 纯骨架标记，逻辑全部移到
  下面四个 JS 文件（2026-08-08 起不再有内联 `<script>`/`<style>`）。
  2026-08-09 新增：计时方式 tab（`#mode-tabs`）+ 倒计时时长面板
  （`#countdown-picker`）、倒计时中心显示（`#countdown-overlay`，与
  `#chrono-center` 互斥）、倒计时进度弧（`#countdown-arc`，svg 里 r=86，画在
  ticks r=92 与 seg r=78 之间，避免和「今日贡献」三段式打架）。
  2026-08-19 新增：补登入口按钮（`#backfill-open-btn`，紧邻 `#mode-tabs` 前面，
  是它的兄弟节点而非子节点）+ 补登对话框（`#backfill-dialog`，任务/日期/时刻/
  分钟四个字段 + `#backfill-message`）。
  2026-09-08 新增：改名浮层（`#rename-overlay`，第三个 `.center` 兄弟层，与
  `#chrono-center`、`#countdown-overlay` 三者互斥）+ 「改任务名」按钮
  （`#rename-open-btn`，塞在 `#controls-row` 里，白蹭那条既有的运行态显隐）。
- `ring.css`（327 行）—— 取代旧 `<style>` 内联块 + `timer-control.css`
  （**均已删除**）。顶部 `tokens-fallback` 哨兵块 = 契约 §3 全表抄件，
  其余样式只准 `var(--…)`（A2）。2026-08-09 新增 `.mode-tabs`/`.duration-*`/
  `.countdown-arc`/`.countdown-overlay.is-time-up` 一组；2026-08-19 新增
  `.backfill-entry`/`.backfill-dialog`/`.backfill-message.is-error|is-duplicate`
  一组；2026-09-08 新增 `.rename-overlay`/`.rename-input`/`.rename-actions`
  与 `#running-task-name` 的可点提示（虚线下划 + `cursor: text`）一组，
  同样全走 token。
- `ring-controls.js`（253 行）+ `ring-instrument.js`（403 行）+
  `ring-countdown.js`（337 行，2026-08-09 新增）+ `ring-backfill.js`
  （182 行，2026-08-19 新增）+ `ring-rename.js`（95 行，2026-09-08 新增）—— 取代旧内联脚本 + `timer-control.js`
  （**均已删除**）。前两个文件的拆分历史：合并后单文件
  冲到 585 行、顶穿单文件行数上限，拆分点选在**「选择你要计的时」**（`ring-controls.js`：
  任务树/两级选择器/`dependsOn` 提示/URL 预选/开始停止取消按钮，
  2026-08-09 新增 `startTimer`/`stopTimer` 两个可复用函数，2026-08-19 新增
  `postCore` 导出，见下）与
  **「画表盘 + 判今天数据」**（`ring-instrument.js`：轮询、`views/gantt`
  今天切片计算、圆环三段式渲染、表芯、图例，2026-08-09 新增一行把最新
  `views/current` 挂到 `window.ringCurrentState`）之间的自然边界，不是硬切。
  `ring-countdown.js` 是第三块：倒计时的 tab/时长面板/本地续算/剩余渲染/
  进度弧/到点自动停/到点提醒全在这一个文件里，**故意不侵入前两个文件的
  内部渲染逻辑**（只读它们暴露的 `window.*` 和几个共享 DOM id）。
  `ring-backfill.js` 是第四块：补登对话框的开关、任务下拉的独立加载
  （自己拉一遍 `GET /api/core/views/tree`，不共享 `ring-controls.js` 的私有
  闭包变量）、时区偏移拼接、三条显示规则的落地，同样不侵入前三个文件。
  跨文件调用一律显式挂 `window.*`（四个 IIFE 词法作用域互不可见，纯静态多
  `<script src>` 顺序加载，不引入模块系统，保持 D18 零构建）：
    - `ring-controls.js` → `window.onStartClicked`、`window.populateTaskOptions`、
      `window.startTimer(taskId)`、`window.stopTimer()`、`window.postCore`
      （2026-08-19 新增导出，供 `ring-backfill.js` 复用同一份请求封装）
    - `ring-instrument.js` → `window.fetchAndRender`、`window.refreshIdleTodayDisplay`、
      `window.renderContributionRing`、`window.ringCurrentState`（最新一次
      `views/current` payload，`null`=上一次轮询失败，`undefined`=还没轮询过）
    - `ring-countdown.js` → 不挂名字给别人调，纯监听者
    - `ring-backfill.js` → 不挂名字给别人调，纯监听者
- `tests/`——`run.sh` 入口、`conftest.py`（静态服务器+浏览器侧桩+探针+
  `views/gantt` 夹具与桩，2026-08-09 新增 `timer_write_bodies` 捕获写请求
  body、`init_scripts` 参数支持 goto 前注入 `localStorage`、
  `COUNTDOWN_STORAGE_KEY` 常量）、`test_ring_instrument.py`（L1–L8 + LT1–LT6，
  取代旧 `test_ring_ui_defects.py` 的 U1–U5——DOM 形状和部分动画语义都变了，
  重写而不是打补丁）、`test_ring_countdown.py`（CD1–CD6，2026-08-09 新增）、
  `test_ring_backfill.py`（BF1–BF7，2026-08-19 新增——对
  `**/api/core/timer/backfill` 单独 `page.route()` fulfill 具体响应体，
  与其余写入面「永远 abort」的判据不同，见文件头注释）、
  `screenshot_dropdown.py`、`check_tokens_fallback.py`。

## 与旧版的关键差异（供下一个接手的人对齐心智模型）

- **旧版「不要改 `renderContributionRing` 内部逻辑」的冻结已解除**：那条冻结
  保护的是上游参考原件的可追溯性，但 F-RING-1 明确要求把贡献圆环整体重做成新的仪表圆环视觉——
  旧的双弧+渐变+引线画法与新设计（分段环+表芯计时器）不是同一件东西，继续
  冻结等于不能完成任务。上游原件本身**没有被动过**（只读、
  只在 `code/` 下派生），只是这次的派生已经不再是"逐字节抄一段"，而是整个
  重新实现。
- **入参形状变了**：旧版 `renderContributionRing(data)` 吃的是中文键
  （`项目`/`当前任务`/`项目占总计划`/…）；新版直接吃 `CurrentOut` 本身
  （`current.project.name`/`current.task.shareOfProject`/…），因为不再有
  "喂给上游原件"这一层翻译需求。契约里"渲染入口存在"这句话仍然成立，
  只是没约束过入参形状。
- **两级选择器**：旧版是一个 `<select>` 用 `optgroup` 承载"分区/项目"、
  `option` 承载任务；新版是两个真正独立的 `<select>`（`#project-select` →
  `#task-select`），项目选完才populate 任务列表。零任务项目仍以
  `<option disabled>` 提示行出现（旧 U3/U4 行为原样保留，只是范围从"跨项目
  optgroup"收窄成"当前已选项目内"）。
- **动画语义变简单**：旧版"任何真实数据变化都播 900ms 增长动画"，新版
  "仅首载 350ms，此后（含真实数据变化）一律直接就位"——见下「避坑」。
- **「今天」数据口径缺口已解决（2026-08-08 后补的一轮）**：第一轮上线时
  `views/current` 只有终身累计口径，撑不起设计文案「今天 · N 分」，当时选择
  不杜撰数字、退回终身累计两段展示。产品决定**不为 ring 开新端点**：
  nexus-core v1.1 已上线的 `GET /api/core/views/gantt` 的 `tasks[].actual`
  本来就是「任务 × 日 → 秒」的形状，直接复用即可。现在环分段是真正的三段式
  今天切片（当前任务=深/次高任务=中/其余任务合计=浅），idle 态表芯显示真实
  「今天 · N 分」（0 也如实显示）。**`views/gantt` 不可达或形状不对时**
  （某个响应缺 `today`/`projects`，或某个项目缺 `tasks` 数组）**仍然静默退回
  第一轮那套终身累计两段展示**——这条兜底路径没有退役，只是从「唯一路径」
  降级成「防御式兜底」，见下「避坑」与 `ring-instrument.js` 里
  `computeTierBreakdown()` 返回 `null` 的分支。

## 倒计时（番茄钟）与正计时的共存方案（2026-08-09）

**口径先讲清楚**（用户裁定，开工中段更正过一次，别按早前"不记账"的旧说法
理解）：倒计时＝预设了目标时长的正计时。「开始倒计时」调用的就是
`window.startTimer(taskId)`——与「开始计时」按钮完全同一个函数、同一条
`POST /api/core/timer/start`；到点自动调用的就是 `window.stopTimer()`——与
「停止并记录」按钮完全同一个函数、同一条 `POST /api/core/timer/stop`。落库、
进档案/投影/圆环这些事后端本来就会做，前端不拦、不新增写入语义。

**共存交互方案：空闲态两个 tab，运行态两者都不见**。

- 空闲态：`#mode-tabs` 两个按钮（正计时/倒计时）决定露出哪个「开始」入口——
  默认「正计时」＝`#start-big-btn`（既有行为零变化）；切到「倒计时」则
  `#start-big-btn` 隐藏（不是移除，`ring-countdown.js` 的 `syncStopwatchStart
  Visibility()` 每 tick 都重新按当前 tab 校正一次，因为这个按钮由
  `ring-instrument.js` 的 `renderIdleCenter()` 动态重建，每次从运行态回到
  空闲态都会带着"默认可见"重新出现），露出 `#countdown-picker`（预设 25/45/
  60 分 chip + 自定义分钟输入 + 「开始倒计时」按钮）。**任一时刻只有一个
  「开始」入口可点**，不给"两种方式都能点"的机会。
- 运行态（`window.ringCurrentState.running === true`，不论哪种方式开始的）：
  `#mode-tabs` 与 `#countdown-picker` 整体隐藏，只留现有的停止/取消两个按钮
  ——这一步不需要额外判断"是不是我的倒计时"，因为**后端本来就只允许一个
  进行中的会话**（`timer/start` 会自动关闭上一个未结束的，现有行为，
  `ring-countdown.js` 不重复判断）。
- 表芯显示互斥：`determineActiveRecord()` 判断"当前在跑的会话是不是我本地
  记的那个倒计时"（比对 `taskId`），匹配则 `#chrono-center` 隐藏、
  `#countdown-overlay` 显示（剩余 MM:SS + 进度弧）；不匹配（例如另一个标签页
  /设备用正计时启动了别的任务）则反过来，且**顺手清理这条失效的本地记录**
  （见下「避坑」）。二者互斥靠切 `hidden`，不靠 z-index 压盖——同一时刻只有
  一个在 DOM 里可见，不存在两段文字叠在一起的风险。
- 手动停止/取消：`#stop-btn`/`#cancel-dialog-confirm` 复用现有按钮逻辑，
  不区分正在跑的是不是倒计时——`ring-controls.js` 的 `stopTimer()` 走完之后
  `fetchAndRender()` 会把 `running` 刷成 `false`，`ring-countdown.js` 下一次
  tick 的 `determineActiveRecord()` 自然判定"不在跑了"，清理本地记录，
  不需要在停止/取消按钮上加任何倒计时专属代码。

**为什么选 tab 而不是"一个按钮，点两下切换成倒计时"**：本模块所有既有交互
（两级选择器、depends-hint、取消确认弹层）都是"状态互斥用整体隐藏，不用
单按钮承载多重语义"的风格（例如运行态直接隐藏两级选择器，不是让它们保持
可见但"看起来禁用"）。倒计时用同一套风格最省认知负担，也最不容易在"到底哪个
按钮对应哪个动作"上出歧义。

## 补登入口（2026-08-19）

**用途**：完成了但没计时的情况下，事后补一条真实记录。表单粒度＝任务 + 日期 +
开始时刻 + 时长（分钟）（人类已定口径，**不是**「日期＋时长」也**不是**
「起止两端」），必须挂具体任务，不允许只挂项目。

**入口位置与可点性**：`#backfill-open-btn` 是 `#mode-tabs`/`#controls-row` 的
兄弟节点，**故意不放进任一个会被运行态/空闲态互斥隐藏的容器**——空闲态、
运行态都可见可点。这是本轮规格点名的要害：补登与「当前是否在计时」完全
独立（契约明写不碰 `timer_state`），前端**不许自己加「必须先停表」的伪
约束**。判定「本轮改对了没」只需看一件事：入口按钮的父节点是不是被
`ring-countdown.js`/`ring-instrument.js` 按 `running` 切 `hidden` 的那几个
id（`#mode-tabs`/`#controls-row`）——不是就对。

**任务下拉**：独立走一次 `GET /api/core/views/tree`（弹层第一次打开时才拉，
拉过之后同一页面会话内不重拉），`<optgroup>` 按项目分组、零任务项目不出现
（补登必须挂具体任务，空列表没意义）。**不复用** `ring-controls.js` 里
`taskSelectEl` 的当前选中值——运行态时那个下拉被锁定成当前在跑的任务，
补登要能挂到*任意*任务，两者语义不同，不能共用同一个 DOM 元素或同一份
`treeData` 闭包变量（三个 IIFE 互相看不见彼此的私有状态，这是本模块一贯的
分工原则，不是本轮新发明的）。

**时区偏移拼接**：`toIsoWithLocalOffset(dateStr, timeStr)`
（`ring-backfill.js`）用 `Date.getTimezoneOffset()` 拼出浏览器本地偏移——
**这个 API 的符号是反的**（东八区返回 `-480`，ISO 偏移要写 `+08:00`），
必须先取负号再判正负，写反了在东半球机器上会静默拼出 `-08:00`，后端会
用错误的偏移解释这个时刻（**不会报错**，因为契约只要求"带偏移"，不校验
"偏移是否与提交者实际时区一致"——这个坑不会被后端拦住，只能靠前端自己
写对）。`test_bf3_*` 用正则 `2026-08-18T14:30:00[+-]\d{2}:\d{2}` 钉住
"必须带偏移"，但**不能**机械验证"偏移值本身对不对"（那依赖跑测试的机器
时区，写死会在别的时区机器上假红）——这条只能靠人读代码核对
`getTimezoneOffset()` 取负号那一行没被删掉。

**三条显示规则的落地位置**：全在 `ring-backfill.js` 的 submit 处理器里，
逐字对应契约「## 补登（规范性 · v1.8，backfill）」一节：
`result.data.duplicate === true` → 固定文案「这段已经补过了。」；成功 →
`` `已记到 ${result.data.date}。` ``（`date` 字段原样回显，**不再本地算一次
归日**）；`!result.ok` → `result.message`（`postCore` 已经把 `detail` 原样
透传出来，本文件不再包一层措辞）。**改这三处任何一处的文案都要同步改
`test_ring_backfill.py` 里对应的精确字符串断言**（BF4/BF5/BF6），
这三条不是"大概意思对就行"的判断题，规格已经把具体字符串钉死。

## 避坑

- **仅首载动画，此后一律直接就位**（F-RING-3，2026-08-08 起的新语义）：
  `drawSegments()` 只在 `ringMounted === false` 且非 reduced-motion 时走
  "先置 0、下一帧动画到终值"的路径，此后（包括真实数据变化）都直接
  `setSeg()` 到终值，不再判断"是不是真变化才决定播不播动画"——**重绘**
  仍然只在数据真变化时发生（`ringKey()` + `lastRingKey` 护栏，逻辑沿用
  2026-08-03 那版），只是"重绘"和"播动画"两件事从此彻底分开，后者只可能
  发生一次。**不要把这两件事重新耦合回去**——旧版正是因为耦合在一起，
  才会在每次真实变化时重放一次完整动画，测试也因此要费劲区分"重放"与
  "首播"，新语义下这个区分问题本身就不存在了。
- **`is-first-load` 类的过渡依赖"先置 0、强制回流、下一帧再赋终值"**：
  少了 `getBoundingClientRect()` 强制回流这一步，浏览器可能把"置 0"和
  "赋终值"合并成一次样式计算，CSS transition 就不会触发（浏览器认为
  "值没变过，何来过渡"）。
- **F-RING-6 前置提示三层防御，缺一不可**（`isUnmetDependency()`）：
  ① 依赖的任务在 `taskIndex` 里查不到（跨项目引用坏/任务被删）→当没有这条
  依赖；② 依赖任务存在但 `done` 字段不是 boolean（nexus-core 还没上线这个
  字段）→不敢断言"未完成"，不算未完成；③ 只有 `done === false` 才算真的
  未完成。**任何一层反过来写（比如把 `done` 缺失当成真值）都会在字段还没
  上线时冒出假的提示**——`tests/test_ring_instrument.py::test_l5_*`
  的 `t_write`/`t_legacy`/`t_dangling` 三个夹具就是专门盯这三层的。
- **取消必须走确认弹层，且确认前不许发任何请求**：`#cancel-open-btn` 只
  `showModal()`，真正的 `postCore("/api/core/timer/cancel")` 只挂在
  `#cancel-dialog-confirm` 的 click 上；`cancel`（Escape/backdrop）事件
  故意留空，靠浏览器默认关闭，不写任何处理逻辑去"顺手"发请求。
- **空闲态不能直接喂 null**：契约里 `running:false` 时 `zone`/`project`/
  `task`/`sessionStartAt` 全为 `null`；`applyState()` 必须先判 `running`
  再进运行态分支，否则读 `current.task.name` 会直接抛错。
- **`shareOfProject` 已经是 0–100 的数字**（契约明确不是 0–1 小数），
  分段绘制直接拿它当 `pathLength="100"` 圆的 dasharray 单位用，不用换算
  周长——这是新版比旧版简化的地方（旧版有一段 `circumference` 计算，
  本版整段退役）。
- **fetch 地址必须是同源相对路径**，轮询间隔沿用 7 秒（建议 5–10 秒
  区间的中间值），无特殊理由。
- **计时状态只认 `views/current.running`**，不自己维护本地"是否在跑"变量。
- **不要在前端补"先 stop 再 start"**：后端 `timer/start` 自动关闭上一个
  未结束的计时。
- **CSS 注释里写 token 名不许直接跟 `*/`**（2026-08-08 插播，auth 已
  踩过全模块性的坑）：`--plan*/--fact*` 这种写法里的 `*/` 会被解析成注释
  提前闭合，静默吞掉后面整段规则、无报错、页面照常渲染，只是丢一整块样式。
  本文件的注释一律写成"`--plan` 族"或把 `*` 与 `/` 断开，交付前用
  `grep -o '/\*' ring.css | wc -l` 与 `grep -o '\*/' ring.css | wc -l`
  比对开闭数量应相等（已核）。
- **零任务的项目也必须出现在下拉里**（2026-08-03 用户实报，两级选择器版
  仍然保留）：`populateTaskOptions()` 里零任务时插入一行
  `<option disabled>` 提示，绝不把项目本身做成可选项。
- `<script src="ring-instrument.js">` 必须在 `</body>` 前、DOM markup 之后
  —— 脚本靠 `document.getElementById` 找元素，顺序反了拿到 `null`。
- **「今天」只认 `gantt.today` 字符串相等比较，绝不用 `new Date()` 拼日期**
  （2026-08-08 明文要求）：`computeTodaySnapshot()` 里唯一的日期判断是
  `a.date === gantt.today`，两边都是服务端给的字符串，客户端不做任何时区/
  时钟运算。夹具 `GANTT_WITH_TODAY_DATA` 里特意让 `t_write` 只有「昨天」的
  记录（`test_lt2_cross_day_boundary_excludes_yesterday`）——如果谁把这行
  改成用本地时间判断"今天"，这条用例会在时区不是 UTC+0 的机器上间歇性抽风，
  而不是稳定报错，非常难查，别引入。
- **0 分和"今天数据不可用"是两个不同的返回值，别用真值判断混起来**
  （`projectTodaySeconds()`/`idleTodayMinutesLabel()`）：`0`（今天真的还
  没有完成的会话）必须显示「今天 · 0 分」；`null`（gantt 不可达/该项目缺
  `tasks` 字段）必须落回「当前没有进行中的计时」不显示数字。**不能写
  `if (!seconds)` 这种把两者揉在一起的判断**——`test_lt3_*` 已经拿这个专门
  验过一次红绿。
- **三档拆分的分母是"项目今日合计"，不是任何终身累计值**：`base =
  tiers.totalSeconds`；`totalSeconds === 0`（今天还没有任何完成会话）时三段
  百分数全按 0 处理，环只剩空轨道，不强行把 0/0 算出 NaN 或硬画满。
- **单个项目缺 `tasks` 字段只让那个项目退回，不拖累别的项目**
  （`computeTodaySnapshot()` 逐项目 `continue`，不是整个响应判不可用）——
  nexus-core 灰度上线新字段时完全可能出现新旧数据混杂，`test_lt5_*` 专门
  验过「只有当前项目受影响」这件事。
- **倒计时本地记录的 taskId 一旦和当前在跑的任务不一致就必须清理**
  （2026-08-09）：`determineActiveRecord()` 里只要 `current.task.id !==
  rec.taskId`（或 `current.running===false`），就顺手 `clearRecord()`——
  不清会留下一条"永远匹配不上却一直存在"的垃圾记录，下次任何任务开始跑
  正计时都会先短暂触发一次误判（虽然当帧就会被再次判定不匹配，但那个瞬间
  的闪烁是可避免的）。`test_cd2b_*` 专门验过清理这一步真的发生了。
- **`window.ringCurrentState` 有三种取值，别把 `undefined` 和 `null` 混为
  一谈**：`undefined`＝还没轮询过一次（页面刚加载，`ring-instrument.js` 的
  首次 `fetchAndRender()` 还没 resolve）；`null`＝轮询过但失败了（网络错误/
  非 200）；正常对象＝拿到了真实的 `views/current` payload。
  `determineActiveRecord()` 对前两种都保守地"不清理、不展示"——如果把
  `undefined`/`null` 都当成"确定不在跑"来清理本地记录，页面刚刷新、真实
  会话还在跑、只是第一次轮询还没跑完的那个瞬间，就会把刚续算出来的倒计时
  记录误删。
- **到点自动停止要有冷却，不能每个 tick（1 秒）都打一次**：
  `handleTimeUp()` 的 `RETRY_COOLDOWN_MS=5000` 防的是 `stopTimer()` 失败
  （真实网络抖动，不是本套件的 abort）时不至于每秒瞎打一次写请求——去掉这
  层冷却在本套件（写入面永远 abort）里会表现成 2.5 秒内打了 4 次，
  `test_cd3_*` 的红绿验证专门抓过这个。
- **beep（Web Audio）与 Web Notification 必须双层 try/catch + `.catch()`
  兜底，缺一层都会漏**：`playBeep()` 里同步的 `new AudioContext()` 用外层
  try/catch，`ctx.resume()` 这个异步分支必须再单独 `.catch(() => {})`——
  只包外层同步调用、不管 `.then()` 回调内部再抛错，异常会以未捕获 promise
  rejection 的形式冒出来，`test_cd6_*` 就是专门盯 `page.on("pageerror")`/
  `page.on("console")` 抓这类漏网之鱼的。**不主动 `Notification.
  requestPermission()`**——到点这一刻没有用户手势上下文，自动请求权限既不
  合规也常被浏览器直接拒绝，只在已经是 `"granted"` 时才发。

### 计时台改名（2026-09-08）

人类要的是「长按六边形生成的通用任务（名字是日期+时间），进计时台改成人话」。
落点是 `ring-rename.js`（第五个 IIFE），只依赖三个已导出的东西，不新增耦合：
`window.patchCore`（本轮新加的导出）、`window.ringCurrentState`、
`window.fetchAndRender`。

两个入口：
- **主**：控件区的「改任务名」按钮 `#rename-open-btn`。人类看了第一版的判词是
  「这个设计不太好，做一个显眼的改名按钮」——只能点圆环里那行小字太隐蔽。
- **次**：直接点圆环里的任务名（保留，因为「所见即所改」比先找按钮快）。

⚠️ **按钮要长在 `#controls-row` 里面。** 那个容器的 `hidden` 由
`ring-instrument.js` 按运行态切（既有逻辑），所以按钮天然只在计时中出现、
空闲态自动消失，**不用再写一份运行态判断**。把它挪出去就得自己补那段判断，
`test_rn7_rename_button_only_exists_while_running` 会红。

- **编辑框必须盖在任务名上，不能就地替换那个节点。**
  `#running-task-name` 的 `textContent` **每一轮轮询都被
  `renderRunningCenter()` 覆写**（那个函数只在 `centerMode` 变化时才重建
  innerHTML，但名字是每次都写）。就地换成 `<input>` 的实现会在下一次轮询时
  被冲掉，用户打到一半的字直接消失。所以走的是 `#countdown-overlay` 已经立过
  的规矩：同为 `.center` 的兄弟层、靠 `hidden` 与 `#chrono-center` 互斥、
  **不进 `#chrono-center` 的 innerHTML 重建逻辑**（那是 ring-instrument.js
  的活，本轮一行没动）。`test_rn6_editor_survives_a_poll` 专门钉这条。
- **点击监听走事件委托，挂在 `#chrono-center` 上。**
  直接给 `#running-task-name` 挂监听，会在中心态切换（空闲↔运行）导致
  innerHTML 重建后失效；要在建它的地方补挂，就得改 ring-instrument.js。
  委托两头都不碰。
- **提交前要复核 `ringCurrentState.task.id` 还是不是开编辑框时的那个。**
  编辑期间计时可能被别处停掉或换了任务，这时候提交会把名字改到**错误的任务**
  上。`ring-rename.js` 的 `save()` 里那句 id 比对就是这道闸。
- **body 只发 `{name}`。** 后端 `TaskUpdate` 是 `_Strict`（extra=forbid），
  多带一个字段就是 422。`test_rn3_save_sends_patch_with_only_name` 钉死这条。
- **写 planner，绝不碰 events**（事实账本只追加）。前端能写是 2026-08-01 的
  产品决定（推翻了「四前端只读」），走的是已注册的写入口
  `PATCH /api/core/planner/tasks/{id}`。

### 暂停 / 继续（2026-09-12，`ring-pause.js`）

人类：「新增一个本次任务暂停按钮（纯前端活）。显示在中心计时圆环和计时面板中。后端接口保持不变。」

- 控件区（只在计时中出现的 `#controls-row`）加「暂停」：调 `window.stopTimer()`（和「停止并记录」
  同一条 `POST /timer/stop`），**成功之后**才把 `{taskId, taskName, projectName, pausedAt}` 写进
  localStorage `nexus.timer.paused.v1`。
- 空闲态出现 `#paused-row`：「已暂停：任务 · 项目　[继续] [完成]」。继续 = `window.startTimer(taskId)`，
  成功后清记忆；完成 = 只清记忆（时间早已入账），不发请求。
- 键与形状是跨模块规范（`contracts/timer-ring-visual-v1.md`「计时控制按钮 + 暂停」）：hive 蜂巢
  中心格 `hex-center-ctl.js` 读写同一个键 —— 两页同源，一边暂停另一边能继续；`storage` 事件即时同步。
- ⚠️ 为什么暂停必须真 stop：后端一段 = start 到 stop，只停前端走秒，暂停那段照样被记成干活，
  事后没有接口能扣掉。
- 发现 `views/current` 正在计同一个 `taskId`（在别处继续了）→ 记忆作废。
- 2026-09-12（二）：**继续接着之前的时间** —— 暂停记忆带 `carriedSeconds` / `startedAt`，继续成功后转写
  `nexus.timer.carry.v1`；`ring-instrument.js::tickElapsedDisplay` 读 `window.ringCarrySeconds()` 加上去（只影响显示）。
  **暂停态也能「取消，不记录」**（产品决定：暂停即入账，取消只作废当前段）：不发请求，只放下"继续"，
  `#paused-note` 说明暂停前的时间已入账、撤不回。「停止并记录」按人类要求**不加**二次确认。
- 测试 `tests/test_ring_pause.py` PA1–PA6（全套 58 条全过）。timer/** 在 conftest 默认 abort，
  用例里后注册一条 route 盖掉它（回 200、翻状态、照样记账，不出网）。

## 已知留给下一轮的缺口

1. 真网关 E2E 套件引用的旧选择器
   （`#task-select` 单选下拉的旧用法、`#start-btn`、`#timer-idle`、
   `#timer-running`、`#running-name`、文件名 `timer-control.css`）已随
   2026-08-08 第一轮改造失配，这一轮补的「今天」数据又进一步改了
   `#seg-rest`→`#seg-second`/`#seg-third`、图例结构。
   那套 E2E 现在显示全绿是因为 `COCKPIT_TEST_PASSWORD` 未设置、整体走
   skip 分支——**不是兼容，是没跑到**。
2. `module_docs/contract.md` 的 `consumes` 列表还没收录 `timer.cancel` 与
   `views.gantt.v1`（nexus-core 那边 `timer.cancel` 本身也还在契约草稿阶段，
   未正式收录进它自己的 `module_docs/contract.md`）；`module_docs/handoff.md`
   里"不要改 `renderContributionRing`"那条冻结描述已经过期。两者都待补。
