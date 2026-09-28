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
  `\\服务器\共享` 路径整个换成 `[路径]`；本机路径（`C:\Users\你\…\a.docx`、`/home/你/…`）只留文件名。
- 聊天、邮件、密码管理器（微信、QQ、钉钉、飞书、Telegram、Slack、Outlook、1Password、KeePassXC……
  名单可改，见 `appOnlyApps`）：**只留程序名，标题为空**。
- 浏览器：只留 `域名 · 页面标题`，完整网址、路径、查询串一律不发。无痕窗口只留程序名。
  域名和标题来自 ActivityWatch 的浏览器扩展；**没装扩展（或扩展没报这段时间）的浏览器段只留程序名**——
  窗口标题里可能有无痕页面，分不清就不发。

隐私默认值：**同步默认关**，要你自己打开；随时可以暂停。关着 / 暂停时程序不读也不发任何东西，
而且**那段时间以后也不会补传**。

## 1. 装 ActivityWatch

到 <https://activitywatch.net/downloads/> 下载，装好后开着就行（本地接口 `http://localhost:5600`）。

- **Windows**：直接装。
- **macOS**：首次运行要在「系统设置 → 隐私与安全性 → 辅助功能」里给 ActivityWatch 授权，
  否则拿不到窗口标题。
- **Linux X11**：直接装。
- **Linux GNOME Wayland**：自带的窗口记录器在 Wayland 下拿不到窗口。先装 GNOME 扩展
  [Focused Window D-Bus](https://extensions.gnome.org/extension/5592/focused-window-d-bus/)，
  再用 [awatcher](https://github.com/2e3s/awatcher) 代替自带的窗口记录器和离开检测。
- 想让浏览器段带上域名和页面标题：装 ActivityWatch 的浏览器扩展（aw-watcher-web，Chrome / Firefox
  商店里有）。不装的话浏览器段只有程序名。
- 只读**本机**的记录：ActivityWatch 开了多设备同步时，别的电脑的桶不会被读。主机名改过导致对不上时，
  `status` 会报错并列出现有的桶，在配置里填 `windowBucket` / `afkBucket` 即可。

## 2. 装 ai-detector

从 Release 下载对应系统的单文件（未签名：Windows 会弹 SmartScreen，点「仍要运行」；
macOS 需要在「隐私与安全性」里放行一次），放到固定位置，然后：

```sh
ai-detector init        # 写默认配置（同步是关的）和规则文件样例
# 编辑配置：填 cockpitUrl 和 deviceToken（见下）
ai-detector once        # 跑一轮看看
ai-detector enable      # 打开同步
ai-detector autostart install   # 开机自启（可选）
ai-detector run         # 或者现在就常驻
```

其他命令：`status`（看上一轮结果）、`pause` / `resume`、`disable`、`autostart uninstall`。
同一个配置目录只能有一个 `run`；`run` 在跑时 `once` 会拒绝（避免两个进程抢游标）。

**设备令牌**：桌面程序没有浏览器登录态，用设备令牌访问 HoneyComb（`Authorization: Bearer`）。
令牌由 HoneyComb 的认证服务发放；用自带的占位认证时，在服务器上执行
`docker compose run --rm -T auth python /app/auth_stub.py token <账号>` 拿一个
（该功能随 v0.3 的设备令牌一起提供），吊销用 `revoke <账号>`。令牌只能调接口，不能登录网页。

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
    { "app": "blender", "taskId": "t_c3", "confidence": 0.7 }
  ]
}
```

`taskId` 在蜂巢里打开任务就能看到，或看 `GET /api/core/views/tree` 的输出。规则写错
（JSON 不合法、正则写坏）时**这一轮不上传**，`status` 会告诉你哪条错了，改好后自动补上。

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
| `deviceToken` | 空 | 设备令牌 |
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

程序名比较时忽略大小写、`.exe`、空格和连字符，所以 `WeChat.exe` 与 `wechat` 是同一个。

日志：`run` 模式写 `ai-detector.log`（与配置同目录），超过 5 MB 启动时清空。

配置文件里有设备令牌。macOS / Linux 上它的权限是 0600（只有你能读）；**Windows 上程序没有另设权限**，
依靠的是 `%APPDATA%` 目录默认只允许本用户访问——别把配置目录挪到共享位置。
