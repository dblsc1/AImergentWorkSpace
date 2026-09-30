# detector.settings.v1 —— 活动检测程序的隐私与离开设置

> **状态**：v1 规范性。本文件是「ai-detector 的隐私选项、离开判定选项长什么样、存在哪、谁能改」的
> **唯一事实**。只增不改不删；不兼容的改动发 `detector.settings.v2`，新旧并行。
>
> - 提供方：`modules/nexus-core`（按租户、按设备存设置；端点见「二」）。
> - 消费方：`modules/ai-detector`（每轮拉一次，按「三」生效）；Cockpit「AI助理」页（读写，给人勾选）。
> - **强制脱敏不在本文件里**：密码、密钥、私钥、银行卡号、身份证号在 ai-detector 程序里写死，
>   任何设置都关不掉——本 schema 里**没有**这些键，发了按未知键 `422`。清单见
>   `modules/ai-detector/module_docs/contract.md`「强制脱敏」。

```yaml
provides:
  - id: detector.settings.v1
    summary: >
      设置文档 DetectorSettings（schemaVersion 1：privacy + idle 两节，字段、类型、缺省、取值范围见「一」），
      与存取它的三个端点：GET/PUT/DELETE /api/core/detector/settings?deviceId=、GET /api/core/detector/devices。
      PUT/DELETE 只收人的会话（带 Bearer 设备令牌的请求 403）。
```

## 一、设置文档 `DetectorSettings`（规范性）

```jsonc
{
  "schemaVersion": 1,
  "privacy": {
    "paths": "full",            // "full" | "half" | "off"
    "pathWhitelist": [],        // 正则数组
    "titles": "keep",           // "keep" | "pseudonymize" | "drop"
    "appOnly": true,
    "appOnlyApps": null,        // null | 字符串数组
    "browser": "domain",        // "domain" | "full" | "off"
    "queryStrings": true,
    "emails": true,
    "phones": true,
    "addresses": true,
    "ips": true,
    "usernames": true,
    "longNumbers": true
  },
  "idle": {
    "afkThresholdMinutes": 0,   // 整数 0–240
    "audibleAsPresent": false,
    "focusAppsEnabled": false,
    "focusApps": null,          // null | 字符串数组
    "focusMaxMinutes": 60,      // 整数 1–480
    "idleSuggestions": false
  },
  "presence": null              // v1.1 追加：null | true | false
}
```

**校验（服务端写入时，全部 `422`）**：

- 顶层只许 `schemaVersion`、`privacy`、`idle`、`presence`（v1.1）四个键；`privacy`、`idle` 里只许下表列出的键。
  **任何未知键 → 422**（包括想关强制脱敏的键，如 `secrets`、`passwords`、`bankCards`）。
- `schemaVersion` 必填，必须是整数 `1`。`privacy` / `idle` 及其中每个键都**可省略**，省略 = 取缺省值；
  服务端存、回的永远是**补齐缺省值后的完整文档**。
- 类型严格：布尔就是 `true`/`false`（`"true"`、`1` 都 422）；整数就是整数（`60.0` 422）；枚举只收列出的值。
- `pathWhitelist`：≤ 20 条，每条 1–200 个字符，必须能编译成正则，且**不许用 RE2（Go）不支持的构造**：
  前后查找 `(?=` `(?!` `(?<=` `(?<!`、反向引用 `\1`…`\9`、`(?P=name)`。服务端据此拒；
  ai-detector 自己再编译一次，编译不过的那条**跳过并写日志**（少保留 = 更保守）。
- `appOnlyApps` / `focusApps`：`null`，或 ≤ 200 项的字符串数组，每项 1–64 个字符。
- 请求体超过 64 KiB → `413`。

### privacy 节：每项单独可选

标题的处理顺序：强制脱敏（关不掉）→ 白名单片段保护 → 下表各项 → 标题代号 / 丢弃。
「例子」一列是**同一条原始标题**在该项开 / 关时的差别（其余项取缺省）。

| 键 | 缺省 | 取值与作用 | 例子（前 → 后） |
|---|---|---|---|
| `paths` | `"full"` | 窗口标题里的本机路径（`C:\…`、`/home/…`、`~/…`）、Windows 共享路径 `\\server\share\…`、`file://` 网址。**`full`**：只留文件名；共享路径与 `file://` 整个换成 `[路径]`（v0.3 之前的行为）。**`half`**：去掉盘符、`~`、`home`/`Users` + 用户名、`/root`、共享路径的服务器名与共享名，**只留最后两级**（上一级目录 / 文件名），分隔符统一成 `/`；什么都不剩就是 `[路径]`。**`off`**：原样保留（用户名仍受 `usernames` 管） | `vim /home/alice/work/garden/plot.gd` → full `vim plot.gd`；half `vim garden/plot.gd`；off `vim /home/[用户]/work/garden/plot.gd` |
| `pathWhitelist` | `[]` | 正则（RE2 语法，区分大小写，要不分就写 `(?i)`）。标题里**命中的片段原样保留**，不受本表任何一项影响；片段之外的部分照常处理。**强制脱敏先于白名单**：白名单里就算写了卡号，卡号也已经被盖掉了 | 白名单 `garden/\S+`：`/home/alice/garden/plot.gd` → `/home/alice/` 照常处理成 `[路径]`，`garden/plot.gd` 原样 → `[路径]garden/plot.gd`。要整条路径原样，就让正则把整条路径包进去 |
| `titles` | `"keep"` | **`keep`**：保留（经上面各项处理后的）标题。**`pseudonymize`**：整条标题换成稳定代号「窗口名N」；标题里带路径的（`paths` 为 `half`/`off` 时才会剩下路径）换成「路径N」。同一条标题永远同一个代号（跨轮、跨天、跨重启），对照表**只存在本机**（`pseudonyms.json`），`ai-detector pseudonyms` 查看，从不上传。**`drop`**：所有程序都只留程序名，标题为空 | `plot.gd — garden — VS Code` → keep 原样；pseudonymize `窗口名3`；drop `""` |
| `appOnly` | `true` | 开：`appOnlyApps` 名单里的程序（聊天、邮件、密码管理器）标题为空，只留程序名 | 微信 `张三：明天把合同发我` → `""` |
| `appOnlyApps` | `null` | 上一项的名单。`null` = 用这台电脑本机配置里的名单（`init` 写入内置默认名单：微信、QQ、钉钉、飞书、Telegram、Slack、Outlook、1Password、KeePassXC……，见 ai-detector README）。程序名比较忽略大小写、`.exe`、空格、`-`、`_`、`.` | |
| `browser` | `"domain"` | 浏览器窗口（需要 ActivityWatch 浏览器扩展报出**对得上**的标签页）。**`domain`**：`域名 · 页面标题`。**`full`**：`完整网址 · 页面标题`（网址里的 `用户:密码@` 被强制脱敏盖掉；查询串看 `queryStrings`）。**`off`**：只留程序名。三种取值下，**无痕窗口、没有对得上的标签页记录**都只留程序名（分不清是不是无痕） | `https://github.com/a/b/pull/1?tab=files` + `Fix #1` → domain `github.com · Fix #1`；full `https://github.com/a/b/pull/1 · Fix #1`；off `""` |
| `queryStrings` | `true` | 开：`browser: "full"` 时去掉网址的 `?查询串` 与 `#片段`。关：整条网址保留（强制脱敏仍会盖掉查询串里的密钥 / 令牌） | 见上一行 |
| `emails` | `true` | 邮箱 → `[邮箱]` | `回复 alice@corp.com` → `回复 [邮箱]` |
| `phones` | `true` | 手机 / 座机（中国 11 位手机、区号座机、`+国家码` 国际号，组间可有空格或 `-`）→ `[电话]`。日期 `2026-09-28` 不算 | `客户 +86 138 1234 5678` → `客户 [电话]` |
| `addresses` | `true` | 中文街道地址（保守启发式）：「路 / 街 / 大道 / 巷 / 弄 / 胡同 + 门牌号 + 号」及其后的楼 / 栋 / 幢 / 座 / 单元 / 层 / 室，连同路名前至多 6 个汉字；或「小区 / 花园 / 公寓 / 家园 / 新村 / 苑 + 数字 + 栋 / 幢 / 号楼」→ `[地址]`。**已知误伤**：标题里的店名、文章标题含「XX路12号」也会被盖；**已知漏网**：没有门牌号的地址、英文地址。路名前更远的省市区可能留下 | `寄到北京市朝阳区建国路88号SOHO现代城5号楼1203室` → `寄到北京[地址]`（路名前 6 个汉字「市朝阳区建国」一起被盖） |
| `ips` | `true` | IPv4（每段 0–255）、IPv6（能被解析、且含至少一个数字）→ `[IP]`；网址主机名是 IP 时同样换掉 | `ssh 192.168.1.20` → `ssh [IP]` |
| `usernames` | `true` | ① 路径里 `home` / `Users` 后面那一级 → `[用户]`（只在 `paths: "off"` 时还看得见）；② 终端提示符形状 `用户@主机:` → `[用户]@[主机]:`；③ 本机登录用户名、本机主机名（各至少 4 个字符）原样出现在标题里 → `[用户]` / `[主机]`。**已知误伤**：用户名 / 主机名恰好是常用词（如主机名 `ubuntu`）时，标题里的这个词也会被换 | `alice@thinkpad: ~/work` → `[用户]@[主机]: ~/work` |
| `longNumbers` | `true` | 6 位以上连续数字（订单号、工号……）→ `[数字]` | `订单 20260926000123` → `订单 [数字]` |

标题里的其他网址（非浏览器程序的标题里出现的 `https://…`、`github.com/a/b`）**总是只留域名**，不受
`browser` 影响——`browser` 管的是浏览器扩展报来的标签页网址。

### idle 节：离开怎么算

ActivityWatch 的离开记录（`afkstatus` 桶的 `afk` 区间）缺省从「在电脑前的时间」里扣掉。下面几项让某些离开区间
**照算在电脑前**。几项同时满足时取对用户最有利的（算在电脑前的最多的）那个。

| 键 | 缺省 | 作用 |
|---|---|---|
| `afkThresholdMinutes` | `0` | 整数 0–240。`0` = 按 ActivityWatch 原样。`N > 0`：**短于 N 分钟**的离开区间算在电脑前。ActivityWatch 自己要无操作若干分钟（aw-watcher-afk 缺省 3 分钟）才记离开，所以 N 小于那个值等于没设 |
| `audibleAsPresent` | `false` | 开：离开区间内，前台是浏览器、且浏览器扩展报告当前标签页**正在出声**（`audible: true`，看视频、听课）的那部分算在电脑前。需要浏览器扩展 |
| `focusAppsEnabled` | `false` | 开：离开区间内，前台是 `focusApps` 名单里的程序（阅读器、会议软件）时算在电脑前，每个离开区间至多算 `focusMaxMinutes` 分钟，超出部分照常扣掉 |
| `focusApps` | `null` | 上一项的名单；`null` = 内置默认名单（Acrobat、SumatraPDF、Okular、Evince、Preview、Foxit、WPS、Zotero、腾讯会议 / wemeet、Zoom、Teams、飞书会议、钉钉会议、Webex、Google Meet 桌面版等，以 ai-detector README 为准）。比较规则同 `appOnlyApps` |
| `focusMaxMinutes` | `60` | 整数 1–480，见上 |
| `idleSuggestions` | `false` | 开：**其余**被扣掉的离开时间里，前台窗口没换的那部分不丢，单独合成段上传，标 `idle: true`、`confidence ≤ 0.3`、`reason` 以「无操作，可能在阅读」开头——**由人决定**算不算。关：照旧丢掉 |

### presence（v1.1 追加）

| 键 | 缺省 | 作用 |
|---|---|---|
| `presence` | `null` | 在场心跳（ai-detector 契约「在场心跳」）开关。`null` = 用这台电脑本机配置的 `presence`（缺省关）；`true` / `false` = 以网页为准。严格布尔或 `null`，其他 422。只增的键：v1.0 的检测程序读到会忽略；v1.0 存下的文档没有这个键，读方按 `null` 处理 |

## 二、端点（nexus-core 实现，规范性）

均在 `/api/core/` 下，经网关登录门，**按租户隔离**（`X-Nexus-Tenant`，同 nexus-core 其余端点）。
`deviceId` 格式 `^[A-Za-z0-9_.-]{1,64}$`（同活动建议上传），不合格式 `422`。错误体 `{detail: string}`。

```jsonc
// GET /api/core/detector/settings?deviceId=dev_3f9a1c2b7d4e5a60
// 人（cookie）与检测程序（Bearer 设备令牌）都可读。
// → 200
{ "deviceId": "dev_3f9a1c2b7d4e5a60",
  "settings": { /* 完整 DetectorSettings */ },   // 这台设备没在网页上设过 = null
  "updatedAt": "2026-09-30T10:00:00+00:00" }     // settings 为 null 时也是 null

// PUT /api/core/detector/settings?deviceId=dev_3f9a1c2b7d4e5a60
// 请求体：DetectorSettings（可省略的键省略即缺省）。只收人的会话。
// → 200，形状同 GET（settings 为补齐后的完整文档）
// → 403：请求带 Authorization: Bearer（设备令牌）；422：校验不过；413：请求体 > 64 KiB

// DELETE /api/core/detector/settings?deviceId=dev_3f9a1c2b7d4e5a60
// 删掉网页上的设置，这台设备回到用本机配置文件。只收人的会话。幂等。
// → 204；403 同 PUT

// GET /api/core/detector/devices
// → 200，按最近活动倒序
{ "devices": [
    { "deviceId": "dev_3f9a1c2b7d4e5a60",
      "lastUploadAt": "2026-09-30T09:55:02+00:00",     // 最近一次上传活动建议（建议过期清掉后可能变 null）
      "lastFetchAt": "2026-09-30T09:55:00+00:00",      // 检测程序最近一次拉设置（只记 Bearer 请求）
      "hasSettings": true,
      "settingsUpdatedAt": "2026-09-30T10:00:00+00:00" } ] }
```

- **设备令牌不能改设置（规范性）**：`PUT`、`DELETE` 请求带了 `Authorization: Bearer …` 一律 `403`，
  不看令牌真假。依据：auth.gate v1.2「带了 Bearer 就只看令牌」——检测程序只能带着令牌过门，
  而浏览器里的 Cockpit 页面走 cookie、不带 Bearer。**前提**：网关对 `/api/core/` 原样转发
  `Authorization` 头（`deploy/nginx` 缺省模板就是这样，不清这个头）。替换网关的部署必须保持这一点，
  否则服务端分不清，设备令牌就能改设置。门外的端到端验证在 `deploy/test/tokens.sh`（CI 多账号 job 经真网关跑：
  令牌 GET 200、PUT / DELETE 403，网页会话 PUT 200、DELETE 204）。
  理由：设备令牌存在用户电脑的配置文件里，被别的程序读走的机会比浏览器 cookie 大；
  它能改设置 = 能把隐私选项全关掉。
- `GET settings` 带 Bearer 时顺手记下这台设备的 `lastFetchAt`（`devices` 端点用）；不带 Bearer 的读不记。
- `devices` 列出：本租户存过设置的设备 ∪ 在活动建议里出现过的设备 ∪ 拉过设置的设备。
- 设置存在独立集合 `detector_settings`，**不进台账、投影、导出、快照恢复**（同活动建议：它不是事实）。

## 三、ai-detector 怎么用（规范性）

- **每轮**（同步开着且没暂停时）先 `GET settings?deviceId=<本机 deviceId>`，带设备令牌。
- `settings` 非 `null`：用它的 `privacy`、`idle` **整节替换**本机配置文件里的同名两节（不逐键合并）。
  `settings` 为 `null`，或服务器回 `404`（老版本 nexus-core 没有这个端点）：用本机配置文件。
- 其他失败（网络错误、5xx、401/403、响应不是合法 JSON）：**这一轮不上传**，游标不动，下一轮重试——
  不能因为拉不到设置就退回本机配置（可能比网页上设的宽松）把数据发出去。
- 读到本机不认识的键：忽略（服务端可能比程序新）；枚举值不认识：这一轮报错不上传。
- 设置只影响**以后**的上传：已经上传的建议不会被改写。

## 四、变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-30 | v1.1 | 追加顶层可选键 `presence`（`null` / 布尔，缺省 `null`）：在场心跳可在 Cockpit 设置里开关。只增，`schemaVersion` 仍为 `1` |
| 2026-09-30 | v1 | 首版。仓主 2026-09-30 定：隐私做成细粒度勾选（路径三档 + 白名单、标题三档、app-only 名单、浏览器三档、各类个人信息单独开关），强制脱敏不进设置；离开判定四项（阈值、出声标签页、阅读 / 会议程序、无操作段作低把握建议）；设置在 Cockpit「AI助理」页改、存 nexus-core、检测程序每轮拉 |
