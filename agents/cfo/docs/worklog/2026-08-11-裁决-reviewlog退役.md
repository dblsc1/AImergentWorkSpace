# CFO 裁决 · `module_docs/reviewlog.md` 退役

- 角色：cfo · 日期：2026-08-11 · tier：normal
- 来源：波2 里程碑文档审计 P2 → CFO 出方案 → **人类裁决选 A（退役）**

## 目标

把七个模块里恒空的 `module_docs/reviewlog.md` 退役：删模板、删七份实例、
清掉规范与脚本里对它的引用，使「文档体系」里不再有一个无人可写的条目。

## 为什么退役（不是"懒得维护"）

审计发现七个模块的 `reviewlog.md` **全是空表头、零数据行**，自项目启动至今。
查写属主表后发现根因**不是疏忽，是结构上没人能写**：

| 角色 | 可写 |
|---|---|
| 模块 arbiter | `module_docs/`（**`reviewlog.md` 除外**）|
| programmer_reviewer | `review/` |
| module_reviewer | `review/reviewreport/` |

arbiter 的写区**明文排除**它，两个 reviewer 的写区都在 `review/` 下、够不到 `module_docs/`。
**没有任何角色被授权写它**——真去写会被写区路签（checks/05）当场拦下。

更准确地说是**半截实现**：`scripts/lib/review.sh:71` 把 `module_docs/reviewlog.md` 列进了
「审核后允许再动的路径」白名单，说明设计意图确实存在（reviewer 批完补一行），
**但授权那半边从没建**。允许改的门开了，能改的人没有。

## 为什么不是"补授权"

1. **没有铁律要求它**。铁律 18 要的是「每次审核一份 **reviewreport**」——那个在
   `review/reviewreport/` 下，**一直正常在用**。文档四件套（铁律 13）是
   worklog / report.json / comm.jsonl / handoff.md，**也没有它**。
   它是模块模板里的第五样东西，不被任何铁律要求。
2. **补授权要动角色边界**，牵一发动全身：arbiter 不做审核、reviewer 够不到 `module_docs/`，
   给谁都别扭。为一个非必需的导航层去改角色写边界，代价与收益倒挂。
3. **信息一点没丢**：审核详情全在 `review/reviewreport/`（含 canonical report.json）。
   缺的只是一层索引。

## 为什么不能"就放着"

这是三条路里最差的一条。一个**恒空、且规则上永远不可能不空**的文档，
和「长期红着、大家学会绕过的门禁」是同一类腐蚀：它训练所有人忽略文档要求，
而文档要求正是本项目全部治理的载体。（nginx-docker 的 `smoke.sh` 红了两天那次，
`rules.md` 里已经写过这句话。）

## 将来若真需要那层索引

正确做法是**脚本从 `review/reviewreport/*.json` 生成**（像 `logs/INDEX.md` 那样），
而不是让人手抄一份必然漂移的副本。生成物不进仓、随时可重建，
也就不存在"谁有权写"的问题——**这个坑的根因就是把生成物当成了人写文档**。

## 可触碰目录

根仓：`agents/`（规范与角色卡）、`code/_template/`（模板唯一事实源）、
`scripts/`（引用清理）。七个模块实例的删除另派机械单（跨仓，各自要签与 attribution）。

## 验收标准与自检门

- [ ] `code/_template/module_docs/reviewlog.md` 删除（唯一事实源，堵住未来新模块）
- [ ] `agents/AGENTS.md` 模板布局图与角色写边界表去掉它（含那句"除外"的括号）
- [ ] `agents/roles/arbiter.md`、`scripts/new_agent.sh` 的写区描述同步
- [ ] `scripts/lib/review.sh` 的白名单去掉该条（否则引用一个不存在的文件）
- [ ] `agents/reference/1 structure/README.md` 的目录说明同步（铁律 11：改一处扫全部）
- [ ] `scripts/selftest.sh` 与 `scripts/gates/run-gates.sh` 全绿
- [ ] 七个模块实例的删除有单可派、不遗漏
