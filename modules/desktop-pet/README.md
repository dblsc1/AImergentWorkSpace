# HoneyComb 桌宠（desktop-pet）

Electron 程序：一个透明、无边框、置顶的窗口，放宠物和浮窗。接口见 `contracts/desktop.pet.v1/contract.md`「三·1」「四」。

## 怎么跑

```sh
cd modules/desktop-pet
npm install
npm start      # 即 electron .
npm test       # 纯逻辑单测（node:test，不需要显示环境）
```

Linux 本版只支持 X11 / XWayland。macOS 不抢焦点、无 Dock 图标。

## 配置

程序数据目录下的 `config.json`（Linux `~/.config/honeycomb-desktop-pet/`，以 Electron `userData` 为准）：

| 键 | 缺省 | 说明 |
|---|---|---|
| `server` | `http://127.0.0.1:8800` | HoneyComb 服务器；浮窗打开 `<server>/hive/mini` |
| `deviceToken` | `""` | 本机设备令牌（`read` 范围）：每 10 秒读 `live-ask` 亮红点、读 `views/lanes` 选宠物包；每 10 分钟读 `activity/settings` 的 `pet.packsByTool`（403 用缺省对照）；空 = 都不读 |
| `pet` | `deepseek` | 默认宠物（人没在看代理时用，托盘「默认宠物」改）；空 = 内置形象 |
| `size` | `200` | 宠物边长（像素），托盘菜单可改 |
| `autostart` | `false` | 开机自启（Windows / macOS） |
| `downloadAssets` | `true` | 启动时后台下载素材 |

同目录的 `pet-token` 是本机桌宠令牌（第一次启动生成）。ai-detector 往 `http://127.0.0.1:47340/pet-event` 发事件时带 `Authorization: Bearer <pet-token 的内容>`。

## 素材

素材不进仓库：第一次启动从 [Houraiji9/ai-pet-7in1](https://github.com/Houraiji9/ai-pet-7in1) 固定提交 `08f2c6f3b2771f2c65ff4d6c14c8dfecba9647bc` 下载到数据目录 `pets/`（`manifest.json` 记了每个文件的大小与 SHA-256，断了下次续）。没下完用内置占位形象。

- 角色素材：CC BY-NC-SA 4.0。原作 ZipZipPipe《大 AI 与小 AI 们》，改编参考图 ApprenTice。
- 蜂巢页另用 6 张静态立绘（千问、Mistral、混元、Llama、MiniMax、Seed）：原作 ZipZipPipe，取自 [shuaizenge/ai-girls](https://github.com/shuaizenge/ai-girls) 固定提交，同样不进仓库；作者声明开放分享、允许再创作，按非商用对待。
- DeepSeek 娘动画来自 [dsh-pet](https://github.com/PC2005-cloud/dsh-pet)，**禁止商用**。
- 任何收费产品、收费服务不得使用这些素材。
