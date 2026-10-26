# agent-lights-gnome

GNOME Shell 扩展 Agent Traffic Light：桌面上每个 AI 代理一个红绿灯。需要 GNOME Shell 45 及以上。

## 安装

```bash
./install.sh
```

等价于把 `extension.js`、`metadata.json`、`stylesheet.css` 复制到
`~/.local/share/gnome-shell/extensions/agent-traffic-light@local`，再 `gnome-extensions enable agent-traffic-light@local`。
Wayland 下首次安装或升级后要注销再登录。UUID 不变，旧版会原地升级。

## 灯语

| 状态 | 灯 |
|---|---|
| `working` | 绿灯慢闪 |
| `waiting_input` / `waiting_permission` | 黄灯快闪 |
| `complete` / `idle` | 绿灯常亮 |
| `error` | 红灯常亮 |
| 状态文件缺失、损坏或超过 10 秒没更新 | 红灯常亮（unknown） |

## 操作

- 拖动移动，拖边缘缩放，位置和大小自动记住。
- 悬停出现 `−` 和 `×`：`−` 把所有灯收成一行小灯（再点 `+` 还原），`×` 隐藏这一盏。
- 双击灯打开控制面板，逐个代理勾选显示 / 隐藏。所有灯都被隐藏时，重新启用扩展会全部恢复。

## 状态文件

扩展每秒读 `~/.agent-traffic-light/agents.json`，由 HoneyComb 检测程序写
（见 `modules/ai-detector/module_docs/contract.md` 「红绿灯」）：

```json
{"updated_at": 1790000000, "agents": [{"key": "codex:7f3a", "label": "garden", "state": "working", "detail": ""}]}
```

`updated_at` 为 Unix 秒。界面配置存在 `~/.agent-traffic-light/shell-config.json`。
