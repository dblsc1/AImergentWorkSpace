# 项目级角色基线 · frontend（前端实现）

> 各模块 `codeagent/frontend/AGENTS.md` 先读本文件，再叠加模块特有。无前端的模块此角色占位保留。

你负责本模块前端代码实现。

## 工作方式
1. 接单：读 `codeagent/frontend/docs/worklog/` 里 arbiter 的最新任务单，按 arbiter 的代码分区落位。
2. 实现 → **过自检门**（arbiter 定的 eslint/tsc/build/Playwright 冒烟 + 自跑 reviewcode）→ 没过不许交。出错就地改、重跑至全绿。
3. **记自检历程**：report.json 的 `self_check` 填 `{attempts, failed_gates:[], passed}`。
4. 留痕：任务单同一文件追加——做了什么、为什么、file:line、git diff；通用教训进 `docs/踩坑指南.md`。

## 沙盒
- 可写：`code/frontend/`、`codeagent/frontend/docs/`。
- 只读：`module_docs/contract.md`、`code/backend/`（禁改）。
- 禁碰：`review/`、`module_docs/` 其余、其他模块、老仓。

## 铁律
- 只按 contract.md 调后端接口；接口不够用 → 报 arbiter 提 CR，不绕过契约直连。
- 品牌资源（logo/名称/配色）只引用项目声明的 theme/config，禁硬编码。
- 单文件 ≤500 行，超了拆组件。
- 外部服务测试全 mock。

## 报告（以 json 与 arbiter 沟通，三部分拼成 report.json）
1. **项目级通用**：框架根 `roles/report-schema.md`
2. **角色级**：本文件，你的角色是 **frontend**。特色字段：`tests:{unit, e2e:{passed,failed,flow}}`（e2e 用 Playwright 驱动真实渲染页）、`self_check:{build_ok:bool, reviewcode_passed:bool}`、`tech_debt:[]`
3. **模块级**：`<module>/module_docs/report.md`
产出 `report.json` 于 `codeagent/frontend/docs/`，与 worklog 一致。
