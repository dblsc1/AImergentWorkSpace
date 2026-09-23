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

**站点前缀**：本版固定为 `/`，整站挂到子路径是后续版本的事。现在就按
`${HONEYCOMB_BASE_PATH}` 写路径，那一版来的时候你的路由不用改。

### 让自己的路由受同一道门保护【冻结】

```nginx
location ${HONEYCOMB_BASE_PATH}my-app/ {
    include /etc/nginx/honeycomb/gate.inc;     # 未登录跳登录页；转发租户头
    include /etc/nginx/honeycomb/inject.inc;   # 可选：注入共享顶栏（仅 HTML 页面）
    proxy_pass http://my-app:3000/;
}
```

`gate.inc` 展开后就是下面四行，名字【冻结】，也可以直接手写：

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
| `/` | 302 到主界面（装了哪个前端声明 `home: true` 就去哪）|
| `/healthz` | 200，不设门，给健康检查用 |
| `/__cockpit/*` | 共享顶栏、设计 tokens、站点图标。不设门（不含用户数据）|
| `/__cockpit/current` | 顶栏计时芯片的数据。设门；未登录或后端挂了回 `{"degraded":true,"running":false}`，不跳登录页 |
| `/favicon.ico` `/favicon.svg` | 站点图标 |

前端页面（hive、ring 及以后装的）被注入同一套顶栏：页签按**已装的前端**生成，没装
的前端没有页签。
