# 2026-08-01 · CFO · A-1 逃生口问责账本修复完成（含端到端闭环验证）

## 做了什么

采用人类给的分账本方案（移植自 X_structure `scripts/lib/emit.sh` +
`checklists/_project/10-ledger-tracked.sh`），**没有**用我原提案（diary 整个入仓）。
人类方案避开我方案的两个坑：无界增长、以及 `console.json` 那类机器路径
会把绝对路径判据的「恒真」坑请回来。

- `scripts/lib/emit.sh`：新增 `_emit_ledger` + `emit_override()`（双写 ledger + diary）
- `.gitignore`：`diary.jsonl` 保持忽略，`ledger.jsonl` 明确不忽略，附注释拦下一个
  「顺手把 logs/* 都忽略掉」的人
- 四个逃生口全部接线（铁律23 修到类，不只修撞见的）
- `scripts/checks/_common/16-ledger-tracked.sh`：新判据，**本条无逃生口**
- `scripts/selftest.sh` 第 51 条：静态扫类 + 沙箱真点火 + 验落点没被 gitignore 吞

## 人类点出我漏了的（我原报告只列两个逃生口，实际四个，两个是坏的）

| 逃生口 | 修前 |
|---|---|
| `MISSION_OVERRIDE` | 有记账，但只写 diary（不入仓）|
| `PUSH_SKIP_GATES` | 同上 |
| `PUSH_UNREVIEWED` | **完全无记账**——`arbiter-push.sh` 唯一 emit 挂在 SKIP_GATES 上。我当天用约 10 次，零留痕 |
| `ALLOW_NO_OUTPUT` | **打印「（记账）」但零 emit 调用**——字面撒谎，比不记账更糟（人读到会以为可追）|

第四个是我和人类都没在报告里列出来的，靠人类要求的类扫描命令找到：
`grep -rhoE 'AIMERGENT_[A-Z_]*(OVERRIDE|SKIP[A-Z_]*|UNREVIEWED)' --include='*.sh' scripts/`
（我实际执行时把 `ALLOW_[A-Z_]*` 也加进了模式，才捞到第四个。）

## 红绿举证

- **pre-fix**：detached worktree 停在修复前的 HEAD，把修好的 `selftest.sh` 拷进去跑
  → 第 51 条 FAIL，四个逃生口逐个点名。**证明断言真的在测这个缺口，不是空转。**
- **post-fix**：`./scripts/selftest.sh` → `PASS 51 · FAIL 0 · N/A 0`

## 端到端闭环（不是只跑断言）

推 A-1 修复本身时真用了一次 `PUSH_UNREVIEWED`：

1. 账本自动落一行，带 `actor=cfo` / 理由 / `script` / `branch` / `head=a9080ba`
2. 随即跑 `checks/16` → **exit=1，拦住**（账本未入仓）
3. `git add logs/ledger.jsonl` → exit=0，提示「本次提交带入 1 条逃生口记录」

**绕过 → 自动记 → 下次提交被拦到证据入仓为止。** 这是 A-1 想要的闭环。

## 自检抓到的意外收获

`selftest` 第 36 条（文档不许硬编码会漂移的条目计数）当场抓住**我自己写的排查表**——
里面写死「15 项着陆检查」，我加了第 16 条后立刻过期。
一份专门讲「闸门在撒谎」的文档自己踩了同类坑，已在排查表原地留记，不删。

## 没做的

- 四个模块的 `mission_complete.sh`/`emit.sh` 副本尚未刷新（要跑
  `scripts/install-gates.sh code/<模块>`）。**下一步做**，否则模块侧的逃生口仍是静默的。
- A-2/A-4 未动，按人类给的顺序排在本周。

## 下一步

按人类顺序：② 鉴权原型（登录页 + auth 服务模块）。
