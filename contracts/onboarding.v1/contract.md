# onboarding.v1 —— 新手引导、未分类、只问一次、一次性补记

> **状态**：v1 草案，待仓主批准。批准后只增不改不删；不兼容的改动发 `onboarding.v2`。
>
> - 提供方：`modules/nexus-core`（v2.44，端点见「三」到「七」）。
> - 消费方：
>   - `modules/hive`：新手引导、新建后补记（「八」）；蜂巢布局见 `hive.stage.v1`。
>   - 桌宠浮窗：实时询问（「三」，见 `desktop.pet.v1`「四」）。
>   - `modules/ai-detector`：原始记录追加 `folder`（「二」）。
>   - 安装器 `honeycomb-connect`（v0.6）：终端标题（「九」）。
>   - `modules/mcp`：不新增工具；AI 照旧经 v2.38「五」的提议端点放窗口。
>
> **仓主原话（2026-11-07，节选）**：「桌宠看到主人在一个新窗口聚焦超过 5 分钟但是不确定这个窗口属于哪个时，桌宠右上角显示红点」「新手引导让用户先建立 1-2 个分区，每个分区 1-2 个项目+任务。然后 ai 挑选高把握的窗口放进去，
> 并且写上记录……其他的记录一律并进未分类」「战略性的灰度隐藏这一块未被分类的窗口块，同时，原始窗口信息非特殊不找用户分类，
> 同一个窗口绝对不能让用户分类两次」「ptyxis.coder 这种必须优化，不能出现这种抽象字段给用户分类的界面上」
> 「不纳入 cockpit 管理的，我们做记录，分到窗口块一级，用户以后创建了，直接给建议，可能匹配的窗口块和窗口」。
>
> 本契约建在 nexus-core v2.38「三级分类」之上：原始记录、窗口块、第一级 / 第二级规则、对账、直接施加都不变。
> 只在账号 `tiers: true` 时生效；`tiers: false` 时本契约的端点照常回空，不改变任何旧行为。

```yaml
provides:
  - id: onboarding.v1
    summary: >
      两类数据：有去向的块进台账，其余块叫「未分类」，只留在窗口块一级、不进台账、不出卡片、不出红点。
      块的显示名 displayName 与「看得懂」legible（服务端一处算）；终端块靠 folder 或连上的代理运行认出。
      实时询问：GET activity/live-ask（人在一个认不出的新窗口连续停留 ≥ liveAskMinutes 才给；同一块名一辈子只问一次，activity_asked 记账）、POST activity/live-ask/answer。
      一次性补记：GET activity/unclassified/candidates?taskId|projectId、POST activity/blocks/claim（多块多天一次记到一处，可写第二级规则）。
      网页分类规则 browserDomains；新手引导状态 onboardingDoneAt；新装缺省打开三级分类与 AI 直接分类。
```

## 一、两类数据（规范性）

| | 是什么 | 进不进台账 | 界面上 |
|---|---|---|---|
| 纳入管理 | 生效状态 `assigned`，或 `generic` 且有 `parts` 的块（v2.38「四」第 1 条） | 进，规则同 v2.38 | 记在任务上 |
| 未分类 | 其余没有被忽略的块（生效状态 `pending`，含未拆的 `generic`） | 不进 | 只在「未分类」区，灰、默认收起、不计数、没有红点 |

- 不新增块状态：`pending` 在界面上统一叫「未分类」。
- 「未分类」里的块随原始记录留存（`NEXUS_RECORD_TTL_DAYS`，缺省 35 天）一起过期，过期后不能再补记（v2.38「二」过期条）。

## 二、显示名与「看得懂」（规范性）

服务端在 `GET activity/blocks`、`GET activity/live-ask`、`GET activity/unclassified/candidates` 的每个块上带出两个键，由**一个函数**算
（同 v2.16 `focus` 的原则：页面与 AI 不许各自再算）：

- `displayName`：字符串或 `null`；
- `legible`：布尔。`false` 的块**永远不出现在让人分类的界面上**（不出实时询问、不出补记候选），只在「未分类」区按类合并成一行。

按块的来源依次判：

1. `keySource: "rule"`（第一级规则给的块名）→ `displayName` = 块名，`legible: true`。
2. **浏览器**（块里时长最多的程序在 ai-detector 的 `browserApps` 名单里；服务端存一份同样的名单，只增）：
   标题形如 `域名 · 页面标题` → `domain` = 「 · 」之前的部分。`domain` 在网页分类规则（「六」）里 → `displayName` = 标题，`legible: true`；
   否则 `legible: false`，合并行叫「浏览器（其他网站）」。标题只有程序名（无痕、没装扩展）→ `legible: false`。
3. **终端**（程序在 ai-detector 的 `defaultTabApps` 名单里；服务端存一份，只增）：
   - 块连上了代理运行（「二·1」）→ `displayName` = 运行的代理名（工作目录名），`legible: true`；
   - 否则块里有记录带 `folder`（「二·2」）→ `displayName` = 时长最多的那个 `folder`，`legible: true`；
   - 否则 `legible: false`，合并行叫「终端（未认出）」。**「ptyxis · Coder」这种就落在这里**。
4. **其他程序**：标题非空、不是代号（`titleIsPseudonym: false`）→ `displayName` = 块名里「 · 」之后的部分，`legible: true`；
   否则 `legible: false`，合并行叫「其他」。

### 二·1 终端连上代理运行

原始记录写入时，程序是终端、`keySource: "auto"` 的记录，按记录的中点找当前租户里 `attend`（v2.17）覆盖这一刻的运行，
**恰好一条**才算连上：记录存 `agentRunId`，`blockKey` 改为 `<程序> · <代理名>`，`keySource: "agent"`。对不上或多于一条：不动。

- 运行带 `taskId` / `projectId` 时，这个块在补记候选里算「代理」匹配（「五」）。
- 只对新写入的记录生效，已存的记录不回头重算。

### 二·2 原始记录追加 `folder`（ai-detector upload.v1 v1.5，追加式）

- 程序在终端名单里、且**脱敏前**的标题里有本机路径（同脱敏表「本机绝对路径」那一行的形状）时，记录追加 `folder`：
  路径的最后一层目录名，1–64 个字符；`~` 本身给 `~`。其余情况不带这个键。
- 隐私：`folder` 与脱敏后标题里留下的「文件名」是同一份信息，不多发；`paths` 选项为 `full` 时照样发（那时标题里本来就有整条路径）。
- 服务端：记录文档追加 `folder`（可缺）。老检测程序不带这个键，块落回第 3 条的「终端（未认出）」或靠代理运行认出。

## 三、实时询问：同一窗口只问一次（规范性）

不再有每日询问卡（仓主 2026-11-07「3 去掉」）。只在**人正在用**一个新窗口、又认不出归属时问，由桌宠的浮窗来问（`desktop.pet.v1`「四」）。

`GET /api/core/activity/live-ask`（人、MCP、带 `read` 范围的设备令牌）→ `200 {ask: null}` 或

```jsonc
{ "ask": { "blockKey": "google-chrome · figma.com · …", "displayName": "figma.com · 首页改版",
           "focusedSeconds": 372, "since": "2026-11-07T10:06:00+08:00" } }
```

`ask` 非空的条件（**全部满足**）：

1. v2.16 `focus.state` 是 `active`，当前窗口所在的块今天的生效状态是 `pending`；
2. 该块 `legible: true`；
3. 块名不在 `activity_asked` 里；
4. 人在这个块上**连续**停留（中间离开或切走不超过 60 秒）≥ `liveAskMinutes` × 60 秒（账号设置，缺省 5）。

**放进项目**：人把它拖进某个项目格子 = `POST /api/core/activity/blocks/assign {date, blockKey, ifSeq, projectId, remember: true}`（v2.38 既有端点，只给项目；只许人，所以浮窗里的蜂巢缩略图是登录后的 hive 页面，见 `desktop.pet.v1`「四」）。
`live-ask` 的 `ask` 里追加 `date` 与 `seq`（块的当前序号），给这一步用。
之后由 AI 把这个块分到项目下的具体任务：走 v2.38「五」的提议（`directApply` 受 `agentDirect` 开关与高置信度阈值管）；没有接 AI 或 AI 拿不准时，块留在该项目的「未分类」。

**只问一次**：`activity_asked {user, blockKey, askedAt, answer}`，`(user, blockKey)` 唯一，**永久保留**，进导出 / 恢复。下列任一发生就写一条（已有就不动）：

- 人对这个块名做了 `blocks/assign`、`blocks/split`、`blocks/ignore`、`blocks/claim`（`answer`：`assigned` / `split` / `ignored` / `assigned`）；
- 人在浮窗上点「不管它」：`POST /api/core/activity/live-ask/answer {blockKey, answer: "unmanaged"}`（只许人；块名不存在 `404`）。

写过的块名**以后都不再问**。「不管它」的块仍留在「未分类」，以后新建任务时仍可出现在补记候选里（「五」）——那是人主动去要的，不算再问。

## 四、账号设置追加（规范性，`activity/settings` 追加式）

| 键 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `liveAskMinutes` | 整数 1–60 | 5 | 「三」第 4 条 |
| `liveAskEnabled` | 布尔 | `true` | `false` = 永远不问 |
| `browserDomains` | 字符串数组，≤ 200 条 | `[]` | 网页分类规则（「六」） |
| `onboardingDoneAt` | ISO 时刻或 `null` | `null` | 新手引导走完的时刻（「七」）；只由 `POST activity/onboarding/done` 写，`PUT` 里给的忽略 |

`PUT` 里可省，省了保留原值；给了就校验（越界 / 类型不对 `422`）。

## 五、一次性补记（规范性）

**候选**：`GET /api/core/activity/unclassified/candidates?taskId=|projectId=&days=35`（人与 MCP；二者恰好给一个，否则 `422`；目标不存在 `404`；`days` 1–35）→

```jsonc
{ "target": { "taskId": "t_…" | null, "projectId": "p_…", "name": "写提示词", "path": "学习 / garden / 写提示词" },
  "totalSeconds": 61200,
  "items": [ { "blockKey": "ptyxis · garden", "displayName": "garden", "match": "agent" | "name" | "ai",
               "days": 9, "seconds": 43200, "firstDate": "2026-10-10", "lastDate": "2026-11-06" } ] }
```

- 只看过去 `days` 天里没过期、生效状态 `pending`、`legible: true` 的块，按块名合并多天。
- `match`（一个块只取第一个成立的）：
  1. `agent`：块连上的代理运行的目标就是这个任务（或这个项目）；
  2. `ai`：有待批的 AI `blockRule` / `blockAction` 提议指向这个目标；
  3. `name`：目标名（任务名、项目名；给 `taskId` 时两者都用）切成词——按空白与标点切，字母数字词 ≥ 2 个字符、汉字词 ≥ 2 个字——
     任一词（不分大小写）出现在 `displayName`、`folder` 或网站域名里。
- 按 `seconds` 从多到少，至多 100 条；`totalSeconds` = 这些条的合计。查不到给 `items: []`。

**记进去**：`POST /api/core/activity/blocks/claim`（只许人）

```jsonc
{ "taskId": "t_…" | "projectId": "p_…",   // 恰好一个
  "blockKeys": ["ptyxis · garden", "…"],   // 1..100
  "remember": true }                        // 缺省 true：每个块名同时写（或替换）一条第二级规则
// 200 → { "claimed": [ { "blockKey": "…", "days": 9, "seconds": 43200 } ], "totalSeconds": 43200,
//         "skipped": [ { "blockKey": "…", "reason": "notPending" | "expired" | "beforeTiers" | "notFound" } ] }
```

- 对每个块名、过去 35 天里每个没过期且生效状态是 `pending` 的日子，做一次 v2.38 的 `assign`（服务端代填 `ifSeq`；`409` 重读重试至多 3 次，仍冲突该天算 `notPending`）。
- `remember: true` 时按块名写第二级规则（同 `assign` 的 `remember`）；规则上限超了整个请求 `422`，什么都不改。
- 每个块名写一条 `activity_asked`（`answer: "assigned"`）。
- 入账走 v2.38「四」对账，`via: "human"`。`tiersSince` 之前的记录本来就不入账：只有这种时间的日子计入 `skipped`，`reason: "beforeTiers"`。
- 返回的 `totalSeconds` 就是界面上「原来花了这么多时间在这上面」的那个数。

## 六、网页分类规则（规范性）

界面上叫「网页分类规则」，键名 `browserDomains`：列在里面的网站才算看得懂。


- `browserDomains` 每条是小写域名（1–253 个字符，不带协议、路径、端口；`*.` 前缀表示含所有子域）。重复的去掉。
- 人对浏览器块做 `assign` / `split` / `claim` 时，服务端把这个块的域名**自动加进网页分类规则**（满 200 条不加、不报错）。
- 只影响「二」的 `legible`，不影响检测程序上传什么。

## 七、新手引导（规范性）

- 显示条件：账号 `onboardingDoneAt` 为 `null` **且**任务树里没有分区。老账号（已有分区）永远不自动出引导。
- 步骤（页面做，服务端只给数据）：
  1. 建 1–2 个分区；
  2. 每个分区建 1–2 个项目，每个项目至少一个任务；
  3. 「AI 放窗口」：对每个新任务（及其项目）调「五」的候选；`match` 为 `agent` 或 `name`、且 `seconds` ≥ 600 的块**直接 claim**（`remember: true`），
     接了 AI CLI 的用户另可由 AI 按 v2.38「五」带 `directApply` 放（`confidencePercent` ≥ 高置信度阈值才放）；
     结果页列出放进去的块与合计时长，每块一个「撤销」（= 对该块名各天 `reset` + 删那条第二级规则 + 删 `activity_asked` 那一条）；
  4. 其余全部留在「未分类」，结束。
- `POST /api/core/activity/onboarding/done`（只许人，无体）→ `onboardingDoneAt` = 此刻（已有就不变），回设置文档。跳过引导也调它。

**新装缺省**（仓主 2026-11-05「自动入账现在就默认打开」）：账号**从没存过**设置文档、**且没有任何分类规则**（`detector.rules` 版本 0）时，
第一次读 `activity/settings` 由服务端建文档：`tiers: true`（`tiersSince` = 此刻）、`agentDirect: {enabled: true, windowsClassify: true, honeycombRecords: false}`。
其余情况（老账号、存过设置、有规则）**一切照旧**，不自动改。

## 八、界面（规范性，给 `modules/hive`）

1. **决策栏**：去掉「待分类窗口块」一节，只留 AI 提议。
2. **「未分类」**：窗口时间画成蜂巢左下角的一个桶，规则见 `hive.stage.v1`「五」。**不带红点、不计入任何待办数**。
3. **新建后补记**：新建任务或项目成功后调「五」的候选；`items` 非空时弹一张卡：
   「找到 N 个窗口块，共 X 小时，可能属于它」，列表默认勾选 `match` 为 `agent` / `name` 的，按钮「记进去」= `claim`。成功后提示「已记进 X 小时」。
4. **显示名**：所有让人分类、补记的地方只显示 `displayName`，不显示原始块名。

## 九、安装器：终端标题（规范性，给 `honeycomb-connect`，v0.6）

- 往 shell 配置里写一段，让终端标题等于当前目录（家目录写成 `~`）：bash（`~/.bashrc`）、zsh（`~/.zshrc`）、PowerShell（`$PROFILE`）。cmd 不做。
- 整段夹在两行标记之间：`# >>> honeycomb terminal title >>>` 与 `# <<< honeycomb terminal title <<<`；再跑一次安装器 = 替换这段；卸载 = 删掉这段。不改标记外的任何一行。
- 已有别的程序在设标题（如 bash 的 `PROMPT_COMMAND` 里已有设置）时追加在后面，不覆盖原有的命令。
- 终端里跑着的 AI CLI 会改标题（如 Claude Code 的「✳ 会话名」），这段时间靠「二·1」连上代理运行认出。

## 十、本版不做（有意的）

- 不回头重算已存记录的 `blockKey`（终端连代理只管新记录）。
- 不装社区终端记录器（aw-watcher-terminal 等）。
- 候选的「名字匹配」不做同义词、拼音、翻译。
- 桌宠见 `desktop.pet.v1`。

## 十一、变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-07 | v1 草案 | 首版，待仓主批准 |
| 2026-11-07 | v1 草案二 | 去掉每日询问卡，改为实时询问 `live-ask`；网站白名单改叫「网页分类规则」；界面改到 `hive.stage.v1` |
