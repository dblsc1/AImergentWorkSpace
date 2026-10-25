# 每小时巡检

## 步骤

1. `Get_Today`（缺省今天）。看 `uncaught`、`unassigned`、`gaps`、`proposalsPending`、`questionsOpen`、`reportSubmitted`、`allClear`。
2. `allClear: true`：结束，不写任何东西。
3. `uncaught.blocks > 0`：调 `Get_Pending_Classify_Windows`，看 `items`（按时长降序，从大的做起）。
   - 反复出现的窗口：写第一级规则，读 rules.md。
   - 只有今天一次的：`CRUD_Windowsblock` 的 `assign`。
4. `unassigned.blocks > 0`：同一次返回里的 `unassigned`，这些块已被第一级收了，缺第二级去向。归类前先 `Get_Match_History`（块名）；去向稳定就写第二级规则，否则 `assign`。读 rules.md。
5. `gaps`：`detectorOffline: true` 是检测程序没在报，先 `Get_Device_Health`，别问人。`false` 多半是人不在电脑前，需要时才用 `Submit_Questions_To_Human`。读 troubleshoot.md。
6. `proposalsPending`、`questionsOpen`：`Get_Proposals`（缺省 `status: pending`）。已在等人的不重复提；`kind: question` 里 `answered` 的要跟进，读 report.md。
7. `reportSubmitted: false`：交 `Submit_Report`，读 report.md。建议傍晚交一次，人要求时随时交。

## 例

```
Get_Today {"date": "2026-10-24"}
```
关键返回：`uncaught {blocks, seconds}`、`unassigned {blocks, seconds}`、`gaps [{from, to, seconds, detectorOffline}]`、`proposalsPending`、`questionsOpen`、`reportSubmitted`、`allClear`。

## allClear 的含义

`uncaught` 与 `unassigned` 都为 0，没有待批提议和待答问题，当天总结已交。不看 `gaps`（空洞是事实，不是待办）。三级分类没开（`tiersOff`）或块太多被截断（`truncated`）时恒为 false。

## 停止条件

- `allClear: true`。
- 剩下的只有人欠的：待批提议、待答问题。等人。
- 同一类问题连续两轮没有进展：写进总结，停。

## 不要碰

- 已入账的段，除非人要求；`retime` 只能动记在未分类或自动记下的段，见 records.md。
- 忽略规则：只能新增，edit / delete 会 400，删除只有人能做。
- 已有待批提议的块，不要再提一次。
- 通用窗口不写第二级规则。

## 坑

- `proposalsPending`、`questionsOpen` 是当前积压，不分日期。
- `gaps` 只统计 08:00 起、长于 10 分钟、没有任何记录的段；正在跑的计时不计入，见 troubleshoot.md。
- 批量工具一次 1–50 条，逐条给结果，看每条的 `ok`。
