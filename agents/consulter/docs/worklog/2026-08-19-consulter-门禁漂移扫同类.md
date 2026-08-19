# 2026-08-19 · consulter · 门禁漂移扫同类（铁律 23）

## 目标

CFO 发现今天早些时候修的 `scripts/mission_start.sh`「写区逗号假绿」（root commit `5d9e3d9`，
selftest 断言 #71）只在框架根生效——7 个模块仓的门禁脚本是**拷贝**，未跟着更新。实测：
6/7 模块（ring/table/gantt/ai-planner/auth/nginx-docker）的 `mission_start.sh`/`doc_impact.sh`
与框架根漂移，导致这些模块每次提交 `checks/_common/13-gates-fresh.sh` 恒红，靠
`MISSION_OVERRIDE` 放行，记账追不上自己（ring/table 各留了一行未提交的 ledger 尾巴）。
本轮：① 同步 6 个模块的门禁副本；② 按铁律 23 加一条框架根级断言，扫全部 `code/*`
主动发现同类漂移，不依赖模块自己触发 `13-gates-fresh.sh`。

## 验收

- 6 个模块各自跑 `install-gates.sh` 同步后，`bash scripts/checks/_common/13-gates-fresh.sh`
  在各自仓内转绿；各自独立 commit，`Agent-Attribution: consulter@<模块>+gates-drift-sweep`。
- 新增 `scripts/gates-drift-scan.sh`：从框架根扫全部 `code/*` 下「已装门禁」的独立子仓
  （判据：存在 `scripts/mission_start.sh` 且是独立 git 仓），逐文件比对 sha256，
  漂移/缺文件都点名「模块 + 文件」，给出可粘贴的修法命令。
- 新增 selftest 断言 #72（`scripts/selftest.d/90-module-gates-drift.sh`）：沙箱内构造
  两个模块仓，验证基线绿、篡改单个模块单个文件后红且点名、恢复后重新转绿。
- 根仓 `scripts/selftest.sh` 从 71 PASS 涨到 72 PASS，0 FAIL。

## 触碰（可写 / 边界）

- 可写：`scripts/`（新增 `gates-drift-scan.sh` + `selftest.d/90-module-gates-drift.sh`）、
  `agents/consulter/docs/`（本 worklog + canonical report）。
- 各模块仓（`code/<模块>/`）：仅门禁脚本副本 + 自己的 `module_docs/`、`agents/consulter/`、
  `logs/ledger.jsonl`（记账尾巴），不碰业务代码、契约、`code/frontend|backend`、`review/`。
- 未推送到任何远端——本轮全部 commit 留在本地，push 是对外动作，留给人类拍板。

## 自检门

- 逐模块 `bash scripts/checks/_common/13-gates-fresh.sh`：同步前红、同步后绿，真实输出见各模块
  commit message 与本报告。
- 根仓 `scripts/gates-drift-scan.sh`：真实红绿反向验证（临时改一字节 ring 的 mission_start.sh
  → 报红点名 ring；`git checkout --` 恢复 → 转绿）。
- 根仓 selftest 沙箱内对 `gates-drift-scan.sh` 的三段式验证（基线绿 / 篡改单模块单文件后红
  且点名 / 恢复后转绿），见 `scripts/selftest.d/90-module-gates-drift.sh` 断言 #72。
- 根仓 `bash scripts/selftest.sh`：72 PASS / 0 FAIL（较本轮开工前 71 PASS 多一条，无回归）。
