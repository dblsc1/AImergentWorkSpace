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

consumes:
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
