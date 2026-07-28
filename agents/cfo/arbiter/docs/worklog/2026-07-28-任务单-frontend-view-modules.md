# 任务单 · 建 ring / table 前端视图模块 + 新增模块配方

- 任务号：`root-frontend-view-modules-260728-224924-336`
- 执行角色：CFO arbiter（建模块与项目级索引属 CFO；模块内实现另派模块角色）
- tier：`normal`
- 分支：`feat/frontend-view-modules`

## 目标

按人类裁决「两个模块，而且需要考虑新增模块的情况」：

1. 建 `code/ring`、`code/table` 两个独立模块骨架；
2. 把「新增一个前端视图模块」固化成**可重复配方**，使加 gantt / garden 是照配方走一遍，
   而不是照抄已有模块；
3. 建项目级依赖索引，含反向索引「改这个契约要通知谁」。

**本任务不写模块内业务代码**——派生原件、填契约由模块角色执行（CFO 角色卡写边界）。

## 可触碰目录（写边界）

- `agents/`（本任务申领的写区路签）

`code/ring/`、`code/table/` 是**各自独立的 Git 仓**，由 `new_module.sh` 生成并自带门禁，
不在本路签覆盖范围内，其内容由各模块角色提交。

## 自检门

1. `scripts/mission_complete.sh` 全绿（pre-commit 强制）
2. `scripts/gates/run-gates.sh` 全绿
3. 两个模块的门禁**实测**而非采信打印：`ls code/<m>/.git/hooks/` 三个 hook 齐全，
   且 `run-gates.sh` 红在 `module_docs/contract.md 还没填实`（正确的失败形状）

## 验收标准

| # | 判据 | 确定命令 |
|---|---|---|
| B1 | 两个模块骨架存在且各自是独立仓 | `git -C code/ring rev-parse --show-toplevel` 指向模块自身 |
| B2 | 门禁真装上 | `ls code/ring/.git/hooks/` 含 pre-commit/commit-msg/pre-push |
| B3 | 门禁红在正确的地方 | `cd code/ring && ./scripts/gates/run-gates.sh` 报 contract.md 未填实 |
| B4 | 配方文件存在且被导航收录 | 文档地图含 `agents/reference/新增前端视图模块.md` 一行 |
| B5 | 依赖索引含反向索引 | `agents/cfo/arbiter/docs/依赖索引.md` 有「改这个契约要通知谁」表 |
| B6 | 远端状态如实登记 | 依赖索引两个模块的远端列为「本地模式·未验证」，非空白、非豁免 |

## 升级条件

- 模块远端落点需要人类授权（对外动作，不自行执行）
- 供给侧契约（nexus-core `contract.md`）不存在时，不得让 `consumes` 指向 HANDOFF 充数
