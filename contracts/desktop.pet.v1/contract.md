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

### 三·2 素材：ai-pet-7in1 全套，自动下载

- 来源固定到一个提交：<https://github.com/Houraiji9/ai-pet-7in1> `08f2c6f3b2771f2c65ff4d6c14c8dfecba9647bc`，取 `assets/<角色>/webm/`（GPT、Claude、Gemini、GLM、Kimi、Grok）与 DeepSeek 娘（`app/assets/deepseek/`，来自 dsh-pet）。换提交 = 改契约追加一行。
- **素材不进本仓 git、不进发布包**。用户机器：桌宠程序第一次启动在后台下载全套到程序数据目录 `pets/`；服务端：安装器下载同一套到 `/hive/pets/` 对应的卷，蜂巢页从那里取。下载断了下次续；下完校验每个文件的大小与 SHA-256（清单随程序发布）。
- 没下完之前：宠物用一个内置的简单占位形象（我们自己画，AGPL），不缺功能。
- 许可（上游 `LICENSE-ASSETS.md`）：角色素材 CC BY-NC-SA 4.0，原作 ZipZipPipe《大 AI 与小 AI 们》，改编参考图 ApprenTice；DeepSeek 娘动画来自 dsh-pet，禁止商用。所以：
  - 设置页「关于」、仓库 `NOTICE`、README 写明以上署名与许可链接；
  - 任何收费产品、收费服务的页面与宣传里**不用**这些素材；
  - 我们改过的素材同样按 CC BY-NC-SA 4.0 发。
- 服务端下载由 compose 一次性服务 `pets-download` 做，web 不等它。

### 宠物包与对照

- **宠物包** = 程序数据目录 `pets/<种类>/` 下一个 `pack.json` 加动画文件。下载时由程序按上游目录生成 `pack.json`：

  ```jsonc
  { "kind": "gpt", "name": "GPT 龙娘",
    "moods": { "working": ["…webm"], "waiting": ["…"], "idle": ["…"], "happy": ["…"], "worried": ["…"] },
    "license": "CC BY-NC-SA 4.0", "credits": "ZipZipPipe《大 AI 与小 AI 们》；ApprenTice", "source": "https://github.com/Houraiji9/ai-pet-7in1" }
  ```

  上游动画名到 mood 的对照表随程序发布（`modules/desktop-pet/mood-map.json`），每个 mood 至少一段。
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
