# {{MODULE_NAME}} · 模块规范

> **级联**：先读框架根 `AGENTS.md` 与 `agents/CONSTITUTION.md`（不可覆盖），本文件只能**加严**。
> 角色专属规范在 `codeagent/<角色>/AGENTS.md`（由 `scripts/new_agent.sh` 生成，写边界已填实）。
> **本文件是本模块的提示词事实源**——任何在本模块干活的 agent 都必须读它。

## 1. 这个模块是什么

（一句话说清 {{MODULE_NAME}} 是什么、对外提供什么。别写实现，写职责。）

## 2. 目录与写权限

沙盒以框架根「角色与写边界」表为准。本模块补充：

- `code/` 内部可再分子模块；每新增一个子模块，`programmer_reviewer` 须在 `review/reviewcode/`
  建立对应的检测脚本（目录镜像），并挂进 `run_all.sh`（不挂＝死代码）。
- （本模块特有的边界补充写在这里。）

## 3. 技术栈与自检门

| 项 | 值 |
|---|---|
| 技术栈 | （填） |
| 启动 | （填真实命令） |
| 测试 | （填真实命令） |
| **交付前必过的自检门** | （填确定性命令，例：lint + typecheck + test + 自跑 reviewcode） |

**没过自检门不许交审核。** 这条是给 programmer 的硬要求，也是 arbiter 开单时必须写进任务单的。

## 4. 流程（arbiter 长期存在；programmer/reviewer 是编号实例）

1. arbiter 需要人手 → `scripts/new_instance.sh <programmer|reviewer>` 开编号实例
   （`codeagent/<容器>/<编号>/{agent.md, session, docs/comm.jsonl}`）。
   **同一块代码复用同一实例**：实例 `session` 文件存 harness session id，
   shell 调用带上它续用，不重开（重开＝冷启动重读全部上下文）。
2. arbiter 开单 → `scripts/mission_start.sh <角色> <任务单> <写区>` **发路签**（写区重叠即拒派）；
   发任务记入自己 `docs/arbiter.jsonl` 与实例 `comm.jsonl`（J4）
3. programmer 实现 + **pytest 级单测（新增代码必须同批新增测试，J2）** + 跑 `review/reviewcode/run_all.sh`；
   留痕落自己代码侧：`code/<子文件夹>/{worklog/, report.json, handoff.md}`（J3）
4. `scripts/mission_complete.sh` 全绿 → **本地 commit**（不推）
5. `scripts/review_start.sh programmer_reviewer <commit>` → reviewer 审**本地 commit**
   + 维护整合级/契约测试（`review/reviewcode/tests/`，J2）
   → `scripts/review_complete.sh ... approved|rejected`
6. rejected → 用实例 `session` **续用同一个 agent 返修**，上限 2 次
7. approved → arbiter 跑**本模块全量 + 契约测试**（`scripts/gates/run-tests.sh`，J5）
   → `scripts/arbiter-push.sh` 推远端（**先审后推：被打回的活不上远端**）
8. 交 CFO 前由 `module_reviewer` 审规范面
9. `scripts/merge-to-integration.sh` 合入 `dev`；**dev → main 由人在用户测试通过后推**

## 5. 对外接口

`module_docs/contract.md` 是唯一事实。任何改变对外行为的改动：**先停**，提 CR 给 CFO，
批准后**先改 contract.md 再改代码**。

## 6. 已知坑（踩过就写这里，不另开文档）

> 这一节是本模块最值钱的部分。**踩过的坑直接写进来**，不要单开踩坑文档——
> 另开文档的结果是没人读。每条写：现象 → 根因 → 怎么绕开。
> 冷启动的 agent 读完本节，应该能避开前人踩过的所有坑。

- （示例格式）**现象**：xxx 间歇失败。**根因**：yyy。**绕法**：zzz，不要回退到 www。

## 7. 冷启动读什么

本文件 → `module_docs/contract.md` → `module_docs/rules.md` → 你的 `codeagent/<角色>/AGENTS.md`
→ `module_docs/handoff.md`（arbiter 维护的接手便条）。
