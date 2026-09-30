# agent-hooks —— 把 AI agent 跑的时间报给 cockpit

一个很薄的客户端：agent（Claude Code、Codex，或者任何你想接的工具）跑起来的时候
喊一声「开始」，跑完喊一声「结束」，cockpit（nexus-core）把这段算成一条
**agent run**——不是人的计时。多条 run 可以同时存在（many-lane），
它们**永远不计入人的时间**，无论跑多久、跑多少条。

纯标准库（`urllib`），Windows / macOS / Linux 通用，不用装任何东西。

## 记录什么 / 不记录什么

上报给 cockpit 的只有：agent 名字、工具名（`claude-code` / `codex` / ...）、
model（如果拿得到）、开始/结束时间戳、结束状态（`done`/`failed`/`cancelled`/`timeout`）、
以及一个可选的「输出在哪」的链接。

**不上报**：不发你的 prompt，不发任何代码，不发命令的 stdout/stderr 内容。
`cockpit-run` 包装命令时，命令的输入输出照常打印在你的终端上，本工具看不到、
也不会去读。

## 装 token

先在网页上登录 cockpit，然后换一个设备 token（`Authorization: Bearer <token>`，
只对 `<cockpit 地址>/api/core/*` 有效）：

```sh
curl -sb "<你登录时浏览器里的会话 cookie>" -X POST <cockpit 地址>/api/auth/tokens
```

也可以用 cockpit 提供的 CLI 登录方式换 token（如果有）。拿到 token 之后，
填进环境变量或者配置文件（下面两选一，环境变量优先）。

## 配置

**环境变量**（适合 CI / 容器，覆盖配置文件）：

| 变量 | 作用 |
|---|---|
| `COCKPIT_URL` | cockpit 地址，比如 `http://127.0.0.1:8800/` |
| `COCKPIT_TOKEN` | 设备 token |
| `COCKPIT_TASK` | 可选。这次 run 挂在哪个任务上；不设就走目录映射，再不然就是收件箱 |

**配置文件**（跨会话常驻，含目录 → 任务的映射）：

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
  "token": "换来的设备 token",
  "tasks": {
    "/home/you/code/project-a": "task-id-1",
    "/home/you/code/project-b/backend": "task-id-2"
  }
}
```

## 任务怎么定（解析顺序）

1. 显式传入的值（`cockpit-run --task xxx` 的那个 `--task`）
2. `COCKPIT_TASK` 环境变量
3. 配置文件 `tasks` 里，当前目录**最长匹配**的那条目录前缀
   （比如同时配了 `/code` 和 `/code/project-a`，在 `/code/project-a/sub` 下跑，
   用的是 `/code/project-a` 那条）
4. 都没有 → 收件箱（`taskId` 不传）

## `cockpit-run`：包一层跑任何命令

给 Codex、shell 脚本，或者任何不方便自己接钩子的工具用：

```sh
python3 tools/agent-hooks/cockpit-run --task task-id-1 -- codex exec "把这个 bug 修了"
```

- `--task`：任务 id，不给就走上面的解析顺序
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
  model 取钩子输入里的 `model` 字段，拿不到就不传）
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

## 相位：代理此刻在干活还是在等你（v0.3 追加，契约先行）

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
开始时的 `label`/`match`（**工作目录名这一级**，不是完整路径）。
**不报**：提示词、工具参数（命令、文件路径）、通知正文、Claude 的回答、transcript 路径。

### Claude Code 钩子：相位

在上面 `SessionStart`/`SessionEnd` 的基础上，再把下面这些事件指向**同一个脚本**，并加
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
| `SessionStart` | 开 run 时带 `phase: "idle"`（会话开着、还没说话）、`label` = 工作目录名、`match` = 工作目录名（不足 3 个字符不带）、`clientKey` = `session_id` 的 SHA-256 前 32 位十六进制（钩子被重试 / 响应丢了时不多开一条 run）。目录名与 v2.1 起就在报的 `agent` 名是同一级信息，不多报 |
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

## 测试

```sh
python3 -m pytest -q tools/agent-hooks
```

纯标准库，`pip install pytest` 之外不需要别的依赖；内进程假 HTTP 服务器录请求，
不用真起 cockpit。CI 里跟着仓库根目录 `python -m pytest -q tools ...` 那一步一起跑。
