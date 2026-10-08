# assistant · 对外接口契约

> 本文件是「AI助理」页对外行为的**唯一事实**。改本文件先改这里、再改代码；破坏性变更必须先评估消费方并留变更记录。

页面分工（仓主 2026-09-30 定）：计时页（ring）= 现在；任务 / 项目页（hive）= 未来；**「AI助理」= 回顾与分析**。
顶栏页签顺序：任务、计时、AI助理。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: assistant.page.v1
    summary: 「AI助理」页面，静态路由 <站点前缀>assistant/。自上而下五块：AI 对话、待确认建议、待分类（2026-10-08）、活动检测设置、回顾。
consumes:
  - id: agent.chat.v1
    contract: ../../../contracts/agent.chat.v1/contract.md
    purpose: >
      「AI 对话」面板（`code/frontend/chat.js`、`#chat-panel`；2026-09-30 从 ring 原样搬来，行为不变）。
      只调 `<前缀>api/agent/`，**从不调任何代理运行时（opencode）自己的接口**。用到：`GET health`（非 200 / 不是 JSON →
      聊天整块不出现；`configured:false` → 显示「去 .env 填 AGENT_API_KEY」；`debug:true` → 每条回答下多一个折叠的
      「调试」，点开才 `GET sessions/{id}/debug?messageId=`，只当文本显示）、会话的列 / 建 / 读 / 删、
      `POST messages` 读 SSE（`start`/`delta`/`tool`/`done`/`error`，不认识的事件忽略；`tool` 只显示「正在查：…」）、
      `cancel`（「停止」按钮）。POST 一律 `Content-Type: application/json`。失败原样显示 `detail`。
      回答正文**当纯文本渲染**（textContent）。Enter 发送、Shift+Enter 换行。
  - id: nexus-core.activity.suggestions.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      「待确认建议」面板（`code/frontend/suggestions.js`、`#suggest-panel`；2026-09-30 从 ring 搬来）：
      GET /api/core/activity/suggestions?status=pending&limit=200 列出，每条可改任务后 POST {id}/confirm {taskId}，
      或 POST {id}/dismiss；「全部确认」只发 `suggestion.taskId` 非空、`confidence ≥ 阈值`（缺省 80%）、
      且 **`idle` 不为真**的条目。用到字段：`total`、`items[].{id,startAt,endAt,durationSeconds,app,title,
      suggestion.taskId,suggestion.confidence,idle}`。`idle: true`（v2.5，前台没换、但无操作）的条目虚线框、
      标「无操作·可能在阅读」，只能逐条确认。GET 404（后端早于 v2.2）→ 面板整块不出现。失败原样显示 `detail`，
      条目留在列表里。app/title 来自别的机器，只当文本渲染。**确认不是计时**：不发 `honeycomb:timer-changed`。
      只在打开页面、每次操作后、标签页重新可见时拉，不轮询。
      **AI 匹配（nexus-core v2.7，仓主 2026-10-02）**：「让 AI 匹配」按钮借 `chat.js` 的 `window.assistantChat.ask(固定的一句话)`
      在当前对话里发一轮（助理经 MCP 的 `propose_activity_matches` 配任务，本页自己不调 matches）；聊天后端没装 /
      `configured:false` 时按钮不出现（听 `assistant:chat-state {configured, generating}`），答的时候禁用并显示「AI 正在匹配…」；
      每一轮结束（`assistant:turn-done`）重拉列表。`suggestion.classifier == "assistant"` 且任务还在树里的条目只出
      「AI 建议：路径 · 把握 N% · 理由」和两个按钮：**「是 ✓」= POST {id}/confirm {taskId}**；**「否 ✗」= POST {id}/unmatch {taskId}**
      （条目留在待确认里，换成任务下拉 + 确认 / 忽略，提示「AI 的建议已否掉」——读 `rejectedTaskIds`）。规则给的建议仍是
      下拉 + 确认 / 忽略。「全部确认」照旧按把握阈值，助理配的也算。`reason` 是模型写的，只当文本渲染。
      **按窗口分组（仓主 2026-10-03）**：列表按 `(app, title, idle)` 原样相等分组，一组一行（程序 · 标题、段数、
      `durationSeconds` 之和、最早 `startAt`–最晚 `endAt`，折叠的逐段明细）；只有一段的组与原来一条一样。一个任务下拉 + 按钮管整组，
      对组里**每一段**逐个发上面的 confirm / dismiss（unmatch 只发带着那个建议任务的段）；部分失败按组报「N / M 段失败」+
      第一条 `detail`，已成功的段不回滚。组的建议：**每一段**的 `suggestion.taskId` 都是同一任务才预选它（助理配的才出「是 / 否」），
      把握取最小值（「全部确认」按它比阈值）；有段指向别的任务或没有建议（比如「否」只成功了一部分）→ 下拉留空、提示「建议不一致」。
      confirm 绝不对 `rejectedTaskIds` 含该任务的段发（跳过并报错）。下拉的选择按「组 + 组此刻的建议」记，重拉后建议变了、或选的任务已不在任务树里就作废；
      行上按钮与「全部确认」发的都是行上看得到的任务，绝不发不在当前任务树里的任务（下拉为空的组不进「全部确认」）。
      从第一个请求到列表重拉完，所有按钮禁用、不接新的操作；重拉失败时，确认 / 忽略成功的段从页面上拿掉（`total` 跟着减，不低于页上条数），
      否掉（unmatch）成功的段仍待确认，留在页面上并在本地清掉建议、记进 `rejectedTaskIds`，等人手动挑。idle 段单独成组，
      照旧不进「全部确认」。界面上的条数（计数、「全部确认 · N」「已确认 N 条」）仍按段数。
      每组一个勾选项「以后这个窗口都记到这个任务」，确认成功后经 `detector.rules.v1` 加一条规则（见下一条的 2026-10-03 段）。
      **提议新任务（nexus-core v2.8，仓主 2026-10-03「AI 能自动加新任务」，草稿 + 一键确认）**：组里每一段的
      `suggestion.newTask.proposalId` 都相同（`classifier == "assistant"`、项目还在任务树里）时，行上出「AI 建议新建任务 · 把握 · 理由」、
      「新建任务：分区 / 项目 /」+ 可改的名字框（≤ 64 字，预填 `newTask.name`），下拉缺省「不选现成任务（新建上面这个）」、仍可改选现成任务。
      **「是 ✓」**：没选现成任务 → 第一段 POST {id}/confirm `{name: 去空白的名字, proposalId}`（名字空则按钮禁用；提议在页面显示后变了 → 409，按组报），后端建任务并在响应里回 `taskId`，
      组里其余段同样 POST confirm `{name, proposalId}`（后端只建一次、其余复用；提议变了的段 409，按组报）；选了现成任务 → 同普通确认。**「否 ✗」**= 对带这个提议的每段 POST {id}/unmatch `{proposalId}`，
      条目留下、换成下拉 + 确认 / 忽略。段的提议不一致、或有段指向任务 →「建议不一致」。项目已不在树里 → 当没有建议。
      **「全部确认」永远不含提议新任务的组**（建任务必须人逐组点）。勾了「以后这个窗口」时规则用建好的 `taskId`。
      **集合（nexus-core v2.10 + v2.9，仓主 2026-10-08「碎片太多」）**：列表最上面一层是**集合**，按集合里各段 `durationSeconds`
      之和从大到小排（一样长的先出现的在前）。归集合：`suggestion.collection.key` 相同的段是一个集合（名字用 `collection.name`）；
      没有这个键的段按「`app` + 归一化的 `title`」归——归一化 = 空白并成一个，去掉**开头**的状态符号（Unicode So 类与 `*` `•` `·`）
      与计数 `(N)` / `[N]`，去完为空则用原标题——所以不用 AI 也能把只差转圈符号 / 计数的窗口并到一起。集合里仍是上面说的窗口行
      （idle 段照旧单独成行、带徽标）。集合头：名字、「共 N 分 · N 个窗口 · N 段 · 起止」、**项目下拉**、**「确认整个集合」**、
      一行说明这一下会怎么记；窗口行收在原生 `<details>` 里（只有一个窗口的集合缺省展开，人展开 / 收起过的重绘后保持）。
      **集合的项目**：集合里有建议的段（`suggestion.taskId` 所在的项目、`suggestion.newTask.projectId`、`suggestion.projectId`，
      且项目还在任务树里）都指向同一个项目时预选它，否则空着让人挑；人挑的只记在页面上（按集合键；集合没了就丢），不发任何请求。
      **集合有项目时**，里面每行的任务下拉只列这个项目的任务，第一项「未分类（只记到这个项目）」是缺省：这样确认 =
      POST {id}/confirm `{projectId}`（nexus-core v2.9，记进项目的未分类；与 `taskId` / `proposalId` 互斥）。规则建议的任务在这个项目里
      → 预选它；在别的项目里 → 这行保留建议、列全部任务。下拉末尾「其他项目…」退回全部任务（没选不能确认），「← 只看集合的项目」退回来。
      助理配的行（是 / 否）与提议新任务的行不受集合项目影响。集合没项目时行同以前（全部任务，没选不能确认）。
      **「确认整个集合」**：对集合里每个窗口行、每一段依次发——行上此刻选了任务的 `{taskId}`，没选而集合有项目的 `{projectId}`；
      **不含** idle 行（由人逐条定）、没东西可发的行（提议新任务而没改选现成任务的、没项目也没选任务的）；某段的 `rejectedTaskIds`
      里有这个任务的不发。逐行报部分失败、成功的不回滚、发完重拉前全部控件禁用，同组确认。勾了「以后这个窗口」而记到未分类的行不加规则（提示一句）。
      **「全部确认」（阈值）含义不变**：只发有任务建议且把握够的行的 `{taskId}`，**从不发 `{projectId}`**。
      集合名、项目名一律 textContent。320 / 390 px 不横滚。
  - id: nexus-core.views.tree.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      待确认建议的任务下拉与「分区 / 项目 / 任务」路径显示：zones/projects/tasks 的 id、name、zoneId。
  - id: nexus-core.sessions.reassign.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      「待分类」面板（`code/frontend/unclassified.js`、`#unclassified-panel`，仓主 2026-10-08：记进未分类的时间要能
      「归入 xxx 任务」）。列出**此刻还记在各项目「未分类」时间桶上**的每一段，人挑任务归进去。
      读：`views.tree.v1` 里每个带 `unclassifiedTaskId` 的项目各发一次
      `GET /api/core/events?type=session.completed&taskId=<桶>&limit=200`（`events.read.v1`，nexus-core v2.11 起只回当前还在
      桶上的段）；用到 `total`、`items[].{id, source, time, data.startAt, data.durationSeconds}`。
      按项目分组（时间多的项目在前）：组头「分区 / 项目」+「N 段 · 共 M 分」（`total` 大于页上条数时注明还有几段没列）；
      每段一行「临时任务 MM-DD HH:MM · N 分」（时间 = `data.startAt`，没有则 `time`；浏览器本地时区）+ 来源徽标
      （`timer-backend` 计时、`manual-backfill` 补登、`activity-confirmed` 电脑检测，其余原样显示 source）。
      每组一个任务下拉：缺省只列**本项目**的任务（已完成的标「（已完成）」），末尾「其他项目…」换成全部项目的任务
      （按「分区 / 项目」分组，可「只看本项目」换回）。没选任务时归入按钮禁用。
      写：行上的「归入」= `POST /api/core/sessions/{eventId}/reassign {taskId}`；组上的「全部归入所选任务」对组里每一段逐个发
      （一个接一个，不并发）。成功（含 `duplicate: true`）的行立即从页面上拿掉；部分失败报「N / M 段没归入：第一条 `detail`」，
      已成功的不回滚；全部成功报「已归入 N 段 → 任务路径」。从第一个请求到列表重拉完，面板里所有按钮与下拉禁用。
      一段都没有、或 events 端点 404（后端早于 v0.6）/ 读取失败 → 面板整块不出现；reassign 404（后端早于 v2.11）按失败原样显示。
      **归入不是计时**：不发 `honeycomb:timer-changed`。只在打开页面、每次操作后、标签页重新可见时拉，不轮询。
      **撤回**：刚归入成功的那一批在提示旁出一个「撤回」按钮 = 对其中每一段 `POST …/reassign {projectId: 原项目}`（放回原项目的桶，
      台账里追加的是又一条改挂，不是删除）；只管最近一次操作，做了下一次操作或重新打开页面就没有了。
      所有名字只当文本渲染。
  - id: detector.settings.v1
    contract: ../../../contracts/detector.settings.v1/contract.md
    purpose: >
      「活动检测设置」面板（`code/frontend/settings.js`、`#settings-panel`）。GET /api/core/detector/devices 列设备
      （下拉：deviceId + 最近活动时刻 = max(lastUploadAt, lastFetchAt)；另显示最近上传、最近拉设置、有没有网页设置），
      选中后 GET settings?deviceId=。`settings: null` → 表单显示契约缺省值并注明「用的是本机配置文件」。
      表单逐键对应契约「一」的 privacy / idle 两节（取值、范围与契约一致，例子取契约表格）。
      **保存**：PUT 整份文档 = 读进来的文档（null 时是缺省值）+ 表单改动——服务端以后追加的键原样带回，不被本页清掉；
      只在有改动、或这台设备还没有网页设置时可点。**恢复默认**：确认后 DELETE（这台设备改回用本机配置文件），
      只在有网页设置时可点。本地先校验（名单条数 / 长度、整数范围、白名单正则的 RE2 禁用构造与语法），不过不发；
      服务端 422 的 `detail` 按「节.键」前缀挂到对应那一项下，对不上的显示在表单底部；403 说明「设备令牌只能读」。
      有没保存的改动时标「有没保存的改动」，换设备先确认。强制清洗（密码、密钥 / 令牌、私钥、银行卡号、身份证号）
      显示为勾着的灰框（disabled），注明「不能关；需要改源码重新编译」——它们不在文档里，本页从不发。
      `presence`：文档里（顶层、privacy 或 idle 节）有布尔 `presence` 时才多出一个勾选项，读写同一位置；没有就不出现、不发。
      devices 404（后端早于 v2.5）→ 面板说明「后端还不支持」，不出表单。页面提示「检测程序下一轮（≤ 5 分钟）生效」。
      面板末尾是「分类规则」（见下一条）。
  - id: detector.rules.v1
    contract: ../../../contracts/detector.rules.v1/contract.md
    purpose: >
      「分类规则」（`code/frontend/rules.js`、`#det-rules`，在活动检测设置面板末尾），按该契约「五」：
      GET rules 填编辑器（每条一张卡：启用、上移 / 下移、删除、程序名正则、标题正则、任务下拉〔未完成任务的路径；
      规则指向已删任务时单列「任务已删除（id）」〕、把握、备注），「加一条」「保存规则」= PUT 整套 + `If-Match`；
      412 保留人的改动并换到最新版本号，再点保存才覆盖；422 的 `errors[]` 挂到对应行、对应格。
      GET drafts/current 非 null → 草稿横幅「AI 草稿：新增 N / 修改 M / 删除 K」+ summary + 折叠的逐条改动
      （新增 / 修改〔旧 → 新〕/ 删除），「应用」一次点击 = POST apply（`If-Match: currentVersion`；有没保存的手改先确认），
      「丢弃」= POST discard；412 / 404 重新拉草稿并提示。聊天一轮结束（`assistant:turn-done` 事件，chat.js 发）、
      标签页重新可见时重拉草稿。所有规则 / AI 文本只当文本渲染。rules 404（早于 nexus-core v2.6）→ 只留一句说明。
      **待确认建议也写规则（2026-10-03）**：`suggestions.js` 的组勾了「以后这个窗口都记到这个任务」且至少一段确认成功时，
      经 `rules.js` 暴露的 `window.assistantRules.prepend(rule)`：GET rules → PUT `{rules: [新规则, ...原有规则]}`、
      `If-Match: "<读到的 version>"`（与 `ETag` 同值）。新规则放**最前**（第一条命中生效，放最后会被更宽的旧规则挡住）：
      `app = "^" + 转义(app) + "$"`、`title = "^" + 转义(title) + "$"`（RE2 元字符 `.*+?^${}()|[]\` 加反斜杠；规则不分大小写），
      `confidence 0.9`、`note「待确认里勾的：程序 · 标题」`（截到 120 字）、`enabled true`。已有 `app`、`title`、`taskId` 完全相同的规则：
      它在第一条（下标 0）且启用 → 不 PUT；否则把它（保留 `id`、改成启用）挪到第一条，同样一次 PUT。
      页面不在浏览器里估哪条规则会先命中（JS 正则 ≠ RE2）。
      412 → 重新 GET 再试一次；仍失败或其它错误 → 报「已确认，但规则没加上：detail」，确认不回滚。规则编辑器没有手改时跟着刷新。
      规则匹配的是「隐私处理后、换代号前」的标题（该契约「一」），页面上的标题与之相同（`[IP]` 这类占位符两边一样），
      **除了**标题代号（`privacy.titles = pseudonymize` 时上传的 `窗口名N` / `路径N`）：这样的组勾选项禁用并注明原因；
      转义后超过 200 个字符的也禁用。
```

## 入口与路由

- nginx 公开前缀：`<站点前缀>assistant/`（缺省 `/assistant/`），设门、注入共享顶栏。页面里的请求都从网关注入的
  `window.HONEYCOMB_BASE` 拼（`contracts/gateway.v1` 第七节）。
- 「回顾」块只放入口：计时页的泳道（`../ring/#lanes-panel`）、任务页（`../hive/`）。网关注入了
  `window.HONEYCOMB_NAV` 且其中没有对应页签时，那条链接不出现。分析功能以后做，本版不加后端。
- 计时页上的「N 条待确认 → AI助理」链接指向本页（见 `modules/ring` 契约）。

## 数据与存储

无。纯静态页面，不持久化任何数据。

## 配置与密钥

无。

## 变更记录

| 日期 | CR | 变更 |
|---|---|---|
| 2026-10-08 | 仓主：记进未分类的时间能不能追加「归入 xxx 任务」 | 新增第三块「待分类」（`unclassified.js`、`#unclassified-panel`，在待确认建议与检测设置之间）：按项目列出还记在「未分类」时间桶上的每一段，下拉限本项目任务（+「其他项目…」），逐段「归入」或「全部归入所选任务」= nexus-core v2.11 的 `POST /api/core/sessions/{eventId}/reassign`；没有可归的段 / 端点 404 时整块不出现 |
| 2026-10-08 | 仓主：碎片太多，同类窗口归成集合、给集合选项目 | 待确认建议最上面一层改为**集合**，按总时长从大到小排：认 nexus-core v2.10 的 `suggestion.collection` / `suggestion.projectId`，没有的按程序 + 去掉开头状态符号 / 计数的标题归并。集合头有项目下拉（建议一致时预选）与「确认整个集合」；集合有项目时行的下拉只列这个项目的任务、缺省「未分类」= confirm `{projectId}`（nexus-core v2.9），「其他项目…」退回全部任务。「确认整个集合」不含 idle 行与提议新任务的行；「全部确认」含义不变、不发 `{projectId}`。`#suggest-list` 由 `<ul>` 改为 `<div>`（行仍是 `li.suggest-item`）。「让 AI 匹配」那句话加「先归集合、标项目」。**须与 nexus-core v2.9（confirm `{projectId}`）同版发布** |
| 2026-10-03 | 仓主：AI 能自动加新任务（草稿 + 一键确认） | 待确认建议认 nexus-core v2.8 的 `suggestion.newTask`：组上出「新建任务：项目 / 名称」（名字可改），「是」= 组里每一段 confirm `{name, proposalId}`（后端只建一次），「否」= unmatch `{proposalId}`；下拉仍可改选现成任务；「全部确认」不含提议的组。「让 AI 匹配」那句话加「现成任务都不合适时可以提议新任务」 |
| 2026-10-03 | 仓主：一个窗口对应一个任务（短段太多挑不过来） | 待确认建议按 `(app, title, idle)` 分组，一组一行、一次挑任务，确认 / 忽略 / 是 / 否对组里每段逐个发，部分失败按组报；有建议的段指向不同任务时提示「建议不一致」。每组勾选项「以后这个窗口都记到这个任务」：确认后往分类规则**最前面**加一条精确匹配这个窗口的规则（`detector.rules.v1` PUT + If-Match，412 重试一次，相同规则不重复加；标题是代号时禁用）。`rules.js` 追加 `window.assistantRules.prepend`。纯前端，不动后端 |
| 2026-10-02 | 仓主：终端按标签页分段 | 活动检测设置面板增「分段」一组：复选框「终端按标签页分段」+ 可改的程序名单（`detector.settings.v1` v1.2 的 `segmentByTitle` / `segmentByTitleApps`）。v1.1 存下的文档没有这两个键时按缺省（开 + 本机名单）显示，保存时带上；所以保存需要 nexus-core 认 v1.2（同版发布） |
| 2026-10-02 | 仓主：AI 先做最简单的活动匹配 + 是 / 否 | 待确认建议加「让 AI 匹配」按钮（借聊天发一轮，答完重拉）；助理配的条目出「AI 建议 + 是 ✓ / 否 ✗」（是 = confirm，否 = nexus-core v2.7 的 unmatch）；`chat.js` 追加 `window.assistantChat.ask` 与 `assistant:chat-state` 事件；聊天副标题改为「能写的只有两样建议」 |
| 2026-09-30 | 仓主：分类规则由 AI 助理写 | 「分类规则」占位换成编辑器 + AI 草稿横幅（detector.rules.v1）；聊天一轮结束时发 `assistant:turn-done`；聊天副标题改为「唯一能写的是分类规则的草稿」 |
| 2026-09-30 | 仓主定新页「AI助理」 | 首版：新模块。聊天（agent.chat.v1）与待确认建议（activity.suggestions.v1）从 ring 搬来、行为不变；待确认建议认 v2.5 的 `idle`（徽标、不进全部确认）；新增活动检测设置（detector.settings.v1）与回顾入口 |
