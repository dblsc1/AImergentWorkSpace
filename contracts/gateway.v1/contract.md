# gateway.v1 · 网关对外接口契约

> 网关是整站唯一的入口（nginx）。本契约规定**部署方怎么在不改、不复制本仓任何文件的
> 前提下**接自己的东西：换认证服务、换登录页、加自己的路由、让自己的路由受同一道门
> 保护、拿到当前租户。
>
> 标【冻结】的条目定了就不随便改；要改就发 gateway.v2，v1 保持可用。
> 实现：`deploy/nginx/templates/default.conf.template`（手写的默认组装）与
> `tools/generate.py`（`install.sh add` 生成的组装），共用 `modules/nginx-docker/`
> 里的门片段、注入片段与静态资源。两份组装都由 CI 真起一遍核对本契约。

```yaml
provides:
  - id: gateway.v1
    summary: 整站入口。认证服务 / 登录页 / 额外路由可替换，租户头由网关覆盖
consumes:
  - id: auth.gate.v1
    contract: ../auth.gate.v1/contract.md
    purpose: 登录门。verify 的 204/401 决定放行与否，204 可带租户头
```

## 一、怎么接：只用自己的 `.env` 与 override 文件

部署方的一切改动放在仓外：

```sh
cd deploy
docker compose -f docker-compose.yml -f /你的/override.yml up -d
```

`.env` 里能设的网关变量：

| 变量 | 缺省 | 作用 |
|---|---|---|
| `AUTH_UPSTREAM` | `auth:8010` | 认证服务地址（`host:port`，须在 compose 网络里可达）【冻结】 |
| `HONEYCOMB_LOGIN_DIR` | 登录门占位件自带的页面 | 登录页静态目录，挂在 `/login/`【冻结】 |
| `HONEYCOMB_EXTRA_ROUTES_DIR` | `deploy/nginx/extra`（空） | 额外路由目录，见第四节【冻结】 |
| `HONEYCOMB_BASE_PATH` | `/` | 站点前缀：整站挂在子路径下时设，如 `/Cockpit/`，以 `/` 开头和结尾。见第七节【冻结】 |
| `AGENT_UPSTREAM` | `agent:8030` | 聊天后端地址（`host:port`），实现 `agent.chat.v1`。见第八节（2026-09-28 追加）|

路径变量写**绝对路径**最稳；相对路径按 compose 文件所在目录解析。

一个完整的 override 例子——换掉认证服务、换登录页、加一条路由：

```yaml
# /srv/my-deploy/override.yml
services:
  my-auth:
    image: my-org/my-auth:1.0
    networks: [honeycomb-net]
  web:
    depends_on:
      my-auth:
        condition: service_started
```

```sh
# /srv/my-deploy/.env（与 deploy/.env 合并使用，或直接写进 deploy/.env）
AUTH_UPSTREAM=my-auth:9000
HONEYCOMB_LOGIN_DIR=/srv/my-deploy/login
HONEYCOMB_EXTRA_ROUTES_DIR=/srv/my-deploy/routes
```

换掉认证服务时，把缺省的占位认证服务关掉——同一个 override 里加：

```yaml
  auth:
    profiles: [disabled]
```

网关对它的依赖是可选的（`required: false`），关掉之后照常启动。

## 二、认证上游【冻结】

- **整个 `/api/auth/` 前缀**原样转给 `AUTH_UPSTREAM`，不止 verify / login / logout。
  你的认证服务在这个前缀下放什么端点都行（短信登录、账号管理……）。
- 网关内部的放行判据只调一个端点：`GET /api/auth/verify`（auth.gate.v1）：
  **2xx 放行，401 跳登录页，403 回 403；其它状态码网关回 500**（nginx auth_request
  的固定语义）。认证服务出错时请回 401，别回 5xx——否则用户看到的是 500 而不是登录页。
- **责任边界**：`/api/auth/` 这个前缀在网关上**不设门**——登录接口必须未登录可达，
  设门就锁死了唯一的入口。所以**认证服务在这个前缀下的每一个非登录端点都必须自己
  鉴权**，网关不替它挡。别以为「在网关后面」就等于「受保护」。
- 网关转给认证服务的请求**不带**客户端的 `X-Nexus-Tenant`（见第五节）。
- verify 子请求带 `X-Original-URI: $request_uri`（原始请求的路径，含站点前缀、未解码），
  并照 `auth_request` 的缺省把原请求的其余头（含 `Authorization`）原样带过去。
  auth.gate v1.2 的设备令牌靠这两样判断「只开 `/api/core/*`、不开页面」；自己写的门
  子请求也要保留这两样，否则令牌一律被拒（失败方向是拒绝，不是放行）。

## 三、登录页【冻结】

- `HONEYCOMB_LOGIN_DIR` 目录挂在 `/login/`，入口 `index.html`，不设门、不注入顶栏。
- 未登录访问受保护页面一律 302 到 `/login/`（相对 Location，端口由浏览器沿用）。

## 四、额外路由【冻结】

- `HONEYCOMB_EXTRA_ROUTES_DIR` 目录里每个 `*.conf.template` 文件，由网关启动时用
  envsubst 渲染，再 `include` 进 server 块。**只有 `.conf.template` 结尾的文件会被读**。
- 文件内容是若干 nginx `location` 块。可用的模板变量：
  - `${HONEYCOMB_BASE_PATH}`：站点前缀，写在 **location 路径**里（nginx 的 location
    路径不接受运行期变量，只能在渲染时写成字面量）。
  - `${AUTH_UPSTREAM}`：认证服务地址。
- 可用的 nginx 变量（写在**取值处**，如 `proxy_pass`、`return`）：
  - `$honeycomb_base`：同 `HONEYCOMB_BASE_PATH`。
  - `$honeycomb_tenant`：当前租户（仅在 include 了 gate.inc 的 location 里有值）。
- 其余 `$xxx` 照常是 nginx 自己的变量——渲染只替换上面列出的名字。

**站点前缀**：按 `${HONEYCOMB_BASE_PATH}` 写路径，整站挂到子路径时你的路由不用改
（第七节）。

### 让自己的路由受同一道门保护【冻结】

```nginx
location ${HONEYCOMB_BASE_PATH}my-app/ {
    include /etc/nginx/honeycomb/gate.inc;     # 未登录跳登录页；转发租户头
    include /etc/nginx/honeycomb/inject.inc;   # 可选：注入共享顶栏（仅 HTML 页面）
    proxy_pass http://my-app:3000/;
}
```

`gate.inc` 展开后就是下面四行，名字【冻结】，也可以直接手写（2026-10-09 起文件里还多四行转发调用方范围，见第九节）：

```nginx
auth_request /__auth_verify;
auth_request_set $honeycomb_tenant $upstream_http_x_nexus_tenant;
error_page 401 = @to_login;
proxy_set_header X-Nexus-Tenant $honeycomb_tenant;
```

- `/__auth_verify`：内部校验入口（`internal`，外部访问 404）。
- `@to_login`：未登录跳转。
- 注意 nginx 的继承规则：location 里只要写了任意一条 `proxy_set_header`，外层的就全部
  失效——所以 gate.inc 必须和你自己的 `proxy_set_header` 写在同一个 location 里。

## 五、租户头 `X-Nexus-Tenant`【冻结】

- 认证服务的 verify 放行时（204）**可以**带响应头 `X-Nexus-Tenant: <租户 id>`。
- 网关把它原样设到每一条受保护转发的请求上；认证服务没给就是空，**空值不转发**。
- **客户端自己带来的 `X-Nexus-Tenant` 一律被覆盖**，到不了任何后端，也到不了认证服务。
  后端能看到的租户只可能来自认证服务。
- 租户 id 格式：`^[A-Za-z0-9_.:-]{1,64}$`。**由后端校验**（nexus-core 不合规即 400，
  不清洗、不截断）；网关只负责转发与覆盖。
- 单人部署（占位认证服务的共享口令登录不给租户）：后端收不到这个头，按单一租户工作，
  行为不变。占位认证服务的账号登录（auth.gate v1.1）给每个账号一个租户。

## 六、其它固定行为

| 路径 | 行为 |
|---|---|
| `/`（即站点前缀，下同，见第七节）| 302 到主界面（装了哪个前端声明 `home: true` 就去哪）|
| `/healthz` | 200，不设门，给健康检查用 |
| `/__cockpit/*` | 共享顶栏、设计 tokens、站点图标。不设门（不含用户数据）|
| `/__cockpit/current` | 顶栏计时芯片的数据。设门；未登录或后端挂了回 `{"degraded":true,"running":false}`，不跳登录页 |
| `/favicon.ico` `/favicon.svg` | 站点图标 |

前端页面（hive、ring 及以后装的）被注入同一套顶栏：页签按**已装的前端**生成，没装
的前端没有页签。

## 七、整站挂在子路径下【冻结】

前面还有一层反代、要把整站挂在 `https://example.com/Cockpit/` 下时，设
`HONEYCOMB_BASE_PATH=/Cockpit/`（外层反代把 `/Cockpit/` 原样转给网关，**不去前缀**）。

- **对外**：下面第六节的每个路径、登录页、前端页面、`/api/...`、额外路由，全部挂在
  前缀下（`/Cockpit/hive/`、`/Cockpit/api/core/...`）。跳转（未登录跳登录页、根路径跳
  主界面）都带前缀。例外只有两个：`/healthz`（给容器健康检查，外层反代不用转）和
  `/__auth_verify`（内部子请求）。
- **对后端与认证服务**：网关转发时**去掉前缀**——nexus-core 永远看到 `/api/core/...`，
  认证服务永远看到 `/api/auth/...`。它们不需要知道前缀，除了下一条。
- **会话 cookie 的 `Path`** 由认证服务定，应设成站点前缀（同一域名下别的站点收不到这个
  会话）。占位认证服务读 `AUTH_BASE_PATH`，compose 把它设成同一个值。
- **前端页面**：网关往每个注入顶栏的页面里注入 `window.HONEYCOMB_BASE`（值即前缀），
  页面的请求与跳转都从它拼，不写死 `/api/...`。没注入时按 `/` 处理。登录页不注入，
  从自己的地址推前缀（`<前缀>login/`），`next` 只接受前缀内的地址。
- 顶栏页签 `window.HONEYCOMB_NAV` 里的地址已含前缀。

CI 把手写与生成的两份组装各按 `/` 与 `/Cockpit/` 真起一遍。

## 八、AI 桥：聊天后端与 MCP（2026-09-28 追加，v0.3）

两条新的受保护路由，都 `include gate.inc`（过门、覆盖租户头）：

| 对外路径 | 转给 | 契约 |
|---|---|---|
| `<前缀>api/agent/` | `AGENT_UPSTREAM`（缺省 `agent:8030`）的 `/api/agent/` | `contracts/agent.chat.v1` |
| `<前缀>api/mcp/` | MCP 服务（缺省 `mcp:8020`）的 `/api/mcp/` | `contracts/mcp.tools.v1` |

- **换聊天后端**：同换认证服务——override 里加自己的服务（接到 `honeycomb-agent-net`），`.env` 里设
  `AGENT_UPSTREAM`，把缺省的 `agent` 服务 `profiles: [disabled]`。前端、MCP 都不用动。
- 两条路由转发时**清掉** `Cookie` 与 `Authorization`（`proxy_set_header ... ""`）：门已经在网关验过了，
  后端只需要租户头；用户凭据不该出现在一个会跑大模型的进程里。门子请求照旧拿得到这两个头
  （子请求读的是客户端原始请求头，不受本 location 的 `proxy_set_header` 影响）。
- `<前缀>api/agent/`：`proxy_buffering off`（SSE 逐条到浏览器），`proxy_read_timeout` 不短于 300 秒
  （聊天后端每 15 秒发心跳）。`<前缀>api/mcp/`：同样关缓冲（Streamable HTTP 可能回 SSE）。
- 设备令牌：auth.gate v1.3 起对 `<前缀>api/mcp/` 也认（让用户自己的 MCP 客户端能接）；
  `<前缀>api/agent/` **不认令牌**，只认浏览器会话。
- 网络：新增内部网 `honeycomb-agent-net`，上面只有网关、MCP、聊天后端。nexus-core、认证服务、mongo
  **不在**这张网上——聊天后端够不着它们，读数据只能经 MCP（`mcp.tools.v1` 第三节）。MCP 同时在两张网上。
- **缺了它们网关照常起**：这两条路由的上游用运行期解析（`resolver 127.0.0.11` + 变量写 `proxy_pass`），
  不在启动时解析——否则关掉 `agent` 服务、或 `AGENT_UPSTREAM` 指向的服务还没起，nginx 直接起不来，整站跟着挂。
  连不上时这两条路由回 502；前端按 `agent.chat.v1` 把聊天面板整块藏起来。


**实现落定（v0.3 MCP 实现 PR，追加）**：

- **网络名【冻结】**：compose 里的网络键是 `honeycomb-agent-net`，按 compose 惯例实际网络名是
  `<项目名>_honeycomb-agent-net`（项目名即 `HONEYCOMB_PROJECT`，缺省 `honeycomb` → `honeycomb_honeycomb-agent-net`；
  同机第二套 `honeycomb-2` → `honeycomb-2_honeycomb-agent-net`）。按项目分开是有意的：同一台机器上两套部署的聊天后端
  不能互相够到对方的 MCP。部署方接自己的服务：与本仓 compose 同项目合并（`-f docker-compose.yml -f override.yml`）时
  在 override 里写 `networks: [honeycomb-agent-net]`；从另一个 compose 项目接时声明
  `networks: {honeycomb-agent-net: {external: true, name: <项目名>_honeycomb-agent-net}}`。**键名与这条命名规则改了就是破坏性变更**（发 gateway.v2）。
- MCP 上游固定 `mcp:8020`，不设变量：换 MCP 实现 = override 里用同名服务 `mcp` 顶替。
- 生成的组装（`install.sh add`）：路由由模块清单声明，`bridge: true` 即本节这套（过门、清凭据头、关缓冲、
  运行期解析），`upstreamEnv: <变量>` 让上游可由 `.env` 换（网关的 `NGINX_ENVSUBST_FILTER` 自动放行它）；
  清单的 `service.networks` 追加 `honeycomb-agent-net`，网关随之接上这张网。所以生成的组装里这两条路由跟着
  `mcp`、聊天后端模块装上才出现；手写的默认组装两条都常驻。
- 验证：CI「网关契约」job 把 `mcp` 用 profiles 关掉（网关照常起、`/api/mcp/` 回 502），聊天后端换成回显请求头的
  小服务（收不到 `Cookie` / `Authorization` / 伪造的租户头）；「多账号」job 用设备令牌经网关调 MCP。

## 九、调用方范围与匿名上报（2026-10-09 追加，v0.4）

auth.gate v1.4 让设备令牌带范围（`report` / `read` / `write`），并让不带凭据的请求只能上报。网关这边四件事：

- **门子请求多带一个头**：`/__auth_verify` 加 `proxy_set_header X-Original-Method $request_method;`
  （子请求自己的方法永远是 `GET`，`$request_method` 给的是原始请求的）。认证服务据「方法 + `X-Original-URI`」
  判断范围够不够。这一句同时盖掉客户端自带的同名头。
- **`gate.inc` 多四行**（原来四行不动，名字照旧【冻结】）：

  ```nginx
  auth_request_set $honeycomb_scope $upstream_http_x_nexus_scope;
  auth_request_set $honeycomb_anonymous $upstream_http_x_nexus_anonymous;
  proxy_set_header X-Nexus-Scope $honeycomb_scope;
  proxy_set_header X-Nexus-Anonymous $honeycomb_anonymous;
  ```

  与租户头同一套纪律：认证服务没给就是空，**空值不转发**；**客户端自己带来的 `X-Nexus-Scope` /
  `X-Nexus-Anonymous` 一律被覆盖**，到不了任何 `include gate.inc` 的后端。`/__cockpit/current` 那条手写了门的
  location 也加了同样四行。
- **手写门的部署方**：第四节那四行照旧可用。但如果你的 location 自己转给 nexus-core 或 MCP，要把上面四行也写上——
  否则后端看不到范围，只剩认证服务那一道。不带凭据的请求到不了你的路由：认证服务只对
  `<前缀>api/core/agents/…` 的四个上报端点放行匿名。
- **匿名上报的限速**：`<前缀>api/core/` 这条 location 上有一个专用的限速区 `honeycomb_anon`：
  **不带 `Authorization` 头**、`POST` 到 `/api/core/agents/` 下的请求，按客户端地址每分钟 60 个、突发 30 个，
  超了回 `429`。带令牌的请求不进这个区（网页会话的这类请求也算在内——页面不发这种请求）。前面还有一层反代时，
  所有人共用一个地址的额度（同登录限次的天花板，`auth.gate.v1` 安全约定 6）：这种部署请在外层限速，或关掉匿名上报。

状态码：认证服务回 `403`（令牌有效但范围不够）→ 网关回 `403`；回 `401`（没登录、坏令牌、匿名不许）→ 照旧跳登录页。

验证：CI「组装冒烟」四格（两份组装 × 两种前缀）与「多账号」跑 `deploy/test/scopes.sh`：三种范围各能做什么、
匿名只能上报、单个吊销、伪造的三个头都被盖掉、限速回 `429`。

## 实现说明（不改接口）

- **2026-09-30 · 上游运行期解析**（0.2.x 修复）：以前 `proxy_pass` 写死主机名，nginx 只在启动时解析一次；
  `docker compose up -d` 只重建了 nexus-core 或认证服务（升级只换了它的镜像）时它换了 IP，网关还打旧 IP，
  `/api/core/`、登录门一直 502，直到重启 web。现在两份组装的每条上游（nexus-core、`AUTH_UPSTREAM`、
  模块清单声明的路由）都用 `resolver 127.0.0.11 valid=10s` + 变量 `proxy_pass`，后端重建后 10 秒内自动跟上；
  转发路径由 `rewrite … break` 去前缀，后端看到的路径与 query 串不变（唯一差别：请求行里**未编码**的
  非 ASCII 或 `"<>` 之类字符，现在按百分号编码转发，含义相同；浏览器本来就会编码）。
  附带：上游没起时网关照常启动，这些路由回 502（顶栏芯片仍回降级 JSON）。
  `AUTH_UPSTREAM` 仍是 `host:port`，主机名须能被 Docker 内置 DNS 解析（compose 服务名 / 网络别名，或直接写 IP）；
  只写在 `extra_hosts`（容器 `/etc/hosts`）里的名字不行。CI：`deploy/test/recreate.sh` 在两份组装、两种前缀下
  单独重建 nexus-core 与 auth，不重启网关，断言 `/api/core/` 200。
- **2026-09-30 · 并入 v0.3**：第八节两条 AI 桥路由（手写与生成的 `bridge: true`）改成同一写法：共用 server 级
  `resolver`（不再每个 location 各写一条），`rewrite` 用 `\Q…\E` 按字面匹配站点前缀并带 `(?s)`，补写与普通路由
  同形的 `proxy_redirect`；生成组装里的变量名统一为 `$honeycomb_up_<序号>`。`recreate.sh` 同样逐个重建 `mcp`、`agent`。
- **2026-10-08 · 让 AI 认窗口（nexus-core v2.15）不动本契约**：第八节的网络划分一个字不改——`honeycomb-agent-net` 上
  仍然只有 web、mcp、agent，nexus-core 不在上面、也不去调聊天后端。「cockpit 主动叫 AI」的方向反过来实现：聊天后端的后台工人
  定时经 MCP 的对内地址去取（`agent.chat.v1` 第十节、`mcp.tools.v1` v1.9），走的是既有的 agent → mcp → nexus-core 这一条，
  带的仍是租户头、不带用户凭据。没有新路由、新变量、新网络。
