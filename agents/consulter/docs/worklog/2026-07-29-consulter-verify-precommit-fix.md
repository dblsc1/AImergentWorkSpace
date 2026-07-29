# 2026-07-29 · consulter · 反向验证「pre-commit 从未装上」修复（6cceec1）

## 任务

前任 handoff：install-gates 原先只装 gates/ + 3 hook，pre-commit 写的是
`[ -x … ] || exit 0`，三个模块的着陆检查从未跑过。框架侧已在 6cceec1 修复。
本任务不看代码下结论，按判例库「恒定答案类」做红/绿反向验证。

## 验证方法与真实结果

全部在沙箱（mktemp 目录 + 临时 git 仓）或可复原的临时改动中进行，框架工作区始终干净。

### 实验 1 · pre-commit 本体（沙箱仓，只装 hook）

- 红：无 `scripts/mission_complete.sh` → `git commit` 退出码 **1**，仓内零 commit，
  打印「缺少 scripts/mission_complete.sh —— 拒绝提交」。
- 绿：放入退 0 的 stub → 提交成功，且 **stub 的输出被打印**（真执行，非存在性检查）。

### 实验 2 · install-gates 全套（沙箱 workspace + git init 模块仓）

- 装后核验：`scripts/mission_complete.sh`、`scripts/hooks/pre-commit`、`scripts/checks/`、
  `scripts/lib/docmap.sh`、`scripts/gates/run-gates.sh` 全部落地；
  `.git/hooks/pre-commit` 与框架版 `cmp` 字节一致。
- 红：裸仓提交 → 着陆检查单真实渲染，3 项红（worklog-changed / standing-docs / gates-fresh），
  `git commit` 真实退出码 **1**、`git log` 确认零 commit。
- override 路径：带 `AIMERGENT_MISSION_OVERRIDE` 仍被 commit-msg 拦（缺 Agent-Attribution）；
  补 trailer 后放行，diary 落 `mission_override` 事件（含 failed 清单与理由）——记账闭环。

### 实验 3 · 断言层（铁律 23：修复必须留断言）

- 红：把 pre-commit 临时改回旧 `[ -x … ] || exit 0` 形状 → selftest 第 32 条
  **FAIL**，整体退出码 1。
- 绿：恢复 → 32 复 PASS。工作区复核干净。

### 实验 4 · check-references

- 红：tracked `.sh` 中植入 `scripts/does-not-exist.sh` 引用 → ❌ 点名、退出码 **1**。
- 绿：移除 → 退出码 0。
- 注：前两次红测「失败」是我自己的手法错误——见教训。

## 结论

**修复为真。** 四组红/绿全部按预期翻转，退出码与仓状态（是否真有 commit）双重核实。
selftest 34/34、check-references 全绿（正向），反向验证补齐了「看代码不算」的那一半。

## 顺带发现（未修，只记录）

1. **mission_start 第 4 项仓型不匹配**：查 `codeagent/<角色>/AGENTS.md`（模块布局），
   项目级角色（consulter/CFO）在框架仓的卡在 `agents/<角色>/AGENTS.md` → 项目级角色
   在框架仓永远过不了起飞单。「恒定答案类·判据仓型不匹配」的新实例。
2. **面板服务名尾多连字符**：`aimergent-panel-mymod-.service`（现网三个实例同形状），
   像 slug 拼接了一个空段。低危。
3. **install-gates 在沙箱安装时会拉起常驻 systemd 面板服务**：有打印、非静默，
   但测试/CI 场景的副作用值得斟酌（本轮已手工清理沙箱服务与 unit 残留）。

## 教训（验证者自身）

同一坑连踩两次：`cmd | tail; echo $?` 取到的是 tail 的退出码——差点把真修复判成假、
又差点把好门禁判成恒绿。**验证退出码必须直取（`cmd >f 2>&1; echo $?`）或用 PIPESTATUS。**
建议入判例库「恒定答案类」注脚：审别人假绿之前，先防自己的 $? 撒谎。
