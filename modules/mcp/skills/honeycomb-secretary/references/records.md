# 改记录

## 工具

- `CRUD_Windowsblock`：某天一个块记到哪。`items` 1–50 条，每条 `{action, date?, block, target?, parts?}`。
  - `assign`：块记到 `target`；对已分类的块等于改去向。
  - `split`：`parts: [{from, to, target}]`，2–10 段，首尾相接、不重叠、覆盖块的全部时长，时刻带偏移（用 `Get_Started.tz`）。首段开头与末段结尾按当天开头 / 结尾处理，只有中间的切点起作用。
  - `generic`：标成通用窗口，以后每天都要拆，不再问规则。
  - `ignore`：这一天的这个块不记。
- `CRUD_Honeycomb` 的 `retime`：把已记的一段时间改挂到别的任务或项目。`{"op": "retime", "eventId": "...", "to": "kf-hpkf-mcpj", "reason": "..."}`。`eventId` 取自 `Get_History` 的 `detail: "segments"` 里 `confirmed` 段。一次一条。
- `CRUD_Honeycomb` 的 `rename`、`move`、`delete`（`target`，`move` 另给 `to`）改任务树，不改时间。`create` 永远要人批准。

## 步骤

1. `Get_History {"fromDate": "2026-10-24", "toDate": "2026-10-24", "who": "human", "detail": "windows", "records": false}` 看块和归类状态；要块内细节把 `records` 设 true。改已入账的段用 `detail: "segments"`。
2. 决定去向前 `Get_Match_History`，读 rules.md。
3. 调写工具，多条放同一个 `items`。

## 例

```
CRUD_Windowsblock
{"items": [{"action": "assign", "date": "2026-10-24", "block": "示例文档", "target": "kf-hpkf-mcpj"}]}
```
返回 `results: [{index, ok, proposalId, status: "pending"}]`。

## AI 可以改什么

- 未分类、未定去向的块：可以提议 `assign` / `split`。
- `retime` 只接受原本记在项目「未分类」桶、或检测程序自动记下的段，其他段（例如人手动计时记在具体任务上的）提议和直接施加都返回 409；已作废的段、目标与现归属相同也是 409。
- 一个块前后明显是两件事才 `split`；否则 `assign`。

## 什么时候只提议

- `retime`：默认提议；直接施加要 `honeycombRecords` 分开关。
- 拿不准去向、或要动的段很长：提议，或用 `Submit_Questions_To_Human` 问人，读 report.md。
- `permissions.canDirectApply` 里对应项为 false：只能提议。

## 坑

- `split` 的段有空隙或重叠，该条失败。
- `create` / `rename` 在同一父级下已有同名时，结果 `note` 会提示；提议照常提交，别重复建。
- 删除前先 `Get_View` 看下面有没有子节点。
- 同一个窗口反复要分，改规则，不要天天手动改。
