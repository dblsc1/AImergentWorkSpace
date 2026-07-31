# 障签 · mission_complete.sh 推断出角色后不传给子检查脚本，写签核验静默判过

- 立障：2026-07-31
- 立障方：CFO（发现来源：table 模块 programmer 实例在完工自查时报告，未被利用，如实上报）
- 判定：**不阻塞**（有记账逃生口；但这是安全性质的缺口，建议尽快修，不要拖成惯例）
- 波及：（不设——不该压住任何人的写区）

## 现象

`scripts/mission_complete.sh` 的角色解析顺序是
「`AIMERGENT_ROLE` 环境变量 → 从暂存的 worklog 路径推断 → 兜底 `unknown`」
（见脚本第 18 行注释、第 30-33 行），推断结果存进本地变量 `role`，用于：
- 选择要跑哪三层检查（`_common` + `checks/$role` + `codeagent/$role/checks`）
- 写进 `logs/diary.jsonl` 的事件记录

但 `collect()` 循环调用每个检查脚本时（第 94 行 `out=$("$c" 2>&1)`），
**从未 `export AIMERGENT_ROLE="$role"`**。子进程里的检查脚本只能看到调用者
环境里原本就有的 `AIMERGENT_ROLE`——如果调用方（比如 git 的 pre-commit hook）
没有显式设置这个变量、完全依赖"从暂存路径推断"这条兜底路径，子进程拿到的
`AIMERGENT_ROLE` 是空字符串。

`scripts/checks/_common/05-write-lease.sh` 第 7 行：
```bash
role=${AIMERGENT_ROLE:-}
...
[ -n "$role" ] || exit 0    # 角色未知时不拦（由 10/40 等条兜底）
```
这行"角色未知时不拦"的设计本意是给真正无法归类的提交一条兜底路径，
**但它没法区分"mission_complete.sh 确实不知道角色"和"mission_complete.sh
知道角色、只是没告诉我"**——本次缺口正好触发的是后一种，前者的兜底逻辑被
错误地套用在了后者身上。结果是：**只要提交时不手动写 `AIMERGENT_ROLE=xxx`，
写区路签检查（05）在常规提交路径上形同虚设**，会静默通过任何越区写入，
不报错、不留痕，和"检查真的跑了并且通过了"在界面上完全一样。

## 已验证影响面

- ring/table 两个模块的 programmer 实例本轮都是在没有手动传
  `AIMERGENT_ROLE=programmer` 的情况下跑 `mission_complete.sh`
  （命令记录见对应 worklog），实际拿到的写签核验是空转的——
  这次没出事是因为两个 agent 本身老实地只写了自己该写的路径，
  不是靠这道闸拦住了什么。
- 影响范围不止这两个模块：`code/_template/` 里的 `mission_complete.sh`
  是所有模块的骨架来源，四个已建模块（ring/table/nexus-core/nginx-docker）
  大概率都带着同一处缺口。

## 解除标志

`export AIMERGENT_ROLE` 出现在根仓 `scripts/mission_complete.sh` 里：

解除标志：`export AIMERGENT_ROLE` @ `scripts/mission_complete.sh`

（这是修好后才会出现的文字，不是现状就有的——上一次立障时吃过这个亏。）

## 归属与建议处置

框架侧。`scripts/mission_complete.sh` **不在** `code/_template/`（核对过，模板目录下
没有这份文件）——它是 `install-gates.sh` 第 76/117 行那份拷贝清单里的一员，
由该脚本直接从根仓 `scripts/` 拷进每个模块。所以本条缺口的唯一事实源就是根仓
`scripts/mission_complete.sh`，不存在"模板也要同步一份"的问题（上一条障签
`2026-07-30-abspath-second-copy.md` 点的是 `run_all.sh` 那种真正有模板副本的
情况，本条不是同一种结构，写便条时不要混淆两者的归属路径）。CFO 不代改，
理由与 `2026-07-30-abspath-second-copy.md` 一致（`scripts/` 归 consulter 维护）。

建议一并做（铁律23：修到类 + 留断言，不要只补这一处）：
1. 根仓 `scripts/mission_complete.sh`：`role` 确定后立即 `export AIMERGENT_ROLE="$role"`，
   再进 `collect()` 循环。
2. 存量四模块（ring/table/nexus-core/nginx-docker）各自重跑
   `scripts/install-gates.sh code/<模块>` 把拷贝集刷新到修复后的版本
   （`install-gates.sh` 的职责就是把这份清单拷进模块，不需要额外的同步机制）。
3. 加回归断言：造一个"不显式传 `AIMERGENT_ROLE`、只靠暂存路径推断角色"的场景，
   故意越区暂存一个文件，断言 `mission_complete.sh` 必须拦下——这是唯一能真正
   验证"推断出的角色确实传给了子进程"的方式，光看 `export` 关键字出现在源码里
   不够。
