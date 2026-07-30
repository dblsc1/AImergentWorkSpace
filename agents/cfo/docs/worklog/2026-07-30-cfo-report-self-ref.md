# worklog · report 的 changed_files 算不到自己

- 日期：2026-07-30
- 角色：CFO
- tier：`simple`

## 目标

修门禁红：`changed_files` 缺本 report 自身路径。

## 可触碰目录（写边界）

`agents/`

## 自检门

1. `./scripts/gates/run-gates.sh`
2. `./scripts/mission_complete.sh`

## 验收标准

| # | 判据 | 确定命令 |
|---|---|---|
| S1 | 报告自身在 changed_files 里 | 读 `report.json` |
| S2 | 门禁绿 | `run-gates.sh` 🟢 |

## 问题：修一个坑带出另一个坑

上一轮 reviewer 打回「`changed_files` 三轮连续落后」，我把取值从**手写**改成
**从暂存区计算** —— 那修掉了「凭记忆枚举」这个根因，reviewer 也认了这个修法。

**但它引入了一个新盲区**：报告自己是在算完之后才写盘的，
所以**它永远算不到自己**。而协议明写「`changed_files` 必须包含本 report 路径」。

这不是"又忘了"，是**取值方式的固有性质** —— 快照是在写盘前拍的，
被拍的东西还没出现。

修法：算完后**强制并入自身**（以及本轮 worklog）。不是回去手写。

## 顺带撞到一次「退出码撒谎」

我用 `git commit … | tail -3` 看输出，得到 `rc=0`，
**而输出里明明写着「worklog-changed 拒绝提交」** —— 两个信号矛盾。

查 `git log`：HEAD 还是上一笔，**commit 根本没成**。
管道后的 `$?` 是 `tail` 的，永远 0。

这是被提醒过的坑，我又踩了一次。**判成败只认 `git log`。**

## 依赖漂移说明

无。只改报告字段取值逻辑与留痕。
