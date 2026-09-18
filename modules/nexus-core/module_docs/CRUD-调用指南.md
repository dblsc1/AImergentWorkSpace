# CRUD 调用指南（对外）

> 面向**外部消费方**：想用代码增删改分区/项目/任务的人。
> 规范性定义在 `contract.md`；本文是照着契约写的**使用说明**，不是第二份契约。
> 两者冲突以 `contract.md` 为准，并请报告——那说明本文过期了。

## 0. 一分钟版

```bash
BASE=http://<你的局域网地址>:8081        # 局域网；本机是 http://127.0.0.1:8081
JAR=/tmp/cockpit.cookies

# 1. 登录（拿 cookie）
curl -s -c $JAR -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' -d '{"password":"你的口令"}'

# 2. 之后每个请求都带 -b $JAR
curl -s -b $JAR $BASE/api/core/planner/zones
curl -s -b $JAR -X POST $BASE/api/core/planner/zones \
  -H 'Content-Type: application/json' -d '{"name":"示例分区一"}'
```

**不带 cookie 一律 401。** 见下方第 1 节。

## 1. 先过门：鉴权

系统前面挡着一道登录门（`code/auth` 模块）。所有 `/api/core/*` 请求
在到达业务后端前会先被 nginx 验一次。

| 情况 | 你会拿到 |
|---|---|
| 没带 cookie / cookie 过期 / 被篡改 | **401** |
| 门服务本身挂了 | **500**（fail-closed，宁可不能用也不放行）|
| 正常 | 业务后端的真实响应 |

```bash
# 登录：204 = 成功，401 = 口令错
curl -s -c $JAR -o /dev/null -w '%{http_code}\n' \
  -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' -d '{"password":"..."}'

# 登出（只清本地 cookie，不吊销服务端令牌，见下方「已知限制」）
curl -s -b $JAR -X POST $BASE/api/auth/logout
```

> **已知限制**：会话是无状态签名，`logout` 不吊销令牌。
> 怀疑 cookie 泄漏时唯一有效手段是**轮换 `AUTH_SECRET`**（所有既有 cookie 立即失效）。
>
> **传输是明文 HTTP**（当前部署没有 TLS）。这道门挡得住「有人打开页面点删除」，
> **挡不住「有人抓包」**。别在不可信网络上用。完整说明见
> `../../auth/module_docs/contract.md` §「本模块挡不住什么」。

## 2. 唯一入口

| 操作 | 路径 |
|---|---|
| 列表 | `GET /api/core/planner/{type}` |
| 新建 | `POST /api/core/planner/{type}` |
| 改 | `PATCH /api/core/planner/{type}/{id}` |
| 删 | `DELETE /api/core/planner/{type}/{id}` |

`{type}` 取值：`zones` | `projects` | `tasks`。**未知 type 返 404 并点名合法取值。**

> **v0.6 变更**：曾经有一套分离端点（`/api/core/zones` 等十二条），
> 与上面并存过一小段时间。**已删除**，现在访问会 404。
> 如果你照着旧文档写了代码，改成 `/api/core/planner/{type}` 即可——
> 请求体、响应、错误消息**逐字节相同**，只有路径变了。

## 3. 三层结构

```
分区 zone  ──┬── 项目 project ──┬── 任务 task
             │                   └── 任务 task
             └── 项目 project
```

建东西必须自底向上：先有 zone 才能建 project，先有 project 才能建 task。

## 4. 三类对象的字段

### zones

```jsonc
// POST 请求体
{ "name": "示例分区一", "color": "#ff9d45", "order": 0 }   // color/order 可选
// 响应
{ "id": "z_7f21a4", "key": "1", "name": "示例分区一", "color": "#ff9d45", "order": 0 }
```

### projects

```jsonc
// POST 请求体（zoneId 与 name 必填）
{ "zoneId": "z_7f21a4", "name": "示例项目四", "plannedWeight": 100.0 }
// 响应
{ "id": "p_3c98de", "key": "1-2", "zoneId": "z_7f21a4", "name": "示例项目四",
  "status": "active", "plannedWeight": 100.0, "plan": null }
```

### tasks

```jsonc
// POST 请求体（projectId 与 name 必填）
{ "projectId": "p_3c98de", "name": "晨跑 3 公里",
  "kind": "normal", "flags": [] }        // kind: normal | ephemeral
// 响应
{ "id": "t_a1b2c3", "key": "1-2-3-1", "name": "晨跑 3 公里",
  "projectId": "p_3c98de", "done": false, "kind": "normal", "flags": [] }
```

## 5. `id` / `key` / `name` 三件套——**最容易踩的一节**

每个对象有**三个不同的东西**，别混：

| | 例 | 会不会变 | 你该拿它干嘛 |
|---|---|---|---|
| `id` | `t_a1b2c3` | **永不变** | **一切引用都用它**。存外键、发事件、写脚本 |
| `key` | `1-2-3-1` | **改名/搬移时重算** | 给人看的路径。**不要用它做检索键或外键** |
| `name` | `晨跑 3 公里` | 随时可改 | 显示 |

**规则**：

- `id` 与 `key` **都由系统生成**。POST 请求体里**不要带 `id`**，带了也不生效。
- **`key` 没有唯一约束**（搬移重算过程中可能短暂重复），唯一性压在 `id` 上。
  **`key` 算错了不致命，`id` 撞了才是事故。**
- `id` **删除后永不复用**。删掉再建同名对象，新 `id` 与旧的不同——
  因为历史事件永久引用着旧 `id`。
- `key` 不做拼音音译（`塔` 和 `她` 音译后会撞成同一个串）。中英文走同一张登记表。

## 6. 改：只改你要改的字段

`PATCH` 是**局部更新**，请求体里只放要改的字段。

```bash
# 改名：只带 name
curl -b $JAR -X PATCH $BASE/api/core/planner/tasks/t_a1b2c3 \
  -H 'Content-Type: application/json' -d '{"name":"晨跑 5 公里"}'

# 换归属：只带 projectId
curl -b $JAR -X PATCH $BASE/api/core/planner/tasks/t_a1b2c3 \
  -H 'Content-Type: application/json' -d '{"projectId":"p_other"}'
```

**别在一个 PATCH 里顺手带上不想改的字段。** 改名只动 `name`，
换归属只动 `projectId`/`zoneId`——两者都不会动 `id`，也不会污染已落库的历史事件。

## 7. 删：不做级联，会被拒

```bash
curl -b $JAR -X DELETE $BASE/api/core/planner/zones/z_7f21a4
```

| 情况 | 返回 |
|---|---|
| 对象是空的（无子对象） | `204` |
| **下面还有子对象** | **`409`** + 消息点名剩余数量 |
| id 不存在 | `404` |

```json
// 409 的消息长这样，直接展示给用户就行
{"detail": "分区 'z_7f21a4' 下还有 2 个项目——不做级联删除，先清空再删"}
```

**为什么不级联**：一次性删掉一整棵子树不可逆。强制你先清空，是让你在
删每一层时都看见自己在删什么。要清空就自己按 tasks → projects → zones 顺序删。

## 8. 报错：直接展示，别自己重写

所有 4xx 的 `detail` 字段**已经点名了具体是哪个字段、哪个值、合法取值是什么**：

```json
{"detail": "项目 的 plannedWeight 不能为负：-5.0"}
{"detail": "任务 kind 非法：'不合法的kind'，合法取值 normal/ephemeral"}
{"detail": "zoneId 不存在：'z_不存在的幽灵'"}
```

**建议直接把 `detail` 显示给用户，不要用自己的措辞包装。**
也**不要在你这边重复实现同一套校验**——后端是权威，你算一遍只会与它漂移。
（`code/table` 就是这么做的，可以照抄。）

| 状态码 | 含义 |
|---|---|
| `400` | 请求体校验不过（字段缺失/取值非法/引用的父对象不存在）|
| `401` | 没登录 |
| `404` | 对象不存在，或 `{type}` 不是三个合法值之一 |
| `409` | 删除被拒（还有子对象）|
| `422` | 请求体不是合法 JSON / 结构对不上 |

## 9. 写完之后：重新拉，别本地推算

写操作成功后，**重新拉一次 `GET /api/core/views/tree`** 拿最新全量树，
不要在本地推算"删了这条之后树长什么样"。

```bash
curl -s -b $JAR $BASE/api/core/views/tree
```

理由：本地维护一份影子状态，迟早与后端漂移，而且这类 bug 最难查。
多一次请求换一个永远正确的视图，这笔交易划算。

## 10. 完整例子：建一棵树再删掉

```bash
BASE=http://127.0.0.1:8081
JAR=/tmp/cockpit.cookies

curl -s -c $JAR -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' -d '{"password":"你的口令"}'

# 建
Z=$(curl -s -b $JAR -X POST $BASE/api/core/planner/zones \
      -H 'Content-Type: application/json' -d '{"name":"演示区"}' \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

P=$(curl -s -b $JAR -X POST $BASE/api/core/planner/projects \
      -H 'Content-Type: application/json' -d "{\"zoneId\":\"$Z\",\"name\":\"演示项目\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

T=$(curl -s -b $JAR -X POST $BASE/api/core/planner/tasks \
      -H 'Content-Type: application/json' -d "{\"projectId\":\"$P\",\"name\":\"演示任务\"}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 直接删分区会被拒（409，点名剩余数）
curl -s -b $JAR -X DELETE $BASE/api/core/planner/zones/$Z

# 正确顺序：自顶向下清空
curl -s -b $JAR -X DELETE $BASE/api/core/planner/tasks/$T
curl -s -b $JAR -X DELETE $BASE/api/core/planner/projects/$P
curl -s -b $JAR -X DELETE $BASE/api/core/planner/zones/$Z
```

## 11. 不要做的事

| 别做 | 为什么 |
|---|---|
| 直连 MongoDB | 绕过校验与投影更新，数据会不一致。**只走 HTTP** |
| 用 `key` 做外键或检索 | 它会因搬移而重算。用 `id` |
| POST 时自己指定 `id` | 系统生成，你给的不生效 |
| 在你这边重复实现校验 | 后端是权威，两份实现必然漂移 |
| 本地推算写操作后的状态 | 重新拉 `views/tree` |
| 直接写 `events` 集合 | 绕过信封校验与防重，重放时会炸在查不出来源的地方 |

## 12. 相关文档

| 文件 | 内容 |
|---|---|
| `contract.md` | **规范性定义**，本文与它冲突以它为准 |
| `../../auth/module_docs/contract.md` | 登录门、会话、已知安全边界 |
| `../../../contracts/yq-event-v1.md` | 事件信封标准（想自己发事件时看）|
