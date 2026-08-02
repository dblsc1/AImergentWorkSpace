# 任务单 · consulter · 2026-08-02

派活方：CFO（联络消息，非文件任务单；本单为 consulter 自建，供 `mission_start.sh` 起飞检查用）
任务 id：`consulter-report-base-lease-ruling`

## 目标

1. 修正 `agents/consulter/docs/findings/report.json` 的 `git.base` —— 现填 `4ab1b36`
   （feat 分支上的 commit），判据要的是本分支相对 PR 目标基线的分叉点。
   这一条正卡住全仓推送门（`scripts/gates/run-gates.sh` 唯一一条红）。
2. 对 CFO 移交的框架问题裁一个落点：**「完工即自动还签」在本仓落在哪**。
   上游 X_structure 放在 `mission_complete.sh`；本仓 `mission_complete.sh` 就是
   pre-commit 钩子主体（`scripts/hooks/pre-commit:23`），每次 commit 都跑，
   在那还签 = 每提交一次把互斥关掉一次。CFO 倾向落在 `dispatch.sh`。
   **裁决产出为文字 + findings，不在本轮实现**（实现动 `scripts/`，越出本轮写区）。

## 可判定的验收标准

- `git rev-parse` 可解析的 40 位 sha 写进 `git.base`，且
  `git merge-base --is-ancestor <base> $(git merge-base HEAD origin/main)` 退 0。
- `./scripts/gates/run-gates.sh` 退出码 0（直取 `$?`，不看管道回显）。
- 本轮全部改动落在 `agents/consulter/` 之内（`git diff --cached --name-only` 逐条核）。
- commit 带 trailer `Agent-Attribution: consulter@root+consulter-report-base-lease-ruling`。
- 裁决写进 `report.json` 的 `findings` / `recommendations`，含驳回理由与替代落点。

## 可触碰目录（写边界）

- 可写：`agents/consulter/`（含 `docs/worklog/`、`docs/findings/report.json`）
- 只读：其余全仓。**特别是 `scripts/`** —— 框架实现改动本轮一律不做，只出裁决。
  （另：`scripts/` 当前在 CFO 的写区路签内，动它同时越写边界与越路签。）
- 不 push（CFO 自己推）。

## 自检门

- `scripts/mission_start.sh consulter <本任务单> agents/consulter/` 起飞检查全过再开工。
- 提交走 `AIMERGENT_ROLE=consulter git commit`（本仓角色自动推断对 J3 布局路径
  全部失效，见本轮 findings F3；不显式传会被判成 `unknown` 而卡在 05 号）。
- `./scripts/gates/run-gates.sh` 全绿。

## 边界声明（谁写的谁不审）

本轮 consulter 只改自己的留痕与报告元数据，未改框架实现，不存在自审自批。
裁决部分是建议，落地由 CFO 或人类拍板后另派单实现。
