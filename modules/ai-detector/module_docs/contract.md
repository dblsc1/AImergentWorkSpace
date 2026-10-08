# ai-detector · 对外接口契约

> 本文件是 ai-detector 对外行为的**唯一事实**。先改这里、再改代码，顺序不可颠倒。
> 破坏性变更必须先评估消费方并留变更记录，禁止悄悄删除既有承诺。
>
> ai-detector 是**桌面程序**（Go 单文件，Windows / macOS / Linux），不是服务端模块：
> 没有 `module.yaml`，`install.sh` 不装它，用户在自己电脑上下载运行。
> 它读本机 ActivityWatch 记下的窗口活动，合并成「段」，**在本机脱敏**，
> 附上分类建议，上传成 nexus-core 的**待确认建议**。它从不写事实：
> 人在界面上确认了，才由 nexus-core 写 `session.completed`。
>
> **当前版本**：`ai-detector.upload.v1` **v1.1**、`ai-detector.config.v1` **v1.1**、新增
> `ai-detector.archive.v1`（2026-09-30）。v1.1 全是追加：隐私从「一套写死的脱敏」变成
> 「写死的强制脱敏 + 一组可单独勾选的选项」（缺省值 = v1.0 的行为），选项可以在 Cockpit 网页上改
> （`detector.settings.v1`）；离开判定四个可选项；本机留档。上传形状只多一个可选字段 `idle`。
> **2026-10-02 追加**：`ai-detector.upload.v1` **v1.2**、`ai-detector.config.v1` **v1.3**——终端类程序
> **按标签页（程序 + 标题）各自成段**（见「合并」的「按标签页分段」）。上传形状一个字段不改，变的只是段怎么切。
> **2026-10-08 追加**：`ai-detector.upload.v1` **v1.3**、`ai-detector.presence.v1` **v1.1**、`ai-detector.config.v1` **v1.4**——
> 规则可以只到项目（`suggestion.projectId`）；网页设置 `autoTrack` 打开时在场心跳带规则对当前窗口的猜测 `guess`。
> 都是追加，`autoTrack` 关着时发出去的内容与此前逐字节相同。打开之后 nexus-core 会把规则高把握命中的段直接记成事实
> （nexus-core v2.14）——上面「人在界面上确认了，才……写」那句从此只在 `autoTrack` 关着时无条件成立。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: ai-detector.upload.v1
    summary: >
      「什么会离开这台电脑」的承诺：只有脱敏后的段（程序名、脱敏标题、起止、时长、建议），
      原始窗口切换记录永不上传。形状见「上传」节。隐私默认值（默认关、可暂停、脱敏规则）
      是本条的一部分，由测试锁住。v1.1：强制脱敏（关不掉）+ 可选隐私项（detector.settings.v1）、
      段增可选 idle。
  - id: ai-detector.archive.v1
    summary: >
      本机留档 `<配置目录>/archive/YYYY-MM-DD.jsonl`：每次发给 cockpit / 分类服务的内容
      （原始标题只过强制脱敏的 raw + 实际发出的 sent），只在本机，按 archiveDays 清理。
      `ai-detector archive` / `preview` 读它。形状见「本机留档」节。
  - id: ai-detector.config.v1
    summary: >
      本机配置文件 `<系统配置目录>/honeycomb/ai-detector.json` 的字段与语义，
      以及规则文件 `rules.json` 的形状。用户可手改，程序每轮重读。
  - id: ai-detector.presence.v1
    summary: >
      （v0.3 追加，**已实现** 2026-09-30）在场心跳：默认关；开了之后约每 15 秒把「此刻前台的程序 + 脱敏标题 + 是否离开」
      报给 nexus-core，只给页面画实时的人那条线。脱敏同上传。见「在场心跳」节。
  - id: ai-detector.agent-status-bridge.v1
    summary: >
      （v0.3 追加，**已实现** 2026-09-30）状态文件桥：默认关；指向一个本机状态文件（通用格式，见「状态文件桥」节，
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

  - id: detector.settings.v1
    contract: ../../../contracts/detector.settings.v1/contract.md
    purpose: >
      每轮 GET /api/core/detector/settings?deviceId= 拉网页上设的隐私 / 离开选项，有就整节替换本机配置的
      privacy / idle；null 或 404 用本机；其他失败这一轮不上传。

  - id: detector.rules.v1
    contract: ../../../contracts/detector.rules.v1/contract.md
    purpose: >
      有段要上传的每一轮 GET /api/core/detector/rules（设备令牌，只读）：服务端存过（version > 0，哪怕是空集）
      就只用它（停用的跳过、Go 编译不过的跳过并写日志）；没存过 / 404 / 拉不到用本机 rules.json。

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
离开桶 `data={status:"afk"|"not-afk"}`，浏览器桶 `data={url,title,incognito?,audible?}`。

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
        "classifier": "rules" },                  // "rules" | "service"；v1.3：规则只到项目时 taskId 为 null、多一个 "projectId"
      "idle": true }                              // v1.1 可选：只在 idle.idleSuggestions 开着、这段是「无操作但前台没换」时出现
  ]
}
```

- **v1.1 `idle`**：只有 `true` 时才带；普通段不带这个键（v2.5 之前的 nexus-core 会忽略它，照收）。
  带 `idle: true` 的段 `suggestion.confidence ≤ 0.3`，`reason` 以「无操作，可能在阅读」开头
  （老服务端不存 `idle`，人从 `reason` 也看得出来）。

- **v1.3 `suggestion.projectId`**：命中的规则只到项目（`detector.rules.v1` v1.1，本机 `rules.json` 也能写）时，
  `suggestion` 是 `{taskId: null, projectId: "p_…", confidence, reason, classifier: "rules"}`；到任务的规则、没命中的段
  不带这个键（形状与 v1.2 相同）。v2.14 之前的 nexus-core 忽略它（这段没有任务建议，照收）。

- **每段都带 `suggestion` 对象**，认不出时 `taskId: null, confidence: 0`，形状不变。
- `durationSeconds` 是段内**在电脑前**的秒数（扣掉离开），`endAt - startAt` 是墙钟跨度，
  中间被吸收的短暂切换算在段里（见「合并」）。
- **幂等**：同一段每次重算出的 `startAt` 相同，重发只留一条（服务端防重）。
  重叠的窗口事件（记录器重启、数据恢复）在本机先裁掉重叠部分，否则起点会随重算位置漂移。
- **分批**：每次请求至多 200 段，上传请求单独 60 秒超时。分类服务在一轮里请求失败一次，
  这一轮剩下的批次就不再调用它（规则照用，没命中的按「分类服务不可用」上传）。
- **游标只在 2xx 之后前进**：一批被确认，游标挪到下一批第一段的开始（从段开始处重算得到同样的段）；
  全部确认才挪到本轮末尾。非 2xx / 网络错误 → 游标停在未确认那一批的开始，下一轮重算重发。
- **送达过的不再算**（v1.2）：游标因为别的段没收口而停着时，已经送达的活动记在状态文件的 `sent` 里，重算时先挖掉，见「按标签页分段」。
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
3. **本机脱敏，先脱敏再合并再上传**：每条窗口标题（以及浏览器扩展报的标签页标题、网址）读进来的第一步是
   **强制脱敏**（下节），然后按**隐私选项**（`detector.settings.v1` 的 `privacy` 节，逐项定义、缺省值、
   前后例子以那里为准）处理。缺省值组合 = v1.0 的全部行为：路径只留文件名、标题里的网址只留域名、
   邮箱 / 电话 / 6 位以上数字替换、聊天邮件密码管理器只留程序名、浏览器只留「域名 · 页面标题」且
   无痕 / 对不上标签页只留程序名。v1.1 新增的 `addresses`、`ips`、`usernames` 缺省也开着。
   **缺省值下的完整行为（v1.0 承诺，仍然成立）**：
     | 形状 | 变成 | 对应选项 |
     |---|---|---|
     | 任意 `scheme://` 网址（http、https、ftp、smb、ssh……） | 只留主机名（主机名是 IP 时再按 `ips` 换成 `[IP]`） | 总是 |
     | `file://…`、没有主机的网址 | `[路径]` | `paths: full` |
     | Windows 共享路径 `\\server\share\…` | `[路径]` | `paths: full` |
     | 没写 scheme 的 `域名/路径?查询` | 只留域名 | 总是 |
     | 本机绝对路径 `C:\Users\…\a.docx`、`/home/…/a.txt`、`~/a.txt`（前面紧挨的字符不是字母数字、`_`、`/` 即可，如「打开/home/…」「(/home/…)」；`/` 开头的至少一层目录） | 只留文件名 | `paths: full` |
     | 邮箱 | `[邮箱]` | `emails` |
     | 手机 / 电话号 | `[电话]` | `phones` |
     | 6 位以上数字串 | `[数字]` | `longNumbers` |
   - `appOnlyApps` 名单里的程序（`appOnly: true`）：`title` 一律为空串，只留程序名。
   - `browserApps` 名单里的程序：`title` = `域名 · 页面标题`（`browser: domain`；`full` 换成网址），取浏览器扩展桶里
     与这段时间重叠最多的标签页，且**窗口标题须以该标签页标题开头**才算对得上（浏览器窗口标题一般是
     「页面标题 - Google Chrome」；对不上多半是扩展没在无痕窗口里跑、重叠的是普通窗口的标签页）。
     无痕窗口（扩展报 `incognito:true`）、**没有对得上的标签页记录**（没装扩展、这段时间扩展没报、标签页标题为空
     或与窗口标题对不上）**一律只留程序名**，不管 `browser` 选什么——这时分不清是不是无痕。
4. 原始窗口事件不离开本机；只有「段」离开。
5. 规则分类（`rules.json`）匹配的是**隐私选项处理后、换代号之前**的标题（本机匹配，不离开本机）；
   发给分类服务的是和上传**完全相同**的标题（换过代号的就是代号）。

### 强制脱敏（规范性，关不掉）

两类场合都跑，**先于**一切别的处理：读进窗口标题 / 标签页标题 / 标签页网址的第一步；以及最后一道——
上传体、分类服务请求体（含候选任务路径）组装时每个字符串字段再过一遍（幂等）。本机留档（`raw` 与 `sent`）
与标题代号对照表里存的也都是强制脱敏之后的文字。

| 类别 | 认法（实现：`secrets.go`） | 换成 |
|---|---|---|
| 私钥 | `-----BEGIN … PRIVATE KEY-----` 到 `-----END … PRIVATE KEY-----` 或行尾 | `[已隐藏:私钥]` |
| 密钥 / 访问令牌 | gitleaks 默认规则的子集，改写为 Go 正则：AWS 访问密钥 ID（`AKIA`/`ASIA`/`ABIA`/`ACCA`/`A3T…` + 16 位）、GitHub（`ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` + 36 位、`github_pat_` + 82 位）、GitLab `glpat-`、OpenAI / Anthropic / DeepSeek 等 `sk-`（`sk-proj-`/`sk-svcacct-`/`sk-admin-`/`sk-ant-api03-` 等，或含一段 ≥20 位无连字符字母数字的 `sk-…`）、Slack（`xox[abposr]-`、`xapp-`、`hooks.slack.com/…`）、Google（`AIza` + 35 位、`GOCSPX-`、`ya29.`）、Stripe（`sk_`/`rk_` + `live`/`test`/`prod`）、npm `npm_`、PyPI `pypi-AgEIcHlwaS5vcmc…`、Hugging Face `hf_`、JWT（`ey….ey….`）、`Bearer <≥16 位>`、网址里的 `用户:密码@`、通用 `关键词(access/auth/api/credential/creds/key/secret/token…) = / : 值`（值 8–150 位、同时有字母数字、香农熵 ≥ 3.5） | `[已隐藏:密钥]`（Bearer、key= 这类只盖值，留下前面的词） |
| 密码 | `password` / `passwd` / `pwd`（含前后缀，如 `db_password`）+ `=`/`:`/`:=`/`=>` + 值；`密码` / `口令` + `：`/`:`/`=`/`是`/`为` + 值。**值不看熵，一律盖** | 只盖值：`password=[已隐藏:密码]` |
| 银行卡号 | 13–19 位数字（组间可有空格 / `-`），首位 2–6（Visa、万事达、银联、运通、JCB、Discover 的发卡行前缀范围），且过 Luhn 校验。一串用空格分组的数字里，**任意连续几组**拼起来满足条件就只盖那几组 | `[已隐藏:银行卡]` |
| 身份证号 | 18 位（末位可为 `X`），GB 11643 校验位正确，且第 7–14 位像出生日期（18xx/19xx/20xx 年、01–12 月、01–31 日） | `[已隐藏:身份证]` |

- **配置文件、网页设置里都没有开关**。`ai-detector.json` 的 `privacy` / `idle` 节里出现不认识的键
  （例如 `secrets: false`、`mandatory: false`）一律忽略，并在日志与 `status` 里警告；网页设置的 schema
  里没有这些键，发了 `422`。**测试锁住**：带着这些键跑，密钥照样被盖。
- 唯一关法是改源码：`secrets.go` 顶部的 `mandatory*` 常量改成 `false` 后自己编译（README「自己构建」）。
  官方发布的程序全是 `true`。
- 取舍：宁可多盖。`Settings: Password: Reset` 这种标题里 `Reset` 也会被当成密码盖掉。
  已知不会误伤（测试锁住）：git 提交号、UUID、日期时间、Unix 时间戳、以 1 / 7 / 8 / 9 开头的长订单号、
  校验位不对的 18 位数字、`Hotkey: Ctrl+Shift+P`、`max tokens: 4096`。
- 为什么不直接引 gitleaks：它是整套扫描器（带 viper、zerolog 等几十个依赖），这个程序零第三方依赖、
  单文件分发；我们只需要其中十几条正则。挑出来的规则保留 gitleaks 的 MIT 许可声明（`secrets.go` 文件头）。

### 标题代号（`titles: "pseudonymize"`）

- 代号按「隐私选项处理后的标题」分配：没见过的标题拿下一个号，「窗口名1」「窗口名2」……；标题里还带着路径的
  （只可能在 `paths` 为 `half`/`off` 时）用「路径1」「路径2」……，两套号各自递增。
- **稳定**：对照表存本机 `pseudonyms.json`（0600），跨轮、跨天、跨重启同一条标题同一个代号；删掉这个文件
  代号从 1 重新编。对照表**从不上传**、不进留档的 `sent`；`ai-detector pseudonyms` 在本机查看。
- 对照表读不了（坏了）：这一轮不上传——重新编号会让旧代号指向新标题。
- 代号在合并之后换：合并键仍按真实标题算，换不换代号，段的切法完全一样。

## 离开判定（规范性，`detector.settings.v1` 的 `idle` 节）

窗口事件扣掉「离开」区间之前，离开区间先按下列选项缩减（缺省全关 = v1.0 行为）：

- `afkThresholdMinutes = N > 0`：短于 N 分钟的离开区间整段不算离开。
- `audibleAsPresent`：前台是 `browserApps` 里的程序时，离开区间与「`audible: true` 的浏览器标签页事件」重叠的部分不算离开。
- `focusAppsEnabled`：前台是 `focusApps` 里的程序时，每个离开区间的前 `focusMaxMinutes` 分钟不算离开。
- `idleSuggestions`：其余仍算离开、但这段时间前台窗口没换（窗口事件覆盖着它）的部分，**不丢**：
  单独做成「无操作碎片」，只和无操作碎片合并（规则同「合并」，键同普通碎片），**绝不并进普通段**
  （否则会被当成在电脑前的时间吸收进去）；收口、丢短段规则相同。上传时带 `idle: true`，
  规则 / 分类服务照常给建议，但把握夹到 ≤ 0.3，理由前加「无操作，可能在阅读」。
  无操作段与普通段可能在墙钟上相邻或被普通段的跨度覆盖——这是有意的，由人确认时判断。

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
- **短于 1 秒的碎片不参与合并**（v1.2）：每一段都从一个 ≥ 1 秒的碎片开始，碎片互不重叠，所以任何两段的
  `startAt`（精确到秒）一定不同——服务端按 `deviceId + startAt` 防重，撞了就会丢一段。

### 按标签页分段（upload.v1 v1.2 · 规范性）

一个终端窗口开很多标签页、每个标签页是一件事（一个 AI 代理会话、一个项目），标签页标题就是这件事的名字。
按上面的规则，20 分钟里在 5 个标签页之间来回切会并成**一段**、挂第一个标签页的标题，其余标签页的时间被吸收掉，
规则也匹配不上。所以对终端类程序改成**每个标签页各自成段**：

- **适用的碎片**（「标签页碎片」）：`segmentByTitle` 开着（缺省开），程序在 `segmentByTitleApps` 名单里
  （缺省内置终端名单，见「本机文件」），不在 `browserApps` 里，且**隐私选项处理后的标题非空**。
  `titles: "drop"`、app-only 名单里的程序标题为空 → 不适用，照旧只按程序合并。无操作碎片（`idle`）不适用，键与合并都照旧。
- **键** = 程序 + 标签页键。标签页键由**隐私选项处理后、换代号之前**的标题算出（和别的合并键一样，
  只在本机用、不上传，所以开着它不多暴露任何东西；`titles: "pseudonymize"` 时段的切法与 `keep` 完全一样）：
  1. 首尾去空白、连续空白压成一个空格；
  2. 去掉结尾的「` - 程序名`」（分隔符 ` - ` / ` — ` / ` – ` / ` | `；最后一节归一后 ≥ 3 个字符且是程序名的一部分，
     如 `org.gnome.Ptyxis` 的窗口标题结尾的 ` - Ptyxis`）；
  3. 标题是 shell 提示符形状「`用户@主机: 当前目录`」（脱敏后是「`[用户]@[主机]: …`」）时，键只取「`用户@主机:`」——
     没起名字的 shell 标签页换目录不拆段（同一台主机上的这类标签页算一件事）；这种标题不做第 4 步，只再小写；
  4. 去掉开头的未读计数「(3) 」和**开头连续的非字母、非数字字符**——状态符号、转圈动画
     （`✳ `、`⠋ `、`● `、`* `……）一直在变，不去掉的话同一个标签页会碎成很多键；
  5. 小写。
- **每个键一条独立的流**：只和同键的标签页碎片合并（间隔 ≤ G 接上，超过 G 收段）；
  `durationSeconds` **只算自己的碎片**——不吸收中间切出去的别的碎片，也不被别的段吸收
  （普通碎片的合并里看不见标签页碎片，那段时间相当于空着）。丢短段（M）、收口规则不变。
  例：A 5 分 → B 20 秒 → A 3 分 → C 4 分 → A 2 分：A 一段（墙钟 14 分 20 秒、`durationSeconds` 600）、
  C 一段（240），B 不足 M 丢掉。
- **段的墙钟跨度可以互相交叠**（A 的跨度盖着 C），`durationSeconds` 不重复计：同一秒只算在一段里。
  同设备交叠的段服务端照收（防重键只看 `startAt`；无操作段早就可以和普通段交叠），由人逐条确认。
- 段的标题仍取该段里停留最久的那条（处理后的）标题。
- **游标与「已送达的区间」**：一个标签页的段可能几个小时不收口（一直每隔几分钟回来一次），期间游标停在它的开始处，
  每轮从那里重算。这期间早已收口、送达的别的段不能每轮再发一遍（多调分类服务、多写留档），更不能在设置变了之后
  被换一种切法再算一次。所以状态文件记 `sent`：**游标之后已经确认送达（2xx）的段所占的时间区间**（段里每个碎片的起止，
  相接的并成一条）。每轮算碎片时先把这些区间挖掉（和扣离开时间一样），送达过的活动不会再进任何段——
  不管从哪重算、合并参数或设置变没变。游标越过的区间随即丢掉；没有交叠的流时游标总在已送达的段之后，`sent` 是空的。
  上传失败的那一轮什么都不记，下一轮重算出一模一样的段重发（幂等同前）。
- **升级后的第一轮按老切法**：老版本的状态文件没有 `sent`，而它的游标可能停在一个已经送达的段的开头
  （开着 `idleSuggestions`、无操作段和普通段交叠时）。所以状态文件里没有 `sentReady` 的那一轮不按标签页分段、短于 1 秒的碎片也照旧参与合并，
  把那样的段原样重算重发（服务端防重）并记进 `sent`；这一轮成功后写 `sentReady: true`，下一轮起按标签页分段。
- **缺省开的理由**：只影响内置名单里的终端程序；不多上传任何字段、键在本机算完就丢；
  对「一个标签页一件事」的用法是必需的，对别的用法最多是终端里短暂停留（不足 M）不再算进相邻的段。
  不想要：本机 `segmentByTitle: false` 或网页设置里关掉，回到 v1.1 的切法。
- **已知限制**：标题以别的方式一直在变的标签页（程序把进度写进标题的中间或结尾）每个标题是一个键，
  各自不足 M 就都被丢——v1.1 里也是如此（异键碎片只有在开头那个键回来时才被吸收）。
  给标签页起固定的名字，或把这个程序从 `segmentByTitleApps` 里拿掉。
  几个没起名字的 shell 标签页分不开（键都是「`用户@主机:`」），段的标题取其中停留最久的那条；`usernames` 开着（缺省）时
  提示符已被换成「`[用户]@[主机]:`」，所以 ssh 到不同主机的 shell 标签页也并在一起——键只能用脱敏后的标题算。

## 本机留档（`ai-detector.archive.v1`，规范性）

「原始的 AI 抓的数据留档可查」：每次真的把段发出去（上传给 cockpit、发给分类服务），在本机追加一行 JSON 到
`<配置目录>/archive/YYYY-MM-DD.jsonl`（本机日期；目录 0700、文件 0600；只追加）。

```jsonc
{ "at": "2026-09-30T10:05:12+08:00",   // 发出去的时刻（本机时区）
  "to": "cockpit",                     // "cockpit" | "classifier"
  "ok": true,                          // 收到 2xx
  "result": "accepted=2 duplicates=0 rejected=0",   // 人读的一句话；失败时是错误
  "segments": [                        // ok=false 时省略（见下）
    { "startAt": "…", "endAt": "…", "durationSeconds": 3600, "app": "code",
      "raw":  "plot.gd — garden — Visual Studio Code",   // 本机原始标题：**只过了强制脱敏**
      "sent": "窗口名3",                                   // 实际发出去的标题
      "idle": false,
      "suggestion": { "taskId": "t_a1", "confidence": 0.9, "reason": "规则 #1 命中", "classifier": "rules" } } ],
  "tasks": 12 }                        // 仅 to=classifier：随请求发出的候选任务条数
```

- **成功才记全文**：失败的请求只记一行 `ok:false` + 错误 + 段数。失败的那批下一轮会**原样**重算重发
  （同一批段、同样的标题），成功时再记全文——这样离线几天也不会每 5 分钟把同一批几百段写一遍。
  「请求其实到了服务端、只是响应丢了」的情况，内容与随后成功那次相同，照样查得到。
- `raw` 是本机原始窗口标题（强制脱敏后），浏览器段也是窗口标题（不是扩展报的网址）。它比 ActivityWatch
  自己存的还少（强制脱敏过），但仍是隐私数据：**只在本机**，从不上传。
- **保留期** `archiveDays`（缺省 30，最小 1）：每轮开始时删掉文件名日期早于「今天 − archiveDays + 1」的文件。
- **磁盘上限估算**：一段一行至多约 2 KB（两份标题 + 建议）；段短于 `minSegmentMinutes`（缺省 3）就丢，
  一天至多 1440 / 3 = 480 个普通段，加同样多的无操作段、再加发分类服务的一份，最坏约 4 MB / 天、
  30 天约 120 MB；日常一天几十段，几十 KB。
- 留档写失败（磁盘满、权限）：日志报错，**不影响上传**（已经发出去的收不回来，不能因为记不下就重发）。
- `ai-detector archive [YYYY-MM-DD]` 打印某天（缺省今天）的留档；`ai-detector preview [N]` 取留档里最近 N 段
  （缺省 20）的 `raw`，按**当前**隐私选项重新处理，与当时的 `sent` 并排打印，改选项前先看效果；
  `ai-detector preview --app <程序> --title <标题>` 对一条样例标题试。预览只在本机终端输出，不联网
  （浏览器段在预览里没有扩展数据，按「对不上标签页」只留程序名）。

## 本机文件（`ai-detector.config.v1`）

目录 = Go `os.UserConfigDir()` + `/honeycomb/`：Windows `%APPDATA%`、macOS
`~/Library/Application Support`、Linux `$XDG_CONFIG_HOME` 或 `~/.config`；
环境变量 `AI_DETECTOR_HOME` 可整体覆盖（测试 / 多份配置用）。

| 文件 | 内容 |
|---|---|
| `ai-detector.json` | 配置（里面有设备令牌）。macOS / Linux 权限 0600；**Windows 上不另设 ACL**，靠 `%APPDATA%` 默认的「仅本用户」访问控制。字段见 README「配置参考」 |
| `ai-detector.lock` | `run` 常驻时持有；`once` 发现锁被别人拿着就拒绝，避免两个进程各自推进游标互相覆盖。**锁是系统的建议锁**（Unix `flock`、Windows `LockFileEx`），跟着持有者进程走，进程一退出（崩溃、断电、容器被杀）系统就收回——残留的文件从不挡人（2026-09-30 改：之前按「文件里的 pid 活没活着」判，容器里程序总是 pid 1，重启后旧文件的「pid 1」被当成活着的持有者，永远拒绝）。文件内容是持有者的 pid，只用于报错提示；文件不删 |
| `ai-detector.state.json` | 游标、`sent`（v1.3，游标之后已送达的区间，见「按标签页分段」）与上一轮结果，程序自己写，别手改 |
| `rules.json` | 规则分类，用户可编辑 |
| `pseudonyms.json` | 标题代号对照表（v1.1），0600，只在本机，见「标题代号」 |
| `archive/YYYY-MM-DD.jsonl` | 本机留档（v1.1），见「本机留档」 |
| `ai-detector.log` | `run` 模式的日志，超过 5 MB 启动时清空 |

配置文件 v1.1 新增（全部可省略，省略 = 缺省）：

| 字段 | 缺省 | 说明 |
|---|---|---|
| `privacy` | 见 `detector.settings.v1`「privacy 节」 | 与网页设置同形状（不带 `schemaVersion`）。网页上设过就以网页为准（整节替换） |
| `idle` | 见 `detector.settings.v1`「idle 节」 | 同上 |
| `archiveDays` | 30 | 本机留档保留天数，< 1 按 30 |
| `presence` | `false` | 在场心跳（v0.3），见「在场心跳」。网页设置 `presence` 非 null 时以网页为准 |
| `presenceSeconds` | 15 | 心跳间隔，5–300，越界按 15 |
| （没有本机键）`autoTrack` | — | 「允许 AI 管理进行中的任务」只在网页设置里（`detector.settings.v1` v1.3）：服务端要读它，只写本机没有用 |
| `agentStatusFile` | `""` | 状态文件桥读的本机路径，空 = 关 |
| `agentStatusIgnore` | `[]` | `key` 前缀，命中的条目忽略 |

配置文件 v1.3 新增（可省略）：

| 字段 | 缺省 | 说明 |
|---|---|---|
| `segmentByTitle` | `true` | 终端类程序按标签页（程序 + 标题）各自成段，见「按标签页分段」。网页设置（`detector.settings.v1` v1.2）里有这个键时以网页为准 |
| `segmentByTitleApps` | `null` | 上一项的名单；`null` = 内置终端名单：GNOME Terminal、Ptyxis、GNOME Console、kitty、Alacritty、WezTerm、Konsole、xterm、Tilix、foot、Terminator、Xfce Terminal、Ghostty、macOS Terminal、iTerm2、Windows Terminal、cmd、PowerShell、Warp、Hyper、Tabby 等（以 `config.go` 的 `defaultTabApps` 为准，只增）。比较规则同 `appOnlyApps`。网页设置里是数组时以网页为准 |

`privacy.appOnlyApps: null` 时用顶层 `appOnlyApps`（`init` 写入内置默认名单；顶层也没有就用内置默认）。
`privacy` / `idle` 里出现不认识的键：忽略并警告（见「强制脱敏」）。枚举值写错、白名单正则编译不过：
这一轮报错不上传（同规则文件写坏），`status` 里看得到。

规则文件形状：

```jsonc
{ "rules": [
    { "app": "code|goland", "title": "garden", "taskId": "t_a1", "confidence": 0.9 },
    { "title": "blog", "projectId": "p_2" }      // config.v1 v1.4：只到项目；taskId 与 projectId 恰好写一个
] }
```

`app` / `title` 是正则（不分大小写），空 = 不限，至少写一个；`title` 匹配的是**脱敏后**的标题
（用户在待确认列表里看到的就是它）。按顺序第一条命中生效；`confidence` 缺省 0.9。

**config.v1 v1.2（追加）**：网页上（Cockpit「AI助理 → 规则」，多由 AI 助理起草、人应用）存过规则后，
以服务端为准，本机 `rules.json` 不再读；服务端从没存过、老 nexus-core、或拉不到时仍用本机文件（离线后备）。
服务端规则命中的 `reason` 是「网页规则 #N 命中」。规则来源变化时写一行日志。见 `contracts/detector.rules.v1`「四」。

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
- 网页开关（`detector.settings.v1` v1.1 的 `presence`）：非 `null` 时以网页为准。开没开先按最近一次拉到的设置
  （同步一轮每 5 分钟拉一次）判断；**要发之前再拉一次设置**，用此刻的隐私选项脱敏，拉不到（404 以外的失败）这次就不发。
- 实现：`run` 里单独一个 goroutine，不等 5 分钟一轮的同步；脱敏调用与上传同一段代码（`buildFragments` + `sendTitles`，
  测试逐条比对心跳与上传出去的标题完全相同）。
- 节奏：`presenceSeconds`（缺省 15，取值 5–300，越界按缺省）。
- 内容：ActivityWatch 窗口桶**最新一条**事件的 `app`/`title` + 离开桶最新状态。**脱敏规则与上传完全相同**
  （标题表、`appOnlyApps`、`browserApps` 须对得上标签页否则按 app-only），在本机做完再发；
  离开时 `app`、`title` 都发 `""`。
- **`guess`（presence.v1 v1.1，追加）**：网页设置 `autoTrack`（`detector.settings.v1` v1.3）为 `true`、人没离开时，
  对当前窗口跑**与上传同一个规则匹配函数**（匹配的是隐私选项处理后、换代号前的标题与程序名，同上传），命中就在心跳里加
  `"guess": {"taskId": "t_a1", "confidence": 0.9, "classifier": "rules"}`（只到项目的规则是 `"projectId"`）；
  **没命中、`autoTrack` 关着、离开时都没有这个键**（请求体与 v1.0 逐字节相同）。规则与上传同源（网页存过用网页的，否则本机
  `rules.json`），心跳这边每 60 秒重拉一次；拉不到 / 本机规则写坏 → 这次不带 `guess`，心跳照发。
  **隐私**：猜测在本机算，发出去的只有目标 id 与把握；`app` / `title` 仍是同一条脱敏路径出来的那份，一个字符不多。
  老 nexus-core 忽略这个键。

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
  须在内置白名单里（`claude`、`codex`、`hermes`、`gemini`、`opencode`、`aider`、`cursor`；只增），否则一律报 `"agent"`
  ——格式对不代表不是隐私（`secret_project:1` 也合格式）；
  `clientKey` = SHA-256(`deviceId` + `key`) 前 32 位十六进制（丢了响应重试不会多开一条运行；原始 `key` 不上传）；`phase` 为映射后的相位，`label` 为脱敏后的 `label`（按上传的标题
  脱敏规则，截到 64 码点；空则不发），`match` 同 `label`（不足 3 码点不发）。不挂任务（落收件箱）。
- 映射后的相位**变了** → `agents/{runId}/phase`：`at` = 本程序发现变化的时刻（本机时钟，最多晚一个轮询间隔），
  **不发 `detail`**；只在从 `waiting_input`/`waiting_permission` 转入 `working` 时带 `reply: true`（等人的状态解除，
  通常是人答了）。从 `idle`/`error` 转回 `working` **不带**——可能是人说了话，也可能是自动重试、定时任务，
  文件格式里没有能分清的信号，宁可少一根连线也不编一根。
- 新鲜文件里某个 `key` **不见了** → `agents/{runId}/stop`：最后状态是 `error` 报 `failed`，否则报 `done`。
- `key → runId` 存进 `ai-detector.state.json`，重启后接着用。`phase`/`stop` 回 404 或 `applied:false, reason:"closed"`
  （服务端已按超时收掉）→ 忘掉这个映射，下次看到这个 `key` 当作第一次看到（同一 `clientKey` 的旧运行已关，会开新运行）。
- 网络失败：本轮不重试，映射与「上次报过的相位」都不前进；那条观测**连同原始 `at`、`reply`** 记进状态文件的待发队列
  （每个 `key` 至多留最近 20 条），下一轮原样重发——`(at, phase)` 相同，服务端按 `duplicate` 去重，`reply` 不会记两次。
  之后新发现的变化另起一条排在后面。
- 实现：`run` 里单独一个 goroutine。一轮里碰到网络失败后，这一轮剩下的请求都不再发（观测照样排进队列）；
  `phase`/`stop` 回 4xx（404、401、403、408、429 以外）表示请求本身不合格，丢掉那一条。
  开新运行时要给 `label` 脱敏，才拉一次网页设置（拉不到这一轮不开新运行）。
- **离开本机的只有**：`key` 的前缀（合规的代理种类名，作 agent/tool 名）、`clientKey`（哈希）、脱敏后的 `label`、相位、时刻、结束状态。`key` 的其余部分、
  `detail`、文件路径都不上传。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-08 | upload.v1 v1.3、presence.v1 v1.1、config.v1 v1.4（追加）：仓主 2026-10-08「我的操作自动替代进行中计时」。规则可以只到项目（`projectId`，上传 `suggestion.projectId`）；网页设置 `autoTrack` 打开时在场心跳带规则对当前窗口的猜测 `guess`（同一个匹配函数，本机算，只发目标 id 与把握）。`autoTrack` 关着时两条请求体与此前逐字节相同。**取代条目**：本文件开头「它从不写事实：人在界面上确认了，才由 nexus-core 写 `session.completed`」仍然成立于本程序（它只上传建议）；但设备打开 `autoTrack` 后，nexus-core 会把规则把握 ≥ 0.9 的段直接记成事实（nexus-core v2.14） |
| 2026-10-02 | upload.v1 v1.2、config.v1 v1.3（追加）：仓主 2026-10-02：终端类程序**按标签页分段**（程序 + 归一化标题各自一条流，`durationSeconds` 只算自己的碎片，段的墙钟跨度可交叠）；新配置 `segmentByTitle`（缺省开）/ `segmentByTitleApps`（缺省内置终端名单），网页设置 `detector.settings.v1` v1.2 可改；短于 1 秒的碎片不参与合并（保证 `startAt` 按秒唯一）；状态文件增 `sent`（游标之后已送达的区间，重算时挖掉，不重发也不重复计）。上传形状不变；关掉 `segmentByTitle` = v1.1 的切法 |
| 2026-09-30 | 实现在场心跳与状态文件桥（状态改为已实现）；在场心跳可由网页设置 `presence`（`detector.settings.v1` v1.1）开关；`agentStatusIgnore` 缺省维持 `[]`。单实例锁改为系统建议锁（修容器里 pid 1 重启后永远拒绝），`ai-detector.lock` 的对外语义（`once` 在 `run` 跑着时拒绝）不变 |
| 2026-09-30 | config.v1 v1.2（追加）：分类规则可以存在 nexus-core（`detector.rules.v1`），每轮拉；服务端存过就以它为准，本机 `rules.json` 变成没存过 / 拉不到时的后备。上传形状不变 |
| 2026-09-30 | v0.3 追加（契约先行）：`ai-detector.presence.v1` 在场心跳、`ai-detector.agent-status-bridge.v1` 状态文件桥；都默认关，新增配置 `presence`/`presenceSeconds`/`agentStatusFile`/`agentStatusIgnore`。上传、脱敏、游标的既有承诺一条不改 |
| 2026-09-30 | upload.v1 v1.1、config.v1 v1.1、archive.v1：仓主 2026-09-30：强制脱敏（密码、密钥、私钥、银行卡、身份证，写死在程序里）；隐私做成可单独勾选的选项（`detector.settings.v1`，缺省 = v1.0 行为，另加地址 / IP / 用户名默认开）；标题代号；离开判定四项；段增可选 `idle`；本机留档与 `archive` / `preview` / `pseudonyms` 命令；每轮从 nexus-core 拉网页设置。在场心跳、状态文件桥里的「脱敏」同样指强制脱敏 + 隐私选项 |
