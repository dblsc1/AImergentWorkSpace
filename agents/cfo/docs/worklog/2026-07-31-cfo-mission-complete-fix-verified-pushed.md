# 2026-07-31 · CFO · mission_complete.sh 角色导出修复独立核实 + 合入 + 推送

consulter（sonnet，独立 worktree）修复了 `blockers/2026-07-31-mission-complete-role-not-exported.md`
点名的缺口：`scripts/mission_complete.sh` 推断出 `role` 后没有 `export`，导致
`checks/_common/05-write-lease.sh` 在自动推断角色的常规提交路径上静默放行越区写入。
按裁决 J7（consulter 自改框架代码不自审自批，交人类/CFO 独立核验代替二审），
本次由 CFO 复核，不采信 consulter 自己的红绿报告：

- `git cherry-pick e714f3a`（`fix/mission-complete-role-export` 分支单提交）合进
  `feat/frontend-view-modules`，得 `d40d1d4`。
- 独立跑 `./scripts/selftest.sh`：50/50 PASS，含新断言第 50 条。
- **独立复现 pre-fix 红**：另开 detached worktree 停在 `6619b35`（立障时的提交，
  尚无本次修复），把 post-fix 的 `selftest.sh` 拷进去跑（借用同一份断言，
  指向 pre-fix 的 `mission_complete.sh`）——结果 49/50，第 50 条 `FAIL`，
  `exit=0`（越区写入确实被静默放行），证实断言真的在测这个缺口，不是空转。
  post-fix 同一断言 `PASS`，`exit=1`，输出含"越出写区路签"。
- worktree 与临时分支已清理。

## 四模块刷新——本次未做

`mission_complete.sh` 是 `install-gates.sh` 拷贝清单里的一份，不在
`code/_template/`。四个已建模块（ring/table/nexus-core/nginx-docker）各自的
副本仍是修复前的版本。consulter 的 worklog 已指出正确顺序：先合入/推送本修复，
再对每个模块跑 `scripts/install-gates.sh code/<模块>`，然后用
`git -C code/<模块> diff -- scripts/mission_complete.sh` 核实确实换了新版本
（不信安装器退出码），最后由该模块当批的 arbiter commit 这次刷新。
这是下一步要做的事，还没做。

已用 `AIMERGENT_PUSH_UNREVIEWED=1` 推上 `origin/v5`（本次改动的独立验证由本
worklog 承担，等同二审）。
