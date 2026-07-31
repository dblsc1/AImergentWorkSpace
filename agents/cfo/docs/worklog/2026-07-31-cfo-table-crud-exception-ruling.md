# 2026-07-31 · CFO · 人类裁决：table 恢复写能力，四前端只读原则的唯一例外

用户看到 ring/table 两个真实页面后，直接问"能不能现在加 CRUD"。这属于
`agents/protocol/supervision.md` 五类必须叫人的"主观产品取舍"，没有现场自
决，抛给用户用 `AskUserQuestion` 选：加进 table 本身 / 单开一个 admin 视图
/ 先不做转做别的。用户选了"加进 table"。

## 处置

`agents/reference/新增前端视图模块.md` 是四前端"默认只读"这条原则的登记
处——已加一条**登记为 table 的例外**（人类裁决 2026-07-31），并明确写清楚
"这条例外只对 table 成立，不因此放宽给 ring/gantt/garden"，防止下一个建
新视图的人抄错先例。没变的部分也写清楚了：写操作仍然只进 nexus-core 一个
入口，table 不因此获得与其他前端互相通信的权限——那条铁律（HANDOFF §2.1
"单一事实来源"）没被这次裁决动到。

按铁律4"改契约先走变更评审，批准后先改契约再改代码"，contract.md 先落
CR（新增 `nexus-core.planner.crud.v1` consumes，`table.static.v1` summary
从"只读展示"改"展示+编辑"，变更记录行写明 v0.2 CR 先于代码），代码还没写，
下一步派 programmer 实现。
