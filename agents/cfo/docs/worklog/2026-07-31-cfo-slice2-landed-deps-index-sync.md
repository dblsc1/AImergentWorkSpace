# 2026-07-31 · CFO · 切片2审核通过落地 + 依赖索引同步

## 切片2（planner CRUD）

sonnet 审核子代理（`aed460ae1129ec090`）verdict=approved，无 escalation。
P1-P12 独立重跑全部通过，P5/P9 独立造场景验证（不采信 programmer 自测）。
两点开放问题独立判定可接受：key 重算不级联子对象、三处可选入参服务端缺省。
过程中顺带修复两个基础设施缺陷（`check_slice1_j10.py` 因 `seed_planner` 返回形状
变化崩溃；`run_all.sh` 两处 pipefail/SIGPIPE 竞态假阴性），均落铁律23反向验证断言。

已用 `AIMERGENT_PUSH_UNREVIEWED=1`（approved report 已在库）推上
`cockpit-nexus-core` `feat/init`（`3c59f85..34b5d41`）。推送时门禁唯一红项是
已知假阳性（`blockers/2026-07-30-abspath-second-copy.md`，命中
`scripts/dispatch.sh:58` 反面教材注释），用 `AIMERGENT_PUSH_SKIP_GATES` 记账绕过。

## 契约文档状态同步

`nexus-core/module_docs/contract.md` 顶部版本说明与 API 表状态列本轮之前停在
v0.2/"未实现"，与已落地并 approved 的实况脱节（铁律11 漏项）。已同步为 v0.4 +
十二条 planner 端点"已实现"，追加提交 `03515bb`，同样用 skip-gates 绕过同一假阳性。

## 依赖索引同步

`agents/cfo/docs/依赖索引.md` 里 ring/table 对 nexus-core views 的依赖状态此前标
"上游未实现，先对 mock"——现在上游已实现且 approved，改为指向真实契约文件
`code/nexus-core/module_docs/contract.md`，并如实记下当前真实缺口：
ring/table 自己的 `contract.md` 仍是模板占位，`code/frontend/` 仍空。

## 下一步

按 `agents/reference/新增前端视图模块.md` 八步配方，向 ring、table 两个模块
各派一个 programmer 实例（sonnet），填契约 + 派生上游原件 + 接真实 API。
