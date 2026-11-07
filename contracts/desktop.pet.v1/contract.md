# desktop.pet.v1 —— 桌宠接口

> **状态**：v1 草案，待仓主批准。批准后只增不改不删；不兼容的改动发 `desktop.pet.v2`。
>
> - 提供方：`modules/ai-detector`（每台电脑上已经在跑的程序，桥接在它里面，缺省关）。
> - 美术实现（「三」）：开源版 = dsh-pet；商用版 = 仓主的日程助手 API（以后接，不在本仓）。
>
> **仓主原话（2026-11-07）**：「把美术独立成一个接口就好了，开源版就用 dsh pet，我们商用的部分暂定我们自己做的日程助手 api，
> 等以后真的商用了，用现在预留的接口改美术就好。」
>
> 一句话：HoneyComb 只说「发生了什么、要说哪句话」，**怎么画、画成谁**全在美术实现里。换美术不改 HoneyComb。

```yaml
provides:
  - id: desktop.pet.v1
    summary: >
      桌宠事件 PetEvent（focus / agentWaiting / agentDone / alert / alertCleared，带一句现成的中文 text）；
      ai-detector 每 10 秒读一次 GET /api/core/views/current 与本机健康状态，有变化才发；
      美术实现二选一：dsh-pet（本机 HTTP，锁 + 气泡 + 动作）或 http（把 PetEvent 原样 POST 给配置的地址）。缺省关。
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
| `focus` | `views/current` 的 `focus` 认出的任务 / 项目变了，或从离开回到电脑前 | `{taskName, projectName, displayName}`（认不出的为 `null`） | 「在写提示词（garden）」；认不出时「在 garden」；只有显示名时「在看 github.com」 | `working` |
| `focus` | 进入离开（`state: "afk"`） | `{afk: true}` | 「休息一下」 | `idle` |
| `agentWaiting` | 某条代理运行进入等人（`waiting_input` / `waiting_permission`） | `{agent, reason: "input" \| "permission"}` | 「garden 在等你回话」/「garden 在等你批权限」 | `waiting` |
| `agentDone` | 某条代理运行结束 | `{agent}` | 「garden 干完了」 | `happy` |
| `alert` | 检测程序本机出问题：ActivityWatch 连不上、上传连续失败 ≥ 3 轮、服务端连不上 | `{kind: "aw" \| "upload" \| "server"}` | 「记录断了：ActivityWatch 没在跑」等 | `worried` |
| `alertCleared` | 上一条 `alert` 的问题恢复 | `{kind}` | 「记录恢复了」 | `happy` |

- **不发**：窗口原始标题、网址、路径、任何令牌。`displayName` 用 `onboarding.v1`「二」的同一个值（已脱敏、看得懂的才有）。
- 同一种 `type` 同样的 `data` 不重复发；`focus` 至少间隔 60 秒（防抖：窗口来回切不刷屏）。
- 发失败只写本机日志，不重试、不影响检测程序其他功能。

## 二、桥接（规范性，`modules/ai-detector`）

- 配置（本机 `config.json` 追加，缺省整节不存在 = 关）：

  ```jsonc
  "pet": { "enabled": true,
           "renderer": "dsh-pet",                    // "dsh-pet" | "http"
           "url": "http://127.0.0.1:3080",           // dsh-pet：独立模式的地址；http：接收 PetEvent 的地址
           "token": null,                            // 只给 http 用：有就带 Authorization: Bearer <token>
           "petId": null,                            // 只给 dsh-pet 用：哪只宠物，null = 它的第一只
           "moods": { "working": null, "idle": null, "waiting": null, "happy": null, "worried": null } }  // dsh-pet 的动作名，null = 不点播动作
  ```

- 数据来源：每 10 秒 `GET /api/core/views/current`（带本机设备令牌，`read` 范围即可）取 `focus` 与 `agents[]`；`alert` 来自本机健康状态（ai-detector 契约「检测程序健康」）。
- `url` 必须是本机回环（`localhost` / `127.0.0.0/8` / `::1`）或 `https://`；不合规不发并写日志（同 `activity.classifier.v1` 的规矩）。不跟随重定向。
- **HoneyComb 的设备令牌绝不发给美术实现**。

## 三、美术实现（规范性）

### dsh-pet（开源版缺省）

dsh-pet（<https://github.com/PC2005-cloud/dsh-pet>，代码 MIT；**素材禁止商用、公开二创须署名**）独立模式：`npx dsh-pet-standalone`，
本机 HTTP，前缀 `/dsh-pet-7340`。每条 PetEvent：

1. `POST /dsh-pet-7340/lock?pet=<petId>` 取锁（拿到 `lockId`）；
2. `POST /dsh-pet-7340/broadcast?pet=…` 把 `text` 写进气泡；
3. `moods[mood]` 非空时 `POST /dsh-pet-7340/anim?pet=…` 点播该动作；
4. `POST /dsh-pet-7340/lock/release?pet=…` 放锁。

- 任何一步失败就放锁（尽力）并结束这一条。dsh-pet 没在跑 = 连接失败，按「一」的发失败处理。
- **不把 dsh-pet 的任何素材打进 HoneyComb 的发布包**；由用户自己装（安装器只在用户同意时跑 `npx dsh-pet-standalone`，并写明素材许可）。
- 已知：dsh-pet 的本机接口没有鉴权，本机任何程序都能调。

### http（商用 / 自定义）

把 PetEvent 原样 `POST` 到 `url`（`Content-Type: application/json`），`2xx` 算送达。仓主的日程助手 API 以后按本节实现即可接入。

## 四、本版不做（有意的）

- 桌宠上画泳道（dsh-pet 只有气泡；以后由美术实现自己读 `views/lanes` 画，或发 `desktop.pet.v1` 追加的事件）。
- 从桌宠反向操作 HoneyComb（点宠物开计时等）。
- 每日小结、待办提醒。

## 五、变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-11-07 | v1 草案 | 首版，待仓主批准 |
