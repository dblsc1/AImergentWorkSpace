# reviewagent 的留痕目录

| 放什么 | 文件 | 说明 |
|---|---|---|
| canonical 报告 | `report.json`（就在本目录下） | 每个任务结束产出一份，必须 commit。未跟踪/工作区脏/写在仓外 = 按未产出计 |
| worklog | `worklog/YYYY-MM-DD-reviewagent-<任务>.md` | 每任务一条，回顾过去：做了什么、为什么 |
| diary | `WorkIterationDiary/<任务id>/reviewagent.jsonl` | 机器 append-only，只用 `scripts/log_event.sh` 写 |

**本目录存在本身就是规范的一部分**：路径表点名的位置必须真实存在，否则 agent 不会写。
