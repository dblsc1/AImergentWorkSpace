# detector.rules.v1 —— 活动分类规则（存在服务端，AI 助理起草、人一键应用）

> **状态**：v1 规范性。本文件是「ai-detector 的分类规则长什么样、存在哪、谁能改、AI 怎么写」的
> **唯一事实**。只增不改不删；不兼容的改动发 `detector.rules.v2`，新旧并行。
>
> - 提供方：`modules/nexus-core`（v2.6，按租户存一套规则 + 至多一份待应用的草稿；端点见「二」「三」）。
> - 消费方：
>   - `modules/ai-detector`：每轮拉规则（设备令牌，只读），见「四」。
>   - `modules/mcp`：`get_detector_rules`（读）、`propose_detector_rules`（写草稿），见 `mcp.tools.v1` v1.2。
>   - Cockpit「AI助理」页的「规则」分页（人读、改、应用 / 丢弃草稿），见「五」。
>
> **为什么有草稿**：仓主 2026-09-30 定，分类规则不靠人手写，由 AI 助理写，并且能**一次写入全部**。
> 按本项目「AI 只提议、人确认」的原则（`mcp.tools.v1` 第六节），AI 一次调用写入的是**一整套规则的草稿**，
> 人在「AI助理 → 规则」看到「新增 N / 修改 M / 删除 K」，**点一下「应用」**整套生效。AI 碰不到生效中的规则。

```yaml
provides:
  - id: detector.rules.v1
    summary: >
      规则集 RuleSet（≤ 500 条 Rule：id、app?/title? 正则、taskId、confidence、note?、enabled）与版本号；
      GET/PUT /api/core/detector/rules（PUT 须 If-Match，冲突 412）；
      草稿 POST /api/core/detector/rules/drafts、GET .../drafts/current、POST .../drafts/{id}/apply|discard。
      PUT、建草稿、应用、丢弃只收不带 Bearer 的请求（带 Bearer 设备令牌 403）。
```

## 一、规则 `Rule` 与规则集（规范性）

```jsonc
// GET /api/core/detector/rules → 200
{ "version": 7,                                  // 整数；从没存过 = 0（rules 为 []）。每次生效的写入 +1
  "updatedAt": "2026-09-30T10:00:00+00:00",      // 从没存过 = null
  "rules": [
    { "id": "r_3f9a1c2b",                        // 稳定 id，见下
      "app": "code|goland",                      // 正则或 null
      "title": "garden",                         // 正则或 null；app / title 至少一个非 null
      "taskId": "t_a1",
      "confidence": 0.9,
      "note": "garden 项目的编辑器窗口",          // 给人看的一句话或 null
      "enabled": true } ] }
```

响应另带 `ETag: "<version>"` 头（带引号的十进制整数）。

**语义**（与 ai-detector 本机 `rules.json` 相同）：按数组顺序，**第一条**命中（`enabled` 为 `true`、
`app` 为 null 或匹配程序名、`title` 为 null 或匹配标题）的规则给出建议 `taskId` + `confidence`。
正则**不分大小写**（ai-detector 编译时加 `(?i)`），RE2 / Go 语法，部分匹配（要整条匹配自己写 `^…$`）。
`title` 匹配的是**隐私选项处理后、换代号之前**的标题（ai-detector 契约「上传」节第 5 条）。

**校验（写入时——PUT、建草稿、应用草稿——服务端全部做，不过 `422`）**：

| 字段 | 规则 |
|---|---|
| 顶层 | PUT 请求体 `{"rules": [...]}`；只许 `rules` 一个键。草稿见「三」 |
| `rules` | 数组，**≤ 500** 条 |
| `id` | 可省略 / `null`：服务端分配 `r_` + 12 位十六进制。给了：`^[A-Za-z0-9_-]{1,64}$`，同一集合内不许重复。**AI 改一条已有规则时必须带回原 `id`**，否则算「删一条 + 加一条」 |
| `app` / `title` | `null`、省略，或 1–200 个字符的正则；**至少一个非 null**。须能编译，且不许用 RE2 不支持的写法（前后查找、反向引用、`(?P=name)`、条件组、原子组、占有量词）——与 `detector.settings.v1` 的 `pathWhitelist` 同一套检查。`\p{Han}`、`(?<name>…)`、`\z` 这类 Go 有的写法放行 |
| `taskId` | 1–128 个字符，**必须是本租户现存的任务**（已删的 422；已完成的照收） |
| `confidence` | 数字，`0 < confidence ≤ 1`，省略 = `0.9`（布尔不收） |
| `note` | `null`、省略，或 ≤ 120 个字符 |
| `enabled` | 布尔，省略 = `true` |
| 每条规则 | 只许上面七个键，未知键 422 |

- 请求体超过 **256 KiB** → `413`。不是合法 JSON → `422`。
- 422 的响应体（规则相关的端点都用这个形状，`detail` 永远是字符串，同 nexus-core 其余端点）：

  ```jsonc
  { "detail": "3 条规则不合规，第一条：rules[4].taskId：任务不存在：t_zz",
    "errors": [ { "index": 4, "field": "taskId", "message": "任务不存在：t_zz" } ] }  // 最多 50 条；index 从 0 起；与规则无关的错误 index 为 null
  ```

- 存回的是**补齐后的完整规则**（七个键都在，缺省值填上、分配的 `id` 填上）。

## 二、生效中的规则：读与人改（规范性）

均在 `/api/core/` 下，经网关登录门，**按租户隔离**（`X-Nexus-Tenant`）。错误体 `{detail: string, …}`。

| 方法 | 路径 | 谁 | 请求 | 成功 |
|---|---|---|---|---|
| GET | `/api/core/detector/rules` | 人（cookie）、检测程序（Bearer）、MCP（对内） | — | `200` 规则集（上面的形状）+ `ETag` |
| PUT | `/api/core/detector/rules` | **只收不带 Bearer 的**（人） | 头 `If-Match: "<version>"`；体 `{"rules": [...]}` | `200` 新的规则集（`version` +1）+ `ETag` |

PUT 的失败：

- `403`：请求带 `Authorization: Bearer …`（不看令牌真假）。**先于一切校验**。
- `428`：没带 `If-Match`，或不是 `"<整数>"` / `<整数>` 的形状（`W/` 前缀忽略；`*` 不收）。
- `412`：`If-Match` 与当前 `version` 不同——别人（另一个页面、刚应用的草稿）先改了。
  体 `{"detail": "...", "currentVersion": 8}`；页面应重新 GET、让人看过再存。
- `422` / `413`：见「一」。

规则整套替换（不是逐条 PATCH）：页面编辑一条也是把整套发回去——500 条的上限下这不贵，而且与「草稿 = 一整套」同一个形状。

## 三、草稿：AI 一次写入全部，人一键应用（规范性）

每个租户**至多一份**待应用草稿。草稿不改生效中的规则，直到人应用。

```jsonc
// 草稿 Draft（建草稿、读当前草稿的响应里）
{ "id": "drf_9c1e2a3b4d5f",
  "status": "pending",
  "author": "assistant",                     // "assistant" | "human"：只是标注，不是鉴权依据
  "summary": "按终端标签页标题把 garden、blog 两个项目的时间分开；删掉两条指向已完成任务的旧规则",  // ≤ 500 字符
  "createdAt": "2026-09-30T10:00:00+00:00",
  "expiresAt": "2026-10-14T10:00:00+00:00",  // createdAt + 14 天；过期 = 当作没有
  "baseVersion": 7,                          // 建草稿时生效规则的 version（参考）
  "currentVersion": 7,                       // 此刻生效规则的 version：应用时 If-Match 带它
  "rules": [ /* 完整的 Rule，已补齐、id 已分配 */ ],
  "diff": {                                  // 与**此刻**生效的规则比（每次响应现算），按 id 对
    "added":   ["r_new1"],                   // 草稿里有、生效规则里没有的 id（按草稿顺序）
    "removed": ["r_old3"],                   // 生效规则里有、草稿里没有的 id（按生效规则顺序）
    "changed": ["r_3f9a1c2b"],               // 两边都有、七个键里任何一个不同的 id
    "unchanged": 12,                         // 两边都有且完全相同的条数
    "reordered": false } }                   // 两边共有的 id 相对顺序变了（顺序决定哪条先命中）
```

| 方法 | 路径 | 谁 | 请求 | 成功 |
|---|---|---|---|---|
| POST | `/api/core/detector/rules/drafts` | **只收不带 Bearer 的**（MCP 对内调用、人） | `{"rules": [...], "summary": "…", "author": "assistant"}`（`author` 可省，缺省 `"assistant"`） | `201` Draft |
| GET | `/api/core/detector/rules/drafts/current` | 任何过门的请求 | — | `200 {"draft": Draft}`；没有（或已过期）`200 {"draft": null}` |
| POST | `/api/core/detector/rules/drafts/{id}/apply` | **只收不带 Bearer 的**（人） | 头 `If-Match: "<currentVersion>"`；无体 | `200` 新的规则集（同 GET rules）+ `ETag`；草稿随之消失 |
| POST | `/api/core/detector/rules/drafts/{id}/discard` | **只收不带 Bearer 的**（人） | 无体 | `204`；幂等（没有这份草稿也 `204`） |

- **建草稿 = 整套替换旧草稿**：新草稿直接顶掉之前没应用的那份（不管是谁建的）。建草稿按「一」全部校验，
  `summary` 必填、1–500 个字符；顶层只许 `rules`、`summary`、`author`。失败 `403` / `413` / `422`（同 PUT）。
- **应用是原子的**：一次写同时做「规则换成草稿的 `rules`、`version` +1、删掉草稿」，条件是
  `version == If-Match` 且草稿还是这一份。失败：
  - `403` 带 Bearer；`428` 没带 / 形状不对的 `If-Match`；
  - `404` 这份草稿不存在、已过期、或已被新草稿顶掉；
  - `412` `If-Match` 不是此刻的 `version`（`{"detail", "currentVersion"}`）——页面重新拉草稿（diff 会按新的生效规则重算）再让人看；
  - `422` 草稿里的 `taskId` 在建草稿之后被删了（同「一」的错误形状）；草稿不动，可以丢弃或让 AI 重写。
- 草稿过期（14 天）后读不到、应用 `404`；服务端在之后的读写里顺手清掉。
- 规则与草稿存在独立集合 `detector_rules`（每租户一个文档），**不进台账、投影、导出、快照恢复**
  （同 `detector_settings`：它是配置，不是事实）。

### 鉴权取舍（规范性）

- **设备令牌只读**：PUT、建草稿、应用、丢弃带 `Authorization: Bearer …` 一律 `403`，理由与前提同
  `detector.settings.v1`「二」（网关对 `/api/core/` 原样转发 `Authorization`；`deploy/test/tokens.sh` 从门外验）。
  设备令牌存在用户电脑的配置文件里，被别的程序读走的机会比 cookie 大；它能改规则 = 能让所有建议指向错的任务。
- **MCP 建草稿走对内地址、不带 Bearer**：MCP 在 `honeycomb-net` 里直连 nexus-core，只带 `X-Nexus-Tenant`
  （`mcp.tools.v1` 第二、三节），所以能建草稿；MCP **只调**建草稿这一个写端点，不调 PUT / apply / discard
  （固定映射，测试锁住）。
- **已知且接受**：MCP 的对外入口认设备令牌（auth.gate v1.3），网关转给 MCP 时清掉 `Authorization`，所以
  **拿着设备令牌的人经 MCP 也能建草稿**（与 AI 助理同等）。草稿不生效，人应用前看得到逐条 diff；最坏结果是
  顶掉一份没应用的草稿。要堵这条得让网关把「令牌 / 会话」告诉 MCP，那是 gateway.v1 的追加，v1 不做。
- `author` 由调用方自报（MCP 固定填 `"assistant"`），**不作任何鉴权依据**，只给页面标注。

## 四、ai-detector 怎么用（规范性）

- **每轮**（同步开着且没暂停、有段要上传时）`GET /api/core/detector/rules`，带设备令牌。
- `version > 0`（网页 / 草稿存过，**哪怕是空集**）：用服务端的规则，**不读**本机 `rules.json`。
  `enabled: false` 的跳过。某条正则 Go 编译不过：跳过那一条并写日志（少一条 = 少一个建议，不影响隐私）。
- `version == 0`、`404`（老 nexus-core）、或**拉取失败**（网络、5xx、401/403、不是合法 JSON）：用本机 `rules.json`
  （离线后备，行为同 v0.3 之前；本机文件写坏照旧这一轮不上传）。规则只影响「建议挂哪个任务」，
  不影响什么离开本机，所以拉不到不必停上传（与 `detector.settings.v1` 不同）。
- 规则来源与版本写进日志（变了才写）：`分类规则：网页 v7（12 条生效）` / `分类规则：本机 rules.json`。
- 服务端规则命中时建议的 `reason` 是 `网页规则 #N 命中`（N = 该规则在服务端数组里的位置，从 1 起，含停用的），
  本机规则仍是 `规则 #N 命中`。

## 五、Cockpit「AI助理 → 规则」页要做的（规范性，给 UI 实现）

本契约的实现 PR **不含** UI；UI 按下面做（`modules/nexus-core/code/frontend` 的 AI助理页）：

1. **规则列表**：GET rules；每行显示 `app`、`title`、任务的显示路径（按 `taskId` 从 `views/tree?includeEphemeral=true`
   现取；查不到显示「任务已删除」并标红）、`confidence`、`note`、`enabled` 开关；可增、删、改、拖动排序。
   存 = PUT 整套，`If-Match` 带读到的 `version`；`412` 提示「规则刚被改过」并重新加载（不静默覆盖）；
   `422` 按 `errors[].index` / `field` 把错标在对应行的对应格上。
2. **AI 草稿横幅**：GET `drafts/current` 非 null 时，在列表上方显示
   「AI 草稿：新增 N / 修改 M / 删除 K（顺序有变）— 应用 / 丢弃」，附 `summary`、`createdAt`、`expiresAt`；
   展开看逐条：新增的行标绿、删除的行标红划掉、修改的行两版并排（改了哪个键高亮）。
   - **应用** = `POST drafts/{id}/apply`，`If-Match: "<draft.currentVersion>"`，**一次点击**，成功后刷新列表；
     `412` / `404` 重新拉草稿并提示；`422` 显示错误（多半是任务被删了）。
   - **丢弃** = `POST drafts/{id}/discard`。
3. 页上一句说明：「规则由 AI 助理起草——在聊天里说『帮我按终端标签页整理分类规则』；AI 只能写草稿，应用要你点。」

## 六、变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-30 | v1 | 首版。仓主 2026-09-30：分类规则不手写，由 AI 助理写，且能一次写入全部。规则存服务端（按租户一套，≤ 500 条，版本号 + If-Match）；AI 经 MCP `propose_detector_rules` 一次写一整套**草稿**，人在「AI助理 → 规则」一键应用；设备令牌只读；ai-detector 每轮拉，服务端没存过 / 拉不到时用本机 `rules.json` |
