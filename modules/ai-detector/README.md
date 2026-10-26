# ai-detector · 自动拿活动数据

不用记得按计时器：ai-detector 在你电脑上读 [ActivityWatch](https://activitywatch.net/)
记下的「当前在用哪个程序、窗口标题是什么、人在不在电脑前」，每 5 分钟合并成一段一段，
**在本机脱敏**后，作为**待确认建议**发给你的 HoneyComb。你在界面上确认了，才算正式记录；
没确认的建议不会出现在任何统计、圆环、甘特里。

对外行为的唯一事实是 [`module_docs/contract.md`](module_docs/contract.md)。

## 采集什么、什么会离开这台电脑

| | 留在本机 | 发给 HoneyComb |
|---|---|---|
| 每次窗口切换（程序、完整标题、秒级时刻） | ✅（在 ActivityWatch 里） | ❌ 永不 |
| 合并后的「段」：起止、在电脑前的秒数、程序名、**脱敏后的**标题、分类建议 | | ✅ |

脱敏（上传前在本机做）：

- 邮箱 → `[邮箱]`，手机 / 电话号 → `[电话]`，6 位以上数字串 → `[数字]`。
- 标题里的网址（http、ftp、smb、ssh……，写没写 `https://` 都算）只留域名；`file://` 与
  `\\服务器\共享` 路径整个换成 `[路径]`；本机路径（`C:\Users\你\…\a.docx`、`/home/你/…`、`~/a.md`，
  紧贴在中文或括号后面也算）只留文件名。`and/or`、`10:30/11:00` 这种不动。
- 聊天、邮件、密码管理器（微信、QQ、钉钉、飞书、Telegram、Slack、Outlook、1Password、KeePassXC……
  名单可改，见 `appOnlyApps`）：**只留程序名，标题为空**。
- 浏览器：只留 `域名 · 页面标题`，完整网址、路径、查询串一律不发。无痕窗口只留程序名。
  域名和标题来自 ActivityWatch 的浏览器扩展，而且要窗口标题以那个标签页的标题开头才算数；
  **没装扩展、扩展没报这段时间、或标题对不上（比如扩展没在无痕窗口里跑）的浏览器段只留程序名**——
  窗口标题里可能有无痕页面，分不清就不发。

隐私默认值：**同步默认关**，要你自己打开；随时可以暂停。关着 / 暂停时程序不读也不发任何东西，
而且**那段时间以后也不会补传**。

### 隐私选项（可以逐项勾选）

上面是缺省值。每一项都能单独改：在 HoneyComb 的 **「自动记录」页**勾选（推荐，改完下一轮生效），
或者写进本机配置文件的 `privacy` 节。网页上设过的，以网页为准。完整说明、缺省值、改前改后的例子见
[`contracts/detector.settings.v1`](../../contracts/detector.settings.v1/contract.md)。

| 选项 | 缺省 | 可选 |
|---|---|---|
| 文件路径 `paths` | `full`：只留文件名 | `half`：只留「上一级目录/文件名」，去掉盘符、用户名；`off`：原样 |
| 路径白名单 `pathWhitelist` | 空 | 正则，命中的片段原样保留 |
| 窗口标题 `titles` | `keep` | `pseudonymize`：换成「窗口名1」「路径1」这样的代号（对照表只在本机，`ai-detector pseudonyms` 查看）；`drop`：只留程序名 |
| 聊天 / 邮件 / 密码管理器只留程序名 `appOnly` | 开 | 名单 `appOnlyApps` 可改 |
| 浏览器 `browser` | `domain`：域名 · 页面标题 | `full`：完整网址（`queryStrings` 管是否去掉 `?查询`）；`off`：只留程序名 |
| 邮箱 / 电话 / 中文地址 / IP / 用户名主机名 / 6 位以上数字 | 全开 | 各自单独开关 |
| 编号代号 `numberedAliases`（只认本机配置） | `["ips","emails","users","hosts"]` | 把对应类型的 `[IP]` / `[邮箱]` / `[用户]` / `[主机]` 换成 `公网IP1`、`邮箱2`、`用户1`、`主机1` 这样的编号（IP 分本机 / 局域网 / 组网 / 公网各自编号）；加 `"domains"` 把网址里的域名换成 `网站1`（端口保留）；`[]` = 都用方括号。对照表 `aliases.json` 只在本机，坏了就不上传；`ai-detector preview` 会列出用到的代号与原值 |

### 强制脱敏（关不掉）

不管上面怎么选，**密码**（`password=…`、`密码：…`）、**密钥 / 访问令牌**（AWS、GitHub、OpenAI / Anthropic /
DeepSeek 的 `sk-…`、Slack、Google、JWT 等，规则取自 gitleaks）、**私钥**、**银行卡号**（Luhn 校验）、
**身份证号**（校验位）都会先被换成 `[已隐藏:密码]`、`[已隐藏:密钥]`、`[已隐藏:银行卡]`……
网页上是灰的，配置文件里写 `"secrets": false` 之类只会得到一条警告。

懂源码、会自己打包的人确实要关：改 `secrets.go` 顶部的 `mandatory*` 常量（`true` → `false`），
按下面「自己从源码构建」重新编译。官方发布的程序这几项全开。

### 离开怎么算（可选，缺省按 ActivityWatch 原样）

- **无操作多久算离开**（`afkThresholdMinutes`）：短于 N 分钟的离开照算在电脑前。
- **有声音的浏览器标签算在电脑前**（`audibleAsPresent`，需要浏览器扩展）。
- **阅读 / 会议程序**（`focusAppsEnabled` + 名单 `focusApps`，每次离开至多算 `focusMaxMinutes` 分钟，缺省 60）。
- **无操作的时段也报上来让我决定**（`idleSuggestions`）：前台窗口没换、但人没动的时段单独成段，
  标「无操作，可能在阅读」、把握 ≤ 0.3，在待确认列表里由你决定算不算。

### 本机留档：发出去了什么，自己查

每次发给 HoneyComb、发给分类服务的内容，都在本机 `archive/YYYY-MM-DD.jsonl` 留一份：
**原始标题**（只过了强制脱敏）和**实际发出去的标题**并排。只在你电脑上，默认留 30 天（`archiveDays`）。

```sh
ai-detector archive              # 今天发了什么
ai-detector archive 2026-09-30   # 某一天
ai-detector preview 20           # 最近 20 段按现在的选项会变成什么（改选项前先看效果，不联网）
ai-detector preview --app code --title "vim /home/me/work/a.go"
ai-detector pseudonyms           # 标题代号对照表
```

## 1. 装 ActivityWatch

到 <https://activitywatch.net/downloads/> 下载，装好后开着就行（本地接口 `http://localhost:5600`）。

- **Windows**：直接装。
- **macOS**：首次运行要在「系统设置 → 隐私与安全性 → 辅助功能」里给 ActivityWatch 授权，
  否则拿不到窗口标题。
- **Linux X11**：直接装。
- **Linux GNOME Wayland**：见下面单独一节。
- 想让浏览器段带上域名和页面标题：装 ActivityWatch 的浏览器扩展（aw-watcher-web，Chrome / Firefox
  商店里有）。不装的话浏览器段只有程序名。
- 只读**本机**的记录：ActivityWatch 开了多设备同步时，别的电脑的桶不会被读。主机名改过导致对不上时，
  `status` 会报错并列出现有的桶，在配置里填 `windowBucket` / `afkBucket` 即可。

### Linux GNOME Wayland

ActivityWatch 自带的 `aw-watcher-window` 在 GNOME 的原生 Wayland 窗口上**什么都看不到**
（只看得见走 XWayland 的老程序），所以窗口桶是空的或只有零星几条，ai-detector 就没东西可传。
2026-09-30 在 Ubuntu 26.04 / GNOME 50.1 上验证可用的做法：

1. 装 GNOME 扩展 **Focused Window D-Bus**（`focused-window-dbus@flexagoon.com`），让 GNOME 通过 D-Bus 报出前台窗口：

   ```sh
   gdbus call --session --dest org.gnome.Shell.Extensions \
     --object-path /org/gnome/Shell/Extensions \
     --method org.gnome.Shell.Extensions.InstallRemoteExtension focused-window-dbus@flexagoon.com
   ```

   桌面上会弹一个确认框，点「安装」。命令可能打印一个 `NoReply` 错误，**其实已经装上了**
   （`gnome-extensions list --enabled | grep focused` 能看到）；不用重新登录。
2. 用 [awatcher](https://github.com/2e3s/awatcher) **v0.4.0 的 bundle 版**（自带 aw-server，一个程序顶
   ActivityWatch 的服务 + 窗口记录器 + 离开检测）代替 ActivityWatch 自带的那一套：先退出 ActivityWatch，
   再运行 awatcher bundle。接口还是 `http://localhost:5600`，ai-detector 不用改配置；桶名按主机名找得到。
3. 不想装扩展的替代办法：登录时在齿轮里选 **Xorg** 会话（「Ubuntu on Xorg」），自带的记录器在 X11 下正常。

**离开（afk）是怎么判的**：离开检测看的是「多久没碰键盘鼠标」（ActivityWatch 缺省 3 分钟，awatcher 同样），
不是看窗口。所以看视频、读长文、开会时人在但没动，会被记成离开，这段时间不进「在电脑前」的秒数——
需要的话用上面「离开怎么算」里的选项（出声的标签页、阅读 / 会议程序、无操作段让你决定）。

## 2. 装 ai-detector

从 [Release](../../releases) 下载对应系统的单文件（未签名：Windows 会弹 SmartScreen，点「仍要运行」；
macOS 需要在「隐私与安全性」里放行一次），放到固定位置，然后：

| 系统 | 文件 |
|---|---|
| Linux x86_64 | `ai-detector-<tag>-linux-amd64` |
| Windows x86_64 | `ai-detector-<tag>-windows-amd64.exe` |
| macOS Apple Silicon | `ai-detector-<tag>-darwin-arm64` |
| macOS Intel | `ai-detector-<tag>-darwin-amd64` |

校验完整性（可选）：同一个 Release 里的 `ai-detector-<tag>-SHA256SUMS.txt` 记了四个文件的
SHA256，下载后 `sha256sum -c --ignore-missing ai-detector-<tag>-SHA256SUMS.txt` 核对。

未签名二进制的放行方式：

- **macOS**：「系统设置 → 隐私与安全性」，拉到底点「仍要打开」（Open Anyway）；或命令行
  `xattr -d com.apple.quarantine ./ai-detector-<tag>-darwin-*`（同一件事，命令行更快）。
- **Windows**：SmartScreen 弹窗点「更多信息 → 仍要运行」（More info → Run anyway）。

```sh
ai-detector init        # 写默认配置（同步是关的）和规则文件样例
# 编辑配置：填 cockpitUrl 和 deviceToken（见下）
ai-detector once        # 跑一轮看看
ai-detector enable      # 打开同步
ai-detector autostart install   # 开机自启（可选）
ai-detector run         # 或者现在就常驻
```

其他命令：`status`（看上一轮结果、隐私选项、强制脱敏状态）、`archive` / `preview` / `pseudonyms`（见上）、`pause` / `resume`、`disable`、`autostart uninstall`。
自启记的是你运行 `autostart install` 时用的那个路径（符号链接不展开，版本管理器升级后照样有效）；
**把程序挪到别处后要重新运行 `autostart install`**。
同一个配置目录只能有一个 `run`；`run` 在跑时 `once` 会拒绝（避免两个进程抢游标）。这把锁是系统锁，
程序一退出（包括崩溃、断电、容器重启）就自动释放，留下的 `ai-detector.lock` 文件不会挡住下次启动。

**设备令牌**：桌面程序没有浏览器登录态，用设备令牌访问 HoneyComb（`Authorization: Bearer`）。
令牌由 HoneyComb 的认证服务发放；用自带的占位认证时，在服务器上执行
`docker compose run --rm -T auth python /app/auth_stub.py token <账号>` 拿一个
（该功能随 v0.3 的设备令牌一起提供），吊销用 `revoke <账号>`。令牌只能调接口，不能登录网页。
v0.4 起令牌分范围：检测程序要上传活动、报在场心跳，**需要 `write`（读写）范围**——在网页「待批准 → Agent 令牌」里选「读写」，
或命令行 `token <账号> --scope write --name 检测程序`（命令行缺省就是 `write`）。`read` / `report` 令牌上传会被 403。
单独吊销这一个：网页列表里点「吊销」，或 `revoke-token <令牌 id>`。

自己从源码构建（不用装 Go，有 docker 即可）：

```sh
docker run --rm -v "$PWD":/src -w /src -e CGO_ENABLED=0 -e GOOS=windows -e GOARCH=amd64 \
  golang:1.23 go build -trimpath -ldflags="-s -w" -o ai-detector.exe .
```

## 3. 规则文件（`rules.json`）

默认分类方式。按顺序第一条命中生效；`app` / `title` 是不分大小写的正则，空 = 不限；
`title` 匹配的是**脱敏后**的标题（也就是你在待确认列表里看到的那个）。

```json
{
  "rules": [
    { "app": "code|goland|idea", "title": "garden", "taskId": "t_a1", "confidence": 0.9 },
    { "app": "firefox|chrome", "title": "^github\\.com", "taskId": "t_b2" },
    { "app": "blender", "taskId": "t_c3", "confidence": 0.7 },
    { "title": "blog", "projectId": "p_2" }
  ]
}
```

规则的目标可以是任务（`taskId`），也可以只到项目（`projectId`，时间记到该项目的「未分类」，以后再归到具体任务）；两个恰好写一个。

`taskId` 在蜂巢里打开任务就能看到，或看 `GET /api/core/views/tree` 的输出。规则写错
（JSON 不合法、正则写坏）时**这一轮不上传**，`status` 会告诉你哪条错了，改好后自动补上。

**更省事的办法：让 AI 助理写。** 对你接上 HoneyComb MCP 的 AI CLI（或开了内置聊天时在 Cockpit「待批准」页里）说「帮我按终端标签页整理分类规则」，
助理读你的项目、任务和最近的窗口标题，写出一整套规则的草稿；你在「待批准 → 分类规则草稿」看过改动点「应用」。
网页上存过规则后，本程序每轮从服务端拉、**不再读本机 `rules.json`**（服务端没存过或连不上时才用它）。

可选的外部分类服务：配 `classifierUrl`，规则没认出来的段会发给它，接口见
[`contracts/activity.classifier.v1`](../../contracts/activity.classifier.v1/contract.md)。
地址必须是 `https://`（本机 `localhost` 除外）；它要鉴权就填 `classifierToken`——
**设备令牌不会发给分类服务**。服务挂了不影响上传，那些段只是没有建议。

## 4. 配置参考（`ai-detector.json`）

位置：Windows `%APPDATA%\honeycomb\`、macOS `~/Library/Application Support/honeycomb/`、
Linux `~/.config/honeycomb/`；设环境变量 `AI_DETECTOR_HOME` 可换目录。程序每轮重读配置，改完不用重启。

| 字段 | 缺省 | 说明 |
|---|---|---|
| `enabled` | `false` | 总开关。关着时不读不发 |
| `paused` | `false` | 暂停。暂停期间的活动以后也不补传 |
| `cockpitUrl` | `http://localhost:8800` | HoneyComb 地址；整站挂子路径就带上，如 `https://host/Cockpit/` |
| `deviceToken` | 空 | 设备令牌（`write` 范围） |
| `deviceId` | `init` 时随机生成 | 本机标识，别改（服务端用它防重） |
| `activityWatchUrl` | `http://localhost:5600/api/0` | |
| `intervalMinutes` | 5 | 多久同步一轮 |
| `mergeGapMinutes` | 5 | G：同一件事中间断开不超过 G 分钟算一段（短暂切出去也吸收进来） |
| `minSegmentMinutes` | 3 | M：在电脑前不到 M 分钟的段丢掉 |
| `maxBacklogHours` | 72 | 离线太久，超过这么多小时的积压丢弃 |
| `rulesFile` | 配置目录下 `rules.json` | |
| `classifierUrl` | 空 | 外部分类服务地址，空 = 只用规则。须 https（本机回环除外） |
| `classifierToken` | 空 | 分类服务自己的令牌，作为 `Authorization: Bearer` 发给它；与设备令牌无关 |
| `windowBucket` / `afkBucket` | 空 | ActivityWatch 桶 id，空 = 按本机主机名找 |
| `appOnlyApps` | 聊天 / 邮件 / 密码管理器名单 | 这些程序只留程序名 |
| `browserApps` | 常见浏览器名单 | 这些程序只留域名 + 页面标题 |
| `privacy` | 见「隐私选项」 | 网页上设过就以网页为准 |
| `idle` | 全关 | 见「离开怎么算」 |
| `archiveDays` | 30 | 本机留档保留天数 |

| `awStartCommand` | 按系统缺省 | 拉起 ActivityWatch 的命令，argv 数组；只认本机配置（见「面板上的状态与「重启 ActivityWatch」」） |
| `awAutoStart` | `true` | ActivityWatch 连续两次连不上时自动拉起；`false` 关闭 |
| `awLogUpload` | `false` | 把脱敏后的 `aw-start.log` 末尾随健康报告上传，让 AI 助理能帮着排查；**只认本机配置**（见「启动日志上传」） |
| `presence` | `false` | 在场心跳（见下）。网页「自动记录」页设过就以网页为准 |
| `presenceSeconds` | 5 | 心跳间隔，5–300 秒；大于 30 的实际按 30 秒发（一拍只往回带最近 60 秒的停留，间隔再长会丢）。旧版本生成的配置文件里是 15，想更跟手就改成 5 |
| （网页上的）`autoTrack` | 关 | 「允许 AI 管理进行中的任务」。**只在网页「自动记录 → 活动检测设置」里开关**，本机配置没有这个键（见下） |
| `agentStatusFile` | 空 | 状态文件桥读的文件路径，空 = 关（见下） |
| `agentStatusIgnore` | `[]` | 状态文件里 `key` 以这些前缀开头的条目不报 |
| `agentLights` | `true` | 红绿灯（见下）：约每秒看本机 Claude Code / Codex / Hermes，写 `~/.agent-traffic-light/agents.json` |
| `agentLightsReport` | `["hermes"]` | 红绿灯替哪几类上报（`claude` / `codex` / `hermes`）；其余只显示。写错值红绿灯不工作并写日志 |
| `segmentByTitle` | `true` | 终端按标签页分段（见下）。网页「自动记录」页设过就以网页为准 |
| `segmentByTitleApps` | 内置终端名单 | 哪些程序按标签页分段；不写 = 内置名单（GNOME Terminal、Ptyxis、kitty、Alacritty、WezTerm、Konsole、iTerm2、Windows Terminal……） |

**终端按标签页分段**：一个终端窗口开很多标签页、每个标签页干一件事（比如各跑一个 AI 代理）时，
每个标签页（程序 + 标题）各自成段、各算各的时间；在标签页之间来回切，回到同一个标签页（间隔不超过 G）
仍接在它自己的段上。标题开头的状态符号（`✳`、转圈动画）、结尾的「 - 程序名」不算区别。所以这些段的起止时间可以互相
盖着，但时长不会重复算。没起名字的 shell 标签页（标题是「用户@主机: 当前目录」）换目录不拆，算同一件事。
标签页标题以别的方式一直在变（程序把进度写进标题）的话每个标题各算各的，不足 M 分钟的都会丢——
给标签页起个固定名字，或者 `"segmentByTitle": false` 关掉。

程序名比较时忽略大小写、`.exe`、空格和连字符，所以 `WeChat.exe` 与 `wechat` 是同一个。

日志：`run` 模式写 `ai-detector.log`（与配置同目录），超过 5 MB 启动时清空。

配置文件里有设备令牌。macOS / Linux 上它的权限是 0600（只有你能读）；**Windows 上程序没有另设权限**，
依靠的是 `%APPDATA%` 目录默认只允许本用户访问——别把配置目录挪到共享位置。

## 5. 实时泳道（v0.3，可选，默认都关）

HoneyComb 的时间线页可以把「人一条线、AI 代理多条线」画在一起。ai-detector 能给它两样东西，都只在
`ai-detector run` 常驻时工作（`once` 不做），关 / 暂停时零请求：

### 在场心跳（`presence`）

每 `presenceSeconds` 秒（缺省 5）把「此刻前台的程序 + 脱敏后的标题 + 是不是离开了」报给 HoneyComb，
页面上的人那条线就是实时的。**脱敏和上传段完全同一套**：强制脱敏、隐私选项（路径、标题代号 / 丢弃、
聊天程序只留名、浏览器要对得上标签页）、网页上的设置——每次发之前都重新拉一次网页设置。离开时程序名和标题都发空。
发不出去就丢掉，不重试、不补发；服务端只留最近 2 小时，不进统计、不进导出。

```json
{ "presence": true, "presenceSeconds": 5 }
```

每一拍带的不只是「此刻这一个窗口」，而是上一拍以来你依次待过的窗口和各自的秒数（串行、不重叠，离开的时间已扣掉）——
几秒钟就切一次窗口时，中间那些短的停留也不会丢。页面上代理泳道里的蓝条「你在看」就是从这里来的：窗口标题就是某个
在跑的代理会话的名字时，这几秒记成「你在看它」。

也可以在网页「自动记录」页打开（`detector.settings.v1` 的 `presence`；那里是「用本机配置」时按上面这个字段）。

### 面板上的状态与「重启 ActivityWatch」（v0.4）

开着 `ai-detector run` 时，它每次心跳之后把「自己在线 + ActivityWatch 怎么样」报给 HoneyComb（正常 / 连不上 / 最近一分钟没有窗口记录 / 已暂停 / 未开启，
不含任何窗口内容）。网页「自动记录 → 活动检测设置」的设备行下面能看到，还有两个按钮：「检测」刷新状态；「重启 ActivityWatch」让**这台电脑上的检测程序**把 ActivityWatch 再拉起来
（只重启 ActivityWatch 服务，不重启检测程序，也不重启面板）。`ai-detector status` 也会打印最近一次报告的状态。

- 用什么命令拉起，**只看本机配置**，网页和服务器改不了、也不会下发命令：`awStartCommand`（argv 数组，不经 shell），例如
  `"awStartCommand": ["C:\\Users\\me\\AppData\\Local\\Programs\\ActivityWatch\\aw-qt.exe"]`。不写就按系统缺省：Windows 找
  `%LOCALAPPDATA%\Programs\ActivityWatch\aw-qt.exe`，macOS `open -a ActivityWatch`，Linux 在 PATH 里找 `aw-qt`；都找不到，面板上显示「这台电脑上没配置启动命令」。
- **自动拉起默认是开的**：ActivityWatch 连续两次心跳都连不上（`paused` / 没开同步时不算）时，检测程序自己用同一条命令拉起它，不用点按钮。
  两次拉起至少隔 30 秒（按钮和自动共用）；滚动 24 小时内自动拉起最多 20 次，超过就不再自动拉（按钮不受影响）；连拉 3 次还没起来就停手，等 ActivityWatch 连续稳定 60 秒再恢复。不想要就在配置里写 `"awAutoStart": false`（同样只认本机配置）。
- **“已启动”是真的起来了**：拉起后检测程序最多等 20 秒看 ActivityWatch 的接口通没通，通了才算成功；没通就是失败，面板会给出大类原因（找不到程序 / 马上退出 / 接口没起来 / 不在桌面会话里 ……）。
  被拉起程序的输出写在配置目录的 `aw-start.log`（上一次的留作 `aw-start.log.1`），`ai-detector status` 会打印最近一次拉起的详情和日志最后 20 行；这些只在你的电脑上，不会发给服务器。
- 网页上的按钮在检测程序暂停 / 没开同步时也能用（它只检测并重启 ActivityWatch）；自动拉起在暂停 / 没开同步时不会动。
- **限制**：按钮和自动拉起都只在检测程序自己正在你的桌面会话里运行时有用——它救不了已经死掉的检测程序（面板会显示「离线」，按钮禁用）。
  让检测程序开机自启用 `ai-detector autostart install`；重启后 ActivityWatch 自己的开机自启（它的托盘设置里）才是根治。

### 启动日志上传（`awLogUpload`，v0.4，默认关）

ActivityWatch 起不来时，真正的原因在 `aw-start.log` 里。默认它**只留在你的电脑上**；面板上的「最近事故」和 AI 助理（经 MCP 的 `get_detector_health`）只能看到枚举级的原因。
想让 AI 助理看到真实的日志来帮你排查，在**这台电脑**的配置文件（`ai-detector status` 会打印它的位置：Linux `~/.config/honeycomb/ai-detector.json`、macOS `~/Library/Application Support/honeycomb/ai-detector.json`、Windows `%APPDATA%\honeycomb\ai-detector.json`）里加一行，保存即可（每轮重读，不用重启）：

```json
{ "awLogUpload": true }
```

- **这个开关只能在本机改**：服务器、网页、AI 助理都改不了。网页上只显示它开没开，没有按钮。随时改回 `false`，下一份报告就告诉服务器「关了」，服务器上的那份日志随即被删除。
- 开着时，每份健康报告多带 `logUpload: true`，以及 `aw-start.log` 的**脱敏尾巴**——只在内容变了才发：最多 40 行、4096 字节、每行 300 字符；
  先在本机脱敏，策略是**整行盖掉**：含 `password` / `token` / `secret` / `cookie` / `authorization` / `bearer` / `api key` 等词（以及 `key=`、`--key` 这样的写法）的整行换成
  `[整行已隐藏：可能含凭据]`（行首时间戳保留），这样一行以 `:` 或 `=` 结尾时下一行也一起盖；命令行里的 `-p 值` / `-S 值` 这类短选项**只是尽力而为**，认不全；
  其余的行里：家目录换成 `~`、你的用户名（不分大小写的子串，短于 3 个字符或 `root` / `user` / `admin` 除外）换成 `<user>`、网址里的 `用户:密码@`、≥ 24 位的十六进制 / 随机串都盖掉，控制字符去掉。
  拿不准（读不到家目录 / 用户名、脱敏后仍含它们）就整段不发。已知盖不住的：全角 / 形近字 / 变形拼写的关键词、值在关键词**前一行**、既没有关键词也没有固定形状的口令、没有数字的 24 位以上纯字母串。**脱敏是尽力而为，不是保证——打开开关后，请到网页「自动记录 → 活动检测设置 → 查看启动日志」看一眼实际上传了什么**，不放心就改回 `false`。
- 日志对服务器和 AI 助理都是**不可信的文本**：只当数据显示，不当指令。
- 连着一台还没升级的服务器（不认这两个新字段，回 422；或日志一长超过它 1 KiB 的体积上限，回 413）时，检测程序会去掉它们重发，一小时内不再上传日志，一小时后再试一次。

### 允许 AI 管理进行中的任务（网页上的 `autoTrack`，默认关）

在网页「自动记录 → 活动检测设置」勾上之后（要同时开着在场心跳）：

- 心跳里多带一样东西：分类规则对**当前窗口**的猜测（哪个任务 / 项目、把握多少）。猜测在本机算，和上传段用的是同一套规则；
  发出去的只有任务 / 项目的 id 和把握，程序名、标题还是原来那份脱敏过的，没有多发任何内容。规则没命中就不带。
- 你没有手动计时的时候，计时页把你正在做的事显示成「自动 · 项目 / 任务」；规则认不出的窗口停留一会儿，页面会问你记到哪。
- 规则把握 ≥ 90% 的段上传后**直接记成时间**，不用再逐条确认；记错了在「自动记录」页里改归属。
  你手动计时的那段时间 AI 不插手。

关掉（缺省）就和以前完全一样：一切都只是待确认的建议。

### 红绿灯（`agentLights`，v0.5）

只读 `~/.claude/sessions/*.json`、`~/.codex/sessions` 下最近的会话记录（只看事件类型）、`~/.hermes` 下的 `gateway_state.json`，把状态写进
`~/.agent-traffic-light/agents.json`（格式同下面的状态文件）。`agentLightsReport` 里的类按状态文件桥同样的规则上报，
状态文件桥会跳过这几类。Claude Code 有钩子，所以缺省只上报 Hermes。网关进程已不在的 Hermes 配置不显示也不上报。

```json
{ "agentLights": true, "agentLightsReport": ["hermes"] }
```

### 状态文件桥（`agentStatusFile`）

给**没有钩子**的 AI 代理用（Claude Code 用 `tools/agent-hooks` 的钩子更准，不用这个）。
某个程序（例如你自己的桌面状态守护进程）把各代理的状态写进一个 JSON 文件，ai-detector 约每 3 秒读一次，
把变化报成 HoneyComb 的代理运行和相位：

```jsonc
{ "updated_at": 1790000000,          // Unix 秒；早于现在 30 秒以上 = 过期，这一轮什么都不报
  "agents": [
    { "key": "codex:7f3a", "label": "garden", "state": "working", "detail": "…" } ] }
// state：working | waiting_input | waiting_permission | complete | idle | error
```

```json
{ "agentStatusFile": "/home/me/.local/state/agents/status.json", "agentStatusIgnore": ["claude:"] }
```

- 第一次看到一个 `key` 开一条运行；状态变了报相位（从「等你」回到「干活」时记一次「你回话了」）；`key` 消失就结束
  （最后是 `error` 记失败）。`key → 运行` 记在状态文件里，重启不会多开。
- **离开本机的只有**：`key` 冒号前的代理种类（只认 claude / codex / hermes / gemini / opencode / aider / cursor，
  别的一律报 `agent`）、`key` 的哈希、脱敏后的 `label`、相位和时刻。`key` 其余部分、`detail`、文件路径都不发。
- 已经装了 Claude Code 钩子的，把 Claude 的前缀填进 `agentStatusIgnore`，否则同一个会话会有两条线。
- 网络断了：没发出去的变化带着原来的时刻排队，恢复后原样补发（服务端去重）。
