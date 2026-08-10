# 2026-08-11 · consulter · P1：15-stale-blocker.sh 从建仓至今没点过火 + 同类 quotepath footgun

任务来源：CFO 转来的波2 里程碑文档审计发现。工作树：`../v5-consulter-blockerfix`
（独立 `git worktree`，全程未碰共享工作树 HEAD，遵守上一轮的教训）。

**补记**：首次提交 `report.json` 的 `git.base` 填了 `merge-base(HEAD, origin/v5)`
（0d17988，本任务在这条分支上实际的分叉点），被 `check-report-schema.sh` 响亮拒绝——
判据要的是 `merge-base(HEAD, origin/main)`（PR 目标基线），不是「这条分支从哪分出去」。
两者在本仓不是同一个点（`v5` 与 `main` 历史已分叉）。改用报错信息直接印出的正确值
`211bdec5559ab4ab798a464272cde5dc27f64740` 后 `run-gates.sh` 通过。

## 病根

`scripts/checks/_common/15-stale-blocker.sh` 用 `_file=${mark##*@}` 从「解除标志：」
一行里切出文件名，但全仓写这一行的**唯一实际写法**是反引号包裹：
`` `文字` @ `文件` ``（三张已删便条与仍在场的
`blockers/2026-08-02-e2e-defaults-to-prod.md` 全一样）。切完残留的反引号与前导空格
原样留进 `$_file`，`[ -f "$_file" ]` 恒假——**门禁没崩、没报错，只是永远不会变红**。

现场复现（改前脚本，`git show HEAD:...`）：给 `conftest.py` 真的落上 `_test` 护栏后，
旧脚本仍 `exit 0`；`blockers/2026-08-02-e2e-defaults-to-prod.md` 现在在这份 worktree
（v5 分支）里判据显示未满足（`code/ring` 这个模块在 v5 分支上还没落地，不含 conftest.py），
所以它本身**目前不该被判满足**——是否已在别的分支满足、要不要销障，交 CFO 核别的 checkout。

## 修法

1. `_trim()`：剥外层空白 → 剥外层一对反引号 → 再剥一次空白。分别应用到
   `@` 两侧和无 `@` 时的整个 `mark`。
2. `_clean_path()`：剥完仍带反引号/空白/全角括号逗号 → 判「解除标志格式不受支持」
   并**主动置红**（不是只让它退化成永远 met=0 的第二种静默假绿——那种失效没有
   任何输出，比第一种更难被人事后翻出来；历史上删掉的 nexus-core 便条就写过
   一句自然语言夹两个反引号，这种写法用旧判据也是永远判不出已解除）。

## 同类扫描（铁律 23，「这个形状还出现在哪」）

按任务要求扫了 `scripts/checks/` 和 `scripts/gates/` 全部对 markdown/git 输出做
「抠路径/标识符然后判存在」的地方：

- **同一个「反引号未剥」的形状**：只有 15 号这一处。`60-doc-paths-exist.sh`、
  `12-standing-docs.sh`（文档地图反查）、`gates/check-references.sh`、
  `30-subreport-in-repo.sh` 都已经用 backtick-aware 的正则（`` `(...)` `` 整体匹配后
  `tr -d` 反引号）或 `jq` 结构化取值，不受影响。
- **quotepath C 转义**（任务里点名的同类 footgun，`任何拿 git 输出的路径去 [ -f ] 的
  地方`，不限 checks/gates 两个目录）：`scripts/lib/paths.sh` 的 `staged_paths`/
  `tracked_paths` 全部走 `-z`（NUL 分隔天然不受 quotepath 影响），`checks/_common/`
  全部经它取路径，安全。`check-report-schema.sh` 与 `lib/review.sh` 已经在 5 处
  显式加了 `-c core.quotePath=false`。**唯一漏了这个 flag 的地方**：
  `scripts/review_complete.sh:51`（生成 canonical report 的 `changed_files`）和
  `scripts/review_start.sh` 的三处展示用 diff——前者是真 bug：`check-report-schema.sh`
  用 `quotePath=false` 取「真实」diff 去比对 `review_target.changed_files`，
  两边各转义一次就对不上，**含中文/非 ASCII 文件名的合法审核报告会被判
  「changed_files 与 diff 不完全一致」而 rejected**（假红，不是假绿——同一个病根，
  发作方向相反）。已修，两处都加 `-c core.quotePath=false`。

前后对比（sandbox 复现，报告贴在 `agents/consulter/docs/findings/report.json` 的
`findings[].summary`）：旧 `review_complete.sh` 对含 `code/文件名.txt` 的区间产出
`["\"code/\\346\\226\\207....txt\""]`，`check-report-schema.sh` 判「不完全一致」拒绝；
新版本产出 `["code/文件名.txt"]`，同一份 schema 检查通过。

## 断言（正反两侧，`scripts/selftest.d/10-false-green.sh` 新增 70–72）

- **70**：反引号 `` `文字`@`文件` `` 格式——未解除必须绿、已解除必须红。两侧各跑一次
  真实的 `bash checks/_common/15-stale-blocker.sh`，直取退出码。
- **71**：格式不受支持（既不是路径也不是 `文字@文件`）必须响亮失败，不能退化成
  「永远 met=0」的第二种静默假绿。
- **72**：`review_complete.sh` 产出的 `changed_files` 经含中文文件名的真实 diff 走一遍
  `check-report-schema.sh`，必须通过（证明 quotepath 修复端到端生效，不只是「加了
  flag」这件事本身）。

`bash scripts/selftest.sh .` 在本 worktree 跑：**72 项全部 PASS**（含新增三项）。
`bash scripts/gates/run-gates.sh` 全绿。`scripts/checks/_common/*.sh` 逐个跑过，
除 `10-worklog-changed.sh`（本文件补上后即满足）外全部 rc=0，包括改后的
`15-stale-blocker.sh` 对当前仓真实状态判据不变（原本就该绿，改完仍绿——没把
「本来该绿」的路测瞎）。

## 谁来验（铁律：consulter 改的框架不能自审自批，裁决 J7）

本轮全部是我自己对框架（`scripts/checks/`、`scripts/review_*.sh`、
`scripts/selftest.d/`）的改动，**没有自审自批空间**。按 J7：CFO 只辅助举证
（可代跑上面列的命令、贴真实输出），最终由人类审。push 大概率会被权限分类器拦——
拦了就停，命令见 `report.json`。

## e2e-defaults-to-prod 现状判定

`blockers/2026-08-02-e2e-defaults-to-prod.md` 的解除标志是
`` `_test` @ `code/ring/review/reviewcode/tests/conftest.py` ``。**在本 worktree
（v5 分支）里 `code/ring` 这个模块还没有落地**（`git ls-files code/ring` 为空），
所以本条判据在这个 checkout 里合法地判「未满足」。任务单说「现场复现」用的是共享
工作区当前状态（`code/ring/` 是未跟踪目录，属于别的并发 agent 的工作），我没有切换
到那个 checkout（铁律：本轮不碰共享工作树）。**销障与否、要不要在哪个分支/哪次
commit 里核实并删这张便条，交 CFO 裁**——我只确认了判据本身现在能正确识别
「一旦满足就会变红」，不代自己下场判定别的 checkout 上现在是否已满足。
