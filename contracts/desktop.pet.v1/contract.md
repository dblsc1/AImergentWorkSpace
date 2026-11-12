# desktop.pet.v1 —— 桌宠接口

> **状态**：v1，仓主 2026-11-07 批准。批准后只增不改不删；不兼容的改动发 `desktop.pet.v2`。
>
> - 提供方：`modules/ai-detector`（桥接，「二」）；`modules/desktop-pet`（Electron 桌宠程序，宠物与浮窗，「三」「四」）。
> - 美术实现（「三」）：开源版 = 我们的桌宠程序加 ai-pet-7in1 素材；商用版 = 仓主的日程助手 API（以后接，不在本仓）。
>
> **仓主原话（2026-11-07）**：「把美术独立成一个接口就好了，开源版就用 dsh pet，我们商用的部分暂定我们自己做的日程助手 api，
> 等以后真的商用了，用现在预留的接口改美术就好。」
>
> 「pet 本来只需要动作，红点，悬停放大等可以作为独立模块」「桌宠直接把 dshpet 美术素材打包进去，只不过留一个新桌宠的接口」。
>
> 一句话：HoneyComb 只说「发生了什么、要说哪句话」，**怎么画、画成谁**全在美术实现里。换美术不改 HoneyComb。
> 桌宠只管动作和气泡；红点、悬停放大、拖放归浮窗（「四」）。

```yaml
provides:
  - id: desktop.pet.v1
    summary: >
      桌宠事件 PetEvent（focus / agentWaiting / agentDone / alert / alertCleared，带一句现成的中文 text）；
      ai-detector 每 10 秒读一次 GET /api/core/views/current 与本机健康状态，有变化才发；
      美术实现：http（把 PetEvent POST 给本机桌宠程序或以后的日程助手 API），或可选的 dsh-pet。缺省关。
      桌宠程序 modules/desktop-pet（Electron，一个透明置顶窗，宠物 + 浮窗）；素材 ai-pet-7in1 全套，自动下载、不进仓库。
      宠物包 pack.json + 宠物包对照（按代理工具选宠物），蜂巢页与桌面共用。
      浮窗：宠物右上角红点，悬停展开登录后的蜂巢缩略页 /hive/mini，把当前窗口拖进项目。
```

## 一、桌宠事件 `PetEvent`（规范性）

```jsonc
{ "v": 1,
  "type": "focus",                 // 见下表
  "at": "2026-11-07T10:12:30+08:00",
  "text": "在写提示词（garden）",   // 现成的一句中文，≤ 60 个字符；美术实现可以直接念 / 显示
  "mood": "working",               // "working" | "idle" | "waiting" | "happy" | "worried"：给美术挑动作用
  "data": { /* 每种 type 的字段，见下表 */ } }
```

| `type` | 什么时候发 | `data` | `text` 的样子 | `mood` |
|---|---|---|---|---|
| `focus` | `views/current` 的 `focus` 认出的任务 / 项目变了，或从离开回到电脑前 | `{taskName, projectName, displayName}`（认不出的为 `null`） | 「在写提示词（garden）」；认不出时「在 garden」；只有显示名时「在看 + displayName」，如「在看 figma.com · 首页改版」 | `working` |
| `focus` | 进入离开（`state: "afk"`） | `{afk: true}` | 「休息一下」 | `idle` |
| `agentWaiting` | 某条代理运行进入等人（`waiting_input` / `waiting_permission`） | `{agent, reason: "input" \| "permission"}` | 「garden 在等你回话」/「garden 在等你批权限」 | `waiting` |
| `agentDone` | 某条代理运行结束 | `{agent}` | 「garden 干完了」 | `happy` |
| `alert` | 检测程序本机出问题：ActivityWatch 连不上、上传连续失败 ≥ 3 轮、服务端连不上 | `{kind: "aw" \| "upload" \| "server"}` | 「记录断了：ActivityWatch 没在跑」等 | `worried` |
| `alertCleared` | 上一条 `alert` 的问题恢复 | `{kind}` | 「记录恢复了」 | `happy` |
| `dialog` | 想让桌宠开口并等人回话（追加，2026-11-08；发送方暂无，留给 ai-detector / 服务端以后用） | `{buttons?: [{id, label}], input?: {placeholder}}`：按钮至多 3 个，`id` 与 `label` 非空字符串；`data` 必须是对象，其余键忽略 | 要说的话（≤ 60 个字符，同上限） | 任意 |

- **不发**：窗口原始标题、网址、路径、任何令牌。`displayName` 读 `views/current.focus.displayName`（nexus-core v2.44 追加，`onboarding.v1`「二」的同一个值，已脱敏、看得懂的才有）。
- `text` 超过 60 个字符截到 59 个字符加 `…`。
- 同一种 `type` 同样的 `data` 不重复发；`focus` 至少间隔 60 秒（防抖：窗口来回切不刷屏；被压掉的 `focus` 事件丢弃，不补发）。
- 发失败只写本机日志，不重试、不影响检测程序其他功能。

## 二、桥接（规范性，`modules/ai-detector`）

- 配置（本机 `config.json` 追加，缺省整节不存在 = 关）：

  ```jsonc
  "pet": { "enabled": true,
           "renderer": "http",                       // "http" | "dsh-pet"
           "url": "http://127.0.0.1:47340/pet-event", // http：接收 PetEvent 的地址（缺省 = 我们的桌宠程序）；dsh-pet：其独立模式地址，如 http://127.0.0.1:3080
           "token": null,                            // 只给 http 用：有就带 Authorization: Bearer <token>
           "petId": null,                            // 只给 dsh-pet 用：哪只宠物，null = 它的第一只
           "moods": { "working": null, "idle": null, "waiting": null, "happy": null, "worried": null } }  // 对 dsh-pet 的工作状态名（取值见其 openapi.yaml）；某项为 null = 跳过 work-status，只换气泡
  ```

- 数据来源：每 10 秒 `GET /api/core/views/current`（带本机设备令牌，`read` 范围即可）取 `focus` 与 `agents[]`；`alert` 来自本机健康状态（ai-detector 契约「检测程序健康」）。
- 状态机：启动后第一轮只建立基线，不发 `agentWaiting` / `agentDone`；运行从 `agents[]` 消失、且最近一次读到的阶段不是失联 → `agentDone`；`views/current` 连续 3 轮失败 → `alert` `server`。
- 桥接不画宠物，本机配置里没有宠物包对照；宠物包对照只有服务端账号设置 `pet.packsByTool` 一份（「三」）。
- `url` 必须是本机回环（`localhost` / `127.0.0.0/8` / `::1`）或 `https://`；不合规不发并写日志（同 `activity.classifier.v1` 的规矩）。不跟随重定向。
- **HoneyComb 的设备令牌绝不发给美术实现**。

## 三、美术实现与宠物包（规范性）

仓主 2026-11-07 定：桌宠做成我们自己的 Electron 程序（「三·1」），素材用 Houraiji9/ai-pet-7in1 的全套，安装器自动下载。

### 三·1 桌宠程序 `modules/desktop-pet`（开源版缺省）

- Electron。**一个**透明、无边框、置顶的窗口，放宠物和浮窗（「四」）两块；代码分两块（`pet` 与 `float`），窗口共用。
- 点穿：窗口缺省忽略鼠标；指针落在宠物不透明像素或浮窗上时才接收（前端命中测试后切换）。
- 平台：Windows、macOS、Linux（Linux 本版只支持 X11 / XWayland）。macOS 做成不抢焦点、无 Dock 图标的常驻程序。
- 收 PetEvent：只在 `127.0.0.1:47340` 监听 `POST /pet-event`，要 `Authorization: Bearer <本机桌宠令牌>`（程序第一次启动生成，存在程序数据目录；安装器把它写进 ai-detector 的 `pet.token`）。没带或不对 `401`。
  所以 ai-detector 对它用「http」美术实现：`pet.renderer: "http"`、`pet.url: "http://127.0.0.1:47340/pet-event"`。
- 收到 PetEvent：按 `mood` 从当前宠物包挑一段动画播（同一 mood 有多段时随机，避开刚播过的）；`text` 显示成头顶气泡 6 秒。
- 宠物包对照：按 `views/current` 里人此刻在看的那条运行的 `tool` 选包（`pet.packsByTool`，「宠物包与对照」）；没有就用 `*`。
- 托盘菜单：换宠物、大小、开机自启、退出。

### 三·3 对话框（追加，2026-11-08）

- 宠物旁的一块通用气泡面板，所有「桌宠跟人说话」都走它：文字、至多 3 个按钮、可选一行输入框、右上角 `×`。
- 主进程接口 `dialog.say({text, buttons?: [{id, label}], input?: {placeholder}, tag?})` → `Promise`：点了按钮 = 该按钮的 `id`；有输入框时在框里按 Enter = 输入的文字（去首尾空格，非空）；点 `×` / Esc / 被撤回 / 消息不合格 = `null`。
- 排队：同一时刻只显示一条，其余按先后排队；一条答完才显示下一条。`dialog.withdraw(tag)` 撤回某个 `tag` 的所有消息（排队的与显示中的，结果 `null`）。文字至多 200 个字符，按钮标签至多 12 个字符，超出截断，超过 3 个按钮的多余的丢弃。
- 键盘：打开时焦点落在输入框或第一个按钮，Tab 在面板内切换，Esc 关闭。窗口点穿照旧：面板显示时它的矩形与宠物、红点、浮窗一样算「命中」，面板之外仍然点穿；面板显示期间窗口按需变大（宽 ≥ 280 像素）。
- 收到 `type: "dialog"` 的 PetEvent（「一」）就 `say` 一条；本版**不**把答案回传给发送方（发送即忘）。
- **点宠物**（按下到抬起挪动 < 5 像素；拖动不算）：打开一个小菜单，三个按钮「打开蜂巢」（用默认浏览器开 `<server>/hive/`）、「再介绍一遍」（重说「三·4」的介绍）、「关闭」；对话框已在显示时点宠物无反应。

### 三·4 桌宠新手引导（追加，2026-11-08，仓主：「进入自动说创建分区/项目/任务，然后介绍一下蜂巢就可以。其他的等用户点了再说。」）

- 程序启动时，本机配了设备令牌才做：用设备令牌读 `GET /api/core/views/tree`（`read` 范围）。没配令牌 = 不自动引导（点宠物的菜单照常能用）。
- 第一次读到**没有分区**：说一句「先去蜂巢建一个分区，再建项目和任务吧。」，按钮「打开蜂巢」；之后每 10 秒再读一次，不重复说。第一次读到**已有分区**（老账号）：不说话，直接记为完成。读失败：不说话，下一轮再试。
- 读到分区、且某个项目下有任务：撤回上面那句，依次说三句，前两句按钮「下一句」，最后一句「知道了」：
  1. 「左边是你的 AI 代理，右上角是你的时钟。」
  2. 「左下角是还没归类的窗口托盘。」
  3. 「把时间元素拖到格子上，就记进那个任务。」
  介绍中途点 `×` 就停；然后停止轮询并把完成时刻存进程序数据目录的 `onboarding.json`（`{"doneAt": ISO 时刻}`）；有这个文件就不再自动引导。
- 逻辑在与 Electron 无关的 `modules/desktop-pet/pet-onboarding.js`，对话框队列在 `dialog-core.js`（都可单测）。网页端的引导见 `onboarding.v1`「七」。

### 三·2 素材：ai-pet-7in1 全套，自动下载

- 来源固定到一个提交：<https://github.com/Houraiji9/ai-pet-7in1> `08f2c6f3b2771f2c65ff4d6c14c8dfecba9647bc`，取 `assets/<角色>/webm/`（GPT、Claude、Gemini、GLM、Kimi、Grok）与 DeepSeek 娘（`app/assets/deepseek/`，来自 dsh-pet）。换提交 = 改契约追加一行。
- **素材不进本仓 git、不进发布包**。用户机器：桌宠程序第一次启动在后台下载全套到程序数据目录 `pets/`；服务端：安装器下载同一套到 `/hive/pets/` 对应的卷，蜂巢页从那里取。下载断了下次续；下完校验每个文件的大小与 SHA-256（清单随程序发布）。
- 没下完之前：宠物用一个内置的简单占位形象（我们自己画，AGPL），不缺功能。
- 许可（上游 `LICENSE-ASSETS.md`）：角色素材 CC BY-NC-SA 4.0，原作 ZipZipPipe《大 AI 与小 AI 们》，改编参考图 ApprenTice；DeepSeek 娘动画来自 dsh-pet，禁止商用。所以：
  - 设置页「关于」、仓库 `NOTICE`、README 写明以上署名与许可链接；
  - 任何收费产品、收费服务的页面与宣传里**不用**这些素材；
  - 我们改过的素材同样按 CC BY-NC-SA 4.0 发。
- 服务端下载由 compose 一次性服务 `pets-download` 做，在 profile `pets` 里，安装器在主栈起好后单独起它，web 不等它。

### 宠物包与对照

- **宠物包** = 程序数据目录 `pets/<种类>/` 下一个 `pack.json` 加动画文件。下载时由程序按上游目录生成 `pack.json`：

  ```jsonc
  { "kind": "gpt", "name": "GPT 龙娘",
    "moods": { "working": ["…webm"], "waiting": ["…"], "idle": ["…"], "happy": ["…"], "worried": ["…"] },
    "license": "CC BY-NC-SA 4.0", "credits": "ZipZipPipe《大 AI 与小 AI 们》；ApprenTice", "source": "https://github.com/Houraiji9/ai-pet-7in1" }
  ```

  上游动画名到 mood 的对照表随程序发布（`modules/desktop-pet/mood-map.json`），每个 mood 至少一段。
  2026-11-08 追加：`moods` 里另有三组交互动作 `click`（点击回应）、`drag`（被拖起）、`walk`（走路），上游缺的段不列，可以为空数组；只认五个 mood 的读方忽略它们。
- 蜂巢页（`hive.stage.v1`）读服务端同样的 `pack.json`。
- **宠物包对照**（服务端账号设置 `activity/settings.pet.packsByTool`，`onboarding.v1`「四」）：缺省 `{ "claude-code": "claude", "codex": "gpt", "gemini-cli": "gemini", "*": "deepseek" }`；按运行的 `tool` 选包，查不到用 `*`。
- 每个宠物包的 `license` / `credits` / `source` 必须填（许可不明写 `unknown`）。

### dsh-pet（可选）

已装 dsh-pet 的用户可让 ai-detector 改发给它（`pet.renderer: "dsh-pet"`）。每条 PetEvent：

1. `POST /dsh-pet-7340/work-status`：工作状态由 `mood` 对照（`pet.moods`），任务文字 = `text`；`moods` 为 `null` 时跳过这一步；
2. 要冒气泡的类型（`agentWaiting`、`agentDone`、`alert`、`alertCleared`）再 `lock` → `broadcast` → `lock/release`；
3. `focus` 的 `afk` 用 `DELETE /dsh-pet-7340/work-status`。

请求体以上游 `openapi.yaml` 为准（实现时放进 `third_party/dsh-pet/openapi.yaml` 并引用）。任何一步失败就放锁（尽力）并结束这一条。dsh-pet 的本机接口没有鉴权。
上游约束（`third_party/dsh-pet/openapi.yaml`，dsh-pet 0.4.5）：`work-status` 的 `task` 至多 40 个码点（超了截 39 加 `…`，气泡仍发完整 `text`）；`lock` / `broadcast` 带 `surface: "any"`；`petId` 为 `null` 时先 `GET /dsh-pet-7340/config` 取 `main` 的第一只，取不到用 `main`；上游以 `200 {ok: false}` 表示失败，同样按失败处理。

### http（商用 / 自定义）

把 PetEvent 原样 `POST` 到 `url`（`Content-Type: application/json`，有 `pet.token` 就带 `Authorization: Bearer`），`2xx` 算送达。我们的桌宠程序（「三·1」）与仓主以后的日程助手 API 都按本节接入。


### 连接脚本 honeycomb-connect（追加，2026-11-08）

- 用户电脑上跑的连接脚本：`release/honeycomb-connect.sh`（Linux / macOS）与 `release/honeycomb-connect.ps1`（Windows），随 Release 发布。
- 输入：服务器地址、设备令牌。令牌只从环境变量或交互输入读，不收命令行参数。
- 可重复跑，每次结果相同；做这几件事：
  1. 终端标题（`onboarding.v1`「九」）；
  2. 桌宠令牌写进 ai-detector 配置的 `pet.token`（桌宠程序数据目录里已有令牌才写，没有就提示先启动一次桌宠）；
  3. 服务器地址与设备令牌写进桌宠程序的 `config.json`；
  4. 不下载、不安装 ai-detector 与桌宠程序本体。
- `--uninstall`：删掉终端标题那段和它写进的键，别的不动。

#### 一键安装（追加，2026-11-08）

上面「不下载、不安装 ai-detector 与桌宠程序本体」由本节取代：同一个脚本现在把客户端这一侧装齐。仍然可重复跑、不要管理员 / sudo、不收令牌命令行参数、任何输出不打印令牌；每步打一行中文结果（已完成 / 跳过 / 该做什么），最后列「已装好」与「还要你手工做」。脚本随 Release 发布，发布时 `__TAG__` 替换成版本标签（与 `honeycomb-install` 同）；没替换（直接从仓库跑）时下载类步骤跳过并说明。

选项：`--no-detector` `--no-hooks` `--no-pet` `--no-shell`（PowerShell：`-NoDetector` `-NoHooks` `-NoPet` `-NoShell`），对安装与 `--uninstall` 同样生效。环境变量 `HONEYCOMB_ASSETS=<目录>`：从该目录按下面的文件名取文件，不联网（测试 / 离线用）。

步骤（顺序固定；一步失败不影响后面）：

1. 终端标题：bash / zsh / PowerShell 写进配置文件的标记段；fish 写独立文件 `~/.config/fish/conf.d/honeycomb-title.fish`；PowerShell 5.1 与 7 的配置文件各写一份（另一份只在其文件夹已存在时）；已有的 `PROMPT_COMMAND` / `prompt` 保留（追加，不覆盖）。Windows 本地脚本策略为 Restricted 时不改策略，只在「还要你手工做」里给命令。
2. ai-detector：从同标签 Release 下载 `ai-detector-<tag>-<os>-<arch>[.exe]`，按 `ai-detector-<tag>-SHA256SUMS.txt` 校验（对不上就丢弃、不安装）；装到用户目录（Windows `%LOCALAPPDATA%\HoneyComb\ai-detector.exe`；macOS `~/Library/Application Support/HoneyComb/ai-detector`；Linux `~/.local/share/honeycomb/ai-detector`）；`init`；把 `cockpitUrl`、`deviceToken` 写进它的配置后 `enable`；注册自启（Windows：「启动」文件夹里的 `honeycomb-ai-detector.vbs`；macOS：`ai-detector autostart install` 写 LaunchAgent 后 `launchctl bootstrap gui/<uid>`；Linux：`autostart install` 写的 XDG 自启文件）；立刻启动。
3. ActivityWatch：检测 `localhost:5600` 与常见安装位置；缺就按系统处理（Windows 有 winget、macOS 有 brew 时询问 y/N 再装，否则给 <https://activitywatch.net/downloads/>）；打印 aw-watcher-web 的 Chrome / Firefox 商店链接（只能手工装）；macOS 提示到「隐私与安全性 → 辅助功能」放行 ActivityWatch。
4. Claude Code 钩子：需要 Python 3（没有就跳过并说明）；从 Release 取 `agent-hooks-claude_hook.py`、`agent-hooks-cockpit_client.py`，按 `agent-hooks-SHA256SUMS.txt` 校验，放到安装目录下 `agent-hooks/`；`url`、`token`（设备令牌）写进 `agent-hooks.json`；把 `tools/agent-hooks/README.md`「Claude Code 钩子」两节的条目（`SessionStart`、`SessionEnd`（`timeout` 5）、其余九个事件 `async: true`）合并进 `~/.claude/settings.json`（或 `$CLAUDE_CONFIG_DIR`）——别人的钩子与别的设置不动；已装过先摘掉我们的再加，所以重复跑结果相同；`~/.claude` 不存在就跳过。Codex 等没有等价钩子，不装。
5. 桌宠（Windows、macOS）：取 `honeycomb-pet-<tag>-win-x64.zip` / `-mac-arm64.zip` / `-mac-x64.zip`，按 `honeycomb-pet-<tag>-SHA256SUMS.txt` 校验；Windows 解压到 `%LOCALAPPDATA%\HoneyComb\pet\`，macOS 的 `.app` 装成 `~/Applications/HoneyComb Pet.app`；写桌宠 `config.json`（`server`、`deviceToken`）；注册自启（Windows「启动」文件夹的 `honeycomb-desktop-pet.vbs`；macOS `~/Library/LaunchAgents/com.honeycomb.desktop-pet.plist`）；启动；等桌宠生成 `pet-token`（最多 10 秒）后写进 ai-detector 配置的 `pet.token`。Linux 没有安装包，跳过。
6. 没有设备令牌时：程序照装，但不写令牌类配置、不启用同步，并提示设好令牌再跑一次。

`--uninstall` 逆向撤回上面全部：停进程、删自启文件（含 `launchctl bootout`）、删安装目录里的程序与钩子脚本、摘掉 `settings.json` 里我们加的钩子条目、删各配置里写入的键（`deviceToken`、`pet.token`、桌宠 `server` / `deviceToken`、`agent-hooks.json` 的 `url` / `token`）、删终端标题段。保留：采集数据与桌宠素材（数据目录）、ActivityWatch 与浏览器扩展（用户自己装的）。

Release 附件（连接脚本用的文件名）：`ai-detector-<tag>-<os>-<arch>[.exe]` + `ai-detector-<tag>-SHA256SUMS.txt`；`agent-hooks-claude_hook.py`、`agent-hooks-cockpit_client.py` + `agent-hooks-SHA256SUMS.txt`；`honeycomb-pet-<tag>-<win-x64|mac-arm64|mac-x64>.zip` + `honeycomb-pet-<tag>-SHA256SUMS.txt`。程序未签名：Windows SmartScreen、macOS Gatekeeper 的提示属预期。

## 四、浮窗（规范性，`modules/desktop-pet` 的 `float` 块）

和宠物在同一个窗口里，**只做三件事**：在宠物右上角画红点、鼠标悬停时展开蜂巢缩略页、收起。

- **红点**：每 10 秒用本机设备令牌（`read` 范围，安装器写进桌宠程序的配置）调 `GET /api/core/activity/live-ask`（`onboarding.v1`「三」）；`ask` 非空就亮红点，空就灭。
- **悬停**：在宠物旁展开一块面板（窗口按需变大），内容是 HoneyComb 的页面 `/hive/mini`（嵌入的网页，用人登录后的会话，会话存在程序自己的会话分区里；不用设备令牌）：
  - 顶上一张卡：`displayName` + 已停留时长（= `focusedSeconds` 取整分钟，如 `6 分钟`）；两个动作：拖进下面的某个项目格子（= `blocks/assign {projectId, remember: true}`，任务由 AI 分），或「不管它」（= `live-ask/answer`）；
  - 下面是蜂巢缩略图，只有项目名；
  - 做完一件事，红点灭，面板 2 秒后收起。没登录时页面显示登录。
- 鼠标移开 800ms（可配）后面板收起。
- 面板开着时 `ask` 变 `null`（人切走了）：卡显示「这个窗口已经离开」，2 秒后收起。
- `assign` 返回 `409`（`seq` 已变）：重新取 `live-ask`，块还在就用新 `seq` 重试一次，否则收起。
- 红点轮询连续 3 轮失败（含令牌失效）熄灭，之前保持原状。
- 红点、轮询、收起计时这些逻辑写在与 Electron 无关的 `modules/desktop-pet/float-core.js`（纯函数，可单测）；`/hive/mini` 是普通网页，浏览器里也能开。

## 五、本版不做（有意的）

- 桌宠上画泳道。
- 每日小结、待办提醒。

## 六、变更记录


| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-07 | v1 草案 | 首版，待仓主批准 |
| 2026-11-07 | v1 草案二 | 改用 dsh-pet 工作状态接口；素材随包附带并写明许可；宠物包与对照；浮窗独立模块 |
| 2026-11-07 | v1 草案三 | 按 TDD 澄清（G-01–G-30）补齐边界；手动计时改为先计时后拖放 |
| 2026-11-07 | v1 | 仓主批准；桌宠改为自己的 Electron 程序 `modules/desktop-pet`，宠物与浮窗同窗；素材 ai-pet-7in1 全套、自动下载、不进仓库 |
| 2026-11-08 | v1（追加） | dsh-pet 发送按上游 0.4.5 的约束（task 40 码点、surface、petId 缺省、`ok:false`） |
| 2026-11-08 | v1（追加） | 对话框组件（「三·3」）、PetEvent `dialog`（「一」）、点宠物小菜单、桌宠新手引导（「三·4」） |
| 2026-11-08 | v1（追加） | 桌宠安装包（「七」） |
| 2026-11-08 | v1（追加） | 连接脚本一键安装：ai-detector、ActivityWatch 检测、Claude Code 钩子、桌宠的下载校验安装与自启，`--uninstall` 全撤回；选项与 `HONEYCOMB_ASSETS`；Release 附件文件名 |
| 2026-11-08 | v1（追加） | 桌宠安装包 mac-arm64 改在 macos-15 runner 上打（macos-14 起不来） |
| 2026-11-12 | v1（追加） | 静态形象包与包清单 `index.json`（「三·5」）；ZipZipPipe 另外 6 个角色的立绘 |

## 七、桌宠安装包

每个 GitHub Release 附（`<tag>` 即发版标签，如 `v0.6.0`）：

| 文件 | 内容 |
|---|---|
| `honeycomb-pet-<tag>-win-x64.zip` | 解压即用的应用目录，内含 `HoneyComb Pet.exe` |
| `honeycomb-pet-<tag>-mac-arm64.zip` | 内含 `HoneyComb Pet.app`（Apple 芯片） |
| `honeycomb-pet-<tag>-mac-x64.zip` | 内含 `HoneyComb Pet.app`（Intel） |
| `honeycomb-pet-<tag>-linux-x64.zip` | 应用目录，内含可执行文件 `HoneyComb Pet` |
| `honeycomb-pet-<tag>-SHA256SUMS.txt` | 以上 zip 的 sha256 |

- 来源：`.github/workflows/pet-packages.yml` 的矩阵（windows-latest、macos-14、macos-15-intel、ubuntu-latest）用 `modules/desktop-pet` 的 `npm run package -- --platform=<win32|darwin|linux> --arch=<x64|arm64> --tag=<tag>`（`@electron/packager`）生成；`release.yml` 复用该工作流并把产物与 SHA256SUMS 挂到 Release。该工作流也可手动触发，只出 artifact、不发版。
- 不签名、不公证：系统会弹安全提示，属预期。
- 不含宠物素材，首次启动照旧下载。
- 以上文件名是对安装器的接口，只增不改。

## 三·5 静态形象包与包清单（规范性，2026-11-12 追加，仓主：「把这些新素材也弄进来」）

- **静态形象包** = `pets/<种类>/pack.json` 加一张图：`{ "kind", "name", "image": "<文件名>", "moods": {}, "license", "credits", "source" }`。没有动画；只认 `moods` 的读方（桌宠程序）当它没有动作，本版桌宠程序不下载也不使用它们。
- 来源固定到一个提交：<https://github.com/shuaizenge/ai-girls> `dcd69f5f4f2dff1a9c0031e82deb49e0fe949c76`，ZipZipPipe 的 6 个角色各一张原图：千问（`qwen`，Q 版）、Mistral（`mistral`）、混元（`hunyuan`）、Llama（`llama`）、MiniMax（`minimax`）、Seed / 豆包（`seed`）。清单 `modules/desktop-pet/manifest-stills.json`（大小 + SHA-256）；主地址 raw.githubusercontent.com 连不上时换 jsDelivr 镜像（同一个提交，同样校验）。换提交 / 加角色 = 改清单并在这里追加一行。
- 同「三·2」：**不进本仓 git、不进发布包**，只由服务端 `pets-download` 下到 `/hive/pets/` 的卷；原图原样保存，抠底在页面上做，不另存改过的图。
- 许可：上游 `NOTICE.md` 记的是「作者声明开放分享、允许再创作」，没有核实到统一的许可证条文；按非商用对待，`pack.json` 的 `license` 照此写。任何收费产品、收费服务的页面与宣传里不用。署名 ZipZipPipe。
- **包清单** `pets/index.json`：`pets-download` 每次跑完重写，`{ "packs": [ { "kind", "name", "cover": "<idle 的第一段>" | "image": "<文件名>" } ] }`，按 `kind` 排序，只列有 `pack.json` 的目录。蜂巢页的形象挑选表读它（`hive.stage.v1`「十一」）。
- 老安装升级后要再跑一次 `pets-download` 才有清单和新形象；没跑之前挑选表显示「形象素材还没下好」，已有的宠物照常。
