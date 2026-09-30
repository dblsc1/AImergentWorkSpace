# agent —— 计时台「问问助手」的聊天后端

`contracts/agent.chat.v1` 的缺省实现：把 [opencode](https://opencode.ai) 包成那份契约。
代理只能用 `mcp.tools.v1` 的只读工具读你的任务和时间，什么都不能写、不能跑命令、不能上网。

用法：在 `.env` 里填 `AGENT_API_KEY`（缺省模型 `deepseek/deepseek-flash`），`docker compose up -d`，
打开计时台，页面下方就有「问问助手」。换模型、接本机 Ollama 见 `deploy/.env.example` 的 AI 助手一节。

**调试窗口**：想看每一轮到底发了什么给模型、模型回了什么（系统提示、历史、工具表、工具结果、思考、工具调用、用量），
在 `.env` 里设 `AGENT_DEBUG=1` 重启，面板上每条回答下面会多一个「调试」。**记录就是你的数据**（完整提示与工具结果），
存在 agent 的数据卷里：**只在自己机器上调试时开**，用完改回 `0`（重启时旧记录自动删掉）。密钥不会被记下。
用 deepseek 或设了 `AGENT_BASE_URL` 时可用。详见 `contracts/agent.chat.v1` 第九节。

实现说明、数据怎么存、怎么测：`module_docs/contract.md`。
