# agent.lane.v1 —— 任何工具都能把自己画成一条代理泳道

> **状态**：v1 规范性。本文件是「一个 AI 代理工具（Claude Code、opencode、Codex、你自己的脚本……）要在 Cockpit 上
> 显示成一条泳道，得讲什么 HTTP」的**唯一事实**。只增不改不删；不兼容的改动发 `agent.lane.v2`，新旧并行。
>
> - 提供方：`modules/nexus-core`（v2.18 起完整实现；各端点的语义细节以它的契约为准，本文件只定**线上协议**并指过去）。
> - 消费方（参考适配器，都在 `tools/agent-hooks`）：Claude Code 钩子 `claude_hook.py`（含它的心跳伴随进程 / 插件 monitor）、
>   通用包装器 `cockpit-run`。别的工具照本文件写一个薄适配器即可，不需要改服务端。
> - **活性规则住在服务端**（仓主 2026-10-09）：适配器只管「有事说一声、没事隔一会儿说一声我还在」；
>   多久没声音算失联、失联了怎么收，是服务端的事，换哪个工具都一样。

```yaml
provides:
  - id: agent.lane.v1
    summary: >
      代理泳道的线上协议：POST /api/core/agents/start（开一条运行，clientKey 幂等）、
      POST /api/core/agents/{runId}/phase（相位：在干活 / 等人 / 空闲 / 出错）、
      POST /api/core/agents/{runId}/heartbeat（我还活着）、POST /api/core/agents/{runId}/stop（结束）。
      声明会发心跳的运行超过 30 分钟没有任何信号即失联，由服务端以 outcome=lost 关在最后一次信号的时刻。
consumes:
  - id: nexus-core.agents.v1 / nexus-core.agents.phase.v1 / nexus-core.agents.liveness.v1
    contract: ../../modules/nexus-core/module_docs/contract.md
    purpose: 四个端点的实现与语义（「AI 代理运行」「相位」「心跳与失联」三节）
  - id: auth.gate.v1
    contract: ../auth.gate.v1/contract.md
    purpose: 设备令牌（Bearer）；v1.4 起的令牌作用域与无令牌上报
  - id: gateway.v1
    contract: ../gateway.v1/contract.md
    purpose: 令牌请求经网关只开 /api/core/*，租户由网关按令牌定
```

## 一、传输与认证

- **HTTP + JSON**，UTF-8。基址是 Cockpit 的地址（缺省 `http://127.0.0.1:8800`），四个端点都在 `/api/core/agents/` 下。
- **认证**：`Authorization: Bearer <设备令牌>`（`contracts/auth.gate.v1`）。运行属于令牌对应的租户；别的租户的 `runId`
  一律 `404`（同形状，不暴露存在与否）。
  - `auth.gate.v1` v1.4 起令牌带作用域：本协议的四个端点只需要 **`report`**。给适配器发只有 `report` 的令牌——
    它读不了任何数据，也改不了任务。
  - 同样自 v1.4：单人部署可以放行**不带令牌**的请求，但只限这四个端点，这样开出来的运行会被标成 `unverified`。
    适配器因此应当允许「没配令牌」：没配就不发 `Authorization` 头，照常上报。
  - 以上两条由 `auth.gate.v1` 定义与实现，本文件只引用；在那之前的部署一律要令牌。
- 请求体里**没有**租户、用户、会话号：身份只从令牌来。
- 错误体统一是 `{"detail": "<给人看的一句话>"}`。适配器不应解析 `detail`，只看状态码。

## 二、四个端点（规范性）

### 1. `POST /api/core/agents/start` —— 开一条泳道

```jsonc
// 请求（只有 agent、tool 必填）
{ "agent": "garden",              // 1–64 字符：代理的名字（参考适配器用工作目录名）
  "tool": "claude-code",          // 1–64 字符：是哪个工具
  "model": "opus",                // 选填，1–64 字符
  "taskId": "t_a1b2c3",           // 选填：挂到哪个任务；与 projectId 至多给一个，都不给 = 收件箱
  "projectId": "p_1",             // 选填：定不出任务时只挂项目
  "phase": "idle",                // 选填：开跑时的相位（见「三」）；缺省 = 不记，读方当 working
  "label": "garden",              // 选填，1–64 码点：泳道上显示的名字；缺省读方用 agent
  "match": "garden",              // 选填，3–128 码点：认「人正在看这个代理」的窗口标题线索
  "clientKey": "9f2c…",           // 选填，1–128 字符，不透明：幂等键，见下
  "heartbeat": true,              // 选填，缺省 false：声明「我会发心跳」，这条运行受活性规则管（见「四」）
  "beatSource": "companion" }     // 选填：谁来发心跳的短标签（见「四」）
// → 201（新开）或 200（同 clientKey 的运行还在跑，回原来那条）
{ "runId": "run_0123456789ab",
  "startedAt": "2026-10-09T09:30:00+00:00",
  "heartbeatSeconds": 900 }       // 建议的心跳间隔（秒）。适配器照它发，不要写死
```

- **幂等（`clientKey`）**：同一租户里带同一个 `clientKey` 的运行**还在跑**时，再 `start` 不开新运行，回原来那条
  （`200`）。所以响应丢了尽管重试，不会多出一条泳道。用不透明值（比如会话号的哈希），不要放原始会话号。
  原运行已经结束（正常 stop、超时、失联）时，同一个 `clientKey` 的 `start` 开一条**新**运行，从此刻算起。
- **改名**：同 `clientKey` 再 `start` 时，这次给了的 `label` / `match` 与存着的不同就换成这次的；别的字段不动。
- 每一次被收下的 `start`（含回原运行的那种）都算一次信号（见「四」）。
- `taskId` / `projectId` 不存在 → `404`；字段超长、枚举外的取值 → `422`。
- 语义全文：nexus-core 契约「AI 代理运行」「只挂项目的运行」「会话改名」。

### 2. `POST /api/core/agents/{runId}/phase` —— 此刻在干什么

```jsonc
// 请求
{ "phase": "waiting_permission",            // 必填，见「三」
  "at": "2026-10-09T10:05:03.120+08:00",    // 必填，带时区偏移：事情发生那一刻取的时间（不是发请求的时间）
  "detail": "Bash",                         // 选填，≤64 码点：短标签（工具名、错误种类），不是正文
  "reply": false }                          // 选填：true = 这次转入是人回话 / 人批准引起的
// → 200
{ "runId": "run_…", "phase": "waiting_permission",
  "applied": true,                          // false 时看 reason
  "reason": null }                          // "duplicate"（同一 at 同一相位，已有）| "capped"（相位条数到上限）| "closed"
```

- 相位是**观测到的转入点**，服务端按 `at` 排序，乱序到达没关系；`at` 超前服务端时钟 300 秒以上 → `422`。
- **`applied:false, reason:"closed"` = 这条运行已经结束了**（见「五」）。
- 语义全文：nexus-core 契约「相位」。

### 3. `POST /api/core/agents/{runId}/heartbeat` —— 我还活着

```jsonc
// 请求：空体，或 {}，或
{ "beatSource": "companion" }    // 选填：谁在发，见「四」
// → 200
{ "runId": "run_…",
  "applied": true,               // false 时 reason 为 "closed"
  "reason": null,
  "heartbeatSeconds": 900 }      // 下一次隔多久（秒）
```

- 便宜、幂等、认证与 `phase` 相同。不改相位、不改任何显示用的字段，只更新「最后一次有信号的时刻」。
- **第一次心跳即声明**：没在 `start` 里带 `heartbeat: true` 的运行，发过一次心跳之后同样受活性规则管。
- `runId` 不存在 → `404`；体里有未知字段 → `422`。

### 4. `POST /api/core/agents/{runId}/stop` —— 结束

```jsonc
// 请求
{ "outcome": "done",             // 必填：done | failed | cancelled（适配器只用这三个）
  "output": "PR #31 已开" }      // 选填，≤512 字符
// → 200
{ "runId": "run_…", "duplicate": false,   // true = 早就结束了，这次什么都没写
  "outcome": "done",                      // duplicate:true 时是**原来**的 outcome（可能是 lost / timeout）
  "durationSeconds": 42, "event": { "…": "…" } }
```

- 重复 stop 不报错（`duplicate:true`）。`lost` 只由服务端写，适配器发 `lost` → `422`；`timeout` 是服务端遗忘超时用的词，适配器不要用。
- 语义全文：nexus-core 契约「AI 代理运行」。

## 三、相位词表

| 相位 | 意思 |
|---|---|
| `working` | 代理在干活 |
| `waiting_input` | 停下来等人**说话** |
| `waiting_permission` | 停下来等人**批准**一个动作 |
| `idle` | 一轮做完了，没在等什么具体的东西 |
| `error` | 一轮因出错结束（限流、认证失败……） |

工具自己的事件怎么映射到这五个词是适配器的事（Claude Code 的映射表在 `tools/agent-hooks/README.md`）。
拿不准就少报：只报 `start` / `stop` 的工具也是一条合法的泳道（整段当 `working` 画）。

## 四、活性：心跳与失联（规范性）

钩子式的适配器只在工具有事件时才说话。工具崩了、被杀了、终端被关了，`stop` 永远不来，泳道就停在最后的相位上。
所以：

- **信号**：被收下的 `start`、`phase`、`heartbeat` 都是信号，都会更新这条运行的 `lastSeenAt`。
- **声明**：`start` 里带 `heartbeat: true`，或者发过一次 `heartbeat`，这条运行就是「会发心跳的」。
- **失联**：会发心跳的运行，距最后一次信号超过 **1800 秒（30 分钟）** 即失联。
  - 读端（`GET /api/core/views/lanes`）不写任何东西，只把它标成 `lost: true`、带上 `lastSeenAt`，它不再算在干活 / 在等人。
  - 之后服务端第一次有机会时把它关掉：`outcome: "lost"`，**结束时刻 = 最后一次信号的时刻**（不是被发现的时刻，
    也不是「开始 + 上限」），所以失联不会把代理时长撑大。
- **间隔**：服务端在 `start` / `heartbeat` 响应里给 `heartbeatSeconds`（当前 **900，15 分钟**，是失联线的一半）。
  适配器照这个值发，没拿到就用 900，并钳在 [60, 3600] 里。**一次没发成（连不上 / 超时 / 5xx）应当在一两分钟内补发**，
  否则下一次正常的心跳正好踩在线上。
- **不声明就不管**：从不发心跳的适配器（老版本、裸 `curl`、只包一条命令的脚本）不受活性规则影响，
  仍然只有服务端的遗忘超时兜底（`NEXUS_AGENT_RUN_TIMEOUT_HOURS`，缺省 12 小时，`outcome: "timeout"`）。
  反过来，会发心跳的运行**不看**这个上限：它活多久算多久——那个上限现在只是给不发心跳的运行留的安全网。
- **`beatSource`**（选填，`^[a-z0-9][a-z0-9_-]{0,31}$`）：适配器有不止一种发心跳的办法时，用它标明这次是哪一种
  （参考适配器用 `companion` / `monitor` / `wrapper`）。服务端只把它当标签记在运行上，连同收到的心跳次数 `beatCount`
  一起在 `views/lanes` 里给出，运行结束后留在那条事实里——用来事后比较哪种办法更可靠，不影响任何判定。

语义全文、读端字段、落账形状：nexus-core 契约「心跳与失联」。

## 五、运行被服务端关了之后

机器睡了一觉、网络断了半小时：适配器再说话时，那条运行可能已经被判了失联。

1. `phase` / `heartbeat` 回 `{"applied": false, "reason": "closed"}`；`stop` 回 `duplicate: true`（`outcome` 是 `lost`）。
   都是 `200`，不是错误。
2. 工具其实还活着的话，适配器带**原来的 `clientKey`** 再 `start` 一次：得到一条新运行（`201`，新的 `runId`，
   从此刻算起），之后的相位 / 心跳发给新的 `runId`。旧的那条保持 `lost`，两条不合并。
3. 工具已经没了的话什么都不用做。

## 六、对适配器的要求（规范性）

适配器跑在别人的工具里，**绝不能拖慢或打断它**：

- **短时限**：每个请求的总时限 ≤ 3 秒（是「总」时限，不是单次 socket 操作的超时）。
- **失败就丢**：连不上、超时、4xx、5xx——都只是「这次没记上」。不重试到成功、不排队攒着、不让宿主工具报错；
  适配器进程一律以 0 退出。唯一值得补发的是心跳（见「四」），而且只是下一轮再发一次。
- **服务端给的数当不可信**：`heartbeatSeconds` 只认正整数（布尔、浮点、`NaN`、字符串、零、负数一律当没给，用 900），
  再钳到 [60, 3600]，而且**每次响应都重新钳**；响应体只读有限的字节数（协议里的响应都是几百字节）。
- **不走系统代理、不跟重定向**：Cockpit 多半在本机 / 局域网；跟着 3xx 走会把令牌带去别的源。
- **空着的时候也要有心跳**：钩子没有事件就不会跑，所以声明了心跳的适配器得另有办法在工具空闲时发——
  一个随工具生死的小进程、工具自己的定时器 / 后台任务机制、包装器里的一个线程，都行。那个办法还应当能
  发现「工具没了」，并替它发 `stop`。做不到就**不要声明心跳**，老老实实让遗忘超时兜底，
  比声明了又发不出来（半小时后被判失联）强。
- **一条运行一个心跳源**：同一条运行同时有两个进程在发心跳没有害处，但没有意义；适配器自己保证只有一个。

## 七、隐私：什么离开这台机器

只有上面请求体里列出的字段。具体到参考适配器：

- **发**：代理名与泳道名（工作目录**名**，或用户给会话起的名字）、工具名、模型名、相位、时刻、
  短标签 `detail`（工具名、通知种类、错误种类这样的固定词）、不透明的 `clientKey`、任务 / 项目 id。
- **不发**：提示词、回复、工具的入参与输出、文件内容、完整路径、原始会话号、环境变量。
- `label` / `match` / `detail` 是适配器能自由填的三个文本字段：**不要往里放正文**。

## 八、例子

### curl

```bash
BASE=http://127.0.0.1:8800; AUTH="Authorization: Bearer $COCKPIT_TOKEN"; J="Content-Type: application/json"
RUN=$(curl -fsS -m 3 -H "$AUTH" -H "$J" "$BASE/api/core/agents/start" \
  -d '{"agent":"garden","tool":"my-cli","phase":"working","clientKey":"k-1","heartbeat":true}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["runId"])')
curl -fsS -m 3 -H "$AUTH" -H "$J" "$BASE/api/core/agents/$RUN/phase" \
  -d "{\"phase\":\"waiting_input\",\"at\":\"$(date -Iseconds)\"}"
curl -fsS -m 3 -H "$AUTH" -X POST "$BASE/api/core/agents/$RUN/heartbeat"      # 每 heartbeatSeconds 一次
curl -fsS -m 3 -H "$AUTH" -H "$J" "$BASE/api/core/agents/$RUN/stop" -d '{"outcome":"done"}'
```

### 参考客户端（Python 标准库，约 20 行）

```python
import json, threading, urllib.request
from datetime import datetime, timezone

class Lane:
    def __init__(self, base, token, agent, tool, key):
        self.base, self.token, self.start_body = base.rstrip("/"), token, {
            "agent": agent, "tool": tool, "clientKey": key, "heartbeat": True, "phase": "working"}
        self.run, self.every, self.done = None, 900, threading.Event()
        self._start()
        threading.Thread(target=self._beat, daemon=True).start()
    def _post(self, path, body=None):
        try:                                           # 3 秒时限；任何失败都只是「这次没记上」
            head = {"Content-Type": "application/json", **({"Authorization": f"Bearer {self.token}"} if self.token else {})}
            req = urllib.request.Request(self.base + path, json.dumps(body or {}).encode(), head, method="POST")
            return json.load(urllib.request.urlopen(req, timeout=3))
        except Exception:
            return {}
    def _start(self):
        out = self._post("/api/core/agents/start", self.start_body)
        self.run, self.every = out.get("runId"), min(max(out.get("heartbeatSeconds", 900), 60), 3600)
    def _signal(self, path, body=None):
        if self.run and self._post(f"/api/core/agents/{self.run}/{path}", body).get("reason") == "closed":
            self._start()                              # 被服务端收了而我们还活着：同 clientKey 重开
    def _beat(self):
        while not self.done.wait(self.every):
            self._signal("heartbeat")
    def phase(self, phase):
        self._signal("phase", {"phase": phase, "at": datetime.now(timezone.utc).astimezone().isoformat()})
    def stop(self, outcome="done"):
        self.done.set()
        if self.run: self._post(f"/api/core/agents/{self.run}/stop", {"outcome": outcome})
```

（示意用：`urlopen(timeout=3)` 只是单次 socket 操作的超时，生产用的适配器要做成总时限，见 `tools/agent-hooks/cockpit_client.py`。）

## 九、换实现要满足什么

- **换服务端**：四个端点的请求 / 响应形状与「四」「五」的规则照本文件；`heartbeatSeconds` 必须小于失联线的一半或与之相等。
- **写新适配器**：满足「六」「七」；至少实现 `start` + `stop`，相位与心跳按能力加。

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-10-09 | v1 | 首版。仓主 2026-10-09：代理泳道全靠 CLI 的钩子，CLI 崩了没人说 stop，泳道要挂到 12 小时的遗忘超时——「同意加心跳，12 小时太长，改成 30 分钟；15 分钟一次心跳」「要通用，走 HTTP，哪个 CLI 都能用」「那就写契约」。把 nexus-core 既有的 `start` / `phase` / `stop`（v2.1、v2.4、v2.13）与本次新增的 `heartbeat` 和活性规则（v2.18）收成一份面向适配器的协议；`beatSource` / `beatCount` 供比较不同的心跳办法 |
