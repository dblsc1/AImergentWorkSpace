# <项目名> · 项目宪法（加严层）

> **启用方式**：复制本文件为 `CONSTITUTION.md` 并填实。不启用就删掉本模板，别留空壳。
> **定位**：框架根 `AGENTS.md` 的通用铁律是**下限**；本文件是本项目在此之上的**加严条款**与**裁决台账**。
> **级联铁律**：本文件只能加严，**不得放松或改写**上层任一条。发现与上层冲突 → 停下上报人类，不许自行解释掉。

## §1 本项目加严条款

> 只写"比通用铁律更严"的部分。与通用铁律重复的不抄——抄了就会两处漂移。

| # | 条款 | 加严的是哪条通用铁律 | 为什么本项目需要它 |
|---|---|---|---|
| C1 | <例：所有对外接口变更必须先过契约校验器再动代码> | 铁律 4 | <理由> |

## §2 术语与不变量

<本项目里含义被固定下来的词、绝对不能破的不变量。放这里是为了让冷启动 agent 不必从代码里猜。>

## §3 环境与边界

<数据落点、密钥注入方式、只读区、任何人不得删除/改写的路径。>

---

# §4 裁决记录（人类已拍板的事）

> 每条裁决**必须**记在这里。口头批准过、但只活在会话里的裁决，等于没批准——下一个 agent 读不到。

| 编号 | 日期 | 裁决内容 | 提出者 | 状态 |
|---|---|---|---|---|
| J1 | 2026-07-29 | cfo_reviewer 职位**先搁置**；空缺期由 consulter 代任 CFO 产出的例行审查（代任=过渡态，职位设立即退出）。例行审查归 reviewer 层、consulter 长期只做模式监督——完整矩阵固化于 `agents/protocol/supervision.md`。 | 用户 | 生效 |
| J2 | 2026-07-29 | 测试分层：programmer 写 pytest 级单元测试；reviewer 写整合级（跨整个代码文件）测试 + 规范性审核。两层都入 push 前测试。 | 用户 | 生效（条文未同步，见 §4.1） |
| J3 | 2026-07-29 | 留痕迁到代码旁：worklog（简短）与 report.json 迁入代码目录旁；每模块一页纸说明，每改必核（落检查项）。 | 用户 | 生效（条文未同步，见 §4.1） |
| J4 | 2026-07-29 | jsonl 沟通留痕改为每 agent 一份（发任务/报完成，append-only）。 | 用户 | 生效（条文未同步，见 §4.1） |
| J5 | 2026-07-29 | arbiter push 前测试范围＝本模块全量 + 消费方契约测试；全仓跨模块全量归合入 dev/main 的 CI。 | 用户 | 生效（条文未同步，见 §4.1） |
| GAP-1 | YYYY-MM-DD | <一句话结论> | <谁> | 已落地 / **未落地** |

## §4.1 已批准但尚未落进契约的裁决 ⚠️

> **这一节是本框架里最容易出事的地方，单独立节。**
>
> 人类已经批准了新行为，但契约文件还写着旧行为。此刻**读契约的 agent 会照旧契约实现出与已批准裁决相反的东西**，而且它不会觉得自己错了——它读的是"唯一事实源"。
>
> 规则：
> 1. 裁决一旦批准而契约未同步，**立即登记到本节**，不许只记在 §4。
> 2. 每条**必须列出待改文件的确切清单**（路径 + 改什么），否则等于没登记。
> 3. 本节非空时，**下一轮实现的第一件事就是清空它**，不得先做新功能。
> 4. 清空后把该条移回 §4 并标"已落地"。

| 裁决编号 | 批准日期 | 已批准的新行为 | 契约当前仍写着的旧行为 | 待改文件清单 |
|---|---|---|---|---|
| J2 | 2026-07-29 | programmer 写单测；reviewer 写整合级测试+规范审核 | `agents/roles/programmer_reviewer.md` 等只写「审核脚本」，未提整合测试职责 | `agents/roles/programmer_reviewer.md`（补整合测试职责）、`agents/roles/programmer.md`（明确单测义务）、`agents/AGENTS.md` 铁律 17（reviewer 第一职责表述）、`scripts/gates/run-tests.sh`（两层测试入口） |
| J3 | 2026-07-29 | worklog+report.json 放代码目录旁；一页纸说明每改必核 | `agents/protocol/report-schema.md` canonical path 表指 `codeagent/<角色>/docs/`；铁律 5/18 落点表述 | `agents/protocol/report-schema.md`（canonical 表）、`agents/AGENTS.md` 铁律 5/13/18、`code/_template/`（骨架）、`scripts/checks/_common/10-worklog-changed.sh`、`20-report-committed.sh`、`check-report-schema.sh`（路径判据）、新检查项：一页纸说明每改必核 |
| J4 | 2026-07-29 | jsonl 每 agent 一份 | `agents/AGENTS.md` 文档四件套表写全仓 `logs/diary.jsonl` 单份 | `scripts/log_event.sh`（落点参数化）、`agents/AGENTS.md` 四件套表、`agents/protocol/orchestration.md`、`scripts/reindex.sh`/`console.sh`（读取端） |
| J5 | 2026-07-29 | push 前＝本模块全量+契约测试 | `scripts/gates/run-tests.sh` 未定义分层范围 | `scripts/gates/run-tests.sh`、`agents/roles/arbiter.md`（push 前义务）、`scripts/arbiter-push.sh`（调用链） |

（本节为空 = 契约与裁决一致，可以正常推进。）
