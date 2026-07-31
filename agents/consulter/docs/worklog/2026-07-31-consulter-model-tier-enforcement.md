# 2026-07-31 · consulter · 模型分档补执行点（销 blockers/2026-07-30-model-tier-not-enforced.md）

## 起因

CFO 立障签 `blockers/2026-07-30-model-tier-not-enforced.md`：`agents/protocol/orchestration.md`
第 6 条（用户 2026-07-30 裁定「reviewer/arbiter 常规轮次用中档 sonnet，升档只在打回复审/
架构级裁决」）在 `scripts/run_agent.sh` 里没有任何传模型的入口——对比权限档有
`AIMERGENT_AGENT_PERMISSION_MODE`，模型档一个都没有。CFO 已因此两次把 programmer 跑上 Opus。

## 改动（三处，均在框架写区内）

1. **`scripts/run_agent.sh`**：新增 `AIMERGENT_AGENT_MODEL` 环境变量入口，
   `model=${AIMERGENT_AGENT_MODEL:-sonnet}`，`args` 里加 `--model "$model"`。
   **默认中档而非继承**——不设该变量时不再由 `claude` CLI 自行决定（往往等于继承派活方的档），
   而是显式传 `sonnet`。同时把 `model` 记进 diary 的 `run_agent` 事件（与既有 `perm` 字段同形），
   为「高档占比」提供可查数字。
2. **`scripts/selftest.sh`**：新增断言 49「模型分档是否有机械执行点」——核
   `run_agent.sh` 同时含 `AIMERGENT_AGENT_MODEL`、`--model`、默认档 `sonnet` 三处，
   缺一不过（铁律 23：修复必须留断言）。
3. **`agents/protocol/orchestration.md` 第 6 条**：补一句机械执行点说明，
   指明 `run_agent.sh` 这条通路已有默认值机制，Claude Code 原生子代理
   （`Agent`/`SendMessage`）那条通路仍需派活方显式传 `model`（暂无同等默认值兜底，
   如实注明未一并解决，不算已修）。

## 验证（红/绿点火，真实输出）

- **selftest 49 · 绿**（对本仓已修复的 `run_agent.sh`）：
  ```
  49. 模型分档是否有机械执行点
    ✅ PASS  run_agent.sh 有 AIMERGENT_AGENT_MODEL 入口且默认中档（sonnet），不是继承
  ── 小结: PASS 49 · FAIL 0 · N/A 0 ──
  ```
- **selftest 49 · 红**（在 `/tmp` 隔离副本里还原成障签描述的缺陷版 `run_agent.sh`，
  即本次改动前的 `HEAD` 版本，`AIMERGENT_AGENT_MODEL` 出现次数为 0）：
  ```
  49. 模型分档是否有机械执行点
    ❌ FAIL  模型分档无执行点 —— 只能靠派活方每次记得（blockers/2026-07-30-model-tier-not-enforced.md 已实证两次跑上 Opus）
  ```
- **`--model` 真实传参**（用假 `claude` 可执行文件回显收到的参数，隔离副本，不联网）：
  - 不设 `AIMERGENT_AGENT_MODEL` → `... --permission-mode acceptEdits --model sonnet ...`
  - 设 `AIMERGENT_AGENT_MODEL=opus` → `... --permission-mode acceptEdits --model opus ...`
- **diary 落地**：同一次隔离运行 `logs/diary.jsonl` 尾行含
  `"perm":"acceptEdits","model":"opus"` —— 档位与既有权限档同形可查。
- `bash -n scripts/run_agent.sh` / `bash -n scripts/selftest.sh` 语法检查通过。

## 边界与未尽

- 只改了框架仓 `scripts/`、`agents/protocol/`；未碰任何模块 `code/`。
- `scripts/README.md`、`agents/reference/manual/文档地图.md` 被 `doc_impact.sh --staged`
  点名为需确认的长期文档；两处对 `run_agent.sh` / `selftest.sh` 的描述都是不列举细节的
  一行摘要（`selftest.sh` 那行已写「条数见运行输出」，符合铁律 36 不硬编码计数的既有约定），
  判定 `no-change-needed`：新增一个环境变量与一条断言不改变这两行摘要的准确性。
- 本改动由**人类审、CFO 辅助举证**（裁决 J7）——consulter 不自批。解除标志
  `AIMERGENT_AGENT_MODEL` 已出现在 `scripts/run_agent.sh`，CFO 可核实后销掉障签。
- 未处理：Claude Code 原生子代理（`Agent`/`SendMessage`）派活时的模型默认值——
  那条通路目前仍需派活方每次显式传 `model` 参数，本轮未新增机制兜底，如实记录，
  不并入「已解决」范围。
