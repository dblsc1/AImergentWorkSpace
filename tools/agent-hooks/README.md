# agent-hooks —— 把 AI agent 跑的时间报给 cockpit

一个很薄的客户端：agent（Claude Code、Codex，或者任何你想接的工具）跑起来的时候
喊一声「开始」，跑完喊一声「结束」，cockpit（nexus-core）把这段算成一条
**agent run**——不是人的计时。多条 run 可以同时存在（many-lane），
它们**永远不计入人的时间**，无论跑多久、跑多少条。

纯标准库（`urllib`），Windows / macOS / Linux 通用，不用装任何东西。

## 记录什么 / 不记录什么

上报给 cockpit 的只有：agent 名字、工具名（`claude-code` / `codex` / ...）、
model（如果拿得到）、开始/结束时间戳、结束状态（`done`/`failed`/`cancelled`/`timeout`）、
你在配置里写的任务 id 或项目 id、以及一个可选的「输出在哪」的链接；v0.3 起另有相位（见下「相位」节：相位名、时刻、短标签、
工作目录名、会话号的哈希）；v0.4 起 Claude Code 会话**你自己起的名字**（`/rename`，见「会话的名字」）。

**不上报**：不发你的 prompt，不发任何代码，不发命令的 stdout/stderr 内容。
`cockpit-run` 包装命令时，命令的输入输出照常打印在你的终端上，本工具看不到、
也不会去读。

## 装 token（或者不装）

钩子只做一件事：上报「这个代理在跑 / 在等你 / 结束了」。所以它只需要**只上报**的权限——
给它一个 `report` 范围的令牌，它读不到你的任务、时间和别的任何数据，也写不了别的东西。

**发一个 `report` 令牌**（令牌从不自动生成，由你发、你决定给谁）：

- 网页上：登录 cockpit → 「AI助理」页 →「Agent 令牌」→ 选「只上报」→ 生成。令牌只显示这一次，页面给出
  可以直接粘贴的配置；不用了在同一个地方单独吊销它。
- 命令行（`deploy/` 下）：`docker compose exec auth python /app/auth_stub.py token <账号> --scope report --name <备注>`
  （不带账号 = 共享口令身份）。

拿到之后填进环境变量或者配置文件（下面两选一，环境变量优先）。

**也可以不装**：单人部署（只开共享口令）缺省接受不带任何凭据的上报，`COCKPIT_TOKEN` / `token` 留空就行。
这样报上来的运行记为「未验证」（`views/lanes` 里 `unverified: true`），一律落收件箱（挂不了任务 / 项目），同时在跑的最多 20 个。
前提是 cockpit 那边没关 `AUTH_ANONYMOUS_REPORT`；开了账号登录（多用户）的 cockpit 不收匿名上报，必须用令牌。

想把运行挂到任务 / 项目上、或者 cockpit 的端口对别人开放时，用 `report` 令牌。别给钩子 `read` / `write` 令牌——
它用不着，丢了的代价却大得多。

## 配置

**环境变量**（适合 CI / 容器，覆盖配置文件）：

| 变量 | 作用 |
|---|---|
| `COCKPIT_URL` | cockpit 地址，比如 `http://127.0.0.1:8800/` |
| `COCKPIT_TOKEN` | 设备令牌（`report` 范围就够）。留空 = 不带凭据上报（见上）。**要和 `COCKPIT_URL` 一起设**：令牌只发往与它同来源的地址，单独设 `COCKPIT_TOKEN` 而地址在配置文件里，令牌不会用 |
| `COCKPIT_TASK` | 可选。这次 run 挂在哪个任务上；不设就走目录映射，再不然就是收件箱 |
| `COCKPIT_PROJECT` | 可选。定不出任务时，这次 run 挂在哪个项目上（见「挂到项目」） |
| `COCKPIT_BEAT` | 可选。谁来发心跳：`auto`（缺省）/ `companion` / `monitor` / `off`（见「心跳」）。配置文件里同名键是 `beat` |

**配置文件**（跨会话常驻，含目录 → 任务、目录 → 项目的映射）：

| 系统 | 路径 |
|---|---|
| Windows | `%APPDATA%\honeycomb\agent-hooks.json` |
| macOS | `~/Library/Application Support/honeycomb/agent-hooks.json` |
| Linux | `$XDG_CONFIG_HOME/honeycomb/agent-hooks.json`（缺省 `~/.config/honeycomb/agent-hooks.json`） |

（内部还认一个 `HONEYCOMB_AGENT_HOOKS_HOME` 环境变量，设了就把 config/state
两个目录都钉死在它下面——只给测试用，跨三个平台把状态/配置目录换成临时目录，
不用管。）

```json
{
  "url": "http://127.0.0.1:8800/",
  "token": "report 范围的令牌，或留空",
  "tasks": {
    "/home/you/code/project-a": "task-id-1",
    "/home/you/code/project-b/backend": "task-id-2"
  },
  "projects": {
    "/home/you/agents/CFO_agent": "project-id-finance",
    "/home/you/code": "project-id-dev"
  }
}
```

## 任务 / 项目怎么定（解析顺序）

先到先得，定出任务就不再看项目（任务本身就在某个项目里）：

1. 显式传入的任务（`cockpit-run --task xxx` 的那个 `--task`）
2. `COCKPIT_TASK` 环境变量
3. 配置文件 `tasks` 里，当前目录**最长匹配**的那条目录前缀
   （比如同时配了 `/code` 和 `/code/project-a`，在 `/code/project-a/sub` 下跑，
   用的是 `/code/project-a` 那条）
4. 显式传入的项目（`cockpit-run --project xxx`）
5. `COCKPIT_PROJECT` 环境变量
6. 配置文件 `projects` 里，当前目录最长匹配的那条目录前缀（规则同第 3 条）
7. 都没有 → 收件箱（`taskId`、`projectId` 都不传）

## 挂到项目：目录 → 项目（v0.4 追加）

大多数代理会话开起来的时候并不知道自己在做哪个**任务**，但一定知道自己在哪个**目录**——而目录属于哪个项目是固定的。
在配置文件的 `projects` 里写一次「目录 → 项目 id」（项目 id 在 cockpit 的项目页 / `GET /api/core/views/tree` 里看），
此后在这个目录（或它下面任何一层）里开的会话就自动属于那个项目，不用每次指定任务：

```json
{ "projects": { "/home/you/agents/CFO_agent": "project-id-finance" } }
```

- 这样的 run **只挂项目、不挂任务**（nexus-core 契约 v2.13「只挂项目的运行」）：代理时长记在这个项目名下，
  时间线上这条泳道显示在这个项目里。它仍然只是代理的时长，**不计入人的时间**。
- 同一个目录既在 `tasks` 又在 `projects` 里时任务优先（上面的解析顺序）。
- 值写错（项目不存在）→ cockpit 回 404，本工具照旧只在 stderr 留一行「HTTP 404」，这次不计时，会话 / 命令不受影响。
- cockpit 还没升到带 v2.13 的版本时，多出的 `projectId` 被忽略，run 落收件箱（同没配）。

**顺带得到的：人的窗口也跟着归到这个项目。** 终端标签页的标题就是会话的名字（Claude Code 会在前面加
「✳」之类的状态符号，终端会在后面加「 - Ptyxis」之类的程序名）。桌面检测程序（`modules/ai-detector`）上传你的前台活动时，
cockpit 把标题去掉这些装饰后与**同一时间在跑的会话**的 `label` / `match`（见下「会话的名字」）比：完全相等，就把这段活动
预先标到这个会话的项目上，你在「AI助理」页确认时项目已经选好，不必等 AI 从标题猜（契约 v2.13「窗口 ↔ 代理会话」）。
它只是预选，不会替你确认任何东西。两个前提：

- 标签页显示的就是会话的名字（Claude Code 钩子自动跟；`cockpit-run` 包的命令用 `--label <标题>` / `--match <标题>` 报同一个名字）；
- 检测程序的隐私设置允许终端标题**原样**上传——标题被去掉或换成代号（「窗口名3」）时对不上，这是有意的。

### 会话的名字（Claude Code 钩子）

泳道的 `label` / `match` 报的是**会话的名字**：你用 `/rename` 给会话起的那个（终端标签页显示的也是它，比如
「Cockpit-Pub-Coder1」）；没起过名就是工作目录名（v0.3 的行为）。钩子从输入里的 `transcript_path`（会话记录，JSONL）
**只读末尾 256 KB**、从后往前找最后一条 `{"type":"custom-title","customTitle":…}` 记录，只取这一个字段——
对话内容不读、不上报；文件不在、读不了、没有这种记录都只是退回目录名。自动生成的标题（`ai-title`）不用：改过名的标签页不显示它。
会话**中途改名**不触发任何钩子，所以每次本来就要报相位的事件顺带看一眼：名字变了才多发一次 `agents/start`
（同一个 `clientKey`，cockpit 给原来那条泳道换名字，不多开一条；nexus-core 契约 v2.13「会话改名」）。`agent` 名仍是目录名。

## `cockpit-run`：包一层跑任何命令

给 Codex、shell 脚本，或者任何不方便自己接钩子的工具用：

```sh
python3 tools/agent-hooks/cockpit-run --task task-id-1 -- codex exec "把这个 bug 修了"
```

- `--task`：任务 id，不给就走上面的解析顺序
- `--project`：项目 id，定不出任务时只挂项目；不给就走上面的解析顺序
- `--agent`：agent 显示名，不给就用当前目录名
- `--tool`：工具名，不给就用被包装命令的可执行文件名（上面例子里是 `codex`）
- `--` 之后的全部原样传给子进程：stdin/stdout/stderr 透传，退出码原样返回

结束状态怎么定：命令退出码 `0` → `done`，非 `0` → `failed`，
你按 Ctrl-C 或者外部发 `SIGTERM` 打断 → `cancelled`。命令被信号杀掉时，
`cockpit-run` 自己也会死于同一个信号（而不是包出一个奇怪的退出码），
上游脚本看到的退出方式跟直接跑这条命令一致。

Ctrl-C 按第一下会提示"再按两次强制结束"——命令这时多半正在自己收尾
（终端已经把 SIGINT 发给整个前台进程组），不用你再等太久；真遇到不理
SIGINT 的命令，按满三次就直接强杀。

**cockpit 连不上、token 不对、配置文件格式不对，都不影响命令本身执行**——
最多在 stderr 打一行警告（只报"连不上/超时/HTTP 几百/配置错误/响应格式不对"
这几个固定说法，不会把 token 或者其他原始错误细节印出来），命令照跑，
退出码照样准确转发。网络调用有总时限（约 3 秒，且是"总时限"而不是单次网络
操作的超时——服务器响应慢、body 一个字节一个字节地吐，都不会拖过这个时限）。

**已知的边界**（ponytail：先不做，真需要再加）：
- SIGTERM 只转发给直接子进程，不管它的子孙进程（比如子进程是个 shell 脚本，
  脚本里再 fork 出来的进程）——要管住整棵进程树得用进程组，这里没做。
- Windows 上没有能转发的 SIGTERM 语义（`os.kill(pid, SIGTERM)` 在 Windows 上
  直接杀进程，不经过任何 handler）；Ctrl-C 在 Windows 上照常工作。

## Claude Code 钩子：自动记会话时间

把下面这段接进 `settings.json`（项目级 `.claude/settings.json` 或者用户级，
按你想多大范围生效来定；**这是你自己要加的配置，本工具不会替你改任何
Claude Code 设置文件**）：

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py" }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py", "timeout": 5 }
        ]
      }
    ]
  }
}
```

两个事件指向同一个脚本，脚本自己从 stdin 的 `hook_event_name` 分流。
**`SessionEnd` 那条务必显式给 `timeout`**（几秒即可）——Claude Code 文档里
`SessionEnd` 钩子默认预算只有 1.5 秒，本工具自己的网络超时已经压到 1 秒，
但加上 Python 解释器启动、读写状态文件的开销，1.5 秒并不宽松，不显式调大的话，
偶尔会在我们的超时生效前，钩子进程就被 Claude Code 自己杀了（不影响会话，
但这次的结束状态就报不上去了）。

- `SessionStart` → 开一条 run（agent = 项目目录名，tool = `claude-code`，
  model 取钩子输入里的 `model` 字段，拿不到就不传；任务 / 项目按上面的解析顺序，用钩子输入里的 `cwd`）
- `SessionEnd` → 关掉这条 run。Claude Code 不会告诉钩子「这次工作算成功还是
  失败」，所以缺省报 `done`；只有 `reason` 是 `prompt_input_exit`
  （在输入框按 Ctrl-C/Ctrl-D 主动退出）才报 `cancelled`
- 两次调用之间用 Claude Code 的 `session_id` 对上号：每个会话各自一个小状态
  文件（文件名是 `session_id` 的哈希，原子写入），不同会话不共用同一份文件，
  多个 Claude Code 窗口同时开着也不会互相踩；`SessionEnd` 处理完就把那个
  会话的文件删掉，不会越攒越多
- 钩子本身**永远 `exit 0`**——网络失败、cockpit 没配、配置文件格式不对、
  状态文件读不了，统统吞掉，最多在 stderr 留一行（同样只报固定分类，不带
  原始错误细节）；绝不会拖慢或打断你的会话

Codex 或者别的 agent 工具，接法见上面的 `cockpit-run`（Codex 目前没有
SessionStart/SessionEnd 这样的钩子机制，用 `cockpit-run` 包一层是目前
最简单的接法）。

## 相位：代理此刻在干活还是在等你（v0.3 追加）

cockpit 的时间线页面要画出「代理 1 在干活、代理 3 在等你批准、你刚回了代理 3」。为此 run 在
开始与结束之间可以报**相位**（nexus-core 契约 v2.4「人一条线、代理多条线的时间线」节）：

| 相位 | 意思 |
|---|---|
| `working` | 在干活 |
| `waiting_input` | 停下来等你说话 |
| `waiting_permission` | 停下来等你批准一个动作 |
| `idle` | 一轮做完了 |
| `error` | 一轮因出错结束（限流、认证失败……） |

一条都不报也行：没报相位的 run 在页面上整条画成「在干活」（就是 v2.1 的样子）。

### 上报什么 / 不上报什么（相位部分）

只报：相位、它发生的时刻（本机时钟）、可选的**短标签** `detail`（只会是工具名如 `Bash`、Claude Code 的
通知种类如 `permission_prompt`、错误种类如 `rate_limit` 这类固定词）、「这次是不是你回话 / 批准引起的」、
开始时的 `label`/`match`（会话的名字；没起名时是**工作目录名这一级**，不是完整路径）。
**不报**：提示词、工具参数（命令、文件路径）、通知正文、Claude 的回答、transcript 路径。

### Claude Code 钩子：相位

在上面 `SessionStart`/`SessionEnd` 的基础上（不改那两条；卸载 = 把这些条目从 `settings.json` 里删掉，
本工具同样不替你改），再把下面这些事件指向**同一个脚本**，并加
`"async": true`——相位只是记录，绝不能让 Claude 等它（`UserPromptSubmit` 钩子同步跑时会挡住模型处理，
Claude Code 给它的默认超时只有 30 秒）。`SessionStart` **保持同步**：它要先把 runId 存好，后面的相位才找得到它。

```json
{
  "hooks": {
    "UserPromptSubmit":   [ { "hooks": [ { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py", "async": true } ] } ],
    "PermissionRequest":  [ { "hooks": [ { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py", "async": true } ] } ],
    "Notification":       [ { "hooks": [ { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py", "async": true } ] } ],
    "PostToolUse":        [ { "hooks": [ { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py", "async": true } ] } ],
    "PostToolUseFailure": [ { "hooks": [ { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py", "async": true } ] } ],
    "Stop":               [ { "hooks": [ { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py", "async": true } ] } ],
    "StopFailure":        [ { "hooks": [ { "type": "command", "command": "python3 /绝对路径/tools/agent-hooks/claude_hook.py", "async": true } ] } ]
  }
}
```

脚本按 stdin 的 `hook_event_name` 分流（规范性映射）：

| Claude Code 事件（读的字段） | 报什么 |
|---|---|
| `SessionStart` | 开 run 时带 `phase: "idle"`（会话开着、还没说话）、`label` / `match` = 会话的名字（没起名时是工作目录名；`match` 不足 3 个字符不带）、`clientKey` = `session_id` 的 SHA-256 前 32 位十六进制（钩子被重试 / 响应丢了时不多开一条 run）。目录名与 v2.1 起就在报的 `agent` 名是同一级信息，不多报 |
| `UserPromptSubmit`（**不读 `prompt`**） | `working`，`reply: true`——你说了话 |
| `PermissionRequest`（`tool_name`） | `waiting_permission`，`detail` = 工具名。这是「要请你批准」那一刻就触发的事件 |
| `Notification`（`notification_type`，**不读 `message`**） | `permission_prompt` → `waiting_permission`（兜底：沙箱网络请求的批准不触发 `PermissionRequest`）；`elicitation_dialog`/`elicitation_url_dialog`/`agent_needs_input` → `waiting_input`；`idle_prompt` → `idle`（你按 Esc 打断时 `Stop` 不触发，靠它把灯收回来）；其余种类不报。`detail` = 种类名 |
| `PostToolUse` / `PostToolUseFailure` | **仅当**上次报的是 `waiting_*` 时报 `working`、`reply: true`（你批准 / 回答了，它接着干）；否则不报——不为每次工具调用发一次请求 |
| `Stop` | `idle` |
| `StopFailure`（`error`） | `error`，`detail` = `error` 字段（`rate_limit`、`authentication_failed` 这类固定枚举） |
| `SessionEnd` | 关 run（不变） |

- `at` 取钩子进程开始处理那一刻。异步钩子是**并行**跑的、到达服务端的顺序不保证，服务端按 `at` 排，所以时间戳必须在
  事件发生时取，不能在发请求时补。
- 上表里的事件**每次都发**（一轮对话也就几条；`UserPromptSubmit` 哪怕 Claude 正在干活也要发——那是一次人的回话）。
  唯一按本地状态决定发不发的是 `PostToolUse`/`PostToolUseFailure`：每会话的状态文件里多记一个「上次报的相位」，
  其余钩子**先写状态、再发请求**。并行钩子读到旧状态的极小窗口里，最坏是这盏黄灯留到下一次 `Stop`/`UserPromptSubmit`
  ——已知上限，不为它加锁。
- 纪律不变：永远 `exit 0`，网络总时限约 1 秒，失败只在 stderr 留固定分类的一行。

事件名与字段核对自 Claude Code 官方文档 <https://code.claude.com/docs/en/hooks>（2026-09-30）：
`PermissionRequest` 在要请求批准时立即触发，而 `Notification` 的 `permission_prompt` 要等提示挂了约 6 秒才触发；
`idle_prompt` 在回答结束约 60 秒且你没打字时触发；`Stop` 在用户打断时不触发，API 出错时触发的是 `StopFailure`；
`UserPromptSubmit` 同步钩子默认超时 30 秒；`"async": true` 只对 `type: "command"` 有效、且不受 `timeout` 约束。

### `cockpit-run`：相位

`cockpit-run` 包命令时把本次的 runId 放进子进程的环境变量 `COCKPIT_RUN_ID`。被包的程序（或它自己的钩子机制）
想报相位时调：

```sh
cockpit-run phase <working|waiting_input|waiting_permission|idle|error> [--run <runId>] [--detail 短标签] [--reply]
```

- `--run` 缺省读 `COCKPIT_RUN_ID`；两者都没有 → stderr 一行提示，什么都不发。
- 第一个参数是字面的 `phase`、且参数里**没有** `--` 时才是这个子命令；`cockpit-run --task X -- phase …` 仍是包一个
  叫 `phase` 的命令，老用法不受影响。
- 包命令时另可给 `--label`、`--match`（缺省都是当前目录名），随 start 一起发。
- 同 hooks 的纪律：**永远退出码 0**，连不上 / 404 / 422 只在 stderr 留一行固定分类，绝不打断调用它的代理。

## 心跳：CLI 崩了泳道不再挂着（v0.4 追加）

钩子只在 Claude Code 有事件时才跑。会话空着的时候没人说话；Claude Code 崩了、被杀了、终端被关了，`SessionEnd`
不会来，泳道就停在最后的相位上。所以每个会话另有一个小进程，做两件事：

- **按服务端给的间隔报「我还活着」**（`POST /api/core/agents/{runId}/heartbeat`，缺省 15 分钟一次）。
  服务端的规则是：会发心跳的运行 **30 分钟**没有任何信号就算失联，泳道变灰、按最后一次信号的时刻收掉
  （协议见 `contracts/agent.lane.v1`，服务端规则见 nexus-core 契约 v2.18「心跳与失联」）。
- **每 30 秒看一眼 Claude Code 还在不在**；不在了就替它报 `stop`（`cancelled`）、清掉本地状态。
  所以正常情况下崩溃在半分钟内就收尾了，30 分钟那条线只在这个小进程自己也没了（断电、整机被杀）时才用得上。

机器睡了一觉、被判了失联而会话其实还开着：下一次心跳（或下一个钩子事件）发现运行已被收掉，会带同一个 `clientKey`
重开一条，从那一刻算起。

服务端给的间隔只认正整数、钳在 60–3600 秒里，每一下都重新钳；响应体最多读 64 KB；状态文件里读回来的东西
（`runId`、时刻、地址）都当不可信处理——`runId` 转义后才进路径，令牌绝不发往从状态文件读来的地址。

它只发 `runId` 和一个标签（`beatSource`，见下），**不发任何别的东西**；3 秒时限，失败不管（连不上时一分钟后再试）。
老服务端没有这个端点 → 404（网关的 HTML 页）→ 什么都不发生，行为同以前。cockpit 自己回 JSON 的 404（库重置 / 换了租户，不认这个 `runId`）按「已结束」处理：带同一个 `clientKey` 重开**一次**，重开不成就停发，等下一个钩子事件。

### 两种起法，同一个循环

| | 伴随进程 `companion` | 插件 monitor `monitor` |
|---|---|---|
| 谁起它 | `SessionStart` 钩子（之后任何事件发现它不在就补一个） | Claude Code 自己（插件的 `monitors/monitors.json`） |
| 怎么装 | 不用装：照上面在 `settings.json` 里配钩子就有 | 把本目录装成插件（见下「装成插件」） |
| 谁管它的生死 | 自己：脱离会话（独立 session、stdio 接 `/dev/null`），Claude Code 退出不等它；状态文件没了 / Claude Code 没了就退，最多活 24 小时（到点后下一个事件补一个） | Claude Code：随会话起、随会话停。只在交互式会话里有（`-p` 没有；Bedrock / Vertex / Foundry 上没有） |
| 怎么认会话 | 钩子把会话号交给它 | Claude Code 不给 monitor 会话号：它按「同一个 Claude Code 进程」到状态目录里认，`/clear` 换了会话就换着跟 |
| 地址 / 令牌 | 继承钩子的环境（含插件设置） | **拿不到插件设置**：地址用钩子记在状态文件里的；令牌只能来自 `COCKPIT_TOKEN` 或配置文件。钩子带了令牌而它没有 → 它让开，由伴随进程来 |
| `beatSource` | `companion` | `monitor` |

两种是同一段代码（`claude_hook.py --beat --source …`），一个会话同一时刻**只有一个**在发：它们抢状态目录里同一把
文件锁（`session-<哈希>.beat`，里面写着持有者的 pid 和起法），先到先得，后到的安静地让开；持有者死了内核自动放锁。

`COCKPIT_BEAT`（或配置文件的 `"beat"`）定用哪条路：

| 值 | 行为 |
|---|---|
| `auto`（缺省） | 钩子是从带 monitor 的插件里跑起来的 → `SessionStart` 就起一个**备用伴随进程**（`--standby`）：它等一个 90 秒宽限期，期间 monitor 接手了（拿到 `.beat` 锁）就退，到点还没人接手（monitor 认不了只在插件设置里的令牌、或没起来）就自己发——**不靠以后的钩子事件**，空闲会话也有心跳；否则直接用伴随进程。任何情形下同一时刻只有一个在发。伴随进程发不出去（认不出 CLI、或钩子带了令牌而自己没有）时不起 |
| `companion` | 只用伴随进程（monitor 起来后立刻退出） |
| `monitor` | 只用 monitor（钩子不起伴随进程；monitor 没来就没有心跳） |
| `off` | 不发心跳。运行不声明心跳能力，服务端照旧只有 12 小时的遗忘超时兜底 |

**Claude Code 在哪个进程**（「确认认出」的定义）：只有两种——环境变量 `COCKPIT_CLI_PID` 明确指了一个活着的进程；或祖先进程（最多 6 层）里
最近的一个名字以 `claude` 开头（或以 `COCKPIT_CLI_NAMES` 里逗号分隔的某个前缀开头）的。**不猜**：祖先是
`CLI(node) → timeout → shell → 钩子` 这种、没有认得出的名字时回「认不出」，不再退到「第一个不是 shell 的」（那常是个短命的辅助进程，
它一退就会替还活着的 CLI 收掉泳道）；经 `node` 跑的 CLI 请设 `COCKPIT_CLI_NAMES=node` 或 `COCKPIT_CLI_PID`。
认不出时**伴随进程不起、monitor 直接退出**：看不出它死了，就不声明心跳（`agent.lane.v1`「六」），这个会话照旧只有 12 小时的遗忘超时兜底。
身份 = (pid, 启动时刻)，由**钩子（启动者）取**并交给伴随进程，伴随进程不事后再取（否则 pid 被复用有空档）。**没有启动时刻的平台（macOS 的 `ps`）
分不出 pid 被复用，按认不出算：不监督、不声明心跳**——那里的运行只靠遗忘超时兜底（已知限制）。
每个会话的状态里有 SessionStart 写下的随机**归属号**（`gen`）和 CLI 身份；发心跳的进程每圈核对、停之前持锁再核对一遍，对不上
（会话被另一个 CLI 恢复，状态已被换掉）就放手，不停新 CLI 的运行。两种起法都有 24 小时寿命上限（按圈数），monitor 每圈重认 CLI 身份。
**状态目录**必须属于本人且组 / 其他人不可写，否则整个不用（= 没有状态 = 老行为）；状态 / 锁文件不跟符号链接、必须是普通文件、
临时文件名随机且 `O_EXCL`；新建目录 0700、文件 0600（里面有 cwd、transcript 路径、地址、pid）。
状态锁等不到（2 秒）时，改状态的操作（改名 / 重开 / 写相位 / 记 unsupported）一律放弃留给下一个事件，只有 SessionEnd 的 stop 照旧往下走。

**Windows**：钩子命令与插件清单里写的是 `python3`，Windows 上多半只有 `python` / `py`：装插件前请改成能用的解释器名（或让 `python3` 在 PATH 上）。两种都不起（没有可靠又不伤人的「这个 pid 还活着吗」——`os.kill(pid, 0)` 在 Windows 上会真的发信号），
运行不声明心跳，行为同以前（12 小时遗忘超时）。`cockpit-run` 的心跳线程在 Windows 上照常工作。

**`cockpit-run`** 包命令期间有一个心跳线程（`beatSource: wrapper`），命令结束即停；`COCKPIT_BEAT=off` 关掉。
它没有 `clientKey`，运行被判失联后不重开。

### 装成插件（monitor 这条路）

本目录同时是一个 Claude Code 插件（`.claude-plugin/plugin.json`、`hooks/hooks.json`、`monitors/monitors.json`——
三个 JSON 把同一个 `claude_hook.py` 接上去，没有别的逻辑）。仓库根的 `.claude-plugin/marketplace.json` 把它列了出来：

```
/plugin marketplace add dblsc1/AImergentWorkSpace
/plugin install honeycomb-lanes@honeycomb
```

启用时 Claude Code 会问两项设置：**Cockpit URL**（没有缺省值，留空 = 不用插件设置，改用环境变量 / 配置文件）和 **Device token**（选填，
存进系统的密钥存储；单人部署开了无令牌上报就留空）。它们以 `CLAUDE_PLUGIN_OPTION_COCKPIT_URL` / `_TOKEN` 交给钩子，
优先级在 `COCKPIT_URL` / `COCKPIT_TOKEN` 之后、配置文件之前。**地址与令牌成对取**：按 环境变量 → 插件设置 → 配置文件，
第一个给了地址的来源，连它的令牌一起用（它没配令牌就不带令牌）；只给令牌不给地址的来源，令牌不用——令牌只会发往它被配置的那个地址。目录 → 任务 / 项目的映射仍然写在配置文件里。

**两种装法只留一种**：装了插件就把 `settings.json` 里手配的那几条钩子删掉，否则每个事件报两遍。
开发时可以不经市场直接加载：`claude --plugin-dir tools/agent-hooks`；改完用 `claude plugin validate tools/agent-hooks` 查一遍。

monitor 是 Claude Code 的实验性功能（清单形状可能还会变）。它的每一行输出都会被当成通知送进会话，
所以这个 monitor **一个字都不输出**（stdout / stderr 整个接到 `/dev/null`）。

### 比较两条路

每条运行上记着 `beatSource`（谁发的心跳）和 `beatCount`（收到几下），结束后留在那条 `agent.run.completed` 里。
用一天之后按来源分组看：

```sh
curl -fsS -H "Authorization: Bearer $COCKPIT_TOKEN" \
  "${COCKPIT_URL%/}/api/core/events?type=agent.run.completed&limit=1000" | python3 -c '
import collections, json, sys
rows = collections.defaultdict(lambda: collections.Counter())
for e in json.load(sys.stdin)["items"]:
    d = e["data"]; r = rows[d.get("beatSource", "(none)")]
    r["runs"] += 1; r["beats"] += d.get("beatCount", 0); r[d["outcome"]] += 1
for source, r in sorted(rows.items()):
    print(source, dict(r))'
```

怎么读：`lost` 多 = 这条路的进程经常自己没了（或发不出去）；`cancelled` 是它发现 Claude Code 没了、替它收的尾；
`beats ÷ runs` 对照运行时长看有没有漏发。在跑的运行看 `GET /api/core/views/lanes` 的 `agents[]`
（`beatSource` / `beatCount` / `lastSeenAt` / `lost`）。想在同一台机器上对比，就隔天换一次 `COCKPIT_BEAT`。

## 测试

```sh
python3 -m pytest -q tools/agent-hooks
```

纯标准库，`pip install pytest` 之外不需要别的依赖；内进程假 HTTP 服务器录请求，
不用真起 cockpit。CI 里跟着仓库根目录 `python -m pytest -q tools ...` 那一步一起跑。
