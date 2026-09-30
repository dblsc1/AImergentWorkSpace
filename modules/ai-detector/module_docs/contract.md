# ai-detector · 对外接口契约

> 本文件是 ai-detector 对外行为的**唯一事实**。先改这里、再改代码，顺序不可颠倒。
> 破坏性变更必须先评估消费方并留变更记录，禁止悄悄删除既有承诺。
>
> ai-detector 是**桌面程序**（Go 单文件，Windows / macOS / Linux），不是服务端模块：
> 没有 `module.yaml`，`install.sh` 不装它，用户在自己电脑上下载运行。
> 它读本机 ActivityWatch 记下的窗口活动，合并成「段」，**在本机脱敏**，
> 附上分类建议，上传成 nexus-core 的**待确认建议**。它从不写事实：
> 人在界面上确认了，才由 nexus-core 写 `session.completed`。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: ai-detector.upload.v1
    summary: >
      「什么会离开这台电脑」的承诺：只有脱敏后的段（程序名、脱敏标题、起止、时长、建议），
      原始窗口切换记录永不上传。形状见「上传」节。隐私默认值（默认关、可暂停、脱敏规则）
      是本条的一部分，由测试锁住。
  - id: ai-detector.config.v1
    summary: >
      本机配置文件 `<系统配置目录>/honeycomb/ai-detector.json` 的字段与语义，
      以及规则文件 `rules.json` 的形状。用户可手改，程序每轮重读。
  - id: ai-detector.presence.v1
    summary: >
      （v0.3 追加，契约先行）在场心跳：默认关；开了之后约每 15 秒把「此刻前台的程序 + 脱敏标题 + 是否离开」
      报给 nexus-core，只给页面画实时的人那条线。脱敏同上传。见「在场心跳」节。
  - id: ai-detector.agent-status-bridge.v1
    summary: >
      （v0.3 追加，契约先行）状态文件桥：默认关；指向一个本机状态文件（通用格式，见「状态文件桥」节，
      例如某个桌面状态守护进程写的），把里面各代理的状态变化报成 nexus-core 的代理运行 + 相位。
      只报相位与时刻，不报状态文件里的自由文本。

consumes:
  - id: nexus-core.activity.presence.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      POST /api/core/activity/presence 在场心跳（v2.4）。形状以本文件「在场心跳」节为准。
  - id: nexus-core.agents.phase.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      状态文件桥：POST /api/core/agents/start（带 phase/label/match）、/{runId}/phase、/{runId}/stop
      （后两条属 agents.v1 / agents.phase.v1，v2.4）。
  - id: nexus-core.activity.suggestions.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      POST /api/core/activity/suggestions 上传段 + 建议。**nexus-core 侧在兄弟切片里
      同步建设，接口形状以本文件「上传」节为准**（那边按这里写的请求体实现）。
      服务端防重键 `aw:<deviceId>:<startAt 归一化 UTC>`，所以本程序重发同一段是安全的。

  - id: nexus-core.views.tree.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      只在配了外部分类服务时读：拼出「分区 / 项目 / 任务」路径作为候选任务发给服务。
      规则分类不读树（规则里直接写 taskId）。

  - id: activity.classifier.v1
    contract: ../../../contracts/activity.classifier.v1/contract.md
    purpose: >
      可选的外部分类服务。规则没认出来的段才发过去；失败一律按「无建议」上传，不阻塞。

  - id: auth.gate.v1
    contract: ../../../contracts/auth.gate.v1/contract.md
    purpose: >
      所有对 cockpit 的请求带 `Authorization: Bearer <设备令牌>`。设备令牌是 auth.gate
      的小版本扩展（兄弟切片建设中）；**头的形状是定的**，本程序只负责原样带上。
```

外部依赖（不在本仓，不进上面的索引）：**ActivityWatch** 本地 REST 接口
`http://localhost:5600/api/0/`，只读，用到三处：

| 接口 | 用途 |
|---|---|
| `GET /buckets/` | 发现桶：`type` 为 `currentwindow`（窗口）、`afkstatus`（离开）、`web.tab.current`（浏览器扩展，可选）。**只认 `hostname` 等于本机主机名的桶**（ActivityWatch 可多设备同步，猜错就会把别的电脑的活动当成本机的上传）；对不上就报错，主机名改过的用配置 `windowBucket` / `afkBucket` 指定 |
| `GET /buckets/<id>/events?start=&end=` | 取时间段内的事件；服务端按「有重叠」筛并裁剪到区间，本程序本地再裁一遍 |

事件形状 `{id, timestamp(UTC ISO8601), duration(秒，浮点), data}`；窗口桶 `data={app,title}`，
离开桶 `data={status:"afk"|"not-afk"}`，浏览器桶 `data={url,title,incognito?}`。

## 上传（`ai-detector.upload.v1` · 规范性）

```
POST <cockpitUrl>/api/core/activity/suggestions
Authorization: Bearer <deviceToken>
Content-Type: application/json
```

```jsonc
{
  "deviceId": "dev_3f9a1c2b7d4e5a60",
  "segments": [
    { "startAt": "2026-09-26T11:05:00+08:00",   // 本机时区偏移，精确到秒
      "endAt":   "2026-09-26T12:07:00+08:00",
      "durationSeconds": 3600,                   // 整数；已扣离开时间，≤ endAt-startAt
      "app": "code",
      "title": "plot.gd — garden — Visual Studio Code",   // 已脱敏；app-only 类程序为 ""
      "suggestion": {
        "taskId": "t_a1",                         // 或 null
        "confidence": 0.9,                        // 0..1
        "reason": "规则 #1 命中",
        "classifier": "rules" } }                 // "rules" | "service"
  ]
}
```

- **每段都带 `suggestion` 对象**，认不出时 `taskId: null, confidence: 0`，形状不变。
- `durationSeconds` 是段内**在电脑前**的秒数（扣掉离开），`endAt - startAt` 是墙钟跨度，
  中间被吸收的短暂切换算在段里（见「合并」）。
- **幂等**：同一段每次重算出的 `startAt` 相同，重发只留一条（服务端防重）。
  重叠的窗口事件（记录器重启、数据恢复）在本机先裁掉重叠部分，否则起点会随重算位置漂移。
- **分批**：每次请求至多 200 段，上传请求单独 60 秒超时。分类服务在一轮里请求失败一次，
  这一轮剩下的批次就不再调用它（规则照用，没命中的按「分类服务不可用」上传）。
- **游标只在 2xx 之后前进**：一批被确认，游标挪到下一批第一段的开始（从段开始处重算得到同样的段）；
  全部确认才挪到本轮末尾。非 2xx / 网络错误 → 游标停在未确认那一批的开始，下一轮重算重发。
- 2xx 响应里的 `rejected`（服务端逐段拒收的段）**不重发**——游标照常前进；日志记拒收条数和第一条理由，
  不记标题。
- 重定向：只跟随同主机、同方法、不从 https 降到 http 的重定向（带着设备令牌，不能被转到别处；
  301/302 会把 POST 改成 GET，GET 的 2xx 不能被当成上传成功）。
- 上一轮失败后用户改了合并参数 G / M：重算的段起点可能不同，服务端防重对不上，日志里明确提示。
- 积压上限 `maxBacklogHours`（缺省 72）：离线太久，游标落后超过上限的部分丢弃并打日志。
  原始记录本来就留在 ActivityWatch 里，本程序不另存队列——ActivityWatch 就是队列。

## 隐私默认值（规范性，测试锁住）

1. **默认关**：`init` 写出的配置 `enabled=false`。关着时**零 HTTP 请求**（连本机
   ActivityWatch 都不读），日志写明「未开启，不上传」。
2. **暂停**：`paused=true`（或 `ai-detector pause`）同样零请求。关 / 暂停期间的活动
   **以后也不补传**：重新打开时游标直接跳到「现在」。
3. **本机脱敏，先脱敏再合并再上传**：
   - 所有标题：
     | 形状 | 变成 |
     |---|---|
     | 任意 `scheme://` 网址（http、https、ftp、smb、ssh……） | 只留主机名 |
     | `file://…`、没有主机的网址 | `[路径]` |
     | Windows 共享路径 `\\server\share\…` | `[路径]` |
     | 没写 scheme 的 `域名/路径?查询` | 只留域名 |
     | 本机绝对路径 `C:\Users\…\a.docx`、`/home/…/a.txt`、`~/a.txt`（前面紧挨的字符不是字母数字、`_`、`/` 即可，如「打开/home/…」「(/home/…)」；`/` 开头的至少一层目录） | 只留文件名 |
     | 邮箱 | `[邮箱]` |
     | 手机 / 电话号 | `[电话]` |
     | 6 位以上数字串 | `[数字]` |
   - `appOnlyApps` 名单里的程序（聊天、邮件、密码管理器，默认名单见 README，可配）：
     `title` 一律为空串，只留程序名。
   - `browserApps` 名单里的程序：`title` = `域名 · 页面标题`（取浏览器扩展桶里与这段时间
     重叠最多的标签页，且**窗口标题须以该标签页标题开头**才算对得上——浏览器窗口标题一般是
     「页面标题 - Google Chrome」；对不上多半是扩展没在无痕窗口里跑、重叠的是普通窗口的标签页）。**完整网址、路径、查询串永不上传**。
     无痕窗口（扩展报 `incognito:true`）按 app-only 处理；**没有对得上的标签页记录**
     （没装扩展、这段时间扩展没报、标签页标题为空或与窗口标题对不上）**也按 app-only 处理**——
     这时分不清是不是无痕，而窗口标题里可能有无痕页面的标题或地址栏网址。
4. 原始窗口事件不离开本机；只有「段」离开。

## 合并（规范性）

输入：窗口事件裁到 `[游标, 现在]`，扣掉 `afk` 区间，脱敏后得到「碎片」（按开始时刻排序）。

- **键**：浏览器 = 程序 + 域名；app-only = 程序；其他 = 程序 + 标题归一化
  （小写、去掉开头的未读计数「(3) 」与未保存标记「● / *」；标题按 ` — ` / ` - ` / ` | `
  切开有 3 段及以上时去掉第一段——编辑器的「文件 — 项目 — 程序」形状，同项目换文件不拆段）。
- 从一个碎片开段；往后看，碎片开始时刻距本段**最后一个同键碎片的结束** ≤ `mergeGapMinutes`（G）
  就继续看，同键则延长本段；超过 G 就收段。中间夹着的异键碎片（短暂切出去）被吸收进本段。
- 段内在电脑前的秒数 < `minSegmentMinutes`（M）的段丢掉。
- 只上传**已收口**的段：`段结束 + G ≤ 现在`。没收口的段下轮从它的开始处重算。
- 全程用绝对时刻（`time.Time`），不按日切：跨午夜、跨夏令时都是一段。

## 本机文件（`ai-detector.config.v1`）

目录 = Go `os.UserConfigDir()` + `/honeycomb/`：Windows `%APPDATA%`、macOS
`~/Library/Application Support`、Linux `$XDG_CONFIG_HOME` 或 `~/.config`；
环境变量 `AI_DETECTOR_HOME` 可整体覆盖（测试 / 多份配置用）。

| 文件 | 内容 |
|---|---|
| `ai-detector.json` | 配置（里面有设备令牌）。macOS / Linux 权限 0600；**Windows 上不另设 ACL**，靠 `%APPDATA%` 默认的「仅本用户」访问控制。字段见 README「配置参考」 |
| `ai-detector.lock` | `run` 常驻时持有（内容是 pid）；`once` 发现有活着的持有者就拒绝，避免两个进程各自推进游标互相覆盖。持有者已退出的旧锁自动接管；内容为空 / 读不懂且不到 5 秒的锁视为「正在启动」，不接管；pid 是自己的不接管 |
| `ai-detector.state.json` | 游标与上一轮结果，程序自己写，别手改 |
| `rules.json` | 规则分类，用户可编辑 |
| `ai-detector.log` | `run` 模式的日志，超过 5 MB 启动时清空 |

规则文件形状：

```jsonc
{ "rules": [
    { "app": "code|goland", "title": "garden", "taskId": "t_a1", "confidence": 0.9 }
] }
```

`app` / `title` 是正则（不分大小写），空 = 不限，至少写一个；`title` 匹配的是**脱敏后**的标题
（用户在待确认列表里看到的就是它）。按顺序第一条命中生效；`confidence` 缺省 0.9。

## 平台差异（只有这两处）

- 配置目录：见上。
- 开机自启 `ai-detector autostart install|uninstall`：记录的是程序被启动时的路径，**不解析符号链接**
  （mise / asdf / brew 的链接升级后仍指向新版）；程序挪了位置要重新 `autostart install`。Windows 启动文件夹放一个
  `.vbs`（隐藏窗口启动）、macOS `~/Library/LaunchAgents/*.plist`、Linux
  `~/.config/autostart/*.desktop`。
- 系统权限（macOS「辅助功能」、GNOME Wayland 的扩展）是 ActivityWatch 的事，见 README。

## 在场心跳（`ai-detector.presence.v1` · 规范性，v0.3 追加）

```
POST <cockpitUrl>/api/core/activity/presence
Authorization: Bearer <deviceToken>
```

```jsonc
{ "deviceId": "dev_3f9a1c2b7d4e5a60", "app": "code",
  "title": "plot.gd — garden — Visual Studio Code",   // 已脱敏
  "afk": false }
```

- **默认关**：配置 `presence`（缺省 `false`）。只有 `enabled && !paused && presence` 时才发；关 / 暂停时零请求
  （同「隐私默认值」第 1、2 条）。只在 `run` 常驻模式下发，`once` 不发。
- 节奏：`presenceSeconds`（缺省 15，取值 5–300，越界按缺省）。
- 内容：ActivityWatch 窗口桶**最新一条**事件的 `app`/`title` + 离开桶最新状态。**脱敏规则与上传完全相同**
  （标题表、`appOnlyApps`、`browserApps` 须对得上标签页否则按 app-only），在本机做完再发；
  离开时 `app`、`title` 都发 `""`。
- 不带时间：服务端按收到的时刻算。**失败就丢**：不重试、不排队、不补发——心跳只描述「现在」，
  补发一条过去的「现在」没有意义。日志只记失败分类，不记标题。
- 服务端只留最近 2 小时、不进导出（nexus-core v2.4「在场心跳」节）；想留成记录的仍走上面的「上传」→ 人确认。

## 状态文件桥（`ai-detector.agent-status-bridge.v1` · 规范性，v0.3 追加）

给**没有钩子**的代理（或已经有一个桌面状态守护进程在看着它们的用户）用：本程序定期读一个本机 JSON 文件，
把里面各代理的状态变化报成 nexus-core 的代理运行与相位。**本程序不猜代理状态**——怎么从各代理的日志 / 状态里
认出「在干活 / 等授权」是写这个文件的那一方的事，不在本契约里。

### 文件格式（通用，谁都可以写）

```jsonc
{ "updated_at": 1790000000,            // 写文件那一刻，Unix 秒
  "agents": [
    { "key": "codex:7f3a",             // 必填，1–128 字符，在这个文件里唯一、在代理这次会话里不变
      "label": "garden",               // 选填：显示名（如工作目录名）
      "state": "working",              // working | waiting_input | waiting_permission | complete | idle | error
      "detail": "…" } ] }              // 选填，自由文本——**本程序不读、不上传**
```

- `state` 映射：`working`→`working`，`waiting_input`→`waiting_input`，`waiting_permission`→`waiting_permission`，
  `complete`/`idle`→`idle`，`error`→`error`；其他值 → 这一条本轮跳过（不产生转入）。
- 格式不对（不是对象、`updated_at` 不是数、`agents` 不是数组）→ 当作「文件过期」处理。单条缺 `key`/`state` → 跳过那一条。

### 行为

- **默认关**：配置 `agentStatusFile`（缺省 `""` = 关）填本机路径才开。同样只在 `enabled && !paused` 且 `run`
  常驻模式下工作；关 / 暂停时不读文件、零请求。
- 约每 3 秒读一次。**文件过期**（读不到、解析不了、`updated_at` 早于现在 30 秒以上）→ 这一轮**什么都不报**：
  不编转入、不 stop。在跑的运行留给服务端的遗忘超时去收（守护进程停了 ≠ 代理停了，本程序分不清）。
- `agentStatusIgnore`（字符串数组，缺省 `[]`）：`key` 以其中任一前缀开头的条目整个忽略。用来**避免重复上报**
  ——例如已经装了 `tools/agent-hooks` 的 Claude Code 钩子，就把 Claude 那一类的前缀填进来，否则同一个会话会有两条泳道。
- 新鲜文件里**第一次看到**某个 `key` → `agents/start`：`agent`、`tool` 都取 `key` 第一个 `:` 之前的部分，
  须匹配 `^[a-z0-9_-]{1,32}$`（`claude`、`codex`、`hermes` 这类代理种类名），否则一律报 `"agent"`；
  `clientKey` = SHA-256(`deviceId` + `key`) 前 32 位十六进制（丢了响应重试不会多开一条运行；原始 `key` 不上传）；`phase` 为映射后的相位，`label` 为脱敏后的 `label`（按上传的标题
  脱敏规则，截到 64 码点；空则不发），`match` 同 `label`（不足 3 码点不发）。不挂任务（落收件箱）。
- 映射后的相位**变了** → `agents/{runId}/phase`：`at` = 本程序发现变化的时刻（本机时钟，最多晚一个轮询间隔），
  **不发 `detail`**；只在从 `waiting_input`/`waiting_permission` 转入 `working` 时带 `reply: true`（等人的状态解除，
  通常是人答了）。从 `idle`/`error` 转回 `working` **不带**——可能是人说了话，也可能是自动重试、定时任务，
  文件格式里没有能分清的信号，宁可少一根连线也不编一根。
- 新鲜文件里某个 `key` **不见了** → `agents/{runId}/stop`：最后状态是 `error` 报 `failed`，否则报 `done`。
- `key → runId` 存进 `ai-detector.state.json`，重启后接着用。`phase`/`stop` 回 404 或 `applied:false, reason:"closed"`
  （服务端已按超时收掉）→ 忘掉这个映射，下次看到这个 `key` 当作第一次看到（同一 `clientKey` 的旧运行已关，会开新运行）。
- 网络失败：本轮不重试，映射与「上次报过的相位」都不前进，下一轮看到的仍是变化、会再报一次（`at` 取新的发现时刻；
  服务端照收，读时合并同相位，重报只是多一条观测，不改变画出来的样子）。
- **离开本机的只有**：`key` 的前缀（合规的代理种类名，作 agent/tool 名）、`clientKey`（哈希）、脱敏后的 `label`、相位、时刻、结束状态。`key` 的其余部分、
  `detail`、文件路径都不上传。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-30 | v0.3 追加（契约先行）：`ai-detector.presence.v1` 在场心跳、`ai-detector.agent-status-bridge.v1` 状态文件桥；都默认关，新增配置 `presence`/`presenceSeconds`/`agentStatusFile`/`agentStatusIgnore`。上传、脱敏、游标的既有承诺一条不改 |
