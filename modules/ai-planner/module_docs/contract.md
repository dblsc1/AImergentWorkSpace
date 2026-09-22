# ai-planner · 对外接口契约

> 本文件是 ai-planner 对外行为的**唯一事实**。破坏性变更必须先评估消费方并留变更记录，
> 禁止悄悄删除既有承诺。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: ai-planner.plan.stream.v1
    summary: >
      对前端出的流式规划端点（SSE）——触发 AI 读全量日程、产低风险写 + 高风险提议，
      边想边报。降级时明确提示、不拖垮主栈。驱动可插拔（codex/deepseek），
      对前端行为完全透明：SSE 事件形状、白名单、actor 注入、降级语义不因驱动而变。

consumes:
  - id: nexus-core.views.export.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 读全量日程（含计时明细）喂给 LLM 作为规划输入。
  - id: nexus-core.views.next-actions.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 读待办区分类，作为 AI 规划参考。
  - id: nexus-core.views.review.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 读每周回顾聚合，作为 AI 规划参考。
  - id: nexus-core.planner.crud.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      受控工具层白名单命令映射的统一写入口（建/改）。受控层每次写请求都注入
      `actor="ai"`，AI 自己无法指定 actor。**明确不消费 events / timer**——
      AI 只碰 planner 这一个命名空间。
  - id: auth.gate.v1
    contract: ../../../contracts/auth.gate.v1/contract.md
    purpose: >
      通过 `POST /api/auth/login` 换取会话 cookie 后再调 nexus-core（或直接注入
      预置 cookie）。本模块只是这份契约的一个消费方，不实现登录逻辑。
  - id: ai-planner-guide-v1
    contract: ../../../contracts/ai-planner-guide-v1.md
    purpose: >
      AI system prompt + 工具契约的唯一事实源（下称「说明书」）。默认路径
      `contracts/ai-planner-guide-v1.md`（仓根相对），可用 `AI_PLANNER_GUIDE_PATH`
      覆盖。**这份说明书本身不随本模块代码分发**，见 handoff.md「怎么跑」一节。
```

## 对外 API

| 方法 | 路径 | 入参 | 出参 | 备注 |
|---|---|---|---|---|
| GET | `/health` | 无 | `{"status":"ok"}` | 健康检查 |
| POST | `/api/planner/plan` | `{"goal":"<自然语言目标>"}` | `text/event-stream`（SSE） | 流式规划 |

SSE 每条 `data:` 是一个 JSON `{type, message, data}`，`type` ∈：

- `thinking` —— 推理/叙述片段，**增量到达**（`message` 是这一块的增量文本，前端拼接展示，不是整段重发）
- `action_plan` —— 结构化动作清单（`data.actions`），**只在整份解析完整后才发一次**——半个 JSON 不会被执行
- `executed` —— 执行了一条低风险写（`actor=ai`）
- `proposal` —— 一条高风险提议（`data` = 提议对象，见下）
- `rejected` —— 白名单外动作被拒，或单条动作执行失败
- `degraded` —— AI 不可用，明确提示，主栈（计时/待办）不受影响
- `done` —— 本次规划结束，`data.proposals` 汇总**仅本次请求**产出的提议（按请求隔离，
  不带出其他请求或更早会话的提议，避免前端确认时打到已被删除的对象）

## 安全边界：受控工具层（不是 LLM）

**核心设计**：LLM（codex 或 deepseek）跑在只读沙盒/无执行权限的上下文里，**自己不执行
任何操作**，唯一产出是一份结构化动作清单（JSON）。真正执行动作的是宿主这边的「受控工具层」
（`code/backend/ai_planner/controlled_tools.py`），它是一张显式白名单：

| 命令 | 风险 | 映射 | 边界 |
|---|---|---|---|
| `read_schedule` | 读 | `GET /api/core/export` | 全量日程 |
| `read_next_actions` | 读 | `GET /api/core/views/next-actions` | 待办分类 |
| `read_review` | 读 | `GET /api/core/views/review` | 回顾聚合 |
| `create_task`/`create_project`/`create_zone` | 低 | `POST planner/{type}` | 层内硬编码注入 `actor=ai` |
| `set_title`/`set_weight`/`set_order` | 低 | `PATCH planner/{type}` | 只改 name/plannedWeight/order；**绝不改 projectId/zoneId/plan** |
| `propose_move`/`propose_reschedule`/`propose_delete` | 高 | **不映射任何 CRUD** | 只产提议对象，受控层不执行 |

三条物理保证（由代码结构兑现，不依赖"模型自觉"）：

1. **白名单外的命令名，受控层没有对应方法**——`events`/`timer`/数据库直连/裸 shell/任意 URL
   在这一层根本不存在，`dispatch()` 对未知命令直接拒绝，不静默降级。底层 nexus 客户端的
   公开面同样刻意收窄：无 delete、无 events、无任意 URL 方法（纵深防御第二道）。
2. **写命令的 `actor` 字段由受控层硬编码注入为 `"ai"`**，工具签名里没有 `actor` 形参，
   LLM 无从伪装成 `actor="human"`。
3. **高风险动作（move / reschedule / delete）只构造「提议」对象，不接触 nexus 客户端**——
   从提议到一次真实 CRUD 写，代码里没有路径。提议交前端逐条展示，用户确认后由前端走
   正常路径（`actor=human`）直接调 nexus CRUD 执行。

`type` 入参对 `task/project/zone`（单数、大小写不敏感）与其复数形式一视同仁，在层边界
归一为 `tasks/projects/zones`——LLM 自然倾向产出单数，归一避免高风险提议被误判类型拒绝。

## 提议对象 schema

```jsonc
{ "id":"prop_…", "kind":"move|reschedule|delete",
  "target_type":"zones|projects|tasks", "target_id":"t_1",
  "payload":{ /* move:{newParent}; reschedule:{plan|null}; delete:{} */ },
  "reason":"为什么这么建议", "proposed_by":"ai", "created_at":"…Z" }
```

## 驱动可插拔：codex / deepseek

两个驱动实现同一个接口（`code/backend/ai_planner/driver_base.py::PlannerDriver`，
`plan(schedule, goal) -> Iterator[StreamEvent]`），产出同样形状的结构化动作清单。
接受方（动作路由 + 受控工具层）不知道也不需要知道当前是哪个驱动——安全边界因此
**换驱动不改变一个字节**：两个驱动都不持有 nexus 客户端、没有任何执行方法，只产建议
交受控层裁夺。

选谁由 `AI_PLANNER_DRIVER=codex|deepseek` 决定（见「配置与密钥」）。

- **codex 驱动**：跑 `codex exec --json --sandbox read-only`，喂说明书（system prompt）
  + 序列化日程文本，用 `--output-schema` 约束只输出一份 JSON 动作清单，复用宿主
  `~/.codex/auth.json`（ChatGPT 登录态，免 API key）。选择结构化输出而非 MCP 工具直连
  nexus 的理由：codex 是带 shell 的通用 agent，工具直连那条路要靠 sandbox 配置封死
  shell/网络才安全，而 sandbox 配置难以确定性回归；结构化输出把安全边界落在受控工具层
  这段可测的 Python 代码里，codex 物理上碰不到 events、伪造不了 actor、执行不了高风险写。
  OpenAI strict schema 要求每个 object 都 `additionalProperties:false`，自由 `args`
  object 不合法，因此 schema 用 `args_json`（把参数编码成一段 JSON 字符串），驱动侧解回
  dict。
- **DeepSeek 驱动**：走 OpenAI 兼容的 `/chat/completions`，`"stream": true` 逐块回 SSE
  `delta`（`delta.reasoning_content` / `delta.content`）。DeepSeek 是推理模型，
  `max_tokens` 是「推理 + 正文」的**合计预算**：给小了推理会吃光配额、正文完全没写出来
  或写到一半被截断——这不是"模型没话说"，是预算问题。判据（流式下最后一个 chunk 会带
  `finish_reason` + `usage.completion_tokens_details.reasoning_tokens`）：

  | 信号 | 判定 | 处理 |
  |---|---|---|
  | `finish_reason == "length"` 且正文（含下方 reasoning 兜底）解析不出动作清单 | 预算耗尽/正文被截断，不是模型没话说 | 自动重试一次，`max_tokens` 翻 4 倍（封顶 8000）；仍不行才终态降级，消息点名 `reasoning_tokens` 与 `finish_reason=length` |
  | `finish_reason != "length"`（通常 `"stop"`）且正文仍为空 | 模型这次真的没有产出正文，与预算无关 | 不重试，消息点名"非预算截断" |
  | 正文非空但解析不出动作清单 | 解析失败，与预算无关 | 走共用解析器 `action_parsing.parse_actions_json`（含剥 markdown 代码块围栏），失败一律降级，不崩 |

  已知模型行为：`stream:true` + `response_format:{"type":"json_object"}` 组合下，偶尔会
  把最终结构化答案整段写进 `reasoning_content`、`content` 留空，且 `finish_reason` 仍是
  `"stop"`。驱动对此兜底：`content` 解析不出动作清单时，用同一份解析器对
  `reasoning_content` 全文再尝试一次，校验标准完全相同（必须是合法 JSON 且形状匹配
  `action_schema.json`），解析不出来仍照常降级。

  两个驱动喂给底层模型的指令与输出格式约束（`planning_prompt.py::build_action_prompt`）
  逐字一致，唯一允许变化的输入是说明书全文、日程、用户目标——这样"同一份说明书，
  两种模型表现不一样"才能归因到模型本身，而不是 prompt 措辞差异。

## 降级行为

驱动不可用（进程缺失/超时/非零退出/输出不可解析）或读日程失败 → 产 `degraded` 事件、
无动作，绝不抛给主流程；`run_planning` 一定以 `done` 收尾。待办/收件箱/回顾走的是
nexus-core 的规则层，没有 AI 照常跑——AI 不是关键路径，挂了不拖垮主栈。

## 宿主服务落点

宿主进程，**不进容器编排**：codex 驱动依赖宿主本机的 `~/.codex/auth.json` 与 `codex`
可执行文件，够不到容器网络，因此与 nexus-core 主栈解耦部署。反向代理侧的接入方式见
`nginx-docker` 模块契约的 `/api/planner/*` 一节。

## 数据与存储

本模块不持久化任何数据、不连任何数据库——无状态服务，所有事实读写都经 nexus-core 契约。
喂给 LLM 的日程数据可能包含使用者的计时/任务隐私信息，部署者需自行确保不把它落进日志
或长期存储；codex 驱动的会话产物落在宿主 `~/.codex/`，不属于本模块管理。

## 配置与密钥

> 同步维护根目录 `.env.staging.example`；只允许变量名和无敏占位值进 Git。

| 环境变量 | 必填 | 用途 | 安全约束 |
|---|---|---|---|
| `AI_PLANNER_NEXUS_BASE` | 是 | nexus-core 基址，如 `http://127.0.0.1:8000` | 缺失立即拒绝启动，不给弱默认值 |
| `AI_PLANNER_NEXUS_PASSWORD` | 二选一 | nexus-core 登录口令 | 只从 env 读，不进代码/日志/仓 |
| `AI_PLANNER_NEXUS_COOKIE` | 二选一 | 预置会话 cookie | 同上 |
| `AI_PLANNER_BIND` | 否 | 监听地址，默认 `127.0.0.1:8700` | 不得默认 `0.0.0.0` |
| `AI_PLANNER_DRIVER` | 否 | `codex`\|`deepseek`，默认 `deepseek` | 非法值直接拒绝启动 |
| `AI_PLANNER_CODEX_BIN` | 否 | codex 可执行文件名，默认 `codex` | `AI_PLANNER_DRIVER=codex` 时必须能找到 |
| `AI_PLANNER_CODEX_MODEL` | 否 | 指定模型，默认走 codex 自身配置 | — |
| `AI_PLANNER_CODEX_TIMEOUT_S` | 否 | codex 超时秒数，默认 120 | — |
| `AI_PLANNER_DEEPSEEK_API_KEY` | `AI_PLANNER_DRIVER=deepseek` 时必填 | DeepSeek API key | 只从 env 读，不进代码/日志/仓 |
| `AI_PLANNER_DEEPSEEK_BASE` | 否 | DeepSeek 端点，默认 `https://api.deepseek.com` | — |
| `AI_PLANNER_DEEPSEEK_MODEL` | 否 | 模型名，默认 `deepseek-v4-flash` | — |
| `AI_PLANNER_DEEPSEEK_TIMEOUT_S` | 否 | 请求超时秒数，默认 120 | — |
| `AI_PLANNER_DEEPSEEK_MAX_TOKENS` | 否 | 推理+正文合计预算，默认 2000 | 小于 200 直接拒绝启动（见上方判据）；预算耗尽时驱动内部自动重试一次翻 4 倍（封顶 8000），不需要手改这个变量 |
| `AI_PLANNER_GUIDE_PATH` | 否 | 说明书路径，默认 `contracts/ai-planner-guide-v1.md` | 缺失立即拒绝启动 |
