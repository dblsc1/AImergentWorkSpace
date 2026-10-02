# assistant · 对外接口契约

> 本文件是「AI助理」页对外行为的**唯一事实**。改本文件先改这里、再改代码；破坏性变更必须先评估消费方并留变更记录。

页面分工（仓主 2026-09-30 定）：计时页（ring）= 现在；任务 / 项目页（hive）= 未来；**「AI助理」= 回顾与分析**。
顶栏页签顺序：任务、计时、AI助理。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: assistant.page.v1
    summary: 「AI助理」页面，静态路由 <站点前缀>assistant/。自上而下四块：AI 对话、待确认建议、活动检测设置、回顾。
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
  - id: nexus-core.views.tree.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      待确认建议的任务下拉与「分区 / 项目 / 任务」路径显示：zones/projects/tasks 的 id、name、zoneId。
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
| 2026-10-02 | 仓主：AI 先做最简单的活动匹配 + 是 / 否 | 待确认建议加「让 AI 匹配」按钮（借聊天发一轮，答完重拉）；助理配的条目出「AI 建议 + 是 ✓ / 否 ✗」（是 = confirm，否 = nexus-core v2.7 的 unmatch）；`chat.js` 追加 `window.assistantChat.ask` 与 `assistant:chat-state` 事件；聊天副标题改为「能写的只有两样建议」 |
| 2026-09-30 | 仓主：分类规则由 AI 助理写 | 「分类规则」占位换成编辑器 + AI 草稿横幅（detector.rules.v1）；聊天一轮结束时发 `assistant:turn-done`；聊天副标题改为「唯一能写的是分类规则的草稿」 |
| 2026-09-30 | 仓主定新页「AI助理」 | 首版：新模块。聊天（agent.chat.v1）与待确认建议（activity.suggestions.v1）从 ring 搬来、行为不变；待确认建议认 v2.5 的 `idle`（徽标、不进全部确认）；新增活动检测设置（detector.settings.v1）与回顾入口 |
