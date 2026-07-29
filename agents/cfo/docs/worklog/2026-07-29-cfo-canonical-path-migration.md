# worklog · CFO canonical 路径迁移（consulter 提到平级）

- 日期：2026-07-29
- 角色：CFO
- 分支：`feat/frontend-view-modules`
- tier：`simple`

## 背景

`8aa2033` 把 consulter 提到与 CFO 平级：`agents/consulter/` 不再挂在 CFO 名下。
理由成立——**它审我的活，挂在我名下是审核独立性的结构性缺陷**，不是目录美观问题。
CFO 同时去掉了多余的 `arbiter/` 一层。

## 做了什么

| 从 | 到 |
|---|---|
| `agents/cfo/docs/report.json` | `agents/cfo/docs/report.json` |
| `agents/cfo/docs/依赖索引.md` | `agents/cfo/docs/依赖索引.md` |
| `agents/cfo/docs/decisions/` | `agents/cfo/docs/decisions/` |

**worklog 不搬**（人类裁决）：它是历史叙事，当时写的就是当时的路径，
搬它等于改写历史。所以仓内仍有指向 `agents/cfo/docs/worklog/` 的引用，
**那些引用是对的，不是漏改**。

同步更新了三处指向已迁移文件的长期文档（铁律 11）：
`agents/reference/manual/文档地图.md`、`agents/reference/upstream/README.md`、
`agents/reference/新增前端视图模块.md`。

## 依赖漂移说明（检查项 90）

无。纯路径迁移，未新增/删除包依赖，未改契约字段，未新增跨模块引用。

## 自检

- `git status` 显示三项为 `R`（rename），不是删除+新增——Git 认得出是同一份东西
- `git grep 'agents/cfo/docs/\(依赖索引\|decisions\)'` 在 `agents/reference/` 下剩余 0 处
