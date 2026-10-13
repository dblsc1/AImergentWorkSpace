## 装

只需要 Docker（Windows / macOS 用 Docker Desktop）。

**Linux / macOS**

```sh
curl -fsSL https://github.com/dblsc1/AImergentWorkSpace/releases/latest/download/honeycomb-install.sh | sh
```

**Windows**（PowerShell）

```powershell
irm https://github.com/dblsc1/AImergentWorkSpace/releases/latest/download/honeycomb-install.ps1 -OutFile honeycomb-install.ps1
powershell -ExecutionPolicy Bypass -File .\honeycomb-install.ps1
```

装完打开 <http://127.0.0.1:8800/>，登录口令脚本会打印一次，也存在 `honeycomb/.env` 里。
再跑一次同一个脚本 = 升级到这一版（保留配置与数据）。

> Windows 脚本在 CI 里用 PowerShell 7 和 5.1 真跑过，并在 Windows 10 真机（自带 PowerShell 5.1 + Docker Desktop）上验收过安装与升级。

**AI**：缺省用你自己的 AI CLI（Claude Code / Codex / opencode）接 HoneyComb 的 MCP，见 README「把你的 CLI 接到 HoneyComb」，不需要模型密钥。
页面里自带的 AI 聊天是**可选的、缺省不开**：`honeycomb/.env` 里加两行 `COMPOSE_PROFILES=agent`、`AGENT_UPSTREAM=agent:8030` 并填 `AGENT_API_KEY`，再 `docker compose up -d`。
之前填过 `AGENT_API_KEY` 的老安装，升级时若 `.env` 里一行 `COMPOSE_PROFILES` 都没提到，脚本会自动补上这两行，聊天照旧；想关就把两行注释掉（脚本以后不再动）。聊天没开着时，升级会顺手删掉这个项目里旧版留下的 agent 容器（手动：`docker compose --profile agent rm -sf agent`）。源码安装不走脚本，要自己加两行。用 override 自带 `agent` 服务、靠旧缺省值 `agent:8030` 转发的部署，也要在 `.env` 里写上 `AGENT_UPSTREAM=agent:8030`，否则聊天接口回 502。「AI助理」页现在叫「待批准」（人批准 AI 提议的）。

想看源码、自己改：clone 仓库，照 README「三十秒跑起来」走源码路线。

## 附件

| 文件 | 用途 |
|---|---|
| `honeycomb-install.sh` / `.ps1` | 一键安装 / 升级 |
| `docker-compose.yml` | 本版组装（镜像 `ghcr.io/dblsc1/honeycomb-*`），安装脚本会下载它 |
| `ai-detector-<tag>-<系统>-<架构>[.exe]` | ai-detector 桌面单文件（linux-amd64 / windows-amd64 / darwin-arm64 / darwin-amd64），装法见 [`modules/ai-detector/README.md`](https://github.com/dblsc1/AImergentWorkSpace/blob/main/modules/ai-detector/README.md) |
| `ai-detector-<tag>-SHA256SUMS.txt` | 上面四个文件的 SHA256 校验值 |
