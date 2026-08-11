# 任务单 · nexus-core review/ 侧两条存量红

- 派活方：cfo · 日期：2026-08-11 · tier：normal · 档位：Sonnet

## 目标

清掉 `code/nexus-core` `run-gates.sh` 的两条红。两条都在 `review/`（reviewer 属地），
上一轮 arbiter 帽守边界没碰，是对的。**CFO 已独立复核，两条都属实。**

## 两条红（CFO 实测）

1. **`review/reviewreport/report.json` 的 `review_target` 是 `null`** ——
   schema 只允许 consulter 为 null。实测：`json.load(...)["review_target"] is None`。
   最后动它的是 `dd15e67`（E1/E4 那轮 reviewer 帽）。
2. **`review/reviewcode/views_predicates.py` 正好 501 行** —— 卡在宪法 C1 的
   「501–1000 需登记」档，而**哪里都没登记**。实测 `wc -l` = 501。

## 验收标准

- [ ] `review_target` 填真实审核目标（本模块非 consulter，不许留 null）
- [ ] 501 行那条：**要么拆到 500 以内，要么按 C1 登记豁免并写明理由**。
      拆优先——501 是"刚过线"，多半能干净拆开；登记只是没法拆时的退路
- [ ] `bash scripts/gates/run-gates.sh` → **exit 0**，贴真实输出
- [ ] `code/backend` 的 **281 passed 基线不退**（跑法：`NEXUS_DB_NAME=<以_test结尾> .venv/bin/pytest -q`；测试套件拒绝指向真库）
- [ ] `review/reviewcode/run_all.sh` 仍 exit 0（**独占跑，别和别的 pytest 并发** ——
      它用固定名测试库，两个实例同时跑会互相清库、产生假红）

## 可触碰目录

`review/`（reviewer 属地）。**禁写 `code/backend/`、`module_docs/`。**

## 自检门

`run-gates.sh` exit 0 + `run_all.sh` exit 0 + 281 基线，三个都贴真实输出，不许只写结论。

## 硬约束

- 铁律 23：若 501 行那条是拆，拆完留断言防再次超线
- trailer 恰一条 `Agent-Attribution: programmer_reviewer@nexus-core+review-side-reds`
- 留痕 + report.json 入仓 commit
- **别切根仓分支**（工作树多方共享）
