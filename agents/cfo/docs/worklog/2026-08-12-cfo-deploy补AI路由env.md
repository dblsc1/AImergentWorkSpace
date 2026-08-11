# worklog · deploy 补 ai-planner 上游 env（拆掉一个活雷）

- 角色：cfo · 日期：2026-08-12 · tier：simple

## 目标

nginx-docker v0.8 加了 `/api/planner/*` 路由，模板引用 `${AI_PLANNER_UPSTREAM}`。
仓根 `deploy/docker-compose.yml` 不在该模块写区，它按规矩停下上报。本轮补上。

## 为什么这是「活雷」不是「待办」

模板是**只读挂载**进容器的，改完就生效于**下一次容器启动**。而 compose 里：
- 没有 `AI_PLANNER_UPSTREAM`
- `NGINX_ENVSUBST_FILTER` 白名单是 `^(NGINX_|NEXUS_|AUTH_)`，**不含 `AI_`**

两条任缺其一，`${AI_PLANNER_UPSTREAM}` 就不会被 envsubst 替换，
nginx 会把它当成**自己的**变量语法去解析、因变量未定义而**拒绝启动**。

即：改完模板到改 compose 之间的窗口里，**nginx 只要因任何原因重启就再也起不来**
（机器重启、`restart: unless-stopped` 触发、手动 restart 都算）。
当时容器还活着只是因为它跑的是**旧的渲染结果**——渲染发生在启动时，不是每次请求。

**失败形态很坏**：报错指向模板行号，与真因（compose 少一个 env）隔着一层。

## 改了什么（两处必须同时改）

1. `NGINX_ENVSUBST_FILTER` → `^(NGINX_|NEXUS_|AUTH_|AI_)`，并写明「加新变量必须同时加前缀」
2. 新增 `AI_PLANNER_UPSTREAM: "${AI_PLANNER_UPSTREAM:-http://172.24.0.1:8700}"`，
   注释写清：ai-planner 是**宿主进程不在 compose 里**（LLM 驱动在宿主，容器够不到），
   所以上游用 bridge 网关而非服务名；**网关地址会随网络重建而变**，附上查询命令。

## 验证（真实输出）

网关地址实查：`docker network inspect cockpit_cockpit-net -f '{{(index .IPAM.Config 0).Gateway}}'` → `172.24.0.1`（与写入值一致）。

重建 nginx 后：

| 检查 | 结果 |
|---|---|
| nginx 容器 | `Up (healthy)` |
| `/healthz` | 200 |
| `/login/` | 200 |
| `/table/` | 302（未登录，符合既有口径）|
| `/api/core/health` | 401（未登录，符合既有口径）|
| **`/api/planner/plan` 未认证** | **401**（不是 302 —— SSE 消费方要机器可分支的码）|

**登录后端到端实跑**（本轮最关键的一条）：
`POST /api/planner/plan` 经 10000 网关拿到真实 SSE 事件流：
`thinking`（含真实推理片段）→ `action_plan`（AI 产出 1 条动作）→
`executed`（`read_schedule`，actor=ai）→ `done`（含 1 条 `move` 提议）。

**安全边界当场被验证生效**：AI 想搬移任务（高风险），产出的是 `proposals` 里的一条提议，
**没有执行**。生产库计数 5/5/11/12 前后不变，`lastWriter="ai"` 的对象为 0。

## 可触碰目录

`deploy/`、`agents/cfo/docs/`。

## 验收标准与自检门

- [x] 两处同时改（少一处 nginx 起不来）
- [x] 上游不写死，走 env 且带默认值；注释给出网关地址的查询命令
- [x] 既有五条路由零回归（真实状态码见上表）
- [x] 新路由未认证返 401 而非 302
- [x] 登录后端到端 SSE 真实跑通，事件流原样记录
- [x] 生产库零写入、零污染（计数比对 + `lastWriter` 扫描）
