# ai-planner · handoff（冷启动接手便条）

> **面向未来接手**：只读这一份就能上手，不是历史流水。做完一个任务，检查这里要不要更新。

## 一句话

ai-planner 是宿主上的 AI 规划服务：读全量日程，帮用户理 GTD，经**受控工具层**
（白名单 + 强制注入 `actor=ai` + 高风险动作只提议不执行）安全地写 nexus-core 的
planner 数据。**驱动可插拔**——codex 与 DeepSeek 是同一接口的两个实现，选谁由
`AI_PLANNER_DRIVER` 决定（默认 `deepseek`）。依赖 nexus-core 契约与一份独立的
system prompt 说明书（见下）。**不进容器编排**——宿主级凭据/进程，够不到容器网络。

## 怎么跑 / 怎么测

- 装依赖：`pip install -r code/backend/requirements.txt`（fastapi / uvicorn / httpx / pydantic / pytest）
- 单测：`cd code/backend && python3 -m pytest tests/ -q`——机械覆盖白名单/高风险隔离/
  actor 注入三条安全边界、降级、提议按请求隔离、驱动选择、两个驱动各自的失败模式、
  DeepSeek 真流式增量与推理预算判据。
- codex 冒烟（不碰真 nexus，用 mock 后端）：
  `AI_PLANNER_NEXUS_BASE=http://127.0.0.1:8000 python3 code/backend/smoke/codex_smoke.py`
- **起真服务**：`bash code/backend/scripts/up.sh`（组装根 `ai_planner.bootstrap:build_app`，
  `uvicorn --factory` 无参工厂）。**不要**直接 `uvicorn ai_planner.service:create_app`——
  `create_app(layer, driver)` 要两个参数，无参工厂调用会 `TypeError`。`up.sh` 会做前置
  校验（凭据齐全 / 绑定非 `0.0.0.0` / 说明书文件存在 / 按驱动分支检查 codex 可执行或
  DeepSeek key），缺一样直接拒绝启动，不会"看起来起来了"实际半残。

**说明书依赖**：本模块的 system prompt + 工具白名单说明书唯一事实源默认在仓根
`contracts/ai-planner-guide-v1.md`（`AI_PLANNER_GUIDE_PATH` 可覆盖），**这份文件不随
本模块代码分发**，需要单独提供，见 `module_docs/contract.md` 的 `ai-planner-guide-v1`
consumes 条目。

## 接口

详见 `module_docs/contract.md`。核心是 `POST /api/planner/plan`，出 SSE 事件流；受控
工具层白名单逐条对齐说明书 §3。两个驱动产出的 SSE 事件形状/白名单/actor 注入/降级语义
完全一致，前端不用关心当前是哪个驱动。

## 避坑 / 技术债

1. **安全边界在受控工具层，不在驱动。** 无论 codex 还是 deepseek，驱动只产出结构化
   动作清单，自己不执行任何东西；受控层（`controlled_tools.py`）才是唯一执行者。
   别把任何驱动当可信输入处理——高风险动作（`propose_*`）不接收 nexus 客户端引用，
   这是物理保证，别为图省事破了它。

2. **codex 结构化输出的 schema 坑**：OpenAI strict schema 要求每个 object 都
   `additionalProperties:false`，自由 `args` object 不合法，所以 schema 用 `args_json`
   （把参数编码成一段 JSON 字符串），驱动侧再解回 dict。两个驱动共用同一份解析逻辑
   （`action_parsing.py`），别各写一份。

3. **DeepSeek 是推理模型，`max_tokens` 是"推理 + 正文"合计预算。** 给小了推理会吃光
   配额、正文为空（实测：20 时正文为空，200 才够）。`config.py` 已断言 `<200` 直接
   拒绝装配，别为了省 token 在部署时调小。**`finish_reason=="length"`** 是"预算耗尽/
   正文被截断"的信号（该自动重试一次，`max_tokens`×4 封顶 8000）；**`finish_reason
   =="stop"` 但正文仍空**是"模型这次真的没话说"（不该重试，重试没有帮助）。两者绝
   不能用同一句降级文案糊弄过去。

4. **别假设 LLM 一定把最终答案写在你期望的字段里。** DeepSeek 在 `stream:true` +
   `response_format:json_object` 下偶尔把答案整段写进 `reasoning_content`、`content`
   留空却报 `finish_reason=stop`。解法是给两个通道都过同一份校验/解析器兜底，而不是
   硬编码"答案只能在 content 里"——任何"给模型两个输出通道"的场景都可能撞见模型选
   错通道。

5. **流式场景下，半成品绝不能提前发给下游执行者。** `content` delta 逐块到达时任何
   中间态都可能是不完整的 JSON，只有等 `finish_reason` 到达才能确认"这就是全部了"。
   `thinking` 可以随 delta 增量转发（前端只展示叙述文本，半句话没有执行风险），但凡
   是会被喂给"唯一执行者"的结构化产出，必须等完整才发一次。

6. **依赖注入要注入"可替换的一层"，不要注入"整个装配好的对象"。** 早期 `DeepSeekDriver`
   接受现成 `httpx.Client` 时，测试"驱动会把 key 放进 Authorization 头"实际测的是测试
   自己造的 client，驱动自己的组装逻辑从没被跑到（假绿）。改成只接受 `transport`
   （httpx 的传输层）后，header/超时/`trust_env` 仍由驱动自己的构造逻辑决定，测试才是
   真的在测驱动。

7. **`from __future__ import annotations` + FastAPI 路由用的 pydantic model 绝不能定义
   在函数内部（局部类）。** `typing.get_type_hints()` 看不到局部作用域，会把请求体
   参数静默误判成查询参数，导致真调用永远 422——单测如果没有真的经 HTTP 打过这条路径
   （标了 `pragma: no cover`），这类坑会捂到真起服务才炸。`PlanReq` 因此定义在
   `service.py` 模块顶层，别挪回函数内部。

8. **任何 `httpx.Client()` 构造都显式传 `trust_env=False`**（除非真的要吃宿主代理
   配置）。宿主常配 `ALL_PROXY=socks5://…` 供上网用，默认 `trust_env=True` 会在构造期
   就因缺 `socksio` 报错——这是装配阶段故障，与目标地址是不是内网地址无关。

9. **codex 依赖宿主 `~/.codex/auth.json`（ChatGPT 登录态），会过期/被吊销。** 过期表现
   为 `codex exec` 非零退出，服务侧已有降级兜底（不崩、不拖垮主栈），但 AI 侧一个动作
   都产不出。这是运行环境问题，不是代码 bug——需要人工跑一次交互式 `codex login`
   重新走 OAuth，agent 无法代做。默认驱动已切到 `deepseek` 正是为了不被这条卡住。

10. **LLM 常用单数 type**（`task` 而非 `tasks`）：受控层已在边界归一，别退回严格拒绝。

**已知技术债**：① SSE 端点的 FastAPI 路由工厂标了 `pragma: no cover`（需要真实
nexus + 驱动才有意义），逻辑核心 `run_planning` 已有独立单测覆盖；② codex `--json`
事件形状可能随 codex 自身版本变化，叙述性展示部分做的是宽松提取，不作为解析依据；
③ 高风险提议被用户确认后、由前端走 `actor=human` 执行的下游路径不在本模块契约范围
内，未被本模块的测试覆盖。
