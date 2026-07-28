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

## consulter 提到与 CFO 平级（人类指出的结构错误）

原结构 `agents/cfo/{arbiter,consulter}/` 把 consulter 挂在 CFO 名下 ——
**结构上等于说它归 CFO 管，而它的职责恰恰是审 CFO 的活。**
这与刚定的「谁写的谁不审」直接打架：一个隶属于被审者的审核者不是独立审核。

改后：
```
agents/cfo/          项目 arbiter（CFO）—— 去掉多余的 arbiter/ 层，项目级它就是 arbiter 本身
agents/consulter/    独立审核与架构顾问 —— 与 CFO 平级
agents/roles/        模块级四角色
```
三个项目级角色是**并列**的：CFO 分派、consulter 独立审、人类裁决。

canonical 路径随之变：
- `agents/cfo/arbiter/docs/report.json` → `agents/cfo/docs/report.json`
- `agents/cfo/consulter/docs/findings/report.json` → `agents/consulter/docs/findings/report.json`

**这是一次典型的改名重构** —— 正是本轮反复留下悬空引用的那类。
这回有 `check-references` 守着：改完首跑就抓出「脚本引用角色 consulter，
但既无 agents/roles/consulter.md 也无 agents/cfo/consulter/AGENTS.md」——
检查器自己的角色解析也按旧结构写着，一并修了。**穷举扫引用在这次的价值直接兑现。**

历史叙事类（worklog/findings/decisions）的路径引用**不改** —— 它们记录的是当时的结构。

## 第四次同形 + 顺带抓到的绝对路径泄漏

CFO 在 16:37 与 16:44 两次以模块相对路径在**根仓**派 programmer_reviewer，两次 rc=2；
中间一次用完整路径 `code/ring/codeagent/…` 是成功的。**同一条边界的第四次撞击。**
它还没 pull 到我的诊断修复（它的树停在 57e43e8）。

隔离复现时顺带抓到一个更糟的：**从模块仓内派活时，生成的提示词里角色卡是绝对路径**
（`/srv/…/ws/agents/roles/programmer_reviewer.md`）。
病根：`card_rel=${card#"$root"/}` —— 角色卡在框架根、任务单在模块仓时，
`$card` 不在 `$root` 之下，前缀剥离不生效，绝对路径就漏进了提示词。

**这正是我批评 copycat 的那个 `@/srv/aimergent/0/…` 病：换台机器就废。**
而我自己写的派单脚本在跨仓场景下就在制造它。改为 `realpath --relative-to`，
实测跨仓派单输出 `../../agents/roles/programmer.md`。selftest 补第 26 条。

**教训**：批评别人硬编码绝对路径的同一天，自己的脚本在另一条路径上做着同样的事。
形状相同、位置不同 —— 铁律 23 说的「这个形状还出现在哪」，我又只看了自己想到的那几处。
