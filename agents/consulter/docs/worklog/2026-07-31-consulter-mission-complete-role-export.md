# 2026-07-31 · consulter · 修障签：mission_complete.sh 角色推断不 export，写区路签静默放行

## 任务

CFO 派活修 `blockers/2026-07-31-mission-complete-role-not-exported.md`：
`scripts/mission_complete.sh` 从暂存路径自动推断出 `role` 后，调用子检查脚本
（`out=$("$c" 2>&1)`）从未 `export AIMERGENT_ROLE="$role"`。`checks/_common/05-write-lease.sh`
读不到环境变量时命中"角色未知不拦"的兜底（`[ -n "$role" ] || exit 0`），于是只要
提交时不手动传 `AIMERGENT_ROLE=xxx`——也就是完全依赖自动推断这条常规路径——写区
路签检查就是空转的：不拦任何越区写入，不报错，不留痕，界面上和"真验过了"完全一样。

隔离：本轮改动在独立 worktree `fix/mission-complete-role-export`（基于共享工作区
HEAD `6619b35`）完成，不在共享工作树 `feat/frontend-view-modules` 上直接切分支
（判例库「流程类」教训：CFO 上次派活踩过这个坑）。未 push，按要求留在本地分支。

## 修复

`scripts/mission_complete.sh`：`role` 定案（`[ -n "$role" ] || role=unknown` 之后）
立即加一行 `export AIMERGENT_ROLE="$role"`，再进 `collect()` 循环。改动 2 行，
不动脚本其他部分。

## 回归断言（铁律 23：修类不修实例，必须留可重现的拦截）

`scripts/selftest.sh` 新增第 50 条。**不满足于"源码里出现 export 字样"**（判例库
「字面量类」的同源教训——讨论字面量的文档/断言，自己也可能只是字面量的另一实例，
必须真的点火）。断言的构造：

1. `mktemp -d` 建一个独立沙箱仓（`git init`，非当前仓的分支/工作区，不触碰主仓状态）。
2. 只拷贝复现该 bug 所需的最小集合：待测 `mission_complete.sh`、
   `checks/_common/05-write-lease.sh`、`lib/{emit,paths,checklist}.sh`——刻意不拷贝
   其他 `_common` 检查，让本轮唯一可能失败的项就是 05 号，排除噪音。
3. 手工在沙箱 `.git/aimergent-leases/programmer.lease` 写入一块与目标文件不相干的
   写区（`nothing-real/`），模拟"已经正常跑过 `mission_start.sh` 领了签"的常规状态。
4. 暂存两个文件：`codeagent/programmer/docs/worklog/x.md`（落在 05 的豁免区，只用来
   触发角色自动推断为 `programmer`，本身不会被拦）+ `secrets/out-of-lease.txt`
   （越出该角色持有的写区，理应被拦）。
5. **不设置 `AIMERGENT_ROLE`**（`env -u AIMERGENT_ROLE`），直接 `bash scripts/mission_complete.sh`，
   完全复现"调用方只依赖自动推断"的真实场景（这也是 `scripts/hooks/pre-commit` 实际
   调用 `mission_complete.sh` 的方式——它本身也不传这个变量）。
6. 断言：退出码非零，且输出含 `越出写区路签`（05 的失败原因原文，不是泛泛的非零）。

## 红/绿证据（真实退出码，非管道回显）

用同一份 `selftest.sh #50`（后续会长期跑）分别对准 pre-fix 与 post-fix 两个真实
checkout，不是临时脚本自证：

- **pre-fix**：`git worktree add --detach <sbx> 6619b35`（障签立签当时的提交，
  bug 仍在），跑 `scripts/selftest.sh <sbx>`：
  ```
  50. 角色自动推断时写区路签是否依然生效
    ❌ FAIL  越区写入未被拦（exit=0）—— role 推断出来后没 export 给子检查，05 写区路签静默放行（障签 2026-07-31）
  ── 小结: PASS 49 · FAIL 1 · N/A 0 ──
  ```
- **post-fix**：同一份 `selftest.sh`，跑在已应用修复的工作树：
  ```
  50. 角色自动推断时写区路签是否依然生效
    ✅ PASS  角色靠暂存路径自动推断、未显式传 AIMERGENT_ROLE 时，越区写入依然被拦（exit=1）
  ── 小结: PASS 50 · FAIL 0 · N/A 0 ──
  ```

两次跑的是同一条断言、同一套沙箱构造逻辑，唯一变量是被测 `mission_complete.sh`
版本——退出码从 0 翻到 1，且失败原因原文匹配「越出写区路签」，不是泛化的非零。
沙箱在临时目录，全程未污染共享工作区，跑完即删。

## 存量四模块（ring / table / nexus-core / nginx-docker）刷新

**未代劳，只给操作步骤**——理由：`install-gates.sh` 从执行它的那个 checkout 的
`scripts/` 拷贝，而我的修复目前只存在于独立 worktree `fix/mission-complete-role-export`
里，尚未合入 CFO 日常操作的共享工作区/远端 `v5`。现在跑刷新只会把旧版本又拷一遍，
是无效动作。正确顺序：

1. 本修复需要先落到 CFO 实际会拿来跑 `install-gates.sh` 的那个 checkout
   （合并到共享工作区分支，或 push 到 `origin/v5` 后 CFO 那边 pull）——这一步不归
   我，consulter 卡里写明"不 push，push 是 CFO 的活"。
2. 之后对四个模块逐一刷新（在能看到修复版本的 checkout 里执行，工作区根即
   `AIMERGENT_WORKSPACE_ROOT`）：
   ```
   scripts/install-gates.sh code/ring
   scripts/install-gates.sh code/table
   scripts/install-gates.sh code/nexus-core
   scripts/install-gates.sh code/nginx-docker
   ```
   `install-gates.sh` 装前会核源仓新鲜度（`AIMERGENT_INSTALL_STALE_OK` 是唯一逃生口），
   四个模块各自是独立 git 工作树，刷新后各自需要一次干净提交固化拷贝结果
   （worklog 建议按模块 arbiter 的写边界各自留痕，不代它们提交）。
3. 刷新后建议用 `git -C code/<模块> diff -- scripts/mission_complete.sh` 确认
   `export AIMERGENT_ROLE` 那行确实进了模块副本，而不是只信 install-gates 的退出码
   （判例库「撒谎式成功」教训）。

本轮判断：**不代劳**这一步，因为此刻代劳等于在无修复的源上空转，会产生"看起来刷新过、
实际没变化"的假象——比不做更危险。留给 CFO 在修复真正落到 v5/共享工作区之后再派单。

## 障签自清

`export AIMERGENT_ROLE` 已出现在 `scripts/mission_complete.sh`，按便条约定的
解除标志，`blockers/2026-07-31-mission-complete-role-not-exported.md` 本次一并删除。

## 自我监督声明

这是 consulter 自己动手修的框架代码（`scripts/mission_complete.sh`、
`scripts/selftest.sh`）——按卡里「谁写的谁不审」与裁决 J7，**本次修复不由我自己签字
放行**，人类复核，CFO 只辅助举证（三件审法出证据，不下结论）。上面红/绿证据已给全，
供复核时直接复现，不需要另外相信我的口头结论。
