# agent —— 计时台「问问助手」的聊天后端

`contracts/agent.chat.v1` 的缺省实现：把 [opencode](https://opencode.ai) 包成那份契约。
代理只能用 `mcp.tools.v1` 的只读工具读你的任务和时间，什么都不能写、不能跑命令、不能上网。

用法：在 `.env` 里填 `AGENT_API_KEY`（缺省模型 `deepseek/deepseek-flash`），`docker compose up -d`，
打开计时台，页面下方就有「问问助手」。换模型、接本机 Ollama 见 `deploy/.env.example` 的 AI 助手一节。

实现说明、数据怎么存、怎么测：`module_docs/contract.md`。
