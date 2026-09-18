# JSON 导入导出指南（对外）

> 面向**外部消费方**：想「导出我的分区/项目/任务 → 在编辑器里改 → 导回去
> 直接生效」的人。规范性定义在 `contract.md`「只读全量导出」与「JSON 一键
> 导入编辑」两节；本文是照着契约写的**使用说明**，不是第二份契约。两者冲突
> 以 `contract.md` 为准，并请报告——那说明本文过期了。

## 0. 一分钟版

```bash
BASE=http://127.0.0.1:8081
JAR=/tmp/cockpit.cookies

# 1. 登录
curl -s -c $JAR -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' -d '{"password":"changeme"}'   # 换成你的真实口令

# 2. 导出，存成文件
curl -s -b $JAR $BASE/api/core/export -o export.json

# 3. 用 python 剥出 zones/projects/tasks 三个数组（拿掉 events/projections，
#    import 端点不接受它们），存成 import.json——也可以直接手改 export.json
#    再删掉这两个键，看你顺手
python3 -c '
import json
data = json.load(open("export.json"))
json.dump({"zones": data["zones"], "projects": data["projects"], "tasks": data["tasks"]},
          open("import.json", "w"), ensure_ascii=False, indent=2)
'

# 4. 编辑 import.json——改名字/权重/排期、删掉一个对象（整段删掉即可）、
#    新增一个对象（复制一份同类型的、删掉它的 "id" 键，其余字段照填）

# 5. dry-run：只看计划，一个字节都不写
DRY=$(curl -s -b $JAR -X POST $BASE/api/core/import \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json;d=json.load(open("import.json"));d["dryRun"]=True;print(json.dumps(d))')")
echo "$DRY" | python3 -m json.tool   # 看一眼计划里要建/改/删什么
CHECKSUM=$(echo "$DRY" | python3 -c 'import sys,json;print(json.load(sys.stdin)["checksum"])')

# 6. 确认计划没问题后，apply：带上第 5 步拿到的 checksum
curl -s -b $JAR -X POST $BASE/api/core/import \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json;d=json.load(open('import.json'));d['dryRun']=False;d['checksum']='$CHECKSUM';print(json.dumps(d))")"
```

**不带 checksum、或 checksum 是过时的 dry-run 结果，apply 一律拒绝。** 见第 4 节。

## 1. 导出：`GET /api/core/export`

```bash
curl -s -b $JAR $BASE/api/core/export
```

```jsonc
{
  "zones":    [ /* 原始文档 */ ],
  "projects": [ /* 原始文档 */ ],
  "tasks":    [ /* 原始文档 */ ],
  "events":   [ /* 事实台账，只读展示用，别拿它去 import */ ],
  "projections": { "proj_current": {...}, "proj_daily_stats": [...] },
  "exportedAt": "2026-08-08T12:00:00+00:00"
}
```

导出的 `zones`/`projects`/`tasks` 就是你要拿去改、再导回去的那三个数组。
`events`/`projections` 是只读展示用的——**导回去时必须删掉这两个键**，
见第 3 节。

## 2. 怎么改这份 JSON

| 想做的事 | 怎么改 |
|---|---|
| 改一个字段（改名、调权重、改排期…） | 直接改那个对象的字段值，`id` 原样保留 |
| 新增一个对象 | 复制一份同类型模板对象，**删掉（或设为 `null`）它的 `id` 字段**，其余字段照填；新项目的 `zoneId`、新任务的 `projectId` 必须填**已存在的**分区/项目 id（不支持"同一批里父子都新建"，见第 6 节） |
| 删除一个对象 | 把这个对象**整段从数组里删掉**（不是清空字段） |
| 什么都不改 | 原样保留——不带 `id` 的判"新建"，带已存在 `id` 且字段都没变的判"不用改"，两者互不打扰 |

**`id`/`key`/`lastWriter` 这三个字段别去改它们的值**：`id` 改了等于"删掉
旧的、新建一个不相干的"（会撞「id 不存在」的 400）；`key` 是系统按名字/
归属重算的，你填什么都不影响结果；`lastWriter` 是系统写的，import 执行后
会统一变成 `"human"`（第 5 节），你现在填什么都会被覆盖。

## 3. `events`/`projections` 必须删掉，不能留着

```jsonc
// 这样提交会直接 400，且明确点名是哪个字段：
{"zones": [...], "projects": [...], "tasks": [...], "events": []}
```

```json
{"detail": "import 请求体不接受字段 'events'——它们是只读事实台账/派生投影，本端点只碰 planner（zones/projects/tasks）；请从导出的 JSON 里删除这些键后再提交，不许静默忽略（events 是 append-only 事实台账，不容绕过）"}
```

**为什么不是静默忽略这两个字段**：`events` 是"发生过什么"的事实台账，
如果你以为改了它（比如手滑把一段计时记录也改了、想"导回去让它生效"），
而服务端悄悄不理它，你会以为改动生效了、其实什么都没变——**这比报错
坏得多**。删掉这两个键、只提交 `zones`/`projects`/`tasks` 三个数组即可。

`exportedAt` 不用管——留着也行，服务端原样接受但忽略。

## 4. 两段式：先 dry-run，再 apply

**`dryRun` 缺省就是 `true`**——不传这个字段，或显式传 `true`，都只算计划、
不写库：

```bash
curl -s -b $JAR -X POST $BASE/api/core/import \
  -H 'Content-Type: application/json' \
  -d '{
    "zones": [ ... ],
    "projects": [ ... ],
    "tasks": [ ... ]
  }'
```

```jsonc
{
  "dryRun": true,
  "checksum": "3f9c2b7a1e8d…",
  "allowDelete": false,
  "plan": {
    "zones":    [{"op":"update","id":"z_7f21a4","fields":{"name":"改后的名字"}}],
    "projects": [{"op":"create","id":null,"fields":{"zoneId":"z_7f21a4","name":"新项目"}}],
    "tasks":    []
  },
  "applied": null,
  "summary": {"create": 1, "update": 1, "delete": 0},
  "skippedDeletes": {"zones": [], "projects": [], "tasks": []}
}
```

看一眼 `plan` 确认要建/改/删的是不是你想要的，再拿着这份响应里的
`checksum` 去 apply（`dryRun:false`）：

```bash
curl -s -b $JAR -X POST $BASE/api/core/import \
  -H 'Content-Type: application/json' \
  -d '{
    "zones": [ ... ],
    "projects": [ ... ],
    "tasks": [ ... ],
    "dryRun": false,
    "checksum": "3f9c2b7a1e8d…"
  }'
```

apply 的响应把 `plan` 换成 `applied`（同构，`create` 的 `id` 已经回填成
真实生成的 id）：

```jsonc
{
  "dryRun": false,
  "checksum": "3f9c2b7a1e8d…",
  "allowDelete": false,
  "plan": null,
  "applied": {
    "zones":    [{"op":"update","id":"z_7f21a4","fields":{"name":"改后的名字"}}],
    "projects": [{"op":"create","id":"p_9c3a01","fields":{"zoneId":"z_7f21a4","name":"新项目"}}],
    "tasks":    []
  },
  "summary": {"create": 1, "update": 1, "delete": 0}
}
```

**`checksum` 是"这份计划的指纹"，不是随便一个字符串**：

- 缺 `checksum` 就 `dryRun:false` → **400**，提示先 dry-run。
- 带的 `checksum` 与"服务端对当前库重新算一遍同一份 JSON 得到的结果"不一致
  → **409**，提示重新 dry-run。**最常见的触发原因**：你 dry-run 完之后，
  又用别的方式（比如浏览器里直接改了一下）动了库；或者你在 dry-run 和
  apply 之间又编辑了一次 `import.json`。两种情况都对应"计划可能已经不准了"，
  服务端拒绝执行，重新跑一次 dry-run 就能拿到匹配当前状态的新 checksum。

```bash
# 409 的响应形如：
{"detail": "计划已过期：提供的 checksum '...' 与对当前库重新算出的 '...' 不一致——库在 dry-run 之后被改动过（或者 payload/allowDelete 变了），请重新对同一份 JSON 跑一次 dry-run 拿到最新计划与 checksum 后再 apply"}
```

## 5. 删除：默认不删，要删必须显式开

**从数组里去掉一个对象，默认不会被删。** 导出的 JSON 里少一个对象，最可能
是你手滑漏拷了一段，不该被当成"我要删掉它"。

```bash
# 库里有 3 个任务，import.json 只带了 2 个 —— 默认（不传 allowDelete）下，
# 少的那个任务 90% 就是不动它。
```

dry-run 的响应会告诉你"少了谁"（`skippedDeletes`），但不会把它排进 `plan`：

```jsonc
{ "plan": {"tasks": []}, "skippedDeletes": {"tasks": ["t_老任务的id"]} }
```

**真的要删，显式传 `allowDelete: true`**（dry-run 和 apply 都要传，且两次
必须一致——不一致会让重算的计划和 dry-run 时不同，从而 checksum 不一致，
见第 4 节）：

```bash
curl -s -b $JAR -X POST $BASE/api/core/import \
  -H 'Content-Type: application/json' \
  -d '{"zones":[...], "projects":[...], "tasks":[...], "allowDelete": true}'
```

**既有的级联保护（409）不会因为走 import 就失效**：如果你从 `projects`
数组里删掉了一个项目，却忘了把它下面的任务也一起从 `tasks` 数组里删掉，
apply 到删除这个项目那一步时仍会撞上「还有任务——不做级联删除」的 409，
和直接调 `DELETE /api/core/planner/projects/{id}` 一样。要整棵子树一起删，
把分区/项目/任务都从各自数组里删掉即可——服务端按"任务→项目→分区"（子
先于父）的顺序执行，整棵一起删是顺畅的。

## 6. 已知限制：新建对象的父对象必须已经存在

新建项目的 `zoneId`、新建任务的 `projectId` 必须指向**库里已经存在**的
分区/项目——**不支持在同一批导入里把父对象和它下面的子对象一起新建**
（新建的父对象在这份 JSON 里根本没有真实 id，没有办法把子对象的引用和它
对上）。

```jsonc
// 这样会 400："新建项目的 zoneId 引用了不存在的分区……"
{
  "zones":    [{"name": "新分区（没有 id）"}],
  "projects": [{"zoneId": "占位符", "name": "新分区下的新项目"}]
}
```

**要新建带子对象的父对象，分两次导入**：第一次只带新分区，apply 后从响应
的 `applied.zones[].id` 拿到真实分区 id；第二次带上这个真实 id 作为新项目
的 `zoneId` 再导一次。

## 7. 报错：直接展示，别自己重写

同「CRUD 调用指南」第 8 节——所有 4xx 的 `detail` 已经点名了具体字段/值，
直接展示给用户就行，不要自己重新包装措辞。

| 状态码 | 含义 |
|---|---|
| `400` | 请求体校验不过（缺字段/引用不存在/`events` 或 `projections` 出现/缺 `checksum`）|
| `403` | 带 AI 凭据自报人的路径——AI 不能用这个端点绕开高风险二次设防（正常人类用户不会撞到这条）|
| `404` | 没登录（鉴权门），或整个端点路径拼错 |
| `409` | `checksum` 过期，或 apply 途中撞上既有的级联删除保护 |
| `422` | 请求体不是合法 JSON / 出现了既不是 `events`/`projections` 也不是本文档列出的字段 |

## 8. 相关文档

| 文件 | 内容 |
|---|---|
| `contract.md` | **规范性定义**，本文与它冲突以它为准（「只读全量导出」「JSON 一键导入编辑」两节）|
| `CRUD-调用指南.md` | 单条建/改/删的直连 CRUD 用法（`import` 是"整批同步一次"，两者不冲突，各按需选） |
| `../../auth/module_docs/contract.md` | 登录门、会话、已知安全边界 |
