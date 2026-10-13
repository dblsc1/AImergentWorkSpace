# agent —— 内置 AI 聊天的后端（可选，缺省不起）

> **v0.4 起可选、缺省关闭**（仓主 2026-10-14：用户接自己的 AI CLI，别删、隐藏）。代码与契约原样保留，开了就回来：`.env` 设 `COMPOSE_PROFILES=agent` 与 `AGENT_UPSTREAM=agent:8030` 两行。

`contracts/agent.chat.v1` 的缺省实现：把 [opencode](https://opencode.ai) 包成那份契约。
代理只能用 `mcp.tools.v1` 的只读工具读你的任务和时间；能写的只有两样建议：**活动分类规则的草稿**（`propose_detector_rules`，要你在「待批准 → 分类规则草稿」点「应用」才生效）和**给待确认的活动配任务**（`propose_activity_matches`，要你在「待批准 → 待确认建议」逐条点「是」才入账；现成任务都不合适时它也可以**提议新任务**，同样要你点「是」才建）。它归类之前会先看你以前是怎么定的（`get_match_history`）：同类窗口照以前的项目 / 任务来，你确认得越多它越准。不能跑命令、不能上网。

用法：在 `.env` 里加 `COMPOSE_PROFILES=agent` 和 `AGENT_UPSTREAM=agent:8030`、填 `AGENT_API_KEY`（缺省模型 `deepseek/deepseek-flash`），`docker compose up -d`，
打开「待批准」页，就有「AI 对话」面板（和待确认建议里的「让 AI 匹配」）。不开的时候它们都不出现。换模型、接本机 Ollama 见 `deploy/.env.example` 的 AI 助手一节。

**调试窗口**：想看每一轮到底发了什么给模型、模型回了什么（系统提示、历史、工具表、工具结果、思考、工具调用、用量），
在 `.env` 里设 `AGENT_DEBUG=1` 重启，面板上每条回答下面会多一个「调试」。**记录就是你的数据**（完整提示与工具结果），
存在 agent 的数据卷里：**只在自己机器上调试时开**，用完改回 `0`（重启时旧记录自动删掉）。密钥不会被记下。
用 deepseek 或设了 `AGENT_BASE_URL` 时可用。详见 `contracts/agent.chat.v1` 第九节。

**自己去认窗口**：你在「自动记录 → 活动检测设置」勾了「允许 AI 管理进行中的任务」、没在手动计时、规则又认不出你正在用的窗口时，
助手在后台看一眼历史和项目，给这个窗口写一条只认它的规则；计时页人那张卡上先是「AI 正在认这个窗口…」，认到了显示
「自动 · 项目 / 任务（AI 认的）」，旁边「不对」一键撤掉、换你自己选；认不出就照常问你。每一轮都在会话列表的「自动识别窗口」里
看得到。每个账号每小时最多 12 次，没有窗口要认时不调模型。不想要：`.env` 设 `AGENT_AUTOTRACK=0`。详见 `contracts/agent.chat.v1` 第十节。

实现说明、数据怎么存、怎么测：`module_docs/contract.md`。
