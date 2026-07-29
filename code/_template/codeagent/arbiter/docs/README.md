# arbiter 的实例目录（J3/J4 后）

| 放什么 | 文件 |
|---|---|
| 个人沟通留痕（J4） | `arbiter.jsonl`（发任务/收报完成，只用 scripts/log_event.sh 写，append-only） |

**arbiter 的任务留痕不在这里**（J3 裁决，迁到模块文档系统）：

- worklog（简短）→ `module_docs/worklog/YYYY-MM-DD-arbiter-<任务>.md`
- canonical report → `module_docs/report.json`
- 一页纸说明（每改必核）→ `module_docs/handoff.md`

角色卡由 `scripts/new_agent.sh arbiter` 生成到本目录上一级的 `AGENTS.md`。
programmer / reviewer 是**编号实例**：`scripts/new_instance.sh <programmer|reviewer> <编号可选>`
生成 `codeagent/<容器>/<编号>/{agent.md, session, docs/comm.jsonl}`；
同一块代码复用同一实例的 session（续用不重开）。
**本目录存在本身就是规范的一部分**：路径表点名的位置必须真实存在，否则 agent 不会写。
