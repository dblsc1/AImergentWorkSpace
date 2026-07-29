# scripts/ —— 全部脚本一览

> 这里的脚本**不是样例，是真正被执行和被安装的东西**。`install-gates.sh` 会把
> `gates/` `hooks/` `checks/` 复制进目标仓；其余在框架根就地运行。
>
> 设计原则：**能被脚本执行的规矩才是真规矩，只能被阅读的规矩都是成本。**
> 要加一条规矩，先问能不能变成 `checks/` 里的一个文件；变不成的才写进铁律。

## ① 人 / CFO 直接跑

| 脚本 | 干什么 | 什么时候 |
|---|---|---|
| `install-gates.sh` | 装门禁、hooks、checks 到目标仓；传 `.` 给根仓自己装 | **clone 后第一件事** |
| `new_module.sh <名字>` | 一个参数建模块：骨架 → 清占位 → 装门禁 → 建三个角色 → 初始 commit → 开 `feat/init` | 开新模块 |
| `new_agent.sh <角色> [模块]` | 建角色实例：复制角色卡 → 填实可读可写 → 写 `.claude/agents/<角色>.md`（内联角色卡 + 出生考卷）→ 建 `checks/` 扩展点 | 建模块时自动调；单独加角色时手动 |
| `selftest.sh [仓路径]` | **23 条闸门有效性断言**：把真实踩过的坑做成测试;新增:17 模板token自毁/18 写产物验落点/19(后) 嵌套仓边界 | 改门禁后 / CI |

## ② 派活与执行

| 脚本 | 干什么 |
|---|---|
| `mission_start.sh <角色> <任务单> <写区...> [--docs …]`（`--checklist` 看单子） | **起飞前检查单**（7 项）：校验任务单四小节 → 申领写区路签（前缀重叠即拒）→ **声明预期文档变更（完工时机器逐条核对）** → 出提示词 |
| `dispatch.sh <角色> <任务单>` | 生成派单提示词：角色卡首行 + 版本哈希 + **开卷判据**（嵌 `mission_complete --list`）+ 任务单原文 |
| `run_agent.sh <角色> <任务单> [模块] [--resume]` | **起独立 Claude 进程执行角色任务**（`claude -p --agent`），不受子代理嵌套限制；`--resume` 按记录的 session id 续用 |
| `exam.sh <角色> [--submit 答案]` | 开工考试（五道通用流程题）。不过 → 打印**派单完整性自查表** + 退回重派，**不铸令牌** |
| `review_start.sh <reviewer> <commit> [留言]` | **起审**：固定被审区间（绑本地 commit，不必先 push），转发报告与 arbiter 留言；**新功能缺测试时提示 reviewer 记录结论**（不硬拦） |
| `review_complete.sh <reviewer> <base> <head> <verdict>` | **收审**：规范检查 + 生成含 `review_target` 的 report.json |
| `new_check.sh <层> <编号-名字>` | **加闸门脚手**：生成骨架、校验 `--describe` 契约、空跑自检 |

## ③ 完工与留痕

| 脚本 | 干什么 |
|---|---|
| `mission_complete.sh` | **着陆检查单**，挂 pre-commit；开头回显起飞时声明的文档变更逐条对照；`--list` 输出判据（开卷用） |
| `lib/checklist.sh` | 起飞单/着陆单共用渲染：一次跑完全部、每项带「判据」与「没过怎么办」 |
| `checks/` | 检查项，**三层级联**，见下节 |
| `log_event.sh <文件> <json>` | 往 diary.jsonl 追加事件（只加不改） |
| `reindex.sh` | 生成 `logs/INDEX.md` 留痕索引 |
| `console.sh [--standalone]` | 生成 `logs/console.json`；`--standalone` 出可传阅的 HTML 快照 |
| `new_task_id.sh` | 生成任务号 |

### checks 的三层级联

```
scripts/checks/_common/       所有角色都跑
scripts/checks/<角色>/         该角色专属
codeagent/<角色>/checks/       本模块给该角色追加的
```

**依次全跑，下层只能加严**（只能新增，不能删掉上层的）——与「模块只能加严项目规范」
是同一条原则，闸门不该有例外。增删改查一条检查 = 加/删/改对应层里的一个文件，
`mission_complete.sh` 永远不用动。

角色解析：`AIMERGENT_ROLE` → 从暂存的 worklog 路径推断 → 只跑 `_common`。

| 层 | 检查 |
|---|---|
| `_common` | **05 写区路签** · 10 worklog · **11 文档同步（治理关系表）** · **12 长期文档体检** · 20 report 已提交 · 40 分支纪律 · 60 路径可解析 · 70 审核意见 · **90 依赖漂移** |
| `arbiter` | 30 子报告落点 · 50 契约同步 · 80 任务单四小节 |
| `programmer` | 81 写边界（不得改 `review/` 与 `module_docs/`） |
| `programmer_reviewer` / `module_reviewer` | 82 审核区间（`review_target` 必须 exact） |

## ④ Git 闸门（唯一合法路径）

| 脚本 | 干什么 |
|---|---|
| `arbiter-push.sh <push参数>` | **唯一合法的 push 路径**：**先跑 run-gates + 校验有 approved 审核** → 铸一次性 lease → fetch-then-push |
| `merge-to-integration.sh` | **唯一合法的合并通道**，目标 = `$AIMERGENT_INTEGRATION_BRANCH`（默认 `dev`）。**以 main 为目标会被拒绝** |
| `lib/emit.sh` | 被所有脚本 source：激活/退出自动写进 diary，控制面板据此实时显示 |
| `hooks/pre-commit` | 调 `mission_complete.sh`，不过拒绝提交 |
| `hooks/pre-push` | 拦直推 main、拦无 lease 的 push |
| `hooks/commit-msg` | 拦缺失/重复/畸形的 `Agent-Attribution` |
| `hooks/cc-push-guard.sh` | 推送拦截的辅助日志 |
| `gates/run-gates.sh` | 确定性门禁总入口：归属 / 报告 / 密钥 / 行数 / hook 安装 / reviewcode |
| `gates/check-report-schema.sh` | 报告协议核验（含空区间假绿修复） |
| `gates/run-tests.sh` | 跑模块测试与构建 |

### 完整流程（2026-07-28 定）

```
CFO   new_module.sh → dispatch.sh arbiter → 开 arbiter session
arbiter
  ├─ 出生答卷 → exam.sh 判卷（不过：先自查派单完整性）
  ├─ mission_start.sh programmer <任务单> <写区>   发路签，重叠即拒
  ├─ run_agent.sh programmer <任务单>              可并发，写区不重合即可
  │    programmer: 实现 → 自检门 → mission_complete.sh → git commit（本地）
  ├─ review_start.sh programmer_reviewer <commit>  ★审的是本地 commit
  │    reviewer: 写 reviewcode 脚本 → review_complete.sh approved|rejected
  ├─ rejected → run_agent.sh programmer --resume（续用，不重开）
  ├─ approved → arbiter-push.sh                    ★先审后推：脏东西不上远端
  ├─ module_reviewer 审规范面（交 CFO 前必过）
  └─ merge-to-integration.sh                       → dev
人   dev → main（用户测试通过后，agent 不得代劳）
```

★ 两处顺序是有意的：**审绑本地 commit，先审后推**。本地 commit 已是不可变、有 SHA 的
真实对象，绑它足够；先审后推让被打回的活永远不上远端，返修不需要 force-push。
代价：review 时拿不到 CI 结果 —— 由本地 `run-gates.sh` + `run-tests.sh` 先顶，CI 作为推之后的第二道网。

### 三层闸门，别搞混

| 层 | 谁拦 | 拦什么 |
|---|---|---|
| **派单层** | `exam.sh` | 没读规范的 agent 不放行接任务（退回重派，无令牌） |
| **提交层** | `pre-commit` → `mission_complete.sh` | 留痕不全、越界、无审核意见 → 拒绝提交 |
| **合并层** | `pre-push` + `merge-to-integration.sh` | 无 approved 审核、门禁不绿 → 拒绝进 dev；main 只由人推 |

**「审核门在 merge 不在 commit」**：提交层只要求 `reviewer_opinion` 字段**存在**
（`verdict` 可以是 `pending`），合并层才要求 `approved`。
否则实现者永远无法先提交形成 candidate，审核就无从开始。

## 非脚本文件

| 文件 | 作用 |
|---|---|
| `gates/.gitleaks.toml` | 密钥扫描规则 |
| `gates/legacy-path-exempt.txt` / `remote-test-exempt.txt` | 豁免名单 |
| `gates/agent-attribution-activation` | 归属检查的启用标记 |
| `module.gitignore` | 模块默认忽略规则 |
| `workflows/ci.yml` | GitHub Actions 模板，由 `install-gates.sh` 装到 `.github/workflows/` |
| `reviewcode/` | 项目级检测脚本落点（模块级在 `<模块>/review/reviewcode/`） |
