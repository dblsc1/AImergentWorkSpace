# {{MODULE_NAME}} · 模块规范

> 级联：先读 `/srv/aimergent/0/AGENTS.md`（项目级，不可覆盖），本文件只能加严。角色专属规范在 `codeagent/<角色>/AGENTS.md`。

## 文档地图（每处的位置与作用；改动即同步，见项目铁律 11）

| 文档 | 位置 | 作用 | 谁维护 |
|---|---|---|---|
| 对外契约 | `module_docs/contract.md` | 模块对外行为的唯一事实；改它先走 CR | arbiter |
| 模块规则 | `module_docs/rules.md` | 本模块特有规则、技术债登记 | arbiter |
| 审核台账 | `module_docs/reviewlog.md` | 一行一条审核结论 | reviewagent |
| 工作留痕 | `codeagent/<角色>/docs/worklog/` | 每任务一文件：做了什么/为什么/踩坑 | 各角色 |
| 踩坑指南 | `codeagent/<角色>/docs/踩坑指南.md` | 精编教训（与流水 worklog 分开） | 各角色 |
| 审核脚本 | `review/reviewcode/` | 检测脚本，目录镜像 code/ | reviewagent |
| 审核报告 | `review/reviewreport/` | 每次审核的结构化报告 | reviewagent |

## 模块职责

（迁移时填写：一句话说清 {{MODULE_NAME}} 是什么、对外提供什么。）

## 目录与写权限

沙盒范围以项目级"角色与沙盒范围"表为准。本模块补充约定：

- `code/backend/`、`code/frontend/` 内部可再分子模块；每新增一个子模块，reviewagent 须在 `review/reviewcode/` 建立对应的检测脚本文件夹（目录镜像）。
- 无前端时 `code/frontend/` 与 `codeagent/frontend/` 保持占位，不删除。

## 流程

1. arbiter 开单 → 写入执行角色的 `codeagent/<角色>/docs/worklog/YYYY-MM-DD-<角色>-<任务>.md`
2. 角色实现+自测+跑 `review/reviewcode/` 全部脚本（= Stage A 自检，持写入位、不阻断提交）+ worklog 留痕
3. 自检通过即 commit+push 形成远端 candidate（交接 reviewer 前远端 tip==candidate，铁律15③）
4. reviewagent 对该 candidate 正式审核（exact target）→ `review/reviewreport/` 记入 `module_docs/reviewlog.md`
5. approved 才经 `merge-to-main.sh` 合 main；rejected 在分支返修+重 push，上限 2 次（审核门在 merge 非 commit，铁律15⑤）

## 接口纪律

对外接口以 `module_docs/contract.md` 为唯一事实。任何改变对外行为的改动：先停，提 CR 给项目 arbiter，批准后先改 contract.md 再改代码。

## 测试

（迁移时填写：测试命令、冒烟链路、如何在本地跑起来。）
