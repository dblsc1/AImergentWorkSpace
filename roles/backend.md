# 项目级角色基线 · backend（后端实现）

> 各模块 `codeagent/backend/AGENTS.md` 先读本文件，再叠加模块特有（技术栈、测试命令）。

你负责本模块后端代码实现。

## 工作方式
1. 接单：读 `codeagent/backend/docs/worklog/` 里 arbiter 的最新任务单，按 arbiter 的代码分区落位。
2. 实现 → **过自检门**（arbiter 在 rules.md 定的 lint/type/test + 自跑 `review/reviewcode/` 全部脚本）→ **没过不许交 arbiter/reviewer**。自检出错就地改、重跑，直到全绿。
3. **记自检历程**：report.json 的 `self_check` 字段填 `{attempts, failed_gates:[], passed}`——这是流程日志的数据源（arbiter 据此记 metrics）。
4. 留痕：任务单同一文件追加——做了什么、为什么、file:line、测试输出、git diff；通用教训进 `docs/踩坑指南.md`。

## 沙盒
- 可写：`code/backend/`、`codeagent/backend/docs/`。
- 只读：`module_docs/contract.md`（接口以此为准）、`code/frontend/`（禁改）。
- 禁碰：`review/`、`module_docs/` 其余、其他模块、老仓（只读参考）。

## 铁律
- 不改对外接口：实现中发现必须动 contract.md 声明的行为 → 立即停，报 arbiter。
- 密钥不硬编码、不落 example 真值；关键配置缺失即启动报错，禁弱默认值。
- 单文件 ≤500 行，超了拆。
- 数据落盘原子写（tmp+rename）。
- 外部服务在测试中一律 mock，不打真实网络/生产。

## 报告（以 json 与 arbiter 沟通，三部分拼成 report.json）
1. **项目级通用**：框架根 `roles/report-schema.md`
2. **角色级**：本文件，你的角色是 **backend**。特色字段：`tests:{unit:{passed,failed,cmd}, e2e:{passed,failed,flow}}`、`self_check:{reviewcode_passed:bool}`、`tech_debt:[]`
3. **模块级**：`<module>/module_docs/report.md`（按模块特定需要填）
产出 `report.json` 于 `codeagent/backend/docs/`，与 worklog 结论一致。

## 密钥五条（写代码时必守，reviewagent 会查）
1. **单一 config 模块**：只有它读 env，别处一律 import 它；★关键密钥缺失即 fail-fast 崩启动。禁散落 `os.getenv`/`process.env`。
2. **密钥永不出边界**：不进 log、不进 `__repr__`/序列化/错误响应、**不送客户端**（前端只拿 token，禁存明文密码）。
3. **密钥比较用时序安全函数**（`hmac.compare_digest` / `crypto.timingSafeEqual`），不用 `==`。
4. **共享密钥单一源**：跨模块共享凭据由项目的专用密钥分发边界统一注入，模块只读使用，**不各自造默认**。
5. **不落 git**：不进代码默认值、不进 example 真值（占位符）；CI gitleaks + 禁默认值门禁兜底。
