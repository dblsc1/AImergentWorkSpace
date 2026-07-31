# 2026-07-31 · CFO · ring/table 前端适配落地 + 新障签

## ring / table 前端适配

两个 programmer 实例（sonnet）并行完成任务单：ring 接 `views/current`（R2-R9 全部
达标），table 接 `views/tree`（R2-R10 全部达标）。两者都正确识别任务单把 R1
（填 `module_docs/contract.md`）派错了角色——programmer 对该文件只读，
`checks/programmer/81-scope-respected.sh` 机械拒绝。两者都没有硬闯写区，而是在
report.json 里备好可直接落地的 patch，交 arbiter。已以 arbiter 身份采纳落地
（ring 提交 `04c7b04`，table 提交 `10bd041`），两个模块 `module_docs/contract.md`
均已填实，`./scripts/gates/run-gates.sh` 均只剩已知假阳性（
`blockers/2026-07-30-abspath-second-copy.md` 同一根因）。

table 侧还有一个值得记录的判断：programmer 把上游原件的增删改弹窗整体移除了
（finding S1），理由是配方文档已经把四个前端定义为只读镜像。复核后判定这是执行
既有成文规则，不构成需要人类现场拍板的"主观产品取舍"，接受不升级，已记进
table 的 arbiter worklog。

两个模块都发现同一个遗留问题：我此前（同日）做门禁同步提交时申领的 arbiter
写签忘记归还，挡住了 programmer 实例的 `mission_start.sh`——已在两边分别用
`mission_start.sh --release arbiter` 还签，核实过之前的提交本身完好。

## 新障签：写区路签核验静默失效

table 侧 programmer 如实上报（未利用）：`scripts/mission_complete.sh` 推断出
`role` 后没有 export 给子检查脚本，导致只要提交时不手动传 `AIMERGENT_ROLE=xxx`，
`checks/_common/05-write-lease.sh` 的"角色未知时不拦"兜底逻辑被错误触发，写签
核验在常规提交路径上静默判过。已立障
`blockers/2026-07-31-mission-complete-role-not-exported.md`，详见该便条。
这是安全性质的框架缺口，影响面覆盖全部四个已建模块，准备派 consulter 修，
CFO 不代改（`scripts/` 与 `code/_template/` 均非 CFO 写区）。

## 下一步

- 派 consulter（sonnet）修障签 + 加回归断言。
- 待用户对两件事表态：根仓 `68dc3bb` 是否推 `origin/v5`；本地 dev nginx 容器
  是否重挂到两个模块的真实产出（当前仍挂着保险柜原件，页面看到的还是假数据）。
- ring/table 均已本地 commit、未推远端，pending 与上述决定一并处理。
