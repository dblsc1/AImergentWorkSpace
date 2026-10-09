# auth.gate.v1 —— 一道登录门（开放契约）

> **契约 id**：`auth.gate.v1`。**当前版本 v1.4**（2026-10-09，纯追加，见文末变更记录）。
>
> **v1.4**：设备令牌带**权限范围**（`report` ⊂ `read` ⊂ `write`），可以**单个吊销**、可以列出；不带任何凭据的请求在单人模式下**只能上报**。见「权限范围与匿名上报」节。v1.3 的令牌与行为不变（= `write`）。
>
> **v1.3**：设备令牌也开 `<站点前缀>api/mcp/`。占位实现已跟上（v0.3 AI 桥实现 PR，`_api_uri`）。
>
> **状态**：规范性。本文件描述的每一条行为都能在 `stub/auth_stub.py` 里找到对应代码，
> 按函数名引用（`token_tenant`、`_login` 等）——**不确定的地方以代码为准，不是以这份
> 文档的措辞为准**。若 stub 改了行为却没同步改这里，或者这里写的东西 stub 根本做不到，
> 说明二者出现了分歧，**先改契约、再改代码**。
>
> **版本号语义**：`v1` 之内只接受**不破坏现有消费方**的追加式变更——新增可选响应字段、
> 可选响应头、新端点。任何会改变**现有端点**的状态码含义、请求/响应形状，或者收紧/放松
> 下面「三条不变量」任何一条的改动，都是**破坏性变更**，必须发布 `auth.gate.v2` 并与
> `v1` 并行存在。理由：契约一经发布，唯一耦合点就是这个 id——任何换掉 `auth.gate.v1`
> 实现的人，都是照着这份文档的承诺去接的。

## 这个契约是什么 / 不是什么

**是**：一道门。校验来人（共享口令，或者账号+密码，或者任何等价的会话机制），通过就发
一个会话凭证，之后每次访问受保护资源前验一次这个凭证；验过的时候**可以**顺带告诉网关
「这是谁的数据」（租户，v1.1）。

**不是**：账号系统。契约只规定门的对外行为，下面这些能力**不在契约内**，消费方不许假设
它们存在：

| 能力 | 状态 |
|---|---|
| 注册 / 建新用户 / 账号管理 | 不在契约内。占位实现用命令行加账号（见「占位实现」），那是实现细节 |
| 找回密码 | 不在契约内 |
| 权限分级（普通用户/管理员…） | 不在契约内——门只有「过」与「不过」两态 |
| 第三方登录（OAuth/SSO/…） | 不在契约内 |
| 监护人同意 / 年龄校验 | 不在契约内 |

契约**只**规定「是谁」的两个出口：`verify` 的 `X-Nexus-Tenant` 响应头，与
`GET /api/auth/me` 的最小形状。

**为什么划这条线**：开源版面向「随便什么人 clone 下来自己跑」，不该替所有下游预先做
「要不要上一套真实账号系统」的决定——捆绑账号系统意味着替他们背下注册流程、密码存储、
找回邮件这些安全责任。需要真账号系统的人写一个实现本契约的新服务替掉 `stub/`，
**网关那边一行都不用改**（`contracts/gateway.v1` 的 `AUTH_UPSTREAM`）。

## 端点

全部前缀 `/api/auth/`。下面没列的路径，占位实现一律 `404`、无 body。替换实现可以在这个
前缀下加自己的端点（gateway.v1 把整个前缀转给认证服务），但**网关不给这个前缀设门**，
非登录端点须自己鉴权。

### `GET /api/auth/health`

| | |
|---|---|
| `200` | `{"status":"ok", ...}`（`application/json`） |

健康检查**不做任何凭证校验**——它必须对没登录的探测器也能打通。

v1.1 占位实现追加两个字段（可选，替换实现可以不给）：`accounts`（开了账号登录且至少
有一个账号）、`sharedPassword`（开了共享口令）。v1.4 再加 `anonymousReport`（此刻不带凭据能不能上报）。自带登录页据此决定显示不显示「账号」
一栏；拿不到这两个字段就当只有共享口令。

### `GET /api/auth/verify`

| | |
|---|---|
| 请求 | 无 body；读 `Cookie` 头里的会话 cookie。（v1.2）带了 `Authorization: Bearer <设备令牌>` 时改读令牌，见下 |
| `204` | 会话有效——**无 body**。**可以**带响应头 `X-Nexus-Tenant: <租户 id>`（v1.1） |
| `401` | 没登录：cookie 缺失 / 签名不对 / 已过期 / 账号已删 / 校验过程抛出任何异常——**无 body**。（v1.2）令牌无效、已吊销、或不是打接口的请求，同样 `401` |
| `403` | （v1.1，可选）登录了，但当前**没有可用租户**（例：家长账号还没选孩子）——**无 body**。（v1.4）设备令牌有效但**范围不够**，同样 `403` |

- **租户头**：格式 `^[A-Za-z0-9_.:-]{1,64}$`（与 gateway.v1、nexus-core 同一格式）。
  不带 = 单一默认租户（nexus-core 的 `u_local`）。网关把它设到每一条受保护转发上，并
  覆盖客户端自带的同名头（gateway.v1 第五节）。
- **多用户实现的纪律**：登录了却给不出租户时回 `403`（或 `401`），**不要回不带租户的
  `204`**——那等于把这个人放进单一默认租户，和所有同样漏了头的人共用一份数据。下游
  （nexus-core `NEXUS_TENANT_STRICT=1`）会兜底拒绝，但源头就该响亮。
- 网关对 `403` 回 `403`（不跳登录页）；这三个状态码之外 nginx 一律当 500。

**设备令牌（v1.2，可选）**：桌面同步程序、AI 代理的钩子这类**没有浏览器 cookie** 的
调用方，带 `Authorization: Bearer <令牌>` 调 `/api/core/*`。

- **只开接口，不开页面**：只有网关转来的 `X-Original-URI`（原始请求的 `$request_uri`，
  含站点前缀、未解码）的路径落在 `<站点前缀>api/core/` 下（v1.3 起也含 `<站点前缀>api/mcp/`，
  给用户自己的 MCP 客户端用，见 `contracts/mcp.tools.v1`）时才认令牌；缺这个头、或落在
  别处（`/hive/`、`/login/`、`/api/auth/`……）一律 `401`。判断前先解一遍 `%XX`，路径里
  有 `.` / `..` 段或反斜杠也 `401`——nginx 按**解码、规范化之后**的路径匹配 location，
  `/api/core/%2e%2e/%2e%2e/hive/` 在它眼里是 `/hive/`，不识破这一层令牌就能开页面。
  查询串不参与判断。
- **带了 Bearer 就只看令牌**，不回落到 cookie：令牌无效就是 `401`，哪怕同时带着有效
  cookie。别的认证方案（如 `Basic`）不算令牌，照旧看 cookie。
- `204` 的租户头与 cookie 路径完全一样：账号令牌带该账号的租户；共享口令身份的令牌
  **不带租户**（= `u_local`）。
- 令牌与会话 cookie **互不通用**：令牌塞进 cookie、cookie 值当令牌，都是 `401`。
- 三条不变量对令牌路径一样成立（无 body、无 IO、任何异常 `401`）。
- （v1.4）令牌可以带范围，`204` 多回 `X-Nexus-Scope`；什么凭据都不带的请求可以只放行上报面。见「权限范围与匿名上报」节。

代码：`do_GET` 的 verify 分支、`verify_access`、`token_tenant`、`device_identity`、`_api_area`。

### `GET /api/auth/me`（v1.1）

| | |
|---|---|
| `200` | `{"ok": true, "user": {"id": "<租户 id>", "name": "<显示名>"}}` |
| `401` | 没登录（同 verify 的 401 判据） |
| `403` | 登录了但没有可用租户（同 verify 的 403），body 可带原因码 |

- **冻结的最小形状**：`ok`、`user.id`、`user.name` 三个字段。实现可以再加字段，不得
  改这三个的含义。
- `user.id` **等于** verify 在同一会话下给出的租户 id；verify 不带租户（单一默认租户）
  时为 `"u_local"`。
- 消费方把**任何非 200** 一律当「没有可用租户」处理，不要只判 401。

代码：`do_GET` 的 me 分支。

### `POST /api/auth/login`

| | |
|---|---|
| 请求体 | JSON 对象：`{"password": "<string>"}`，或 `{"username": "<string>", "password": "<string>"}`（v1.1） |
| `204` | 通过——响应带 `Set-Cookie`（新签发的会话），**无 body** |
| `401` | 账号或口令不对（缺字段按空串比对，同样判不对）——**无 body** |
| `400` | `Content-Length` 不是合法整数（**断开连接**）；或请求体不是合法 JSON 对象（不断连）——**无 body** |
| `413` | `Content-Length` 为负，或超过 4096 字节——**无 body**，**断开连接** |
| `429` | （v1.1）同一来源错太多次，暂时拒绝，带 `Retry-After`——**无 body** |

- 不带 `username`（或为空）= 共享口令登录；带 = 账号登录。实现可以只支持其中一种，
  不支持的那种按 `401` 处理。
- 账号不存在与口令错**不可区分**：同一个 `401`，耗时也要相近（占位实现对不存在的账号
  照样算一遍哈希），否则能拿来枚举账号。
- `login` 在任何分支下都不返回 body。

代码：`_login`、`check_login`、`FailLimiter`。

### `POST /api/auth/tokens`（v1.2，可选）

登录了的网页用户给自己发一个设备令牌——Windows 用户不必碰 docker 命令行。

| | |
|---|---|
| 鉴权 | **只认会话 cookie**，不认 Bearer（令牌不能换出新令牌） |
| 请求 | 必须 `Content-Type: application/json`；body 可空，或 `{"label": "<≤64 字符>"}`。（v1.4）`{"name"?: "<≤64 字符，不含控制字符>", "scope"?: "report" \| "read" \| "write"}`——`name` 与 `label` 同义，`scope` 缺省 `write` |
| `201` | `{"token": "<令牌>", "tenant": "<租户 id>", "expiresAt": "<UTC ISO 8601>"}`（`application/json`）。`tenant` 与 `/me` 的 `user.id` 同义（共享口令身份为 `"u_local"`）。（v1.4 追加字段）`id`、`name`、`scope`、`createdAt`、`expiresAt`，同下面列表里的一条 |
| `401` | 没有有效会话 cookie（只带 Bearer 也是 `401`）|
| `400` | body 不是 JSON 对象、`label` 不是字符串或超长；`Content-Length` 不是合法整数（**断开连接**）。（v1.4）`name` 含控制字符、`scope` 不是三个取值之一 |
| `409` | （v1.4）这个身份名下未到期的令牌已到上限（占位实现每个身份 100 个、全部身份合计 1000 个）。**吊销一个就腾出一个名额**（吊销 = 删记录） |
| `413` | `Content-Length` 为负或超过 4096 字节（**断开连接**）|
| `415` | `Content-Type` 不是 `application/json` |
| `503` | 本实例不发令牌（占位实现：没设 `AUTH_SECRET`，或没有令牌文件）、或写令牌文件失败 |

- `Content-Type` 的要求是 CSRF 防线：跨站表单发不出这个类型，跨站 `fetch` 带它要先过
  CORS 预检（本服务不回 CORS 头）；再加上 cookie 本身 `SameSite=Lax`。
- `label` v1.2 / v1.3 的占位实现只校验、不存；v1.4 起存下来（`name`），列表里看得到。
- 令牌只出现在这个响应体里，**任何日志都不许记令牌**。

### `POST /api/auth/tokens/revoke`（v1.2，可选）

| | |
|---|---|
| 鉴权 / 请求 | 同 `POST /api/auth/tokens`（cookie、`application/json`、body 可空）|
| `204` | 调用者这个身份（租户）的**全部**设备令牌作废，无 body。别的身份不受影响。（v1.4）body 带 `{"tokenId": "<令牌 id>"}` 时**只作废这一个**（记录随即删掉，名额立刻回来；再吊销同一个 id 回 `404`，同「没有这个令牌」）|
| `404` | （v1.4）带了 `tokenId`，但自己名下没有这个令牌（别人的、到期清掉的、从没有过的，同一个回答）|
| 其它 | `401` / `400` / `413` / `415` / `503` 同上；带了 `tokenId` 但不是 16 位十六进制的字符串（含 `null`）→ `400`，不会当成「吊销全部」 |

吊销之后新发的令牌照常可用。占位实现在本进程立即生效；命令行吊销 `RELOAD_EVERY`（2 秒）
内生效。

代码：`_tokens`、`_read_json`、`token_state`、`issue_device_token`。

### `GET /api/auth/tokens`（v1.4，可选）

| | |
|---|---|
| 鉴权 | **只认会话 cookie**（设备令牌看不到令牌列表）|
| `200` | `{"tokens": [{"id", "name", "scope", "createdAt", "expiresAt"}]}`——自己名下未到期、没被吊销的令牌，按签发时间排。时间是 UTC ISO 8601 |
| `401` / `503` | 同 `POST /api/auth/tokens` |

- **永远没有令牌本身**：它只在签发那一次的响应体里出现，服务端哪里都没存。
- `hct1` 老令牌没有 id、没有记录，**列不出来**，也不能单个吊销——要作废它们只能吊销整个身份。
- 不支持的实现回 `404`；消费方把非 `200` 一律当「这里不能管理令牌」。

代码：`_list_tokens`、`list_tokens`。

### `POST /api/auth/logout`

| | |
|---|---|
| 请求体 | 不读 |
| `204` | 总是如此——响应带 `Set-Cookie` 把会话 cookie 覆盖成立即过期（`Max-Age=0`），**无 body** |

`logout` 不校验当前 cookie，它做的是「让客户端清掉自己的会话」，不是「服务端吊销某个
会话」。占位实现的会话是无状态签名，吊销手段见「占位实现」。

## 三条不变量

（2026-10-09 补充，不变量 2 的一部分：**verify 路径也不做同步日志 IO**——写日志的管道一堵，所有认证请求跟着卡死。
占位实现对 `/api/auth/verify` 不记请求日志（200 / 401 / 403 与内部错误→401 都一样）；别的端点照记。）

`verify` 站在 nginx `auth_request` 的关键路径上，对**每一个**受保护请求先做一次内部子
请求。三条都围绕「它绝不能成为整个受保护面的瓶颈或单点故障」。

### 1. `/api/auth/verify` 只返状态码与租户头（v1.4：再加范围头、匿名头），不返 body

nginx `auth_request` 丢弃子请求的 body，返 body 纯属浪费，还会给人「它返了 body 我就能
读到」的错觉。要传给后端的信息只能走响应头（`auth_request_set`），v1.1 的租户头就是这
样传的。

### 2. `verify` 不做 IO、不查库

`auth_request` 是**同步**子请求：`verify` 的延迟叠加到每一个受保护请求上，它依赖的库一
抖动，整个受保护面跟着抖。占位实现的 `token_tenant` 只做字符串切分、一次 HMAC、一次整数
比较；账号信息（租户 → 会话盐）来自内存视图，由后台线程在账号文件变化时重读
（`Accounts`），不在请求里读文件。设备令牌（v1.2）同理：`device_tenant` 比对的吊销
纪元来自内存视图 `Epochs`，同一个后台线程重读令牌文件；只有发令牌 / 吊销这两个
端点在请求线程里写文件，它们不在 `auth_request` 的路径上。

### 3. `verify` 任何内部错误都必须收敛成 401，不许裸奔成 500

nginx 对 `200/204/401/403` 之外的返回一律当错误，整个上游请求失败——auth 一次意料之外
的故障就会连坐全部业务请求。宁可错误地拒绝一个合法用户（重新登录一次），也不能让 auth
的一次抖动波及全部受保护流量。`token_tenant` 用宽 `except` 把所有异常收成「无效」；
`_cookie_value` 自己解析 Cookie 头，不用标准库 `http.cookies`（畸形头在那里会抛）。
`verify_tenant` 再包一层宽 `except`：畸形、超长的 `Authorization` 头一样只是 `401`。

## 权限范围与匿名上报（v1.4）

仓主 2026-10-09：「token 可以让我们给智能体不同权限：读 / 写 / 只上报进度」「token 不是自动生成的，由用户
生成令牌、交给特定的管理智能体」「请求没有 token 的话，就只能上报」。起因：沙箱里的编码代理只需要把自己的
状态报给 cockpit，不该顺带读得到主人的任务与时间；而 v1.2 的设备令牌一律是全部接口权限，吊销只能整个身份一起。

**实现本节是可选的**（同设备令牌）。不实现的门照旧：令牌没有范围（= `write`）、匿名一律 `401`。

### 调用方分类（规范性）

verify 按请求**出示的凭据**把调用方分成四类。**出示了就只看它，绝不降级**：

| 出示的 | 类别 | 无效时 |
|---|---|---|
| `Authorization: Bearer <令牌>` | 设备令牌，范围 `report` / `read` / `write` | `401`——**不**当成匿名，也不回落到 cookie |
| 会话 cookie（值非空） | 人 | `401`（过期的会话去登录页，不当成匿名） |
| 别的 `Authorization` 方案（如 `Basic`），或不止一个 `Authorization` 头 | 不算令牌：前者照旧只看 cookie，后者一律 `401` | `401` |
| **什么都没带**（没有 `Authorization` 头、没有会话 cookie） | 匿名 | —— |

**「出示了」看头在不在，不看值是不是空**（2026-10-09 修订）：`Cookie` 里有 `cockpit_session=`（值为空）、或带了
`Authorization` 头（值为空 / 全是空白），都算出示了凭据，且是无效的 → `401`，不当成匿名。别的 cookie（如 `theme=dark`）
不算出示。否则一个因为变量没设而发出空 `Authorization` 的脚本，会被悄悄降成匿名上报。

### 范围（规范性）

三个范围**严格嵌套**：`report` ⊂ `read` ⊂ `write`。

- **`report`（只上报）**：恰好四个端点——`POST <前缀>api/core/agents/start`、
  `POST <前缀>api/core/agents/{runId}/phase`、`…/{runId}/stop`、`…/{runId}/heartbeat`（「上报面」）。
  别的什么都没有：没有任何 `GET`、没有 `<前缀>api/mcp/`、不能上传事件 / 活动 / 在场心跳。
- **`read`（只读）**：上报面 + `<前缀>api/core/` 下的 `GET` + `<前缀>api/mcp/`（MCP 只收 `POST`；
  会写的工具由 MCP 按范围拒绝，见 `contracts/mcp.tools.v1`）。
- **`write`**：v1.2 / v1.3 设备令牌的全部（`<前缀>api/core/*` 与 `<前缀>api/mcp/`）。**只许人做的端点
  对任何范围都还是只许人**（后端按「带没带 Bearer」拒绝，本契约不改它）。

**放行表（调用方 × 端点，规范性）**。门（verify）是第一道，后端是第二道；括号里写的是哪一层回的。

| 端点 | 人（会话 cookie） | `write`（含 `hct1`） | `read` | `report` | 匿名（单人模式、开关开着） |
|---|---|---|---|---|---|
| 上报面（上面四个 `POST`） | ✓ | ✓ | ✓ | ✓ | ✓，且只能动匿名开的运行（后端） |
| `<前缀>api/core/` 的 `GET` | ✓ | ✓ | ✓ | `403`（门） | `401`（门） |
| `<前缀>api/core/` 的其它方法（上传事件 / 活动 / 心跳、计时、planner 写……；`HEAD` / `OPTIONS` 也算） | ✓ | ✓ | `403`（门） | `403`（门） | `401`（门） |
| 只许人的端点（改检测设置 / 规则、给活动配任务、改挂……） | ✓ | `403`（后端） | `403`（门；`GET` 以外） | `403`（门） | `401`（门） |
| `<前缀>api/mcp/` 只读工具 | ✓ | ✓ | ✓ | `403`（门） | `401`（门） |
| `<前缀>api/mcp/` 会写的工具 | ✓ | ✓ | 工具级拒绝（MCP） | `403`（门） | `401`（门） |
| `<前缀>api/agent/`、页面、`<前缀>api/auth/tokens*` | ✓ | `401` | `401` | `401` | `401` |

- **缺省拒绝**：`report`、`read`、匿名只认表里写明的「方法 + 路径」，别的一律不放。`write` 不看方法
  （v1.2 的行为，老网关不转方法时照常）。
- **上报面按原样字节全匹配**：对 `X-Original-URI` 去掉查询串后的**原样**路径做全匹配
  `<前缀>api/core/agents/(start|<runId>/(phase|stop|heartbeat))`，`runId` 只许 `[A-Za-z0-9_-]{1,64}`。
  **不解码、不规范化**——带 `%XX`、`//`、结尾 `/`、大小写不同的写法都不在表里。这样门认的路径与 nginx
  规范化后转给后端的只可能是同一个。`read` 的 `GET` 沿用 v1.2 的判法（解一遍 `%XX`、拒绝 `.` / `..` 段与反斜杠）。
- **方法只认 `X-Original-Method`**（网关转来的原始请求方法，见「消费方怎么接」）。缺这个头时 `report` /
  `read` / 匿名**什么都过不了**（失败方向是拒绝）。`X-HTTP-Method-Override` 之类的方法覆盖头不读。
- 有效令牌但范围不够 → `403`（网关回 `403`，不跳登录页）；匿名不在表里 → `401`。

### verify 多回的两个头（规范性）

| 响应头 | 何时带 | 值 |
|---|---|---|
| `X-Nexus-Scope` | 设备令牌与匿名的 `204` | `report` / `read` / `write`。**人的会话不带** |
| `X-Nexus-Anonymous` | 匿名的 `204` | `1` |

网关把这两个头**总是覆盖**着转给后端（客户端自带的同名头到不了后端，同租户头的纪律，`contracts/gateway.v1`
第九节），后端据此再拦一遍，并把匿名开的运行标成未验证（nexus-core 契约「调用方范围」节）。不变量 1 改述为
「只返状态码与这几个头，不返 body」。

### 匿名只能上报（规范性）

- **只在单人模式**：只开共享口令、一个账号都没有（数据只有 `u_local` 那一份）。有账号 = 没有可归属的租户，
  匿名一律 `401`——不猜是谁的。
- 放行时**不带租户头**（= `u_local`），带 `X-Nexus-Scope: report` 与 `X-Nexus-Anonymous: 1`。
- **开关**：占位实现 `AUTH_ANONYMOUS_REPORT`，缺省 `true`（仓主定）；`false` = 匿名一律 `401`。
- **这意味着什么，照直说**：开着的时候，**能连到这个端口的任何人**不带任何凭据就能往泳道里加代理运行记录。
  读不到任何东西、改不了任何已有的东西（见下面「后端的隔离」），但能加。端口只在本机 / 可信内网时这是方便；
  端口对外开放时请关掉，或者只发 `report` 令牌。
- **滥用的边界**：网关给不带 `Authorization` 的上报请求单独限速（`contracts/gateway.v1` 第九节）；后端限制
  同时在跑的匿名运行个数；字段长度沿用既有上限。
- **后端的隔离**（nexus-core 实现，写在这里是因为它是匿名能放行的前提）：匿名开的运行标 `unverified`；匿名只能
  给匿名开的运行报相位 / 心跳 / 结束，对别的运行与不存在的运行回同一个 `404`；匿名的 `clientKey` 自成一个名字空间，
  认领不到、也改不了带令牌开的运行；匿名给的 `taskId` / `projectId` / `match` 不采用（否则 404 与否就是一个
  「这个 id 存在吗」的探针）。

### `hct2`：范围写在令牌里

verify 不能查库（不变量 2），所以范围必须在令牌自己身上，并且在签名里。占位实现的新格式：

```
hct2.<签发时间戳>.<到期时间戳>.<纪元>.<范围>.<令牌 id>.<租户>.<HMAC-SHA256(AUTH_SECRET, "device2|" + 前七段 + "|" + 会话盐 + "|" + 代)>
```

- 范围、令牌 id（16 位十六进制的随机数）、到期时刻、租户都在被签的那一段里：改任何一段签名就对不上。
- 签名域是 `"device2|"`，与 `hct1` 的 `"device|"`、会话 cookie 的都不同：把 `hct1` 的各段挪进 `hct2` 的外壳
  （或反过来）签名对不上；`hct1` 的段数里也塞不进一个范围。
- **`hct1` 照常可用 = `write`**。新发的一律 `hct2`（缺省范围 `write`，脚本不用改）；`hct1` 只验不发。
- **令牌从不自动生成**：只有人发——登录了的网页会话（`POST /api/auth/tokens`）或命令行。没有任何端点、
  任何启动流程会替人发一个；设备令牌换不出新令牌（v1.2）。

### 单个吊销（规范性）

- 每个 `hct2` 令牌在令牌文件里有一条记录：`{tenant, name, scope, createdAt, expiresAt}`——
  **不含令牌本身**。verify 读的是它的内存视图（与纪元同一个后台重读），所以仍然不做 IO。
- 这张表是**白名单**：`hct2` 的 id 不在表里、或记录的租户与令牌里的不一致 → `401`。
  单个吊销 = **删掉那一条记录**（不留「已吊销」的墓碑）。于是「列得出来的」与「还能用的」是同一张表，
  手改文件删掉一条等于吊销它。
- **有界**：每个身份名下未到期的记录至多 100 条、全部身份合计至多 1000 条（身份数本身无界——含已删账号留下的
  记录——所以光有前一条不够），满了发新令牌回 `409`；吊销一个就腾出一个名额。到期的记录在下一次写文件时清掉。
  吊销整个身份（v1.2，纪元 +1）照旧，并清掉它名下的全部记录。
- 生效时间同 v1.2：网页操作在本进程立即生效；命令行 `RELOAD_EVERY`（2 秒）内。**命令行新发的令牌也一样**——白名单要等重读才有它，发出来的头两秒里是 `401`（v1.3 的 `hct1` 在令牌文件已存在时是立即可用的；脚本里发完请等一下）。重读按文件的
  「修改时间 + inode + 大小」判断变没变——同一个时间刻度里连写两次也不会漏读。

### 三条不变量照旧成立

1. **不返 body**：多的只有两个响应头。
2. **无 IO**：范围在令牌的签名里；放行表是一条预编译的正则与几次字符串比较；令牌表与纪元一样是内存视图。
   verify 的路径上没有新增任何文件访问（测试：把令牌文件删掉后的两秒内 verify 的结果不变）。
3. **任何异常 `401`**：新增的每一步都在同一层宽 `except` 之内。范围不够的 `403` 是判定结果，不是异常。

代码：`verify_access`、`_verify`、`scope_allows`、`_report_uri`、`REPORT_RE`、`device_identity`、
`issue_device_token`、`revoke_token`、`revoke_identity`、`list_tokens`、`_list_tokens`。

## 消费方怎么接

用 `contracts/gateway.v1`：`include /etc/nginx/honeycomb/gate.inc;` 一行即受门保护并
拿到租户。自己手写 nginx 时要点：

- 承载 `verify` 的 location 标 `internal`；`proxy_pass_request_body off` 并清空转发的
  `Content-Length`；**不把客户端的 `X-Nexus-Tenant` 转给认证服务**。
- `auth_request_set $tenant $upstream_http_x_nexus_tenant;` 取租户，
  `proxy_set_header X-Nexus-Tenant $tenant;` 转给后端——这一句同时覆盖掉客户端自带的同名头。
- `error_page 401 = @登录页跳转;`。
- `/api/auth/` 本身不设门（设门就锁死了唯一入口）。
- （v1.2，要用设备令牌时）verify 子请求须带上 `X-Original-URI $request_uri` 与客户端的
  `Authorization` 头。后者不用写：`auth_request` 子请求默认把原请求的全部头转过去
  （`proxy_pass_request_headers` 缺省 on）；前者 gateway.v1 的两份组装都已设。缺
  `X-Original-URI` 时令牌一律 `401`——失败方向是拒绝，不是放行。
- （v1.4，要用范围 / 匿名上报时）verify 子请求再带 `X-Original-Method $request_method`（子请求自己的方法永远是
  `GET`）；并且**每一条**转给后端的受保护 location 都要
  `auth_request_set $scope $upstream_http_x_nexus_scope;` `proxy_set_header X-Nexus-Scope $scope;`，
  `X-Nexus-Anonymous` 同理——取不到就是空，空值不转发，**同时把客户端自带的同名头盖掉**。只做了前一半
  （转了方法、没覆盖这两个头）的网关会让匿名请求在后端看起来像人的会话：两半必须一起做。
  gateway.v1 的 `gate.inc` 与两份组装都已做好。缺 `X-Original-Method` 时 `report` / `read` / 匿名一律过不了。
- 不设门、但会转给上游的 location（认证服务自己的 `/api/auth/`、验证子请求、不设门的路由）要把三个 `X-Nexus-*`
  头都置空（`proxy_set_header X-Nexus-Scope "";` 等）：空值不转发，客户端自带的那份也一并丢掉。两份组装都是这样，
  `tools/test_install.py` 逐条 location 核对。

### 部署要求：后端不得绕过网关（规范性）

后端（nexus-core、MCP）把**没有** `X-Nexus-Scope` 当成「人的会话 / 对内直连」= 全部权限——这是缺省，不是漏洞，
但它成立的前提是：**到后端的每一条路都经过网关**，且网关**两半都做了**（转 `X-Original-Method`、覆盖转发范围 /
匿名头）。因此：

- 后端的端口不映射到宿主、不对外（compose 里它们只在内部网络上）；谁能直连后端，谁就能自己写这几个头。
- 失败方向（网关这一侧）：老网关不转 `X-Original-Method` → 认证服务对 `report` / `read` / 匿名一律拒绝，放行的
  只剩 `write`（本来就是全部权限），所以「老网关 + 新后端」不会把低权限请求放成无头请求；`error_page` 与内部重定向
  只落到 `@to_login` / 降级 JSON 这样不转给后端的命名位置。
- 唯一没有这层保护的是**转了方法、却没覆盖范围头**的半成品网关：低权限请求会在后端显得像人的会话。上一条的「两半必须
  一起做」就是为它写的。

## 换实现要满足什么

- [ ] 实现上面的端点、方法与每个状态码的含义（含 verify / login / logout「不返回 body」）
- [ ] 满足「三条不变量」，尤其 verify 的**无 IO** 与**异常一律 401**——最容易在「顺手
      给 verify 查一下账号是否被禁用」时被破坏
- [ ] 多用户：verify 带 `X-Nexus-Tenant`（合格式），`/me` 的 `user.id` 与之相等；给不出
      租户时 403/401，不回不带租户的 204
- [ ] 会话 cookie：`HttpOnly`、`SameSite=Lax`、`Secure`（可经配置关闭，仅用于本机 HTTP
      调试，**默认必须开启**）、`Path=<站点前缀>`（缺省 `/`，整站挂子路径时见
      `contracts/gateway.v1` 第七节）。cookie 名不强制叫 `cockpit_session`
- [ ] 口令、签名的比较用抗时序攻击的方式
- [ ] 缺关键配置时**拒绝启动**，不接受弱默认值
- [ ] 登录按来源限次
- [ ] （可选，v1.2）设备令牌：只在 `X-Original-URI` 落在 `<前缀>api/core/` 下时认、识破
      编码过的 `..`；与会话 cookie 互不通用；可吊销；发令牌只认 cookie；日志不记令牌。
      （可选，v1.3）令牌也认 `<前缀>api/mcp/`；不支持的实现在那里对 `Bearer` 回 `401`，
      MCP 仍可凭会话 cookie 用。**只加这一个前缀**，`api/agent/` 等其余路径照旧 `401`
      不支持设备令牌的实现对 `Bearer` 回 `401`，`/api/auth/tokens` 回 `404`
- [ ] （可选，v1.4）范围与匿名上报：范围在令牌的签名里（verify 仍无 IO）；`report` / `read` / 匿名**缺省拒绝**、
      上报面按原样字节全匹配、方法只认 `X-Original-Method`；出示了坏凭据 `401`，**绝不降级成匿名**；匿名只在
      给得出唯一租户时放行，且必须带 `X-Nexus-Anonymous`；`204` 带 `X-Nexus-Scope`；单个吊销不靠请求里读文件。
      不实现的门：令牌一律当 `write`、匿名一律 `401`，后端行为与 v1.3 相同

**不要求**：cookie 值的编码方式（无状态签名或服务端会话表都行）、账号怎么存怎么管。

## 安全约定

**1. 缺关键配置必须拒绝启动，不许有弱默认值。** 占位实现：没设共享口令、账号文件里也
没账号时 `main` 直接 `sys.exit`（「一道没有口令的门等于没有门」）。默认值的问题不是
「这次会被滥用」，是**必然**会被滥用——跑起来了本身会给人「配好了」的错觉。拒绝启动的
失败是响亮的，弱默认值的失败是沉默的。

**2. 口令与签名的比较用定时安全比较，不用 `==`。** `hmac.compare_digest`：共享口令、
账号口令哈希、会话签名三处。`==` 遇到第一个不同字符就返回，耗时泄漏猜对了多长的前缀。

**3. 口令只存哈希。** 占位实现：scrypt（n=2^14, r=8, p=1），每个账号独立随机盐；账号
文件原子写、权限 `0600`。

**4. 日志不记 cookie、不记 body，并截断到 200 字符。** `log_message` 只记方法与状态码。
`BaseHTTPRequestHandler` 会把攻击者可控的原始字节（畸形请求行、超长 URL）塞进日志格式
串，不截断 = 任何人都能往日志里写任意内容、任意长度。

**5. 拒收请求体时必须断开连接。** `_reject_body` 回 `400`/`413` 前先
`close_connection = True`。实测（2026-09-16）：早退而不读完 body，剩下的字节会被当成
**下一个请求行**解析，9KB 的 `aaaa...` 变成一条 400 日志把整个载荷打进日志里——既是协议
错位，也是攻击者可控的日志写入。断连是唯一干净的出路。

**6. 登录限次按网关看到的对端地址算。** 网关转来的 `X-Forwarded-For` 是
`$proxy_add_x_forwarded_for`：客户端自带的值在前，网关亲眼看到的对端在**最后**。只信
最后一个——前面的谁都能伪造，信了就能换个假 IP 无限试。已知天花板：网关前面再套一层
反代时，最后一个是那层反代的地址，所有人共用一个额度；这种部署请在外层反代限次，或
换真账号服务。

**7. 设备令牌（v1.2）只开接口、不进日志。** 令牌是长期凭证（缺省一年），所以：只认
打 `<前缀>api/core/`（v1.3 起加只读的 `<前缀>api/mcp/`）的请求（偷到令牌也打不开页面、换不出新令牌）；只出现在发令牌的
响应体与命令行的标准输出里，不进任何日志与提示；签名域与会话 cookie 分开
（`"device|"` 前缀 + 不同载荷形状），两者互不通用。

**8. 范围与匿名（v1.4）：缺省拒绝，出示了就不降级。** 权限小的调用方（`report`、`read`、匿名）只认放行表里
写明的「方法 + 路径」；路径按原样字节比，不给编码与规范化留缝。带了坏令牌是 `401`，不是「那就当匿名」——
否则吊销一个令牌等于把它降成匿名权限，而不是收回。范围头与匿名头由网关覆盖，客户端写不进去。匿名上报缺省开，
是仓主的决定，代价写在「匿名只能上报」里：**能连到端口的人都能往泳道里加记录**。

## 占位实现

`stub/auth_stub.py` 是本契约的参考实现：纯标准库、零依赖（「它一旦需要 pip install，
就失去了当占位件的资格」）。`tools/install.py` 在没有模块提供 `auth.gate.v1` 时按这个
固定路径找它，所以不能改名或挪位置。怎么跑写在 `stub/stub.yaml`。

**两种登录，可以同时开**：

| | 共享口令 | 账号+密码（v1.1） |
|---|---|---|
| 开法 | `AUTH_PASSWORD` | `AUTH_USERS_FILE` + 命令行加账号 |
| 数据 | 所有人一份（verify 不带租户 = `u_local`） | 每个账号一份（verify 带该账号的租户） |
| 适合 | 单人自用 | 家里 / 小团队局域网 |

多用户部署请**只开账号**，并打开 nexus-core 的 `NEXUS_TENANT_STRICT=1`。两种同时开着
时，知道共享口令的人都进 `u_local`（启动横幅会警告）。

**账号命令**（compose 里在 `deploy/` 下跑；服务没起也能跑）：

```bash
docker compose run --rm auth python /app/auth_stub.py adduser alice   # 加账号，读两遍密码
docker compose run --rm auth python /app/auth_stub.py passwd alice    # 改密码
docker compose run --rm auth python /app/auth_stub.py deluser alice   # 删账号（数据不动）
docker compose run --rm auth python /app/auth_stub.py users           # 列出：名字 + 租户
```

- 名字：小写字母、数字、`_ . -`，1–32 位；登录时不分大小写。密码至少 8 位。
- 租户 id 缺省随机（`u_` + 16 位十六进制）。**从共享口令切到账号、想保留原来的数据**：
  `adduser <名字> --id u_local`，这个账号就接手原来那一份。
- 非终端（脚本）从标准输入读一行密码。
- 账号命令的读—改—写持文件锁（`<账号文件>.lock`），同时跑几条也不丢更新；密码在拿锁之前读。
- 账号文件改了 `RELOAD_EVERY`（2 秒）内生效。**改密码、删账号会作废该账号所有已登录的
  会话**（每个账号有一个会话盐，签名里带着它，改密码 / 删账号时换掉）。
- 共享口令的会话只能靠换 `AUTH_SECRET`（或不设它、重启）整体作废；去掉 `AUTH_PASSWORD`
  后它签过的会话也立即失效。

**设备令牌**（v1.2）：

```bash
docker compose exec auth python /app/auth_stub.py token alice    # 给 alice 发一个，打到标准输出
docker compose exec auth python /app/auth_stub.py token          # 共享口令身份的（须开着共享口令）
docker compose exec auth python /app/auth_stub.py revoke alice   # 作废 alice 的全部令牌
docker compose exec auth python /app/auth_stub.py revoke         # 作废共享口令身份的全部令牌
# v1.4：范围、备注、列出、单个吊销
docker compose exec auth python /app/auth_stub.py token alice --scope report --name 沙箱里的编码代理
docker compose exec auth python /app/auth_stub.py tokens alice   # id、范围、是否有效、到期、备注（没有令牌本身）
docker compose exec auth python /app/auth_stub.py revoke-token 0000000000000000   # 只作废这一个
```

- （v1.4）`--scope report|read|write`，缺省 `write`；新发的都是 `hct2`。令牌打到标准输出，id 与范围打到标准错误。

- 用法：`Authorization: Bearer <令牌>` 调 `<站点前缀>api/core/...`（v1.3 起也可调 `<站点前缀>api/mcp/`）。只开接口，不开页面。
- 格式（`hct1`，v1.4 起只验不发；新格式 `hct2` 见「权限范围与匿名上报」节）：
  `hct1.<签发时间戳>.<纪元>.<租户>.<HMAC-SHA256(AUTH_SECRET, "device|" + 前四段 + "|" + 会话盐 + "|" + 代)>`，
  无状态，服务端不存令牌表。共享口令身份的租户段为空，「会话盐」取共享口令的派生值
  （`_shared_sess`）。
- **吊销**：令牌文件 `{"gen": "<随机>", "epochs": {"<租户，共享口令为 ''>": n}}`（v1.4 多一个键 `tokens`：
  `hct2` 令牌的记录，没有令牌本身；老文件没有这个键 = 空表）（原子写、
  `0600`、持 `<令牌文件>.lock`）。每个租户一个整数纪元，令牌记着签发时的纪元，对不上就
  作废，`revoke` 把纪元 +1；`gen` 是这份文件的「代」，签进每个令牌。文件在发第一个令牌
  （或第一次吊销）时建出来。verify 读的是内存视图，后台线程 `RELOAD_EVERY`（2 秒）内
  重读——命令行发的令牌、命令行的吊销都是 2 秒内生效。
- **失败方向是拒绝**：令牌文件不在 = 没有有效令牌；**删掉它 = 全部令牌作废**（重建时
  换一代），不会让吊销过的令牌复活。启动时文件读不出来 = 令牌一律 `401` 直到修好；
  运行中文件坏了沿用旧视图。
- **跟着账号走**：签名里带账号的会话盐，改密码、删账号时该账号的令牌一并作废；换掉或
  去掉 `AUTH_PASSWORD` 后共享口令身份的令牌失效（会话 cookie 在这一点上维持 v1 行为不变）。
- **必须设 `AUTH_SECRET`**：不设时每个进程随机生成密钥，命令行签的令牌服务端不认、
  重启后全部失效——所以命令行拒绝发、`POST /api/auth/tokens` 回 `503`。发布版安装脚本
  写的 `.env` 已带它；`deploy/` 下手搭的，自己在 `.env` 里加。
- 换 `AUTH_SECRET` 会作废所有令牌与所有会话。

**环境变量**：

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `AUTH_PASSWORD` | 与账号二选一 | 无 | 共享口令。没设它、账号文件里也没账号 = 拒绝启动 |
| `AUTH_USERS_FILE` | ❌ | 无（不开账号登录） | 账号文件路径。compose 里是 `/data/users.json`（named volume `honeycomb_auth_data`） |
| `AUTH_SECRET` | ❌（设备令牌必填） | 每次启动随机 | 签 cookie 与设备令牌用；随机 = 每次重启所有人重新登录，且不发设备令牌 |
| `AUTH_COOKIE_SECURE` | ❌ | `true` | 本机 HTTP 调试须显式设 `false` |
| `AUTH_BIND` | ❌ | `127.0.0.1:8010` | 监听地址 |
| `AUTH_SESSION_DAYS` | ❌ | `30` | 会话有效期天数 |
| `AUTH_BASE_PATH` | ❌ | `/` | 站点前缀，cookie 的 `Path` 跟着它（compose 里 = `HONEYCOMB_BASE_PATH`）；设备令牌只认 `<它>api/core/`（v1.3 起加 `<它>api/mcp/`）。格式不对拒绝启动 |
| `AUTH_TOKENS_FILE` | ❌ | 账号文件同目录的 `tokens.json`（compose 里 = `/data/tokens.json`，同一个数据卷；只开共享口令时也在） | 设备令牌的纪元文件（v1.2）。它与 `AUTH_USERS_FILE` 都没设 = 不发令牌 |
| `AUTH_TOKEN_DAYS` | ❌ | `365` | 设备令牌有效期天数（v1.2） |
| `AUTH_ANONYMOUS_REPORT` | ❌ | `true` | （v1.4）单人模式下不带凭据的请求能不能上报代理运行。**开着 = 能连到端口的人都能往泳道里加记录（读不到任何东西）**；端口对外开放时设 `false`。有账号时它不起作用（匿名一律 `401`） |

**限次**：同一对端 15 分钟内错 10 次，之后 `429` 直到窗口滑过去。只数失败。

测试：`contracts/auth.gate.v1/tests/test_stub.py`（真起进程、真发 HTTP）；端到端见
`deploy/test/accounts.sh`、`deploy/test/tokens.sh`（CI「多账号」）。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-17 | v1 首版。契约文本从 `stub/auth_stub.py` 的实际行为反推得出 |
| 2026-09-23 | 站点前缀（`contracts/gateway.v1` 第七节）：cookie `Path` 从固定 `/` 改为站点前缀（缺省仍是 `/`，未挂子路径的部署零变化）；占位实现加 `AUTH_BASE_PATH`；自带登录页从自己的地址推前缀，`next` 只接受前缀内地址 |
| 2026-09-23 | **v1.1（纯追加）**：verify 的 `204` 可带 `X-Nexus-Tenant`，可回 `403`（登录了但没有可用租户）；新增 `GET /api/auth/me`，冻结最小形状 `{ok, user:{id, name}}`，`user.id` 等于 verify 的租户；login 请求体可带 `username`；login 可回 `429`；health 可带 `accounts` / `sharedPassword`；换实现清单加多用户与限次两条；安全约定加「只存哈希」「限次按网关看到的对端算」。占位实现加账号+密码（scrypt、账号文件、命令行管理、改密码/删账号作废会话），共享口令模式行为不变。引用改为按函数名，不再按行号 |
| 2026-10-09 | **v1.4（纯追加，契约先行）**：权限范围与匿名上报（仓主 2026-10-09）。① 新令牌格式 `hct2`，签名覆盖身份 + 范围 + 令牌 id + 到期；范围 `report` ⊂ `read` ⊂ `write`，`hct1` 照常可用 = `write`；② verify 读网关转来的 `X-Original-Method`，对 `report` / `read` / 匿名按放行表缺省拒绝，范围不够回 `403`，`204` 多回 `X-Nexus-Scope` / `X-Nexus-Anonymous`；③ 什么凭据都不带的请求在单人模式下只放行上报面（`AUTH_ANONYMOUS_REPORT`，缺省开），出示了坏凭据仍是 `401`；④ `POST /api/auth/tokens` 收 `name` / `scope`、多回 `id` 等字段、可回 `409`；`POST /api/auth/tokens/revoke` 收 `tokenId`（单个吊销，可回 `404`）；新增 `GET /api/auth/tokens`；命令行 `token --scope --name`、`tokens`、`revoke-token`；⑤ health 可带 `anonymousReport`；⑥ 令牌文件多一个键 `tokens`（令牌记录 = 白名单，有界）。**2026-10-09 评审修订（仍是 v1.4）**：单个吊销 = 删记录、不留墓碑（吊销腾出名额，列表与 `201` 不再有 `revoked`，重复吊销回 `404`）；全部身份合计 1000 条的总上限；出示了空的会话 cookie / 空的 `Authorization` = `401`，不再当匿名；verify 路径不记请求日志；网关不设门的 location 也要置空三个 `X-Nexus-*` 头；补「部署要求」一节。三条不变量不变（不变量 1 的措辞补上新的两个头）。消费方（网关）须转 `X-Original-Method` 并覆盖转发两个新头——`gateway.v1` 第九节 |
| 2026-09-28 | **v1.3（纯追加，契约先行）**：设备令牌认的路径从 `<前缀>api/core/` 扩到再加 `<前缀>api/mcp/`（`contracts/mcp.tools.v1` 的对外入口）。只多开一个只读接口前缀，页面、`api/auth/`、`api/agent/` 照旧 `401`；v1.2 的实现不认它只是少一个能力，失败方向是拒绝。三条不变量不变。占位实现随 v0.3 AI 桥实现 PR 跟上（`_api_uri` 认两个前缀，测试 `test_bearer_opens_mcp_but_not_agent`） |
| 2026-09-28 | **v1.2（纯追加）**：设备令牌。verify 可读 `Authorization: Bearer`（只在 `X-Original-URI` 落在 `<前缀>api/core/` 下时认，带了 Bearer 不回落到 cookie；原有 cookie 路径行为不变）；新增 `POST /api/auth/tokens`、`POST /api/auth/tokens/revoke`（只认 cookie、须 `application/json`）；消费方须给 verify 子请求带 `X-Original-URI`（gateway.v1 两份组装早已带）；换实现清单加一条可选项；安全约定加第 7 条。占位实现：`hct1.` 无状态令牌、按租户纪元吊销（`AUTH_TOKENS_FILE`）、`AUTH_TOKEN_DAYS`、命令行 `token` / `revoke`；没设 `AUTH_SECRET` 不发令牌。三条不变量不变 |
