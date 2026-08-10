# 任务单 · 2026-08-11 · consulter · 修 18-secret-literal.sh 属性引用误报

来源：CFO 转来的框架维护单（objection，来自 ai-planner programmer 波2-A）。

## 目标

`scripts/checks/_common/18-secret-literal.sh` 把 `json={"password"`+`: cfg.nexus_password}`
这类**变量引用**误判成硬编码密钥字面量。修掉这个误报，且不能放松对真实硬编码字面量的
识别——误报比没门禁更坏，它教下一个 agent 去改对的代码迎合扫描器。

## 可判定的验收标准

1. 复现：修前跑 `bash scripts/checks/_common/18-secret-literal.sh` 对含
   `cfg.nexus_password` / `self.password` / `obj.attr.sub` 形状的样本必须能重现误报。
2. 修后同样样本零误伤；真实硬编码字面量（含加引号的属性链伪装）仍必须被拦。
3. 铁律 23：扫一遍 `scripts/checks/` 和 `scripts/gates/` 里做同类「变量引用识别」的脚本，
   同形状问题一并修；扫描范围与结果（几处/修了几处）写进报告。
4. 铁律 23：留下可重现的断言（`scripts/checks/` 或 selftest 用例），正反两侧都要覆盖。
5. `scripts/selftest.sh` 全量跑绿；对改动文件本身跑 dogfood（`git add` 后跑该 check）确认
   不会把自己的留痕拦下。

## 可触碰目录

- `scripts/checks/_common/18-secret-literal.sh`（判据本体）
- `scripts/selftest.d/`（断言，扩既有 #61 而非新开一条）
- `agents/consulter/docs/`（自己的留痕：worklog / findings / report.json / 本任务单）

不改业务代码，不改契约。

## 自检门

- `bash scripts/selftest.sh` 全量绿。
- `bash scripts/checks/_common/18-secret-literal.sh`（staged 状态）rc=0。
- **consulter 不能自审自己改的框架**（裁决 J7）：本任务单执行完毕后，`report.json` 的
  `reviewer_opinion` 必须写明交人类审、CFO 只辅助举证，不下 approve/reject。
