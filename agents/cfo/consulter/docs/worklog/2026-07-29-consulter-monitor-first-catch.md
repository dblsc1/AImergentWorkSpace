# 监控接管后的第一条真事件

## 事件

`review_complete.sh module_reviewer <base> <head> approved 探针` 在**根仓**跑，rc=1。

## 根因不是 bug，是角色用错了层级

落点算出来是 `codeagent/module_reviewer/docs/report.json`，
而根仓的白名单 `.gitignore` 不放行 `codeagent/` → `assert_trackable` 拦住。

**rc=1 是正确行为**：它拒绝写一份不会被跟踪的报告。另一 AI 加的 A2 修复按设计工作了。

## 但暴露了一个真缺陷：报错说的不是真问题

报错说「落点被 gitignore 吞掉」，**没告诉人真正的问题是「module_reviewer 是模块级角色，
根仓的独立审核角色是 consulter」**。人看到这条只会去改 .gitignore —— 那是错的修法。

已修：模块级审核角色在根仓运行时，直接指出该用 consulter，并附分工
（consulter 审 CFO/arbiter/programmer；CFO 审 consulter 改的框架）。selftest 补第 24 条。

**一般化**：错误信息要说**该怎么办**，不是只说**哪里错**。
「落点被吞」是现象，「你用错了角色层级」才是原因；只报现象会把人引向错误的修法。

## 我自己在这轮又犯了两次

1. **在 CFO 工作树里跑了会写东西的脚本**去复现故障，生成了一份未跟踪的 consulter 报告。
   已删除还原，但**这是同一个撞车错误的第二次**——诊断必须在隔离 clone 里做。
2. 我的监控格式器把 `rc=0` 也标成「脚本失败」，挂上去几分钟后自查发现。
   **「监控启动成功」不等于「监控会正确响」**，这与「门禁绿不等于验过」同形。

两次都记在这里，不藏。测试跑进 CFO 的 diary 的那两行是 append-only，删不掉，如实留着。

## 同一类错误的第三次：模块级 vs 根仓

监控又抓到 `dispatch.sh rc=2`：CFO 想派 programmer_reviewer 去修三份坏的 run_all.sh，
任务单路径 `codeagent/programmer_reviewer/docs/worklog/…` 是**模块相对**的，而它人在框架根。

三次同形（review_complete 角色层级、dispatch 任务单路径、更早那次 checks 跨仓误伤），
说明**「模块级 vs 根仓」这条边界在框架里不够显形**，不是 CFO 记性差。

修法一致：报错直接指出**站错了仓**，并列出任务单实际在哪个模块、该怎么 cd。
selftest 补第 25 条。

**一般化（与本文件上半部分同一条）**：
错误信息要说**该怎么办**，不是只说**哪里错**。
- 「落点被 gitignore 吞掉」→ 真问题是角色用错层级
- 「找不到任务单」→ 真问题是站错了仓
只报现象会把人引向错误的修法（去改 .gitignore、去找文件），而那两条路都是死的。
