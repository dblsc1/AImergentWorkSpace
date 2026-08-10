# 任务单 · P1：15-stale-blocker.sh 反引号解除标志恒假 + quotepath 同类 footgun

来源：CFO 转来的波2里程碑文档审计发现。

## 目标

`scripts/checks/_common/15-stale-blocker.sh` 的解除标志解析对全项目实际使用的
反引号格式恒假（门禁从建仓至今没点过火），修对并扫同类（铁律 23）。

## 可触碰目录（写区边界）

`scripts/checks/`、`scripts/review_complete.sh`、`scripts/review_start.sh`、
`scripts/selftest.d/`、`agents/consulter/`（自己的留痕）。不改业务代码、
不代 CFO 销 `blockers/` 便条。

## 要做的事

1. 修 `scripts/checks/_common/15-stale-blocker.sh` 的 mark 解析（剥反引号与前后空白）。
2. 铁律 23 修到类：扫 `scripts/checks/` 与 `scripts/gates/` 同类形状；扫全 `scripts/`
   下「拿 git 输出的路径去 `[ -f ]`」的 quotepath footgun。
3. `scripts/selftest.d/` 补正反两侧断言。
4. `blockers/2026-08-02-e2e-defaults-to-prod.md` 现状只判定，不代 CFO 销障。

## 验收标准（自检门）

自检门：`bash scripts/selftest.sh .` 全绿（含新增断言 70/71/72）；
`bash scripts/gates/run-gates.sh` 全绿；逐条跑 `scripts/checks/_common/*.sh`
全部 rc=0；正反两侧都要点火验证，不能只加「已解除必须红」。

详情与真实输出见 `agents/consulter/docs/findings/report.json` 与同目录
`2026-08-11-consulter-stale-blocker反引号解析与quotepath同类修.md`。
