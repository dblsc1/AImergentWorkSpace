# module_reviewer 的留痕目录

| 放什么 | 文件 |
|---|---|
| canonical 报告 | `report.json`（本目录下，必须 commit） |
| worklog | `worklog/YYYY-MM-DD-module_reviewer-<任务>.md` |
| diary | `WorkIterationDiary/<任务id>/module_reviewer.jsonl`（只用 scripts/log_event.sh 写） |

角色卡由 `scripts/new_agent.sh module_reviewer` 生成到本目录上一级的 `AGENTS.md`。
**本目录存在本身就是规范的一部分**：路径表点名的位置必须真实存在，否则 agent 不会写。
