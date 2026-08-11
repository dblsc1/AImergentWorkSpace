# worklog · report-schema 补 `git.base` 的具体算法

- 角色：cfo · 日期：2026-08-11 · tier：simple

## 目标

把 `git.base` 的**计算命令**写进 `agents/protocol/report-schema.md`，
终结「每个 agent 凭直觉猜一次、被门禁拒一次、再回来改一次」的重复损耗。

## 为什么

2026-08-11 一天之内，**四批 agent 在同一条上各踩一次**：

| 谁 | 填错成什么 |
|---|---|
| CFO（我） | `e90c567` —— 上一份 report 的 resolved_head |
| consulter | 用了 `origin/v5` 而非 merge-base |
| nexus-core arbiter | `HEAD~1`（`dd15e67`，分支中段） |
| reviewlog 退役那批（多个模块） | 各自版本的同一错法 |

**文档本来是写对的**（§字段规则 `base` 那条明确说「必须取自 PR 目标分支」，
还专门警告别填 feature 分支的中间 commit）。**问题不是没写，是只讲了语义没给命令。**

判据要的「分支从目标基线的分叉点」与直觉的「我的改动从哪开始」在长命 feature 分支上
差很远；而唯一给出命令的地方是**门禁的报错信息**——也就是说，你必须先失败一次才看得到。

**还有一个真陷阱**：多数模块仓**没有 `origin/main`**（`origin/HEAD` → `origin/feat/init`），
所以门禁提示里那条 `git merge-base HEAD origin/main` 在模块仓直接跑不通，
得用 `git merge-base HEAD main`。踩过的人里有人在这上面又卡了第二回。

## 改了什么

`agents/protocol/report-schema.md` 的 `base` 字段说明下补一段：
根仓与模块仓两条命令、最常见的错法、以及「改完 base 要重新机械核对 `changed_files`」
（base 前移后，区间内建了又销的文件净变化为零、不再在 diff 里，留着会被判「声称了不存在的文件」——
这个我自己也踩了，164→162）。

## 这属于哪一类

与今晚另外三条（密钥判据、M6、doc-paths）不同：那三条是**判据本身错**，
这条是**判据对、文档对、但可执行性差**。修法也不同——不改代码，把「怎么做」写到
人（和 agent）第一次会读到的地方。

**通则**：一个正确但需要读者自己推导出命令的规范，会以「每人失败一次」的价格收税。
四次就该把命令写进去了。

## 可触碰目录

`agents/protocol/`、自己 `docs/`。

## 验收标准与自检门

- [x] 两条命令都给（根仓 / 模块仓，含 `origin/main` 不存在的坑）
- [x] 点名最常见错法，且说明为什么直觉是错的
- [x] 带上「改完 base 要重核 changed_files」这个连带动作
- [x] `scripts/gates/run-gates.sh` 全绿
