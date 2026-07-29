# 2026-07-29 · consulter · J2–J5 迁移落地（清空 §4.1）

## 做了什么（契约优先的顺序）

1. **协议**：report-schema canonical 表迁 J3 落点（arbiter→`module_docs/report.json`、
   programmer→`code/<子文件夹>/report.json`、reviewer→`review/reviewreport/report.json`），
   旧 `codeagent/<角色>/docs/` 标注过渡期兼容（存量模块）；新增 J3/J4 配套落点节
   （worklog 简短、handoff 每改必核、comm.jsonl 每 agent）。
2. **规范**：AGENTS.md 模板树重画（实例模型 + session 复用）、角色写边界表、
   四件套表、铁律 13/17/18 措辞（J2 测试分层 + J5 push 前全量入 17）。
3. **模板**：`module_docs/worklog/`；固定角色树 → `programmer/`、`reviewer/` 实例容器；
   代码侧 `handoff.md` + `worklog/`；arbiter docs README 重写；模块 AGENTS.md 流程节、
   模块文档地图落点表。
4. **脚本**：新增 `new_instance.sh`（编号实例：agent.md + session + comm.jsonl，
   reviewer 容器实例化 programmer_reviewer 卡）；new_module 只生成 arbiter（并修掉
   尾部「三角色已生成」的撒谎输出）；aim/README 入口。
5. **检查**：10 扩为 `*/worklog/*.md`；新增 `14-handoff-fresh.sh`（每改必核，双出路：
   同批更新 or report docs_reviewed 表态）；check-report-schema 认新映射；
   check-references ② 豁免模块内路径 `module_docs/`、`review/`。
6. **测试分层（J2/J5）**：run-tests 发现域扩 `review/reviewcode`（reviewer 整合/契约
   测试 `tests/` 自动入 pytest 发现）；programmer/reviewer/arbiter 三卡写入义务。
7. **断言**：selftest #38（三层认 J3 布局）/#39（checks/14 在位）/#40（实例模型落点）。
8. **台账**：J2–J5 移回 §4 标已落地，§4.1 清空。

## 验证（全部真实退出码，直取）

- selftest 40/40 正向绿；#38/#39/#40 逐条红/绿：抽模板 handoff→红1、卸 14 执行位→红1、
  断 aim 入口→**首轮没红**（断言 grep 太弱，命中文件名残留）→收紧为 `[new-instance]=` 
  精确匹配后红1/绿0。**红测抓住了断言自身的弱判据——这就是点火的意义。**
- checks/14 本体四路：缺一页纸→红1；同批更新→绿0；没动没表态→红1；report 表态→绿0。
- check-references：先红（② 把模块内新路径当框架路径核）→豁免后绿 0。
- 端到端沙箱：new_module 出新布局（旧固定角色树不再生成）、new_instance 编号递增
  （programmer/1、/2，reviewer/1）、session/comm.jsonl 落地、整合测试在 run-tests 发现域内。
  沙箱与面板服务已清理。

## 决策记录

- **过渡期兼容**：旧 canonical 路径仍被门禁承认——CFO 仓存量四模块 pull 后不至于当场恒红；
  新模块一律新布局。存量迁移由 CFO 侧另行安排。
- **logs/diary.jsonl 保留**为脚本事件账本（override 记账），与 J4 的每 agent comm.jsonl
  分工明确写进四件套表——J4 的「每 agent 一份」指 agent 沟通，不指脚本事件。
- session id 单独成文件（`session`），不混进 agent.md——机器态与规范文案分离。

## 遗留

- run_agent.sh 尚未自动读写实例 `session` 文件（现靠 arbiter 手动 --resume）；下轮接线。
- 存量模块（CFO 仓四个）迁新布局：待 CFO 排期，过渡期兼容兜底。
- 本 commit 仍是 consulter 改框架 → 待 CFO 交叉审。

## 七、pull 完整性（同日追加，用户指令）

「落地」判据升级为**新克隆完整可复现**，职责写入角色卡「仓库职责」节并进系统提示词：
- `.claude/` 曾被白名单 `/*` 静默吞掉——克隆里根本没有子代理 prompt。已放行 `!/.claude/`
  并跟踪，selftest #41 断言生成物与源逐字节一致（红/绿已点火）。
- `logs/diary.jsonl` 维持忽略是**有意设计**（入仓则每次提交后工作树永远脏，.gitignore 注明），
  代价：override 记账只在本机可审——已知取舍，非疏漏。
- 收尾动作固化：每轮 commit 后推 v5；新根级文件登记白名单 + `git ls-files` 验证；
  大改后新克隆跑 selftest。
