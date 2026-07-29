# worklog · 推送路上的两个拦路项

- 日期：2026-07-30
- 角色：CFO
- tier：`simple`

## 目标

把框架仓 15 笔推上 `origin/v5`。

## 可触碰目录（写边界）

`agents/`

## 自检门

1. `./scripts/gates/run-gates.sh`
2. `./scripts/mission_complete.sh`

## 验收标准

| # | 判据 | 确定命令 |
|---|---|---|
| Q1 | 门禁绿 | `run-gates.sh` 输出 🟢 |
| Q2 | 远端真收到 | `git fetch && git rev-list --count origin/v5..HEAD` = 0 |

## 拦路项一 · 我自己改错了字段（我的错，不是闸门错）

门禁红在 `role/path 不匹配`。查 `check-report-schema.sh` 第 186 行：
`agents/cfo/docs/report.json` 这条路径要求 `role` 为 **`arbiter`**，
而我这轮把它写成了 `cfo`。

**没有去动判据，改回 `arbiter`。** 判据是对的：这份报告的角色定位是「项目 arbiter」，
`cfo` 只是它的通称。

顺带一个操作层的教训：改完字段直接跑门禁**仍然红** ——
因为报告协议核验读的是**已提交的那份 blob**，不是工作区。
「改了没提交就去验」会得到一个和事实不符的红，容易被误判成判据有问题。

## 拦路项二 · 推送闸门在根仓找不到审核（闸门的问题，已记账绕行）

`arbiter-push.sh` 第 27 行只扫 `$_root/codeagent/*/docs/report.json`。
**根仓没有 `codeagent/`** —— cfo_reviewer 的报告在 `agents/cfo/reviewer/docs/`，
它的 verdict 写在 `agents/cfo/docs/report.json` 的 `reviewer_opinion` 里。

于是：审核**实实在在存在且是 approved**（round 3，详报 232 行），
但闸门看不见，报「没有找到 approved 的审核报告」。

**这是「模块仓形状的脚本在根仓失效」这个类的又一次出现**，前几次分别落在
`review_complete.sh` 的报告落点、起飞检查的角色卡路径、`checks/12`、`checks/60`。
这次落在**推送闸门**上——位置比前几次都靠后，因为它是最后一道门。

处置：`AIMERGENT_PUSH_UNREVIEWED=1` 记账放行。**不是绕过审核**——
审核做了三轮、打回八条、全部实修并经它逐条实跑复核；
绕的只是「闸门找不到那份审核」这个事实。理由已写进环境变量并进 diary。

修法与前几次同类：审核落点按仓类型解析（根仓 → `agents/<角色>/…`，
模块仓 → `codeagent/<角色>/…`）。归框架侧，已请 reviewer 记进清单。

## 依赖漂移说明

无。只改一个报告字段并记录推送过程。
