# PRD · GTD 大版本（gtd-v1）

- 日期：2026-08-10 · 属主：CFO · 状态：**待用户批准**
- 上游：与用户 5 轮架构澄清（本文 §12 决策台账）
- 本文是**派活唯一需求源**：任务单只引条目编号，不复述。

## 0. 背景与目标

现有系统在 GTD「组织」层已很强（分区/项目/任务 + 依赖/权重/计划期/计时），
缺 GTD 的三个流程视图：**捕捉（Inbox）**、**下一步行动（待办区）**、**回顾（Review）**，
以及一个**有写能力的 AI 规划助理**。本版补齐，且**零后端重构** —— 全是加读端 + 一个
`actor` 字段 + 一份 AI 说明书 + 一个宿主 AI 服务。

| 目标 | 成功指标（机械可判） |
|---|---|
| G1 零摩擦捕捉 | 任意页一键记想法，不选项目即落 Inbox |
| G2 待办区自动生成 | 打开即见「现在可做」排好序，被依赖 block 的自动进「等待」 |
| G3 待办区离线可用 | 断网 / LLM 挂 → 待办区规则层照常，不瘫 |
| G4 AI 能读能写 | AI 读全量日程 + 通过统一 CRUD 建/改/搬/排，写者留痕 |
| G5 AI 有边界 | AI 碰不到 events；删除/搬移/改期必人确认；越界被判据拦 |

## 0.1 术语映射（GTD → 你的系统）

| GTD | 系统落地 |
|---|---|
| Inbox 收件箱 | well-known `p_inbox` project（挂「未分类」zone） |
| Clarify 理清 | 把任务从 p_inbox 搬到正式 project（改 projectId，J10 key 重算） |
| Next Action 下一步 | 未完成 + `dependsOn` 全 done 的任务 |
| Waiting 等待 | 未完成 + 有前置未 done |
| Context 情境 | zone（按分区分组待办） |
| Someday/Maybe | **本版不做**（用户裁决③：task 不动，进了就 active） |

## 1. 收件箱（F-INBOX，属主 nexus-core + table/前端）

- **F-INBOX-1** well-known `p_inbox` project + 一个「未分类」zone，由种子创建；
  **禁删保护**（删 p_inbox 返 409，理由：它是捕捉落点）。
- **F-INBOX-2** 全局快速捕捉入口（顶栏「＋ 记一笔」或快捷键）：只填标题即建 task 到 p_inbox，
  **不要求选项目/分区**。其余字段留空（active、无 plan、无 deps）。
- **F-INBOX-3** 理清：Inbox 里的任务可**搬移**到正式 project（复用现有 PATCH projectId，key 重算）。
  UI 给「归类到…」选择器。
- **F-INBOX-4** Inbox 视图：一个「收件箱」区，列 p_inbox 下未搬走的任务，逐条可归类/删除。

## 2. 待办区（F-TODO，属主 nexus-core 读端 + 前端）

- **F-TODO-1** 规则读端 `GET /api/core/views/next-actions`（后端算，**离线确定，不依赖 LLM**）：
  - **可做**：`done=false` 且 `dependsOn` 全 done
  - **等待**：`done=false` 且有前置未 done（单列，标出被谁 block）
  - **过期**：计划期 end < 今天且未完成（琥珀标）
  - **排序**：权重 `plannedWeight` 降序；计划期今天到期/过期置顶；同权重按 key 稳定序
  - 按 zone 分组（情境）
  - **依赖环防御（grill 技术底线，必做）**：遍历 `dependsOn` 判「可做」时必须有**环检测 / 深度上限**。
    禁环校验是 cockpit-v1 后加的，历史数据可能有环，读端撞环会**死循环崩服务**。撞环的任务
    降级为「可做」并打警示标（不是崩），加一条 pytest 喂含环 fixture 断言不死循环。
  - **无 plan 任务归类**：默认进「可做」（GTD：无 due 的 next action 照样可做）；O 开放，用户试用后调。
- **F-TODO-2** 「今天」口径：日期用服务端归日（Asia/Shanghai），前端不 `new Date` 拼（本项目踩过的类）。
- **F-TODO-3** 每行播放钮 → 计时页 `?task=<id>`（复用 L2）；完成打勾 → PATCH done，
  下游任务（依赖它的）自动从「等待」升「可做」（读端重算，前端刷新）。
- **F-TODO-4** AI 增值入口：待办区顶「🤖 让 AI 帮我规划」按钮 → 触发 F-AI，**按需，不高频**。
- **F-TODO-5** 待办区是独立视图（新页 `/todo/` 或 table 页一个 tab，实现轮定）。

## 3. AI 规划助理（F-AI，属主新模块 `ai-planner` + 宿主服务）

- **F-AI-1** 运行时：**宿主上一个 AI 规划小服务**（不进 compose 容器 —— codex 在宿主够不到容器），
  驱动 codex agent（`codex mcp-server` / `exec`，复用 `~/.codex/auth.json` ChatGPT 登录态，**免 API key**）。
- **F-AI-2** 工具面：**AI 不接触裸 codex shell** —— 包在一个**受控工具层**后面（白名单命令层，
  可选叠加 codex sandbox 禁网/禁 shell 逃逸）。AI 只能调该层暴露的 planner 白名单命令
  （建/改/搬/排），**events / timer / mongo 直连 / 凭据文件全不在白名单**。
  **信任边界 = 受控层（我们写的，可信），不是 codex（通用 agent，不可信）**（grill 2026-08-10 修订）。
  受控层形态（codex sandbox 禁 shell 只留工具 / MCP 工具白名单 / SDK function-calling）留 O2 实现轮定，
  但「AI 只走受控白名单、不碰裸 shell」是**架构硬约束**，不是可选。
- **F-AI-3** 输入：发**全量日程**（zone/project/task + 计划期/依赖/权重 + **计时明细**，用户已放开），
  转自然语言喂 LLM。**注**：这些数据出本机到 OpenAI；仍绝不进公开仓（数据护栏已在）。
- **F-AI-4** 混合确认（用户裁决）：
  - **低风险**（建任务、排待办、改标题/权重）→ AI 直接调 CRUD，`actor=ai` 留痕
  - **高风险**（搬移 projectId、改计划期、**删除**）→ AI 产「提议清单」，UI 逐条展示，
    **人确认后才调 CRUD**。删除**必确认**（用户裁决②）。
  - 分流规则写进 F-GUIDE 说明书，且**后端二次设防**：见 F-API-3。
- **F-AI-5** 流式 UI（渐次加载）：agent 边想边做，实时显示「读了 X → 建议建 Y → 搬 Z（待你确认）」，
  不白屏等十几秒。
- **F-AI-6** 降级：codex 不可用 / 无网 / 超时 → 明确提示「AI 规划暂不可用」，**待办区规则层照常**（G3）。
  绝不因 AI 挂而阻断 GTD 主流程。

## 4. 写者留痕（F-ACTOR，属主 nexus-core）

- **F-ACTOR-1** planner 写入口（统一 CRUD）增 `actor` 维度：`human` / `ai`，随每次写记录。
  **actor 由请求来源决定，不是自报字段**（grill 修订）：AI 的写全部经受控工具层（F-AI-2），
  该层**注入** `actor=ai`，AI 无从伪装 human；人的写走前端正常路径，`actor=human`。
  信任落在「谁的路径」不落在「谁说自己是谁」。
- **F-ACTOR-2** 留痕落点：planner 对象加 `lastWriter` + 一条**审计流水**（append-only，形状由
  nexus-core arbiter 定；可复用 events 台账的 append-only 纪律但**独立集合**，不混进 yq-event 事实流）。
  **兼任「中间态可追溯」**（grill 修订）：AI 批量写**不做事务/回滚**（用户裁决），一批操作崩在半路时，
  审计流水记到「做到第几步」，靠它查 + 手动收拾。可查即可。
- **F-ACTOR-3** 前端展示：AI 写过的对象可视化标记（小 🤖 角标），让你一眼看出哪些是 AI 动的。

## 5. AI 说明书（F-GUIDE，属主 CFO + nexus-core）

- **F-GUIDE-1** 一份文档 = AI agent 的 system prompt + 工具契约，落 `contracts/ai-planner-guide-v1.md`。
  内容：三类对象（zone/project/task）+ 字段含义 + 唯一写入口 + **边界**（只 planner、events 禁碰、
  删除/搬移/改期必提议确认、必带 actor=ai）+ GTD 语境（Inbox/待办/依赖/权重怎么用）。
- **F-GUIDE-2** 说明书是**契约**：AI 服务加载它当 system prompt；改边界改这一处，AI 行为跟随。

## 6. 每周回顾（F-REVIEW，属主 nexus-core 读端 + 前端）

- **F-REVIEW-1** 只读读端 `GET /api/core/views/review`：聚合本周计划vs事实、过期项目、
  久未动任务（长期无计时且未完成）、Inbox 待清空计数。全是现有 events/planner/投影的重新聚合，**零模型变动**。
- **F-REVIEW-2** 回顾视图：一屏 GTD 每周回顾清单，可从每项跳到对应对象处理。

## 7. 数据与契约增量（F-API，属主 nexus-core；**契约先行**）

- **F-API-1** `actor` 字段 + 审计流水（F-ACTOR）。
- **F-API-2** 读端 `views/next-actions`（F-TODO-1）、`views/review`（F-REVIEW-1）—— **只读**，
  实时算（个人数据量小，不物化投影）。
- **F-API-3** **高风险确认的可信边界**（grill 修订）：高风险操作（DELETE、改 projectId、改 plan）的确认，
  签发方必须与 AI 物理隔离 —— **受控工具层（F-AI-2）对高风险命令只产「提议」，自己不执行**；
  提议经 UI 展示，**人点确认后由前端正常路径（actor=human）直接调 CRUD**。即 AI 根本没有执行高风险写的路径，
  不是「AI 写了再靠令牌拦」。令牌机制若仍需要（O4），签发密钥**绝不落进受控层/codex 可及的文件系统**。
- **F-API-4** `p_inbox` 禁删（F-INBOX-1）。
- **F-API-5** events / timer / 投影核心 / 标识三分 **全不动**。契约 bump 一版收录 F-API-1..4。

## 8. 非功能

| 类 | 要求 |
|---|---|
| 隐私 | 发 LLM 的数据出本机（用户放开含计时明细）；但**绝不进公开仓**（数据护栏已在，X-cockpit 开源前清） |
| 降级 | LLM 挂 → 待办区规则层照常（G3）；AI 服务与主栈解耦，互不拖累 |
| 安全 | AI 只 planner；events 隔离；高风险 AI 写后端二次设防（F-API-3）；写者留痕；删除必确认 |
| 性能 | 待办区读端实时算（数据量小）；AI 按需触发不高频；codex 慢，流式呈现 |
| 离线 | 待办/收件箱/回顾全走本地栈；仅 AI 增值层需外网 |
| 许可 | AI 服务 MIT/Apache 依赖；codex 是本机现成 |

## 9. 验收清单（机械优先）

| # | 判据 | 方式 |
|---|---|---|
| A1 | 一键捕捉落 p_inbox，不需选项目 | 前端 E2E |
| A2 | 待办区：可做/等待/过期分类正确，权重排序正确 | next-actions 读端 pytest + 前端 E2E |
| A3 | 完成任务 → 其下游从「等待」升「可做」 | 读端 pytest |
| A4 | 断网/AI 挂 → 待办区照常 | E2E 断 AI 服务后验待办 |
| A5 | AI 低风险写 actor=ai 落库 + 角标 | 集成测试 |
| A6 | AI 高风险写无确认令牌 → 后端 401/403 拒 | nexus-core pytest（F-API-3 二次设防） |
| A7 | AI 删除 → 必走确认流 | E2E |
| A8 | AI 只能调受控层白名单命令，events/mongo/凭据不在白名单；裸 shell 不可达 | 受控层工具白名单测试 |
| A8b | AI 无执行高风险写的路径（删/搬/改期只产提议，执行走人的前端路径） | 集成测试：AI 直发高风险 CRUD 应无认证 |
| A8c | actor 由受控层注入，AI 无法带 actor=human | 受控层测试 |
| A8d | 待办区读端喂含环 fixture 不死循环 | pytest |
| A9 | p_inbox 删除 → 409 | pytest |
| A10 | 回顾读端聚合正确 | pytest |
| A11 | AI 流式 UI 边生成边显示 | E2E |

## 10. 派活切分与档位（遵宪法 D17：普通 Sonnet / 复杂 Opus / 禁 fable subagent）

| 模块 | 条目 | 档位 |
|---|---|---|
| CFO | 契约（planner actor/读端）、AI 说明书 F-GUIDE、协调 | — |
| nexus-core | F-API-*、F-ACTOR、F-TODO-1、F-REVIEW-1、F-INBOX-1 | normal |
| ai-planner（新模块） | F-AI-*（宿主服务 + codex 驱动 + 工具面 + 流式）| **Opus**（agentic + 安全边界 + 外部集成） |
| table / 新 todo 前端 | F-INBOX-2..4、F-TODO-3..5、F-REVIEW-2、F-ACTOR-3 | normal |
| 种子/部署 | p_inbox 种子、AI 服务纳入自启 | simple |

## 11. 开放问题（实现轮定，不阻塞批准）

- O1 待办区落点：新页 `/todo/` vs table 一个 tab（前端出两案 CFO 择一）。
- O2 AI 服务与 codex 的具体接线：`mcp-server` 常驻 vs 每次 `exec`（ai-planner agent 冒烟后定）。
- O3 审计流水 schema（nexus-core arbiter 定，契约先行）。
- O4 确认令牌机制（F-API-3）：短时签名 token vs 会话内一次性 nonce（nexus-core 定）。
- O5 新模块 `ai-planner` 是否纳入 X-cockpit monorepo（与迁移工程一起定）。

## 12. 决策台账（本 PRD 依据的用户裁决，2026-08-10）

- 收件箱 = 特殊 project + 搬移（方案①A）
- AI 规划走 LLM，非规则算法；规则层打底 + AI 增值层（②，分层由 CFO 建议、用户认可）
- AI 有**写能力**，走 SDK/codex 运行时，复用本地 gpt 登录态
- 混合确认：低风险自动 / 高风险（搬移·改期·删除）确认；**删除能删但必确认**
- 发 LLM 数据可含**精确计时明细**；功能做成流式（渐次加载）
- task 模型**不动**，进了就 active（③）
- 用户成熟经验：CRUD 统一（已有）+ 写者留痕（新增 actor）+ schema 统一（已有）+ AI 说明书（新写）
- 红线（CFO 定，宪法级）：AI 只写 planner，events 事实台账绝不碰

## 13. grill-me 技术审查修订（2026-08-10，批准前）

挖出致命洞：**PRD 原把安全建在「信任 codex 守说明书」上，但 codex 是有 shell 的通用 agent，
说明书对它是废纸** —— 它能 mongosh 改 events、curl 任何端点、伪造 actor、自签令牌。

用户裁决的解法（已固化进上文）：
- **①②③ 信任模型**：不给 codex 裸 shell，包进**受控工具层**（白名单 planner 命令，可选叠 sandbox），
  信任边界=受控层（可信）而非 codex（不可信）。actor 由受控层注入（F-ACTOR-1 改）；
  高风险写 AI 只产提议、执行走人的前端路径（F-API-3 改）；工具面硬约束（F-AI-2 改）。
- **批量事务**：不做事务，审计流水兼中间态可追溯（F-ACTOR-2 改），可查即可。
- **待办区环**：读端必须环检测/深度上限防死循环（F-TODO-1 加，技术底线，A8d）；
  无 plan 任务默认「可做」，试用后调。
- **codex 可用性**：用户评估较高，降级（F-AI-6）保留但非重点。

验收补 A8/A8b/A8c/A8d 落实上述。**grill 通过**：解法有效且已成文，可派活。
