# onboarding.v1 / hive.stage.v1 / desktop.pet.v1 —— TDD 文档（待仓主确认）

> v0.6 三份契约（`onboarding.v1`、`hive.stage.v1`、`desktop.pet.v1`）的测试清单。第六节的澄清已写进契约，以契约为准。手动计时（`hive.stage.v1`「三·1」）与桌宠程序 `modules/desktop-pet`（Electron）的用例由实现方按契约补。


约定：条款写法 `OB·三·4` = onboarding.v1「三」第 4 条；`HS·二·3` = hive.stage.v1「二」第 3 条；`DP·一` = desktop.pet.v1「一」。
标 ※ 的用例依赖「第六节」里建议的明确写法，仓主不同意建议时该用例改写或删。
所有 nexus-core 用例用既有夹具：`client`（TestClient）、`seeded`、`clock`（钉服务端时钟）、`_tier_common.py` 的 `rec / upload / blocks / day / db`，库名 `nexus_core_test`。

---

## 一、nexus-core（后端）

### 1.1 放哪、怎么跑

- 目录 `modules/nexus-core/code/backend/tests/`，运行：
  `cd modules/nexus-core/code/backend && NEXUS_TZ=Asia/Shanghai python -m pytest -q tests/<文件>`（CI 同款，全量 `python -m pytest -q`，需本机 Mongo）。
- 新建文件（真实文件名以此为准）：

| 文件 | 管什么 |
|---|---|
| `tests/_onboarding_common.py` | 共用小工具：`rec(..., folder=)`、`beat_timeline(client, clock, [(秒, app, title, afk)])` 造在场时间线、`run_attending(...)` 造带 attend 的代理运行、`make_tiers_account()` |
| `tests/test_block_display.py` | displayName / legible（OB·二） |
| `tests/test_record_folder_agent.py` | 记录的 `folder`、终端连代理运行（OB·二·1、二·2） |
| `tests/test_live_ask.py` | live-ask、live-ask/answer、`activity_asked`（OB·三） |
| `tests/test_unclassified_candidates.py` | 补记候选（OB·五 前半） |
| `tests/test_blocks_claim.py` | blocks/claim（OB·五 后半）、浏览器域名自动加入（OB·六） |
| `tests/test_onboarding_settings.py` | 设置新键、browserDomains、onboarding/done、新装缺省（OB·四、六、七） |
| `tests/test_agent_place.py` | agents/{runId}/place、place-rules、`agents/start` 查表（HS·三） |

- 必须同批改的登记表（漏了会静默少守一块，conftest 里已有同类注释）：`conftest.py` 的 `_COLLECTIONS`、`tests/test_export.py` 的 `_COLLECTIONS`、导出 / 恢复 / 快照的集合登记表，加入 `activity_asked`、`agent_place_rules`。
- 既有测试要改：`test_activity_settings.py::test_defaults_and_readers` 用的是全新账号，新装缺省生效后它读到 `tiers:true`。改法：该测试先写一条 `detector.rules` 版本 ≥ 1（或先存一份设置）再读，使它继续测「老账号缺省不变」。

### 1.2 用例

**A. 显示名与看得懂（`test_block_display.py`）。** 每例同时读 `GET blocks`；NC-D12 验三个端点一致。

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| NC-D01 | OB·二·1 | 记录 `block:"示例文档"`（keySource rule） | GET blocks | `displayName:"示例文档"`, `legible:true` |
| NC-D02 | OB·二·2 | chrome 记录标题 `figma.com · 首页改版`；设置 `browserDomains:["figma.com"]` | GET blocks | `legible:true`，`displayName` = 该标题（归一化后的标题，见 G-08） |
| NC-D03 | OB·二·2 | 同上标题，`browserDomains:[]` | GET blocks | `legible:false`，`displayName:null` |
| NC-D04 | OB·二·2 | chrome 记录标题只有程序名（无痕） | GET blocks | `legible:false` |
| NC-D05 | OB·二·2、六 | `browserDomains:["*.notion.so"]`；标题 `www.notion.so · x` | GET blocks | `legible:true` |
| NC-D06 ※ | OB·六 | 同上；标题 `notion.so · x`（根域） | GET blocks | 按 G-08 建议：`legible:true` |
| NC-D07 | OB·二·2 | 标题 `Figma.com · x`（大写域名），规则 `figma.com` | GET blocks | `legible:true`（比较不分大小写） |
| NC-D08 | OB·二·2 | 块里 chrome 60 秒 + code 30 秒，标题带域名且在规则里 | GET blocks | 按「时长最多的程序」判为浏览器：`legible:true` |
| NC-D09 | OB·二·2 | firefox 标题 `foo.com · x`，但 `firefox` 不在服务端 browserApps 名单（用不在名单的假程序名 `weirdbrowser`） | GET blocks | 不按浏览器判，走第 4 条：displayName = 「 · 」之后 |
| NC-D10 | OB·二·3 | 终端（`ptyxis`）记录，`agentRunId` 已连上运行 `garden` | GET blocks | `displayName:"garden"`, `legible:true` |
| NC-D11 | OB·二·3 | 终端记录带 `folder` 两个：`a` 共 10 秒、`b` 共 40 秒 | GET blocks | `displayName:"b"`；两者秒数相等时取字典序小者（G-07） |
| NC-D12 | OB·二（一个函数） | 同一块在三种状态各一份：规则块、浏览器块、终端未认出块 | 分别读 `GET blocks`、`GET live-ask`（块当前窗口）、`GET unclassified/candidates` | 三处同一块的 `displayName`/`legible` 完全一致 |
| NC-D13 | OB·二·3 | `org.gnome.Ptyxis` 标题 `Coder`，无 folder 无运行 | GET blocks | `legible:false`（仓主点名的「ptyxis · Coder」） |
| NC-D14 | OB·二·3 | 终端记录既有 `folder` 又连上运行 | GET blocks | 运行优先：`displayName` = 代理名 |
| NC-D15 | OB·二·4 | 程序 `code`，标题 `plot.gd`，`titleIsPseudonym:false` | GET blocks | `displayName:"plot.gd"`, `legible:true` |
| NC-D16 | OB·二·4 | 同上，`titleIsPseudonym:true` | GET blocks | `legible:false` |
| NC-D17 | OB·二·4 | 程序 `code`，标题为空（块名就是程序名） | GET blocks | `legible:false` |
| NC-D18 | OB·二 | 所有 `legible:false` 的块 | GET blocks | `displayName` 恒为 `null`；同时带 `kind`（G-05 ※：`browser`/`terminal`/`other`） |
| NC-D19 | OB·二（服务端名单） | 终端名单、浏览器名单 | 对照 ai-detector 的 `defaultTabApps` / `defaultBrowsers` | 服务端名单 ⊇ 检测程序缺省名单（按 G-09 的附录字面量对照；两边各一条测试读同一份字面量） |
| NC-D20 | v2.38 回归 | 设备令牌 | GET blocks | 仍 `403` |

**B. 记录的 folder 与终端连代理运行（`test_record_folder_agent.py`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| NC-R01 | OB·二·2 | tiers 开 | upload 记录带 `folder:"garden"` | 200 accepted；记录文档有 `folder:"garden"` |
| NC-R02 | OB·二·2 | 老检测程序 | upload 不带 `folder` | 200；文档无 `folder` 键；该终端块 `legible:false` |
| NC-R03 ※ | OB·二·2 | — | upload `folder` 为 `""` / 65 个字符 / 数字 | 该条进 `rejected[index]`，其余照收（G-10） |
| NC-R04 | v2.38「一」防重 | 先传 `folder:"a"`，再传同起点 `folder:"b"` | 两次 upload | 第二次 `duplicates:1`；文档仍是 `a` |
| NC-R05 | OB·二·1 | 终端记录 `keySource auto`，中点落在恰好一条 attend 内 | upload | 文档 `agentRunId` = 该运行，`blockKey:"ptyxis · <agent>"`，`keySource:"agent"` |
| NC-R06 | OB·二·1 | 中点处没有任何 attend | upload | `blockKey` 仍为 `app · 归一化标题`，`keySource:"auto"`，无 `agentRunId` |
| NC-R07 | OB·二·1 | 中点处两条运行的 attend 同时覆盖 | upload | 不动（同 NC-R06） |
| NC-R08 ※ | OB·二·1 | attend 为 [10:00:00, 10:05:00)，记录中点恰为 10:05:00 / 10:04:59.9 | upload | 前者不连，后者连（G-11：半开区间） |
| NC-R09 | OB·二·1 | 记录带 `block` 非空（keySource rule） | upload | 不碰，仍 `rule` |
| NC-R10 | OB·二·1 | 程序 `code`（非终端）也在 attend 内 | upload | 不碰 |
| NC-R11 | OB·二·1 | 另一租户有运行 attend 覆盖 | upload | 不连（只看当前租户） |
| NC-R12 | OB·二·1、十 | 先传记录，之后才开运行 | 事后读 | 已存记录不回头重算 |
| NC-R13 | OB·二·1 | 连上的运行带 `projectId` | GET candidates?projectId= | 该块 `match:"agent"` |
| NC-R14 ※ | OB·二·1 / v2.38「一」重算 | 已连代理的块，之后第一级规则版本变化触发重算 | 读 | 按 G-11：`keySource:"agent"` 的记录不被重算 |

**C. 实时询问与只问一次（`test_live_ask.py`）。** 造数据：tiers 开；用 `clock` + `beat_timeline` 造在场心跳（窗口 `chrome`，标题 `figma.com · 首页改版`，设置 `browserDomains:["figma.com"]`，块默认 `pending`）。除特别写明，`liveAskMinutes` = 缺省 5。

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| NC-L01 | OB·三 | 连续停留 372 秒 | GET live-ask | 200；`ask` 含 `blockKey, displayName, focusedSeconds, since, date, seq` |
| NC-L02 | OB·三·4 | 连续停留恰好 300 秒 | GET live-ask | `ask` 非空 |
| NC-L03 | OB·三·4 | 连续停留 299 秒 | GET live-ask | `ask:null` |
| NC-L04 | OB·三·4 | 停留 200 秒 → 切到别的块 60 秒 → 回来 100 秒（共 300） | GET live-ask | 切走恰好 60 秒不算断：`ask` 非空（`focusedSeconds` 口径见 G-02） |
| NC-L05 | OB·三·4 | 同上，切走 61 秒 | GET live-ask | 断了，从回来起算：`ask:null` |
| NC-L06 | OB·三·4 | 停留 200 秒 → afk 60 秒 → 回来 100 秒 | GET live-ask | 离开恰好 60 秒不算断：`ask` 非空 |
| NC-L07 | OB·三·4 | 同上，afk 61 秒 | GET live-ask | `ask:null` |
| NC-L08 | OB·四 | `liveAskMinutes:1`，停留 60 秒 / 59 秒 | GET live-ask | 60 秒非空，59 秒 null |
| NC-L09 | OB·四 | `liveAskMinutes:60`，停留 3600 秒 | GET live-ask | 非空（时间线保留期要够，见 G-02） |
| NC-L10 | OB·四 | `liveAskEnabled:false`，停留 900 秒 | GET live-ask | `ask:null` |
| NC-L11 | OB·三·1 | 窗口 afk | GET live-ask | `ask:null` |
| NC-L12 | OB·三·1 | 最新心跳距今 > 90 秒（`focus:null`） | GET live-ask | `ask:null` |
| NC-L13 | OB·三·1 | 该块已被第二级规则管（`assigned`） | GET live-ask | `ask:null` |
| NC-L14 | OB·三·1 | 该块 `ignored` / 已拆的 `generic` | GET live-ask | `ask:null` |
| NC-L15 | OB·三·2 | 终端 `ptyxis · Coder` 停留 600 秒（`legible:false`） | GET live-ask | `ask:null` |
| NC-L16 | OB·三·3 | `activity_asked` 已有该块名，停留 900 秒 | GET live-ask | `ask:null` |
| NC-L17 | OB·三（永远不再） | 同块先 assign 再 reset 回 pending，再停留 900 秒 | GET live-ask | 仍 `ask:null`（asked 已写） |
| NC-L18 | OB 头、`tiers:false` | `tiers:false`，停留 900 秒 | GET live-ask | `200 {"ask":null}`，不是 4xx |
| NC-L19 | OB·三 | 人 / MCP / `X-Nexus-Scope: read` 的设备令牌 | GET live-ask | 都 200 |
| NC-L20 | v2.19 范围 | `X-Nexus-Scope: report` 的令牌 | GET live-ask | `403` |
| NC-L21 | OB·三（date/seq） | 块无文档 | GET live-ask | `date` = NEXUS_TZ 的今天；`seq:0` |
| NC-L22 | OB·三（date/seq） | 同块被对账建了文档（seq +1） | GET live-ask | `seq` 等于 `GET blocks` 里该块的 `seq`，可直接用于 `blocks/assign` 的 `ifSeq`（用它真调一次 assign 得 200） |
| NC-L23 | OB·三·1 | 第二台设备更晚报心跳在另一个窗口 | GET live-ask | 只看最近报心跳的那台（v2.16）：按那台判 |

**D. 只问一次的写入（`test_live_ask.py`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| NC-A01 | OB·三（asked） | 一个 pending 块 | `blocks/assign` | 写一条 `activity_asked`，`answer:"assigned"`，有 `askedAt` |
| NC-A02 | OB·三 | 同上 | `blocks/split` | `answer:"split"` |
| NC-A03 | OB·三 | 同上 | `blocks/ignore` | `answer:"ignored"` |
| NC-A04 | OB·三 | 同上 | `blocks/claim` | `answer:"assigned"` |
| NC-A05 | OB·三（已有就不动） | 块先 `ignore` 写了 asked，再 reset，再 assign | 读库 | 仍一条，`answer:"ignored"`，`askedAt` 不变（`(user,blockKey)` 唯一） |
| NC-A06 | OB·三 | assign 回 `409`（ifSeq 错）/ `404`（目标不存在）/ `422` | 读库 | 不写 asked |
| NC-A07 ※ | OB·三（只列四个） | 对块做 `reset`、`generic`、`POST block-rules` | 读库 | 不写 asked（G-14） |
| NC-A08 | OB·三 | pending 块 | `POST live-ask/answer {blockKey, answer:"unmanaged"}` | 200；asked 一条 `answer:"unmanaged"` |
| NC-A09 | OB·三 | 上例之后 | GET live-ask（再停留够久）/ GET blocks | `ask:null`；该块仍是 `pending`，仍出现在 `status=pending` 里 |
| NC-A10 | OB·三（「不算再问」） | 上例之后，建一个名字相匹配的新任务 | GET candidates | 该块仍出现在候选里 |
| NC-A11 | OB·三 | 块名在任何日子都不存在 | answer | `404` |
| NC-A12 | OB·三 | `answer` 为 `"assigned"` / `""` / 缺键；`blockKey` 缺键 | answer | `422` |
| NC-A13 | OB·三（只许人） | 设备令牌（Bearer）；MCP 调用方头（沿用 `test_caller_scope.py` 的头） | answer | 都 `403` |
| NC-A14 ※ | OB·三 | 已有 asked 再 answer | answer | 200，幂等，`answer`/`askedAt` 不变；体 `{blockKey, answer, askedAt}`（G-14） |
| NC-A15 | OB·三（永久保留） | asked 一条；钟拨到 `NEXUS_RECORD_TTL_DAYS`+60 天后触发惰性清理 | 读库 | asked 仍在；而同块的原始记录已过期 |
| NC-A16 | OB·三（进导出 / 恢复） | asked 两条 | `GET export` → 清库 → restore | 恢复后两条一模一样；`export` 计数含该集合 |
| NC-A17 | OB·七（租户） | 租户 A 有 asked | 租户 B 读 live-ask | 不受影响 |

**E. 补记候选（`test_unclassified_candidates.py`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| NC-C01 | OB·五 | 项目 `garden`、任务 `写提示词`；终端块 `ptyxis · garden` 连上该任务的运行，9 天各 4800 秒 | `?taskId=` | 200；`target.{taskId,projectId,name,path}`（`path:"学习 / garden / 写提示词"`）；`items[0]` 含 `blockKey, displayName, match:"agent", days:9, seconds:43200, firstDate, lastDate`；`totalSeconds:43200` |
| NC-C02 | OB·五 | 同上 | `?projectId=` | `target.taskId:null`，`path:"学习 / garden"` |
| NC-C03 | OB·五 | — | 不给 / 同时给 taskId 与 projectId | `422` |
| NC-C04 | OB·五 | — | taskId 不存在 / projectId 不存在 | `404` |
| NC-C05 | OB·五 | — | `days=0` / `36` / `abc` ※ | `422`；`days=1` 与 `35` 接受（G-12） |
| NC-C06 | OB·五 | 记录在 10 天前 | `days=7` / 缺省 | 7 时 `items:[]`；缺省（35）时有 |
| NC-C07 | OB·五 | 同块名分布在 3 天，每天两台设备重叠 | GET | 一条；`days:3`；`seconds` = 逐天并集之和 |
| NC-C08 | OB·五 | 候选池里混入：`assigned`、`ignored`、已拆 `generic`、`legible:false`、已过期日子的块 | GET | 全不出现 |
| NC-C09 ※ | OB·一、五 | 未拆的 `generic` 块 | GET | 按 G-06 建议：不出现（v2.38 里它的生效状态是 `generic`） |
| NC-C10 | OB·五 `match` | 块同时满足 agent 与 name | GET | `match:"agent"`（只取第一个成立的） |
| NC-C11 | OB·五 `match` | 块连的运行指向本项目（给 projectId） | GET | `match:"agent"` |
| NC-C12 ※ | OB·五 `match` | 块连的运行挂在本项目下的某任务（给 projectId） | GET | 按 G-12 建议：算 `agent` |
| NC-C13 | OB·五 `match` | 有待批 AI `blockRule` 提议指向目标，块不满足 agent；name 也满足 | GET | `match:"ai"` |
| NC-C14 | OB·五 `match` | 提议已批准 / 已拒绝 / superseded | GET | 不算 `ai`，落到 `name` 或不出现 |
| NC-C15 | OB·五 `match`·3 | 任务名 `写 Garden 提示词`；folder `garden-notes` | GET | 切词 `garden`（≥2 字母数字），不分大小写，出现在 folder → `match:"name"` |
| NC-C16 | 同上 | 词出现在 displayName / 网站域名里 | GET | 各 `name` |
| NC-C17 | 同上 | 任务名词 `a`（1 字符）、`写`（1 汉字） | GET | 不用这些词；无其他词则不出现 |
| NC-C18 | 同上 | 任务名 `提示` + 窗口 displayName 含 `提示` | GET | 2 个汉字的词命中 |
| NC-C19 ※ | 同上 | 项目名 `学习计划`（连续汉字），displayName 含 `学习` 不含 `学习计划` | GET | 按 G-12：连续汉字为一个词，整词子串匹配 → 不出现 |
| NC-C20 | 同上 | 给 `taskId`：任务名不命中、项目名命中 | GET | 命中（两者都用）；给 `projectId` 只用项目名 |
| NC-C21 ※ | OB·五 | pending、legible、但 agent / ai / name 全不成立的块 | GET | 不出现（G-12） |
| NC-C22 | OB·五 | 120 个命中块 | GET | `len(items)==100`，按 seconds 降序，`totalSeconds` = 这 100 条合计 |
| NC-C23 | OB·五 | 无命中 | GET | `items:[]`，`totalSeconds:0` |
| NC-C24 | OB 头 | `tiers:false` | GET | 200，`items:[]` |
| NC-C25 | OB·五 | 设备令牌；MCP | GET | 设备令牌 `403`；MCP 200 |
| NC-C26 ※ | OB·五 | 目标是项目的「未分类」任务 | GET | 200，按 G-12 可当目标 |

**F. 一次性补记 claim（`test_blocks_claim.py`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| NC-K01 | OB·五 | 块在 3 天里各 pending；`tiersSince` 更早 | `claim {projectId, blockKeys:[k]}` | 200；`claimed:[{blockKey,days:3,seconds}]`；`totalSeconds` = 三天秒数和；`skipped:[]` |
| NC-K02 | OB·五、v2.38「四」 | 同上，跑 `reconcile.run_round` | 读台账 | 每天一条 `session.completed`，`source:"activity-block"`，`ai.confirmed:true`，`via` 为 `human`，subject 为该项目的「未分类」桶 |
| NC-K03 | OB·五 | 同上，给 `taskId` | claim | 台账 subject = 该任务 |
| NC-K04 | OB·五 `remember` | 缺省 | claim | 写一条第二级规则（块名 → 目标，`via:"assign"`） |
| NC-K05 | OB·五 | 已有同块名规则指向别处 | claim | 规则被替换，规则总数不变 |
| NC-K06 | OB·五 | `remember:false` | claim | 不写规则 |
| NC-K07 | OB·五 | 块在某天已 `assigned` | claim | 该块 `skipped:[{reason:"notPending"}]`（无可记的天） |
| NC-K08 | OB·五 | 块的所有天已过留存期（钟拨远） | claim | `skipped reason:"expired"` |
| NC-K09 | OB·五 | 块只在 `tiersSince` 之前的天里 | claim | `skipped reason:"beforeTiers"`，不入账 |
| NC-K10 | OB·五 | 块名不存在 | claim | `skipped reason:"notFound"` |
| NC-K11 ※ | OB·五 | 同块 5 天：2 天已过期、1 天 assigned、2 天 pending | claim | 只记 2 天；该块不进 `skipped`（G-13：skipped 只收一天都没记成的块） |
| NC-K12 | OB·五（409 重试） | 用 monkeypatch 让第 1 次写某天时 `seq` 被改走 | claim | 重读重试成功，该天 `claimed` |
| NC-K13 | OB·五 | 某天持续 409 满 3 次 | claim | 该天算 `notPending`；其余天照记 |
| NC-K14 | OB·五 | 规则已 1999 条，claim 两个新块名 `remember:true` | claim | `422`；两个块都没改；无 asked；无规则 |
| NC-K15 | OB·五 | 规则 2000 条，claim 的块名已有规则（替换） | claim | 200（替换不增加条数） |
| NC-K16 | OB·五 | — | 缺目标 / 同时给 taskId+projectId / `blockKeys:[]` / 101 个 / 非数组 / 含非字符串 | `422` |
| NC-K17 | OB·五 | — | 目标不存在 | `404` |
| NC-K18 | OB·五（只许人） | 设备令牌；MCP | claim | `403` |
| NC-K19 | OB·五 / OB·三 | 两个块 claim 成功 | 读库 | 每个块名一条 asked，`answer:"assigned"` |
| NC-K20 ※ | OB·五 / OB·三 | claim 里一个块被 skipped | 读库 | skipped 的块不写 asked（G-13） |
| NC-K21 | OB·五 | `totalSeconds` | 对比 candidates | claim 的 `totalSeconds` 等于同一目标 candidates 里这些块的合计 |
| NC-K22 | OB·五 | 36 天前有 pending 日子（假设记录仍在，用关闭 TTL 清理造） | claim | 只动过去 35 天里的日子 |
| NC-K23 | OB·六 | chrome 块 `figma.com · x`，claim `remember:true` | claim | `browserDomains` 多了 `figma.com`；再 claim 同域不重复 |
| NC-K24 | OB·六 | `browserDomains` 已 200 条 | claim 浏览器块 | 200，不加，不报错 |
| NC-K25 | OB·六 | 浏览器块 | `blocks/assign`、`blocks/split` | 同样自动加域名 |
| NC-K26 | OB·六 | 标题认不出域名的浏览器块（无痕） | claim | 不加任何域名 |
| NC-K27 ※ | OB 头 | `tiers:false` | claim | 按 G-16：`409`，`detail` 同 v2.38「六」风格 |

**G. 设置新键、新手引导、新装缺省（`test_onboarding_settings.py`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| NC-S01 | OB·四 | 已存旧设置文档（无新键） | GET settings | 补缺省：`liveAskMinutes:5, liveAskEnabled:true, browserDomains:[], onboardingDoneAt:null` |
| NC-S02 | OB·四 | — | PUT `liveAskMinutes` = 0 / 61 / `"5"` / 5.5 / true | 全 `422` |
| NC-S03 | OB·四 | — | PUT `liveAskMinutes` = 1 / 60 | 200，读回一致 |
| NC-S04 | OB·四 | — | PUT `liveAskEnabled` = `"yes"` / 1 | `422` |
| NC-S05 | OB·四 | — | PUT `browserDomains` 201 条 / 非数组 / 含非字符串 | `422` |
| NC-S06 | OB·六 | — | PUT `browserDomains` 200 条 | 200 |
| NC-S07 ※ | OB·六 | — | PUT 条目 `https://a.com`、`a.com/x`、`a.com:80`、空串、254 字符 | `422`（G-08） |
| NC-S08 ※ | OB·六 | — | PUT `["A.COM","a.com","*.b.com"]` | 读回 `["a.com","*.b.com"]`（转小写、去重、保序） |
| NC-S09 | OB·四 | 设置里 `liveAskMinutes:10` | PUT 不带该键 | 保留 10 |
| NC-S10 | OB·四 | — | PUT 带 `onboardingDoneAt:"2026-01-01T00:00:00Z"` | 200，读回仍 `null`（忽略） |
| NC-S11 ※ | OB·四 | — | PUT `onboardingDoneAt:"x"` | 忽略，不 422（G-17） |
| NC-S12 | v2.38「六」 | — | PUT 缺 `If-Match` / 版本不符 | `428` / `412`（回归，新键不改） |
| NC-S13 | OB·七 | 人 | `POST onboarding/done`（无体） | 200；体为设置文档，`onboardingDoneAt` ≈ 此刻（带偏移的 ISO） |
| NC-S14 | OB·七（已有就不变） | 上例之后 | 再 POST | `onboardingDoneAt` 不变 |
| NC-S15 | OB·七 | 设备令牌；MCP | POST done | `403` |
| NC-S16 | OB·七 | — | done 前后其他设置键 | 不变 |
| NC-S17 | OB·一（改动入口） | `browserDomains` 空，浏览器块 `legible:false` | PUT 加该域 | 再读同一块 `legible:true` |
| NC-N01 | OB·七 新装缺省 | 全新租户：无设置文档、`detector.rules` 版本 0 | GET settings | `tiers:true`，`tiersSince` ≈ 此刻，`agentDirect:{enabled:true,windowsClassify:true,honeycombRecords:false}` |
| NC-N02 | OB·七 | 上例之后 | 再 GET | 文档已存：`tiersSince` 不变、不再创建 |
| NC-N03 | OB·七 | 已存过设置文档（哪怕 `tiers:false`） | GET | 原样，不改 |
| NC-N04 | OB·七 | `detector.rules` 版本 ≥ 1，无设置文档 | GET | 旧缺省（`tiers:false`，`agentDirect` 全 false），**不**建文档 |
| NC-N05 | OB·七「第一次读」 | 全新租户，设备令牌（read）先读 | GET | 同样建好；与人读到同一份 |
| NC-N06 | OB·七 | 全新租户并发 10 个 GET | 并发 | 只建一份，所有响应 `tiersSince` 相同 |
| NC-N07 | OB·七 + v2.38「六」 | 新装后 | 人 `PUT` 带创建后的 ETag；`POST suggestions/{id}/confirm` | PUT 用读到的版本号成功；confirm `409`（三级分类已开） |
| NC-N08 ※ | OB·七「只对全新账号」 | 无设置文档、无规则，但库里已有该租户的 `events`（老用户） | GET | 按 G-18：不改，`tiers:false` |
| NC-N09 | OB·七 + 题目要求 | 创建后 | 读 `tiers` 开后的行为：上传原始记录 | `POST records` 照收；对账会入账 `tiersSince` 之后的记录 |

**H. 代理去向（`test_agent_place.py`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| NC-P01 | HS·三 | 运行 `garden` 无目标；项目 P | `POST agents/{runId}/place {projectId, remember:true}` | 200 `{runId, taskId:null, projectId, rule:{agent:"garden", projectId}}` |
| NC-P02 | HS·三 | 同上，给 `taskId` | place | `taskId` 有值；规则带 `taskId`；`projectId` 回该任务的项目 ※（G-20） |
| NC-P03 | HS·三 | 缺省 `remember` | place | 等于 `true` |
| NC-P04 | HS·三 | `remember:false` | place | `rule:null`；表里无该行 |
| NC-P05 | HS·三 | 同 agent 已有规则，换目标再 place | place | 规则被替换，`(user,agent)` 只有一行 |
| NC-P06 | HS·三 | — | 不给目标 / 两个都给 / `remember:"yes"` | `422` |
| NC-P07 | HS·三 | runId 不存在 | place | `404` |
| NC-P08 | HS·三 | 目标不存在 | place | `404`；运行不变、无规则 |
| NC-P09 | HS·三（只许人） | 设备令牌（Bearer）；MCP | place | `403`（设备令牌必须是 403） |
| NC-P10 | HS·三 读投影 | 运行已有 `working` 1800 秒（今天 10:00–10:30），之后才 place 到项目 P | `views/lanes`、`views/agent-time`、按天统计 | 整条 1800 秒都算 P（从开始算，不是只算 place 之后） |
| NC-P11 | HS·三 | 之后再 place 到项目 Q | 同上 | 秒数改记 Q，P 归零 |
| NC-P12 | HS·三（事实只追加） | place 之后 | 投影重建（既有 rebuild 入口） | 重建后结果与重建前一致 |
| NC-P13 ※ | HS·三 | 已结束的运行 | place | 200，投影改（G-20） |
| NC-P14 | HS·三 上限 | 已有 500 条规则 | 新 agent place `remember:true` | `422`；运行没改挂；规则数仍 500 |
| NC-P15 | HS·三 上限 | 已有 500 条，place 的 agent 已在其中 | place | 200（替换） |
| NC-P16 | HS·三 | 已有 500 条 | place `remember:false` | 200（不写规则不受上限管） |
| NC-P17 | HS·三 查表 | 规则 `garden → P` | `agents/start {agent:"garden"}`（无 taskId/projectId） | 运行 `projectId=P` |
| NC-P18 | HS·三 | 规则 `garden → 任务 t` | start 同上 | 运行 `taskId=t` |
| NC-P19 | HS·三 | 规则 `garden → P`，start 给 `taskId` / 给 `projectId=Q` | start | 以给的为准 |
| NC-P20 | HS·三（完全相同） | 规则 `garden → P`；start `agent:"Garden"` / `"garden-2"` / `"gard"` | start | 都不挂（大小写、前缀均不匹配） |
| NC-P21 | HS·三 | 无规则 | start | 与旧行为一致 |
| NC-P22 ※ | HS·三 | 规则的目标项目已被删 | start | 按 G-20：当没规则处理，运行不挂目标，不报错 |
| NC-P23 | HS·三 | 两条规则 | `GET agents/place-rules`（人、MCP） | 200，列出两条；设备令牌 `403` ※（G-20 定形状） |
| NC-P24 | HS·三 | 一条规则，已有运行挂着 | `DELETE place-rules/{agent}`（人） | 200/204，规则没了；已挂的运行不变 |
| NC-P25 ※ | HS·三 | 规则不存在 | DELETE | `404`（同 v2.38 block-rules 删除） |
| NC-P26 | HS·三（只许人） | 设备令牌；MCP | DELETE | `403` |
| NC-P27 | HS·三 进导出 / 恢复 | 三条规则 | export → 清库 → restore | 三条一模一样；租户隔离：另一租户读不到 |
| NC-P28 ※ | HS·三 | agent 名含空格、`/`、中文 | DELETE（URL 编码） | 按 G-20 写明的编码能删到 |

### 1.3 实现顺序

先写（红）：NC-N01–N06 与 NC-S01–S16（设置是所有后续的地基）→ NC-D01–D20 + NC-R01–R04（显示名 + folder）→ NC-L / NC-A（实时询问）→ NC-C → NC-K。`test_agent_place.py` 与前面互不依赖。

可并行的子任务（文件互不冲突；共用 `_onboarding_common.py` 与登记表的改动由子任务 1 先落）：

| 子任务 | 内容 | 估算行数（含测试） |
|---|---|---|
| NB-1 | 设置新键 + browserDomains 校验 + `onboarding/done` + 新装缺省 + 登记表（`conftest`、`test_export`、导出登记）+ 改 `test_defaults_and_readers` | 实现 ~200 + 测试 ~280 |
| NB-2 | 显示名函数（一处）+ `GET blocks` 带 `displayName/legible` + 服务端 browserApps / terminalApps 名单 | 实现 ~160 + 测试 ~220 |
| NB-3 | 记录 `folder`（入库校验）+ 终端连代理运行（`agentRunId`、`keySource:"agent"`） | 实现 ~120 + 测试 ~200 |
| NB-4 | `activity_asked` 集合 + assign/split/ignore/claim 钩子 + live-ask + live-ask/answer（依赖 NB-1、NB-2） | 实现 ~260 + 测试 ~330 |
| NB-5 | candidates + claim（依赖 NB-2、NB-4 的 asked） | 实现 ~300 + 测试 ~380 |
| NB-6 | `agent_place_rules` + place + place-rules + `agents/start` 查表 + 投影重算（独立） | 实现 ~220 + 测试 ~300 |

NB-6 与 NB-1…5 可同时开；NB-2、NB-3 可同时开；NB-4 在 NB-1/2 之后，NB-5 最后。

---

## 二、hive（页面）

### 2.1 放哪、怎么跑

- 纯逻辑（Node，内置 assert，无 npm）：`modules/hive/code/frontend/`，新文件 `hex-stage.js`（蜂巢新布局的选择与计算）、`hex-pet-pick.js`（宠物挑选与 +N）、`hex-claim-card.js`（补记卡）、`hex-onboard.js`（引导状态机）；各配 `*.test.js`，加进 `run_tests.sh`。运行：`modules/hive/code/frontend/run_tests.sh`（单个：`cd modules/hive/code/frontend && node hex-stage.test.js`）。
- 要看 DOM / 指针拖动：`modules/hive/code/frontend/tests/`，新文件 `test_hive_stage.py`、`test_hive_onboarding.py`、`test_hive_claim_card.py`（沿用 `test_hive_focus.py` 的 `base` / `browser` 夹具、`page.route()` 桩接口、假时钟；`Hive` 助手类）。运行：`cd modules/hive/code/frontend/tests && python -m pytest -q -rs`（CI 在装了 playwright 的 job 里同路径跑）。
- 路由：`/timer` 跳转与 `/hive/mini`、`/hive/pets/<种类>/` 静态路径归 nginx-docker：`nginx-docker/tests/` 里加 `test_hive_routes.py`（同该目录现有风格，用 curl / 请求库打网关配置）。

### 2.2 用例

**A. 布局、中心环、时钟、桶（node：`hex-stage.test.js`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| HV-S01 | HS·二·1 | `attend` 里人此刻在看运行 r2 | `pickRing(runs, attend, now)` | 取 r2 |
| HV-S02 | HS·二·1 | 人没在看任何代理，今天最后看过 r1（`until` 最大） | 同上 | 取 r1 |
| HV-S03 | HS·二·1 | 今天没看过 | 同上 | 空环（`null`） |
| HV-S04 | HS·二·1 | 看 r1 → 换看 r2 | 状态推进 | 环换成 r2 |
| HV-S05 | HS·二·2 | `focus` 有 projectId=p1 | `pickBubbleHost(focus)` | `{project:"p1"}` |
| HV-S06 | HS·二·2 | `focus.state:"afk"`；`focus:null`；projectId 为 null | 同上 | 回时钟（`{clock:true}`） |
| HV-S07 | HS·一 桶 | 今天未分类 3720 秒 | `bucketText` | `未分类 · 今天 1 小时 2 分` |
| HV-S08 ※ | HS·一 桶 | 0 秒；59 秒；3600 秒 | 同上 | `未分类 · 今天 0 分`；同左；`1 小时 0 分`（G-24 定写法） |
| HV-S09 | HS·二·5 | 块：两个 `legible:true`（600 秒、1200 秒），三个 `legible:false` 分属 browser / terminal / other ※ | `drawerRows` | 可拖行按秒降序（1200 在前）；灰行按类合成三行，名称 `浏览器（其他网站）`/`终端（未认出）`/`其他`，`draggable:false` |
| HV-S10 | HS·二·5 | 只有一类看不懂 | 同上 | 只出一行灰行 |
| HV-S11 | HS·二·5 | 拖块放到格子 p1，块 `{date,blockKey,seq}` | `dropBlock` | 请求体恰为 `{date, blockKey, ifSeq:seq, projectId:"p1", remember:true}`，打 `POST activity/blocks/assign` |
| HV-S12 | HS·二·5 | 成功 | 状态 | 块从抽屉消失；桶秒数减去该块秒数；提示 `已记下：以后 <displayName> 都算 <项目名>` |
| HV-S13 | HS·二·6 | 409 / 422 / 网络失败 | 状态 | 被拖物回原位；提示里含原因；抽屉与桶不变 |
| HV-S14 | HS·二·4 | 宠物拖到格子 p1 | `dropPet` | 打 `POST agents/{runId}/place`，体恰为 `{projectId:"p1", remember:true}` |
| HV-S15 | HS·二·4 | place 成功，规则 `{agent:"garden"}` | 状态 | 提示 `已记下：以后 garden 都算 <项目名>`；3000ms 后提示消失（假时钟：2999 在、3000 灭）；宠物移到该格子边 |
| HV-S16 | HS·二·4 | `remember`/规则为 `null` 的响应 | 状态 | 不显示「以后…都算」那句 ※（G-20） |
| HV-S17 | HS·二·4、6 | 放到空白处 | `drop(null)` | 不发任何请求，被拖物回原位 |
| HV-S18 | HS·二·6 | place 返回 404 / 422 / 网络失败 | 状态 | 宠物回原位并提示原因 |
| HV-S19 | HS·一 | 视口 1280 宽 / 1279 宽 ※ | 布局选择 | 1280 为新布局；1279 的行为见 G-24 |
| HV-S20 | HS·一 | 格子文字 | 渲染模型 | 每个格子只含项目名（无任务名、无时长、无红点） |

**B. 干活的宠物与左上角宠物（node：`hex-pet-pick.test.js`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| HV-P01 | HS·二·3 | 3 条挂了项目的运行 | `pickWorkers` | 3 只，`plus:0` |
| HV-P02 | HS·二·3 | 恰好 4 条 | 同上 | 4 只，`plus:0`（无 `+N`） |
| HV-P03 | HS·二·3 | 5 条 | 同上 | 4 只，`plus:1`，显示 `+1` |
| HV-P04 | HS·二·3 | 9 条 | 同上 | 4 只，`plus:5` |
| HV-P05 | HS·二·3 | 6 条，最近活动时间各不同 | 同上 | 取最近活动的 4 条；被去掉的是最久没动的两条（字段见 G-21） |
| HV-P06 | HS·二·3 | 最近活动时间并列 | 同上 | 并列时按 runId 升序，结果稳定 ※ |
| HV-P07 | HS·二·3 | 0 条 | 同上 | 空，`plus:0` |
| HV-P08 | HS·二·3 | 运行挂 `taskId`（任务在项目 p1 下） | 定位 | 宠物停在 p1 格子边；挂的是项目「未分类」任务同样停在该项目格子 |
| HV-P09 | HS·二·3 | 运行 phase `working` / `waiting_input` / `waiting_permission` / `idle` | `moodOf` | `working` / `waiting` / `waiting` / `idle`；其余 phase（如 `error`）见 G-21 ※ |
| HV-P10 | HS·二·3、DP·三 | 运行 `tool:"claude-code"`，`packsByTool:{"claude-code":"x","*":"dsh"}` | `packOf` | `x` |
| HV-P11 | DP·三 | `tool:"codex"` | 同上 | `*` → `dsh` |
| HV-P12 | HS·二·3 | `packsByTool` 没有 `*`；缺省包 | 同上 | `dsh`（缺省，见 G-21） |
| HV-P13 | HS·二·3 | 宠物渲染 | 模型 | 只含名牌：`label` 优先，没有用 `agent`；不含明细 |
| HV-P14 | HS·二·4 | 3 条运行：无目标 / 只有 projectId / 只有 taskId | `pickIdle` | 只有无目标那条在左上角；其余在格子边 |
| HV-P15 | HS·二·4 | 已结束的运行（今天跑过） | 同上 | 不出现（只算在跑，G-21） |
| HV-P16 | HS·二·4 | 6 条无目标运行 | 同上 | 每条一只（左上角不受 4 只上限管；G-21 ※） |
| HV-P17 | HS·二·4 | 随机走动：注入固定种子与时钟，走 600 步 | `stepWander` | 位置始终在左上角矩形内 |
| HV-P18 | HS·二·4 | 宠物成功 place 后 | 状态 | 该运行不再属左上角（下次拉取已有目标） |

**C. 浏览器里的行为（playwright：`test_hive_stage.py`）。** 接口桩：`views/tree`、`views/current`、`views/lanes`、`activity/blocks`、`agents/*`。

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| HV-B01 | HS·一 | 1440 宽，桩数据齐全 | 打开蜂巢页 | 中心有环（代理名 + 已用时间）；右上角有时钟（今天时长 + 项目名）；左下角有桶 |
| HV-B02 | HS·二·6 | 一只左上角宠物 | 用指针事件按住、移动、放到格子 p1 | 拖动中目标格子带高亮类；松手发 place；请求只有一次 |
| HV-B03 | HS·二·6 | 同上 | 放到空白处 | 无请求，宠物回到原位（位置与按下前相同） |
| HV-B04 | HS·二·6 | 桶抽屉里一个可拖块 | 拖到格子 | 发 assign（体见 HV-S11）；成功后块消失、桶数字减少 |
| HV-B05 | HS·二·5 | 抽屉里的灰行 | 尝试拖 | 不进入拖动状态，无请求 |
| HV-B06 | HS·二·2 | `focus` 项目由 p1 变 p2 | 推假时钟 | 气泡在 600ms 内（假时钟 ≤600）移到 p2 格子旁 |
| HV-B07 | HS·二·2 | `focus.state` 变 `afk` | 推时钟 | 气泡回时钟 |
| HV-B08 | HS·二·1 | 人换看另一条运行 | 推假时钟 | 环的内容在 600ms 内换成那条运行 |
| HV-B09 | HS·二·2 | 无手动计时；把时钟拖到格子 p1 | 拖 | 发「开始手动计时」请求（端点见 G-23），目标 p1；再点时钟发停止 |
| HV-B10 | HS·一 | 访问 `/timer` | 请求 | 跳转到蜂巢页（nginx 用例 HV-N01） |
| HV-B11 | HS·一 | — | 决策栏 | 仍在页面上，只含 AI 提议；没有「待分类窗口块」一节 |
| HV-B12 | OB·八·2 | 未分类桶有时间 | 看页面 | 桶无红点；决策栏 / 顶栏的待办数不含它 |
| HV-B13 | HS·四 | 点格子 p1 | 看面板 | 左半边：「计时」与「对话」各一条时间线 + 合计；右半边：每条运行一行（名字、working 秒数、当前状态），底部「token 花费」显示 `—`；有关闭按钮，点后消失 |
| HV-B14 | HS·四 | 整个过程 | 拦截请求 | 只请求 `views/lanes`、`views/agent-time`、`activity/ledger`，没有任何新读端 |
| HV-B15 | HS·四·1 | — | 页面代码 | 不调用 `agents/*/usage`（本版只定形状，不实现） |
| HV-B16 | HS·二·4 | 点宠物名牌 | — | 只显示宠物与名牌，明细要点格子后看（页面上无明细文字） |

**D. 新手引导（`hex-onboard.test.js` + `test_hive_onboarding.py`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| HV-O01 | OB·七 | `onboardingDoneAt:null`、树里 0 个分区 | `shouldShowOnboarding` | `true` |
| HV-O02 | OB·七 | `onboardingDoneAt:null`、已有 1 个分区（老账号） | 同上 | `false`（永远不自动出） |
| HV-O03 | OB·七 | `onboardingDoneAt` 非空、0 分区 | 同上 | `false` |
| HV-O04 | OB·七 | 设置读取失败 / 树读取失败 | 同上 | `false`（不乱出） ※ |
| HV-O05 | OB·七·1 | 引导第 1 步 | 页面 | 可建 1–2 个分区；第 3 个被拒（按钮禁用或提示）※（G-19） |
| HV-O06 | OB·七·2 | 2 步 | 页面 | 每个分区 1–2 个项目，每个项目至少一个任务；任务数为 0 时不能进下一步 |
| HV-O07 | OB·七·3 | 新建任务 t1 / 项目 p1 | 页面调 candidates | 对任务及其项目都调（taskId 与 projectId 各一次） |
| HV-O08 | OB·七·3 | 候选：`match:"agent"` 600 秒、`match:"name"` 600 秒 | `pickAutoClaims` | 两个都选中（恰好 600 满足 ≥ 600） |
| HV-O09 | OB·七·3 | 候选：`name` 599 秒；`ai` 5000 秒；`agent` 599 秒 | 同上 | 都不选 |
| HV-O10 | OB·七·3 | 同一块在任务与项目两次候选里都出现 | 同上 | 只 claim 一次（去重） |
| HV-O11 | OB·七·3 | 选中 2 块 | 页面 | 发 `blocks/claim`，体 `{taskId或projectId, blockKeys:[…], remember:true}` |
| HV-O12 | OB·七·3 | claim 返回 `claimed` 2 条（1800 秒、600 秒） | 结果页 | 列出 2 块（`displayName`，不是原始块名）+ 合计 `totalSeconds`；每块一个「撤销」 |
| HV-O13 | OB·七·3 | 点某块「撤销」 | 页面 | 对该块名各天 `blocks/reset`（带该天最新 `ifSeq`）+ 删该条第二级规则 + 删该块 `activity_asked`（后一项无端点，见 G-15 ※） |
| HV-O14 | OB·七·4 | 引导走完 | 页面 | 其余块不动（无多余请求）；发 `POST onboarding/done` |
| HV-O15 | OB·七 | 点「跳过」 | 页面 | 也发 `POST onboarding/done`，不再出引导（刷新后 `shouldShow` 为 false） |
| HV-O16 | OB·七·3 | 接了 AI CLI 的用户 | 页面 | 另有按 v2.38「五」带 `directApply` 放的入口；`confidencePercent` < 阈值的不放 ※（阈值取自 settings） |

**E. 新建后补记卡与显示名（`hex-claim-card.test.js` + `test_hive_claim_card.py`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| HV-C01 | OB·八·3 | 新建任务成功 | 页面 | 调 candidates?taskId= |
| HV-C02 | OB·八·3 | 新建项目成功 | 页面 | 调 candidates?projectId= |
| HV-C03 | OB·八·3 | `items:[]` | 页面 | 不弹卡 |
| HV-C04 | OB·八·3 | items 3 条，合计 9000 秒 | 页面 | 弹卡：`找到 3 个窗口块，共 2.5 小时，可能属于它`（小时数写法见 G-24） |
| HV-C05 | OB·八·3 | items：`agent`、`name`、`ai` 各一 | 页面 | 默认勾选 `agent` 与 `name`，`ai` 不勾 |
| HV-C06 | OB·八·3 | 取消勾选一项后点「记进去」 | 页面 | claim 的 `blockKeys` 只含勾选的；`remember:true` |
| HV-C07 | OB·八·3 | claim 返回 `totalSeconds:7200` | 页面 | 提示 `已记进 2 小时` |
| HV-C08 | OB·八·3 | claim 失败（422 / 网络） | 页面 | 提示原因，卡不关 ※ |
| HV-C09 | OB·八·3 | 全不勾 | 页面 | 「记进去」禁用 |
| HV-C10 | OB·八·4 | 卡片、抽屉、结果页 | 渲染 | 全部只出现 `displayName`；任何位置都找不到原始块名（用含 `ptyxis ·` 的块名断言页面 DOM 不含） |
| HV-C11 | OB·八·1 | 决策栏 | 渲染 | 无「待分类窗口块」一节 |
| HV-C12 | HS·二·5 | 抽屉 | 渲染 | 同样只显示 `displayName` |

**F. 路由（`nginx-docker/tests/test_hive_routes.py`）**

| 编号 | 契约条款 | 操作 | 期望 |
|---|---|---|---|
| HV-N01 | HS·一 | GET `/timer` | `301`/`302` 到蜂巢页（具体码与目标见 G-24） |
| HV-N02 | DP·四 | GET `/hive/mini`（未登录） | 返回页面（登录提示由页面脚本出） |
| HV-N03 | DP·三 | GET `/hive/pets/dsh/<配置文件>` | 200，内容与宠物包一致；不存在的种类 404 |

### 2.3 实现顺序

先写：HV-S（纯逻辑，最稳）→ HV-P → HV-O / HV-C（node 部分）→ 再写 playwright 部分（HV-B、HV-O、HV-C 的浏览器用例）。

| 子任务 | 内容 | 估算行数 |
|---|---|---|
| HB-1 | `hex-pet-pick.js`（挑 4 只 + `+N`、包对照、mood 映射、左上角筛选、走动）+ 测试 | 实现 ~140 + 测试 ~160 |
| HB-2 | `hex-stage.js`（环、气泡、桶文案、抽屉行、drop 请求组装）+ 测试 | 实现 ~220 + 测试 ~220 |
| HB-3 | 蜂巢页新布局接线（`hex-app.js` 改动、`/timer` 并入、拖动 Pointer Events）+ `test_hive_stage.py`；依赖 HB-1、HB-2 | 实现 ~400 + 测试 ~300 |
| HB-4 | `hex-onboard.js` + 引导页 + `test_hive_onboarding.py`；依赖 NB-5 的 claim 契约（可先用桩） | 实现 ~260 + 测试 ~240 |
| HB-5 | `hex-claim-card.js` + 补记卡 + 决策栏去掉待分类块 + `test_hive_claim_card.py` | 实现 ~160 + 测试 ~180 |
| HB-6 | 完成记录面板（HV-B13–B16）；依赖 HB-3 | 实现 ~180 + 测试 ~120 |
| HB-7 | nginx 路由 + `test_hive_routes.py` | 实现 ~20 + 测试 ~60 |

HB-1、HB-2、HB-4、HB-5、HB-7 互不冲突可同时开；HB-3 要等 HB-1/2；HB-6 最后。

---

## 三、ai-detector（桥接）

### 3.1 放哪、怎么跑

- 目录 `modules/ai-detector/`（Go，`package main`，与现有 `*_test.go` 同包）；运行：`cd modules/ai-detector && go vet ./... && go test -count=1 ./...`（CI 同款）；单个：`go test -run TestFolder ./...`。
- 新文件：`folder_test.go`（`folder` 提取，写法同 `redact_test.go` 的表驱动）、`pet_test.go`（事件产生）、`petsink_test.go`（dsh-pet / http 两种美术实现）。实现文件对应 `folder.go`、`pet.go`、`petsink.go`。
- 假服务：沿用 `live_test.go` 的 `httptest.NewServer` 写法，建三个假服务：假 HoneyComb（`views/current`、`records` 记录收到的头与体）、假 dsh-pet（记录每个请求的路径、方法、体）、假 http 美术（可返回 2xx / 500 / 302）。时钟用现有测试里注入的 `now`。

### 3.2 用例

**A. `folder`（`folder_test.go`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| AD-F01 | OB·二·2 | 终端 `ptyxis`，脱敏前标题 `~/garden` | 记录生成 | `folder:"garden"` |
| AD-F02 | OB·二·2 | `/home/xia/garden` | 同上 | `garden` |
| AD-F03 | OB·二·2 | `C:\Users\张三\proj` | 同上 | `proj` |
| AD-F04 | OB·二·2 | 标题 `~` | 同上 | `folder:"~"` |
| AD-F05 | OB·二·2 | `/home/xia/garden/`（尾斜杠） | 同上 | `garden` ※ |
| AD-F06 | OB·二·2 | 目录名恰好 64 字符 | 同上 | 原样 |
| AD-F07 ※ | OB·二·2 | 目录名 65 字符 | 同上 | 按 G-10：截到 64 个字符（不丢弃） |
| AD-F08 | OB·二·2 | 标题不含路径（`Coder`） | 同上 | 不带 `folder` 键（JSON 里无该键） |
| AD-F09 | OB·二·2 | 编辑器 `code`、浏览器，标题含路径 | 同上 | 不带 `folder`（程序不在终端名单） |
| AD-F10 ※ | OB·二·2 | 终端标题 `user@host: ~/garden`（bash 常见形状） | 同上 | `garden`（G-10：路径形状须覆盖它） |
| AD-F11 ※ | OB·二·2 | 标题含两条路径 | 同上 | 取第一条（G-10） |
| AD-F12 | OB·二·2 | `defaultTabApps` 里的每个程序名各一 | 同上 | 都按终端处理 |
| AD-F13 | OB·二·2 隐私 | `paths:"full"`，标题含路径 | 记录 + 脱敏标题 | `folder` 与脱敏后标题里的文件名一致；`folder` 不含上一级目录 |
| AD-F14 ※ | OB·二·2 隐私 | `paths:"half"` / `"off"` | 同上 | 按 G-10：`half` 照发；`off` 不发 |
| AD-F15 ※ | OB·二·2 隐私 | `titles:"drop"`、`"pseudonymize"` | 同上 | 按 G-10：都不发 `folder` |
| AD-F16 | OB·二·2（追加式） | 记录 JSON | 序列化 | 既有键与形状不变（沿用 `records_test.go` 的形状断言），仅多一个可缺的 `folder` |
| AD-F17 | v2.38 回归 | 带 `folder` 的记录批量上传 | 上传 | 每批 ≤ 500，游标与重试语义不变（沿用 `records_test.go` 同类用例，加 `folder`） |
| AD-F18 | OB·二·2 | 标题含路径的终端记录 | 脱敏 | 标题仍按原脱敏表处理（路径被替换），`folder` 是脱敏前提取的 |

**B. 桌宠事件（`pet_test.go`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| AD-E01 | DP·二 | 配置无 `pet` 节 | 跑 2 轮 | 对假 HoneyComb 与假美术均零请求 |
| AD-E02 | DP·二 | `pet.enabled:false` | 同上 | 零请求 |
| AD-E03 | DP·二 | `enabled:true` | 推时钟 | 每 10 秒 GET 一次 `views/current`；带本机设备令牌 `Authorization: Bearer`；不用管理员头 |
| AD-E04 | DP·一 `focus` | focus 任务 `写提示词`、项目 `garden` | 轮询 | 发 `focus`，`text:"在写提示词（garden）"`，`mood:"working"`，`data:{taskName,projectName,displayName}` |
| AD-E05 | DP·一 `focus` | 只有项目，认不出任务 | 同上 | `text:"在 garden"` |
| AD-E06 | DP·一 `focus` | 只有 `displayName`（如 `github.com`） | 同上 | `text:"在看 github.com"`（来源字段见 G-04） |
| AD-E07 | DP·一 `focus` | 三者均认不出 | 同上 | 不发（无话可说）※ |
| AD-E08 | DP·一 `focus` | `state:"afk"` | 同上 | `focus`，`data:{afk:true}`，`text:"休息一下"`，`mood:"idle"` |
| AD-E09 | DP·一 `focus` | afk 后回到电脑前，目标与 afk 前相同 | 同上 | 仍发 `focus`（「从离开回到电脑前」） |
| AD-E10 | DP·一 防抖 | 两次目标变化相隔 59 秒 / 60 秒 | 同上 | 59 秒的那条不发；60 秒的发（G-27：被压掉的不补发） |
| AD-E11 | DP·一 | 同一 `type`、同样 `data` 连续出现 | 同上 | 只发一次 |
| AD-E12 | DP·一 `agentWaiting` | 运行 `garden` 变 `waiting_input` | 同上 | `data:{agent:"garden",reason:"input"}`，`text:"garden 在等你回话"`，`mood:"waiting"` |
| AD-E13 | DP·一 | `waiting_permission` | 同上 | `reason:"permission"`，`text:"garden 在等你批权限"` |
| AD-E14 ※ | DP·一 | 启动后第一次轮询就有运行在等人 | 同上 | 按 G-27：发一次（以启动时状态为基线则不发，建议基线不发） |
| AD-E15 | DP·一 `agentDone` | 运行从 `agents[]` 消失 | 同上 | `agentDone`，`text:"garden 干完了"`，`mood:"happy"`（判据见 G-27） |
| AD-E16 | DP·一 `alert` | ActivityWatch 连不上 | 同上 | `alert`，`data:{kind:"aw"}`，`text` 含「ActivityWatch 没在跑」，`mood:"worried"` |
| AD-E17 | DP·一 `alert` | 上传连续失败 2 轮 / 3 轮 | 同上 | 2 轮不发；3 轮发 `kind:"upload"` |
| AD-E18 | DP·一 `alert` | 服务端连不上 | 同上 | `kind:"server"`（判据见 G-27） |
| AD-E19 | DP·一 `alertCleared` | 上一条 `alert` 的问题恢复 | 同上 | `alertCleared`，`data:{kind}` 同上一条，`text:"记录恢复了"`，`mood:"happy"` |
| AD-E20 | DP·一 | 无 alert 时恢复 | 同上 | 不发 `alertCleared` |
| AD-E21 | DP·一 | 所有事件 | 检查信封 | `v:1`，`at` 带偏移 ISO，`text` ≤ 60 字符；超长任务名时 `text` 截断（G-27） |
| AD-E22 | DP·一 不发 | `views/current` 里放原始标题 `SECRET-TITLE`、网址、路径、令牌值 | 所有 PetEvent 与所有发往美术的请求体 | 全文搜不到这些串 |
| AD-E23 | DP·一 | `displayName` | 与 `onboarding.v1`「二」同值 | 事件 `data.displayName` 等于服务端给的 `displayName`，不另算 |
| AD-E24 | DP·一 | 美术端 500 / 连不上 | 同上 | 只写本机日志，不重试（该条只请求一次），检测程序的上传 / 心跳照常 |

**C. 美术实现（`petsink_test.go`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| AD-D01 | DP·二 `url` | `renderer:"dsh-pet"`，`url` 为 `http://127.0.0.1:3080` / `http://localhost:3080` / `http://[::1]:3080` / `http://127.0.0.2:1` / `https://x.example` | 发一条 | 全部允许 |
| AD-D02 | DP·二 `url` | `http://example.com`、`http://localhost.evil.com`、`http://10.0.0.5` | 发一条 | 不发，日志写原因 |
| AD-D03 | DP·二 | 美术端回 302 到别的地址 | 发一条 | 不跟随：对跳转目标零请求 |
| AD-D04 | DP·二 令牌 | `renderer:"dsh-pet"`、`token:"T"`、HoneyComb 设备令牌 `DEV` | 全流程 | 假 dsh-pet 收到的所有请求头与体里没有 `DEV`、也没有 `Authorization` |
| AD-D05 | DP·二 令牌 | `renderer:"http"`，`token:"T"` | 发一条 | 带 `Authorization: Bearer T`；没有 `DEV`；`token:null` 时不带该头 |
| AD-D06 | DP·三 dsh-pet | `focus` 事件 | 发 | `POST /dsh-pet-7340/work-status`，体 `{state:<moods 对照>, task:<text>}`；不调 lock / broadcast |
| AD-D07 | DP·三 dsh-pet | `agentWaiting` | 发 | 顺序：`work-status` → `lock` → `broadcast`（气泡写 `text`）→ `lock/release` |
| AD-D08 | DP·三 | `agentDone`、`alert`、`alertCleared` | 发 | 各走 D07 同样的顺序 |
| AD-D09 | DP·三 | `focus` 带 `afk:true` | 发 | `DELETE /dsh-pet-7340/work-status`，不 POST |
| AD-D10 | DP·三 | `moods.working:null` | 发 `focus` | 不发 `work-status`（只换气泡 / 无动作），无报错 ※ |
| AD-D11 | DP·三 | `petId:"x"` / `null` | 发 | `x` 传给 dsh-pet 的方式见 G-27；`null` 用第一只 |
| AD-D12 | DP·三 | 假 dsh-pet 的 `broadcast` 返回 500 | 发 | 尽力放锁（收到 `lock/release`）；该条结束，不重试 |
| AD-D13 | DP·三 | dsh-pet 没在跑（连接被拒） | 发 | 按 DP·一的发失败处理：日志、不重试、不影响其他功能 |
| AD-D14 | DP·三 http | `renderer:"http"` | 发 | `POST url`，`Content-Type: application/json`，体 = PetEvent 原样 |
| AD-D15 | DP·三 http | 返回 200 / 204 / 500 | 发 | 2xx 算送达；500 只写日志不重试 |
| AD-D16 | DP·二 | `renderer` 不是 `dsh-pet` / `http` | 加载配置 | 配置校验报错（沿用 `config.go` 的 `enum` 校验），不发 |
| AD-D17 | DP·二 | `pet.moods` / `packsByTool` 配置 | 加载 | 缺省值合法；`packsByTool` 的作用见 G-27 |

### 3.3 实现顺序

先写：AD-F（最小，复用 `redact_test.go` 夹具）→ AD-D01–D05（配置与令牌防线，先把隐私钉死）→ AD-E → AD-D06 起。

| 子任务 | 内容 | 估算行数 |
|---|---|---|
| AB-1 | `folder.go` + 记录追加 `folder` + `folder_test.go`（不依赖其他任务） | 实现 ~60 + 测试 ~140 |
| AB-2 | `pet.go`：配置、10 秒轮询、事件产生、防抖、去重、alert 状态机 + `pet_test.go` | 实现 ~260 + 测试 ~320 |
| AB-3 | `petsink.go`：dsh-pet 与 http 两种发送、url 校验、令牌隔离 + `petsink_test.go`（先用 AB-2 的事件类型定义，可与 AB-2 同时开，只共享 `PetEvent` 结构体） | 实现 ~180 + 测试 ~260 |

三者文件互不冲突；AB-2、AB-3 先约定 `PetEvent` 结构体放 `pet.go`。AB-1 要等 NB-3 约定的 `folder` 字段名（已在契约里：`folder`）。

---

## 四、浮窗（`/hive/mini` 页面 + 小程序）

### 4.1 放哪、怎么跑

- `/hive/mini` 页面（hive 前端的一部分）：`modules/hive/code/frontend/hex-mini.js`（页面逻辑）、`tests/test_hive_float.py`（playwright；不用 `test_hive_minihive.py`，那是决策卡的小蜂巢）。运行：`cd modules/hive/code/frontend/tests && python -m pytest -q -rs test_hive_float.py`。纯逻辑部分 `hex-mini.test.js`，加进 `run_tests.sh`。
- 小程序（形态未定，契约「实现前定」）：测试先对**核心逻辑**写，形态无关。拟建 `modules/pet-float/float-core.js`（轮询、红点状态、位置计算，纯函数）与 `modules/pet-float/float-core.test.js`，运行 `cd modules/pet-float && node float-core.test.js`。选定 Electron / Tauri 后，壳不写自动化测试，核心逻辑测试不变。

### 4.2 用例

**A. `/hive/mini` 页面**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| FW-M01 | DP·四 | 接口桩 `live-ask` 回 `ask{displayName:"figma.com · 首页改版", focusedSeconds:372, date, seq:3, blockKey}` | 打开 `/hive/mini` | 顶上一张卡：显示 `displayName` 与已停留时长（`6 分钟`；写法见 G-24）；页面里找不到原始 `blockKey` |
| FW-M02 | DP·四 | 同上 | 看下半部 | 蜂巢缩略图只有项目名（无任务、无时长） |
| FW-M03 | DP·四 | 同上 | 把卡拖到项目格子 p1 | 发 `POST activity/blocks/assign`，体恰为 `{date, blockKey, ifSeq:3, projectId:"p1", remember:true}`；没有 `taskId` |
| FW-M04 | DP·四 | assign 返回 200 | 假时钟推进 | 红点灭的信号发出（页面向外发「完成」消息 / 事件）；窗口收起信号在 2000ms 时发出（1999ms 未发） |
| FW-M05 | DP·四 | 同 FW-M01 | 点「不管它」 | 发 `POST live-ask/answer`，体恰为 `{blockKey, answer:"unmanaged"}`；成功后同 M04 的灭红点与 2 秒收起 |
| FW-M06 | DP·四 | assign 返回 409（seq 已变） | 拖入 | 显示原因，不收起，卡回原位 ※（G-28） |
| FW-M07 | DP·四 | answer 返回 404 | 点「不管它」 | 显示原因，不收起 ※ |
| FW-M08 | DP·四 | 接口桩对没登录回 401 | 打开 | 页面显示登录入口，不显示卡与蜂巢 |
| FW-M09 | DP·四 | 任意 | 拦截请求 | 所有请求不带 `Authorization`（用人的登录会话），请求里无设备令牌 |
| FW-M10 | DP·四 | `ask:null` | 打开 | 显示空态（G-28 定写法），不显示拖拽卡 |
| FW-M11 | DP·四 | 普通浏览器直接打开（非小程序） | 打开 | 正常渲染（页面不依赖小程序注入） |
| FW-M12 | DP·四 | 拖到空白处 | 放 | 无请求，卡回原位 |

**B. 小程序核心逻辑（`float-core.test.js`）**

| 编号 | 契约条款 | 前置数据 | 操作 | 期望 |
|---|---|---|---|---|
| FW-F01 | DP·四 红点 | 假时钟 | 启动 | 每 10 秒请求一次 `GET /api/core/activity/live-ask`，带本机设备令牌；10 秒内不重复请求 |
| FW-F02 | DP·四 红点 | `ask` 非空 | 轮询 | 红点 `on` |
| FW-F03 | DP·四 红点 | `ask` 由非空变 `null` | 轮询 | 红点 `off` |
| FW-F04 ※ | DP·四 红点 | 请求失败 / 401 | 轮询 | 按 G-28：保持上一状态；连续失败 3 轮熄灭 |
| FW-F05 | DP·四 位置 | 取得桌宠窗口矩形 | `badgePos(petRect, screen)` | 贴桌宠窗口右上角 |
| FW-F06 | DP·四 位置 | 取不到桌宠位置（`null`） | 同上 | 贴屏幕右下角 |
| FW-F07 | DP·四 悬停 | 鼠标进入红点 | 事件 | 弹出窗口，地址为 `/hive/mini`；URL 不含令牌 |
| FW-F08 | DP·四 令牌 | 弹出页面 | 检查 | 设备令牌只用于红点轮询，不传给页面 |
| FW-F09 | DP·四 | `ask:null` 时悬停 | 事件 | 不弹出（红点已灭）※（G-28） |
| FW-F10 | DP·四 | 收到页面的「完成」消息 | 事件 | 红点灭，窗口 2000ms 后关闭 |
| FW-F11 | DP·四 「只做三件事」 | 模块导出 | 检查 | 没有绘制宠物、没有 `PetEvent` 相关代码 |

### 4.3 实现顺序

先写：FW-F（纯逻辑，形态无关）与 FW-M01–M05（页面）。壳（Electron / Tauri）在仓主定形态后另派，不在本文件范围。

| 子任务 | 内容 | 估算行数 |
|---|---|---|
| FB-1 | `hex-mini.js` + `/hive/mini` 页 + `hex-mini.test.js` + `test_hive_float.py`（依赖 NB-4 的 live-ask 契约，可先用桩；依赖 HB-3 的蜂巢缩略图组件，或先用 `hex-minihive.js` 的几何） | 实现 ~220 + 测试 ~220 |
| FB-2 | `modules/pet-float/float-core.js` + 测试 | 实现 ~90 + 测试 ~120 |
| FB-3 | 壳（待形态） | 待定 |

FB-1、FB-2 互不冲突。

---

## 五、用例总数

| 模块 | 数量 |
|---|---|
| nexus-core | 显示名 20 + 记录 14 + 实时询问 23 + 只问一次 17 + 候选 26 + claim 27 + 设置 17 + 新装 9 + 去向 28 = 181 |
| hive | 布局 20 + 宠物 18 + 浏览器 16 + 引导 16 + 补记卡 12 + 路由 3 = 85 |
| ai-detector | folder 18 + 事件 24 + 美术 17 = 59 |
| 浮窗 | 页面 12 + 小程序核心 11 = 23 |
| 合计 | 348 |

---

## 六、契约里写得不清、没法写测试的地方

每条：位置 / 问题 / 建议写法。按对实现影响从大到小排。

**G-01 `focus.state` 取值对不上。** OB·三·1 写 `focus.state` 是 `active`；nexus-core v2.16 里只有 `"present"` 与 `"afk"`。
建议：改成「`focus.state` 是 `present`」。

**G-02 「连续停留」的数据来源和算法没写。** OB·三·4 只说「中间离开或切走不超过 60 秒」。没定：(a) 用什么算——v2.16/2.17 的在场心跳时间线只留最近 2 小时，`liveAskMinutes` 最大 60 分钟，勉强够；(b) 离开 / 切走的那段时间算不算进 `focusedSeconds`；(c) `since` 是连续段的起点还是当前窗口的起点；(d) 连续段跨午夜时 `date` 取哪天；(e) 「同一个块」怎么在心跳里判（见 G-03）。
建议：「`focusedSeconds` = 最近一段连续停留里在该块上的秒数合计（离开或切走的时间不计）；一段连续 = 相邻两段该块时间的间隔 ≤ 60 秒；`since` = 这一段连续的第一拍时刻；`date` = 此刻的 NEXUS_TZ 本地日；时间线只看最近 2 小时。」

**G-03 live-ask 怎么得到当前窗口的 `blockKey`。** 在场心跳只有 `app/title`，没有块名；v2.38 又规定服务端上传时不匹配第一级规则（块名由检测程序算好随原始记录带来）；而原始记录晚到几十秒到分钟。live-ask 的 `blockKey` 无从得来。
建议：写明「live-ask 对当前窗口的块名：用同一设备最近 10 分钟内、`app` 与归一化标题相同的最新一条原始记录的 `blockKey`；没有这样的记录就当 `app · 归一化标题`，`keySource: "auto"`，不匹配第一级规则。」

**G-04 `focus` 里没有 `displayName`。** DP·一 的 `focus` 事件 `data.displayName` 与文案「在看 github.com」要求读 `views/current.focus.displayName`，但 v2.16 的 focus 形状没有这个键，OB 也没写要加。另外浏览器块的 `displayName` 是整条标题（OB·二·2 = 标题，如 `figma.com · 首页改版`），而文案示例是只有域名的「在看 github.com」。
建议：OB 追加「`views/current.focus` 与 `views/lanes.human.focus` 追加 `displayName`（字符串或 `null`，由同一个函数算）」；文案写成「在看 + displayName」，示例改为「在看 figma.com · 首页改版」。

**G-05 看不懂的块没有「类」。** OB·二 说 `legible:false` 的块在「未分类」区「按类合并成一行」（「浏览器（其他网站）」「终端（未认出）」「其他」），但接口里没有类这个键，页面只能自己再猜一遍，违反「服务端一处算」。
建议：每个块追加 `kind: "browser" | "terminal" | "other"`（所有块都带，和 `legible` 同一个函数算）。

**G-06 未拆的 `generic` 算不算「未分类」。** OB·一说未分类 = 生效状态 `pending`「含未拆的 `generic`」，但 v2.38 里未拆的通用窗口生效状态就叫 `generic`，不是 `pending`；候选（OB·五）又写「生效状态 `pending`」。
建议：写明「候选与 claim 只处理生效状态 `pending` 的块，未拆的 `generic` 不进候选；『未分类』区与桶的合计是 `pending` + 未拆 `generic`」，或反过来全含；二选一写死。

**G-07 终端块 `displayName` 取「代理名」还是「工作目录名」。** OB·二·3 写「运行的代理名（工作目录名）」，括号里的话不知道是同义解释还是另一个来源；`folder` 并列时的取舍也没写。
建议：「`displayName` = 运行的 `agent` 字段（与 `blockKey` 里『 · 』之后的部分相同）；`folder` 并列时取字典序最小者。」

**G-08 浏览器域名的细节。**
(a) `*.example.com` 是否含 `example.com` 本身；(b) 标题里的域名比较是否不分大小写；(c) 非法条目（带协议 / 路径 / 端口、空串、> 253）是 `422` 还是静默清掉；(d) 大写输入转小写还是拒；(e) 浏览器块 `displayName` 取原标题还是归一化后的标题；(f) 标题里没有「 · 」时 `domain` 是什么。
建议：「(a) 含根域；(b) 是；(c) `422`；(d) 转小写、去重、保持首次出现的顺序；(e) 归一化后的标题（块名里 `app · ` 之后的部分）；(f) 没有『 · 』就当认不出域名，`legible:false`。」

**G-09 服务端的浏览器名单 / 终端名单没写字面量。** OB·二说「服务端存一份同样的名单，只增」，没说匹配方式（精确 / 不分大小写）、没写初始内容，也没说检测程序用户自配的 `browserApps` 与服务端不一致时怎么办。名字还对不上：检测程序里缺省名单叫 `defaultTabApps` / `defaultBrowsers`，配置键是 `browserApps`。
建议：在 OB 加附录列出两份名单的字面量（取 `config.go` 当前内容），匹配 = 程序名不分大小写整串相等；用户自配的额外程序名服务端不认（按「其他」）；两边各一条测试读同一份附录对照（NC-D19）。

**G-10 `folder` 的边界。** 没写：(a) 路径形状具体含哪些（OB 只说「同脱敏表『本机绝对路径』那一行的形状」；bash 默认标题 `user@host: ~/garden` 是否算）；(b) 目录名 > 64 个字符丢弃还是截断；(c) 标题里有多条路径取哪条；(d) 根目录（`/`、`C:\`）；(e) `paths` 为 `half` / `off` 时、以及 `titles` 为 `drop` / `pseudonymize` 时发不发。OB 隐私一条的意思似乎是「标题里没留下文件名就别多发」，但没明说。
建议：「(a) 路径可出现在标题任意位置，含 `user@host: ~/x` 与 `C:\…`；(b) 截到 64 个字符；(c) 取第一条；(d) 根目录不发 `folder`；(e) `paths` 为 `off`、或 `titles` 为 `drop` / `pseudonymize` 时不发；`full`、`half` 照发。」服务端对 `folder` 越界（空串 / > 64 / 非字符串）：该条进 `rejected`，其余照收。

**G-11 终端连代理运行的边界。** (a) `attend` 区间 `[at, until)` 还是闭区间，中点恰好落在边界；(b) `<代理名>` 取运行的 `agent` 还是 `label`；(c) v2.19 的 `unverified` 运行是否参与；(d) 已连代理的块（`keySource:"agent"`）会不会被 v2.38「一」的重算改回去。
建议：「(a) 半开区间 `[at, until)`；(b) `agent`；(c) `unverified` 不参与（与 v2.17 一致）；(d) `keySource:"agent"` 的记录不参与重算。」

**G-12 候选的匹配规则。**
(a) 汉字「按汉字词 ≥ 2 个字」：连续汉字没有空格，`学习计划` 是一个词还是切成 `学习`、`计划`、`习计`…；(b) 「出现在」是子串还是整词；(c) 一个块有多个 `folder` / 多个域名时怎么取；(d) 三种 `match` 都不成立的块是否列出（`match` 枚举里没有「无」）；(e) `match:"agent"` 对 `projectId` 目标，运行挂在该项目下的某个任务算不算；(f) 项目的「未分类」任务能否当目标；(g) `days` 越界是 `422` 还是截断（OB·五写的是区间，没写后果）；(h) `target.path` 对项目目标的形状。
建议：「(a) 连续的汉字是一个词，整词做子串匹配，不做 n-gram；(b) 子串，不分大小写；(c) 块里任何一个 `folder` / 域名命中都算；(d) 都不成立的块**不列出**；(e) 算；(f) 能；(g) `422`；(h) 分区名 / 项目名，用 ` / ` 连接，如 `学习 / garden`。」

**G-13 claim 的 `skipped` 粒度与口径。** (a) 一个块名有的天已过期、有的天已 assigned、有的天 pending，`skipped` 怎么报；(b) 只有 `tiersSince` 之前的天：这些天**是否仍被 assign**（OB 说本来就不入账，只说计入 skipped）；(c) `claimed[].seconds`、`totalSeconds` 是被记到的天的块秒数，还是其中 `tiersSince` 之后会入账的秒数；(d) `skipped` 的块要不要写 `activity_asked`；(e) `blockKeys` 重复；(f) 给的块名 `legible:false` 能不能 claim。
建议：「(a) 只要有一天被记成，该块只进 `claimed`；一天都没记成才进 `skipped`，`reason` 优先级 `notFound` > `expired` > `beforeTiers` > `notPending`；(b) `beforeTiers` 的天不 assign；(c) 都按『被记成的天』里 `tiersSince` 之后的秒数；(d) 不写；(e) 去重；(f) 不拦（人明确点的）。」

**G-14 `activity_asked` 的写入时机和 answer 接口。** (a) 对块的操作失败（409 / 404 / 422）要不要写——应不写；(b) 只列了 assign / split / ignore / claim，`reset`、`generic`、`block-rules POST` 要不要写；(c) `live-ask/answer` 已有记录时的返回；(d) 「块名不存在 `404`」指什么范围的存在（任一天的原始记录、还是 `activity_blocks` 文档）。
建议：「(a) 只在操作成功后写；(b) 都不写；(c) 幂等，`200 {blockKey, answer, askedAt}`，已有的不改；(d) 在留存期内任一天有原始记录，或有 `activity_blocks` 文档。」

**G-15 引导「撤销」没有可用的接口。** OB·七·3 的「撤销」要「删 `activity_asked` 那一条」，但没有任何端点能删；「对该块名各天 `reset`」要页面对每一天读 `seq` 再发（最多 35 天 × N 块的请求）。
建议：加一个端点 `POST /api/core/activity/blocks/claim/undo {blockKeys}`（只许人）：服务端对这些块名过去 35 天里 `via:"human"` 且来自该次 claim 的日子做 `reset`、删对应第二级规则、删 `activity_asked`；`200 {undone:[{blockKey, days}]}`。否则这条只能页面侧长循环，没法稳定测。

**G-16 `tiers:false` 时「照常回空」的具体。** OB 头说端点「照常回空」，只有 live-ask 的 `{ask:null}` 形状是明写的。candidates 回 `200 {items:[]}`？claim 回什么？
建议：「`tiers:false` 时：`live-ask` → `200 {ask:null}`；`candidates` → `200`，`items:[]`，`totalSeconds:0`；`claim` → `409`，`detail`：『未打开三级分类』；`live-ask/answer` 照常写。」

**G-17 `onboardingDoneAt` 相关。** (a) `PUT` 里给了**非法值**是忽略还是 `422`（OB·四同时写了「给了就校验」与「给的忽略」，v2.38 对 `tiersSince:"x"` 现状是 `422`）；(b) `POST onboarding/done` 会不会让设置 `version` +1（+1 会让页面手里旧版本的 `PUT` 吃一个 `412`）；(c) 「引导显示条件」由页面判，任务树读取失败时该怎么办。
建议：「(a) 与 `tiersSince` 一致：值不合格 `422`，合格但给了就忽略；(b) 第一次写 `version` +1，已有就不动；(c) 读不到就不出引导。」

**G-18 新装缺省会把没碰过设置的老用户翻开。** OB·七的判据只有「没有设置文档且 `detector.rules` 版本 0」。一个 v0.2.x 老用户若从没存过设置、没写过规则，但已有计时记录，升级后第一次读设置就被打开 `tiers:true` 与 AI 直接分类，行为大变。另外没写：创建出的文档 `version` 是 0 还是 1；GET 在并发下只建一份；设备令牌 / MCP 的 GET 是否也触发创建。
建议：「判据加一条：该租户在 `events` 里没有任何事件（全新）；创建文档 `version: 1`；创建用 upsert（`insert-if-absent`），任何调用方首次读都可触发。」

**G-19 引导步骤的上下限与结果。** OB·七·1「建 1–2 个分区」「每个分区 1–2 个项目」：是页面强制上限，还是推荐值；能否在 0 个任务时继续；`directApply` 入口的阈值取哪个设置。
建议：「分区 ≤ 2、项目 ≤ 2、每个项目任务 ≥ 1 为页面硬限制（超出禁用按钮）；『跳过』任何步骤都可走；`directApply` 阈值读 `activity/settings.highConfidenceThresholdPercent`。」

**G-20 代理去向（HS·三）的未定处。**
(a) 「整条运行改挂」在事件流里是什么事实、叫什么（仓库是只追加的），投影重建后要能一致；(b) 已结束的运行能不能 place；(c) 给 `taskId` 时响应里的 `projectId` 填什么（示例两个键都在）；(d) 规则目标后来被删，`agents/start` 怎么办；(e) `GET place-rules` 的响应形状；(f) `DELETE` 不存在的规则回什么；(g) `agent` 名含 `/`、空格、中文时路径参数怎么编码；(h) `agent` 为空串；(i) 响应里 `remember:false` 时 `rule:null`；(j) 上限 500 时替换已有 agent 是否算超（应不算）；(k) 设备令牌读 `place-rules` 是否 `403`。
建议：「(a) 追加事件 `agent.run.placed`（`data:{runId,taskId?,projectId?,at}`），投影按最后一条算；(b) 可以；(c) 任务所在项目；(d) 当没有规则处理，运行不挂目标，不报错；(e) `{items:[{agent,taskId,projectId,createdAt,broken}]}`，目标已删的 `broken:true`；(f) `404`；(g) 按 URL 百分号编码，服务端解码后精确比较；(h) `422`；(j) 不算；(k) 设备令牌 `403`，与「人与 MCP」一致。」

**G-21 干活的宠物与左上角宠物的边界。**
(a) 「今天在跑」是此刻在跑，还是今天跑过（含已结束）；(b) 「最近活动时间」取运行的哪个字段（`lastAt` / 心跳时间 / `phaseAt`）；(c) 并列怎么办；(d) 运行 `phase` 到 `mood` 的映射（`error`、失联）；(e) 左上角宠物个数是否也封顶；(f) 缺省包的名字；(g) 服务端账号设置里的 `packsByTool` 的键位置与校验（DP·三 说「读服务端账号设置里的同名键」，但 OB·四 的设置表没有它）。
建议：「(a) 此刻在跑；(b) `views/lanes` 的 `agents[].lastAt`（没有则 `startedAt`）；(c) 按 `runId` 升序；(d) `working`→`working`，`waiting_input`/`waiting_permission`→`waiting`，`idle`、`error`、失联→`idle`；(e) 左上角也最多 4 只，多的显示 `+N`；(f) `dsh`；(g) `activity/settings` 追加 `pet: {packsByTool: {…}}`（对象，值为字符串，`*` 缺省 `dsh`；不合格 `422`）。」

**G-22 中心环取数的细节。** HS·二·1 的「此刻在看」与「今天最后看过」没说用 `attend` 的哪个字段：`until` 离此刻多少算「此刻」；跨夜（0 点后）「今天」按 NEXUS_TZ。
建议：「`attend` 最后一条的 `until` ≥ 此刻 − 15 秒算此刻在看；『今天』= NEXUS_TZ 本地日。」

**G-23 手动计时的接口和冲突。** HS·二·2 的「拖时钟到格子 = 开始手动计时；再点 = 停」没写调用哪个端点、已在计时时拖到另一格怎么办、「今天已记时长」读哪个端点。
建议：「开始 = 既有 `POST timer/start`（`projectId`）；已在计时则先停再开；停 = 既有 `POST timer/stop`；今天已记时长 = `views/lanes.human.todaySeconds`（若没有此键则先在 nexus-core 追加）。」（端点名以现有契约为准，仓主定。）

**G-24 一批显示文案与窄屏。** (a) 桶文案在 0 秒 / 不足 1 分钟时怎么写；(b) 补记卡里「X 小时」取几位小数（9000 秒是 `2.5` 还是 `2 小时 30 分`）；(c) 「已停留时长」写法；(d) 视口 < 1280 时怎么显示（「手机端不做」之外的 1024–1279）；(e) `/timer` 跳转的状态码与目标地址。
建议：「(a) `未分类 · 今天 0 分`；(b) 一位小数，去掉末尾 `.0`：`2.5 小时` / `2 小时`；不足 0.1 小时写 `不到 6 分钟`；(c) 整分钟：`6 分钟`；(d) < 1280 显示『请用更宽的窗口』占位，不渲染布局；(e) `302` 到 `/hive/`（临时跳转，保留以后改）。」

**G-25 左上角宠物的「随机走动」不可测。** 没有区域大小、速度、种子的约定。
建议：「走动由可注入的随机源与时钟驱动；断言只限『位置始终在左上角区域内、每步位移 ≤ 速度 × 时间』。」

**G-26 「完成记录」的数据来源写得太虚。** HS·四 说「数据来自既有读端，本版不新增读端」，但左半边要的「计时 / 对话各一条时间线」中，对话（`attend` 按项目聚合）在 `views/lanes` 的窗口（最近 2 小时）之外取不到；「落在该项目及其任务上的时段」`activity/ledger` 的形状也没引。
建议：写明读哪个端点、取哪天（今天）、每条时间线的元素形状 `{from, to, kind}`；如果 2 小时窗口不够，就改成「完成记录只显示最近 2 小时」。

**G-27 桌宠桥接的状态机与外部接口。**
(a) 启动后第一次轮询就看到在等人的运行，发不发 `agentWaiting`；(b) `agentDone` 的判据——运行从 `agents[]` 消失，还是读到 `agent.run.completed`（失联、被人 stop 是否算）；(c) 被 60 秒防抖压掉的 `focus` 事件是丢弃还是延后补发；(d) `alert.server` 的判据（`views/current` 连续失败几轮）；(e) `text` 超过 60 字符怎么截；(f) dsh-pet 的 `lock` / `broadcast` / `lock/release` 的请求体、`petId` 怎么传，契约只说「见其 `openapi.yaml`」，而 `third_party/dsh-pet/` 现在还没有；(g) 本机 `pet.packsByTool` 在桥接里没有用途（桥接不画宠物）；(h) `moods` 某项为 `null` 时是跳过 `work-status` 还是发默认状态。
建议：「(a) 启动第一轮只建立基线，不发；(b) 运行从 `agents[]` 消失且最近一次读到的阶段不是失联 → `agentDone`；(c) 丢弃；(d) 连续 3 轮失败；(e) 超 60 字符截到 59 个字符加 `…`；(f) 先把 dsh-pet 的 `openapi.yaml` 放进 `third_party/dsh-pet/`，契约引用其中的请求体；假 dsh-pet 的测试按那份文件写；(g) 删掉 `ai-detector` 本机配置里的 `packsByTool`，只留服务端设置那份；(h) 跳过 `work-status`，只换气泡。」

**G-28 浮窗的交互未定处。** (a) 鼠标移开后弹出窗口是否收起；(b) `ask` 在窗口开着时变 `null`（人切走了）卡片怎么办；(c) `/hive/mini` 里 assign 返回 `409`（seq 已变）怎么处理；(d) 红点轮询失败 / 令牌失效时红点状态；(e) 怎么取桌宠窗口位置（跨平台、dsh-pet 有没有接口）；(f) 「关掉」指什么（退出程序还是收起窗口）；(g) `since` 与 `focusedSeconds` 的关系（页面用哪个显示停留）；(h) 程序形态（Electron / Tauri）未定，测试位置依赖它。
建议：「(a) 移开 800ms 后收起（可配）；(b) 卡显示『这个窗口已经离开』并 2 秒后收起；(c) 重新取 live-ask，若块还在就用新 `seq` 重试一次，否则收起；(d) 连续 3 轮失败熄灭，期间保持；(e) 先用屏幕右下角，取位置作为以后；(f) 退出程序；(g) 停留 = `focusedSeconds`；(h) 先写 `float-core`（形态无关），形态定了再加壳。」

**G-29 版本号与文件位置。** 三份契约都写「nexus-core v2.44」，但 `modules/nexus-core/module_docs/contract.md` 目前最高到 v2.41；v2.42–v2.44 的节还不存在，本契约批准后需要在那里补，否则「提供方」没有对应条款。契约名 `hive.stage.v1` 标了「待定」。
建议：批准时一并在 nexus-core 契约里补 v2.42（displayName/live-ask/asked）、v2.43（candidates/claim/settings）、v2.44（place）三节，并把契约名定下来再开工。

**G-30 「今天」与跨午夜。** 桶、`live-ask.date`、「今天最后看过」都用「今天」，没说 NEXUS_TZ；一次连续停留跨午夜时 `date`/`seq` 对应哪天的块。
建议：「全部取 NEXUS_TZ 本地日；跨午夜的停留在 0 点处断开，`focusedSeconds` 从 0 点后起算。」
