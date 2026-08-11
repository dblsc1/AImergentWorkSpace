# 可迁移项目框架 · 项目级规范（级联根）

所有 agent 开工前必读本文件。模块级、角色级规范只能在本项目选定的工作模式内加严，不得悄悄改写上层规则；发现冲突立即停下并上报项目 arbiter。

## 这是什么

本仓是 clone 即用的项目框架，提供模块骨架、分层角色卡、契约文档协议和无密钥确定性 CI。它不包含任何具体业务模块、平台实现、运行数据或环境密钥。

`code/_template/` 是新模块结构的唯一事实源；用 `scripts/new_module.sh <相对路径>` 生成实例，不在已生成副本里反向维护模板。

## 顶层结构

```text
<项目根>/
├── AGENTS.md                 # 规范根（唯一必读）
├── CONSTITUTION.md           # 项目加严条款 + 裁决台账（填实即启用）
├── README.md                 # clone 后入口
│
├── agents/                   # ① AI 工作区
│   ├── roles/                #   模块级四角色卡
│   ├── cfo/                  #   项目 arbiter（CFO）：角色卡 + docs/
│   ├── consulter/            #   独立审核与架构顾问，**与 CFO 平级，不隶属于它**
│   ├── review/               #   项目级 reviewcode / reviewreport
│   └── reference/            #   开设指南、文档地图、架构说明、经验教训
│
├── code/                     # ② 代码区
│   ├── _template/            #   模块骨架唯一事实源
│   └── <模块>/               #   scripts/new_module.sh 生成
│
├── logs/                     # ③ 日志区
│   ├── INDEX.md              #   全量留痕索引（脚本生成，留痕本身仍分散在各角色目录）
│   ├── diary.jsonl           #   机器事件流水
│   └── console.html          #   任务控制台原型
│
└── scripts/                  # ④ 脚本区
    ├── checks/               #   完工检测的可增删改查检查项
    ├── gates/ hooks/ workflows/
    └── dispatch.sh exam.sh mission_complete.sh new_module.sh …
```

**四分区口径**：`agents/`＝AI 的规范与留痕；`code/`＝代码；`logs/`＝日志与面板；`scripts/`＝一切可执行。
根目录只允许三份文件，新增根级文件必须同时登记进 `.gitignore` 白名单（它会静默吞掉未登记的新文件）。

运行数据、备份和密钥目录由项目通过 env 明确指定，一律位于 Git 工作树之外或已忽略的 `.runtime/` 中。

## 工作模式

### 轻量模式

适用于单人、无外部消费方的实验。保留 Git、`.gitignore`、`code/`、项目自检和密钥边界；可不启用 CFO、多角色并发、独立审核门、canonical report 和远端治理。出现外部消费方时，先补真实 `contract.md`。

### 完整治理模式

适用于多人、多模块、需要可审计交付或有外部消费方的项目。启用 CFO / 模块 arbiter / frontend / backend / reviewagent、报告协议、独立审核和 CI/合并门。下文带“完整治理”的要求只在本模式强制。

## 模块统一模板（2026-07-29 裁决 J2–J5 后的布局）

```text
<module>/
├── AGENTS.md  CLAUDE.md
├── module_docs/              # 模块级文档系统，arbiter 写
│   ├── contract.md           #   对外契约（铁律 4，不迁）
│   ├── rules.md  report.md
│   ├── handoff.md            #   一页纸说明，每改必核（checks/14）
│   ├── worklog/              #   arbiter 简短日志
│   └── report.json           #   模块级交接（arbiter canonical）
├── codeagent/
│   ├── arbiter/              #   长期存在：AGENTS.md + docs/arbiter.jsonl
│   ├── programmer/<编号>/     #   arbiter 用 new_instance.sh 开：
│   │                         #     agent.md（实例卡）+ session（一行 id，复用）+ docs/comm.jsonl
│   └── reviewer/<编号>/       #   同构（实例化 programmer_reviewer 角色卡）
├── code/
│   ├── backend/              #   可再拆 backend1/ backend2/ …
│   │   ├── <源码>
│   │   ├── handoff.md        #   代码侧一页纸，programmer 写，每改必核
│   │   ├── worklog/          #   简短
│   │   └── report.json       #   子文件夹交接任务书（programmer canonical）
│   └── frontend/             #   同构
└── review/
    ├── reviewcode/           #   审核脚本 + reviewer 的整合级/契约测试（tests/）
    └── reviewreport/         #   审核详报 + report.json（reviewer canonical）
```

核心原则：`codeagent/` 只放规范与 agent 实例（卡、session、jsonl），业务代码只在 `code/`，
审核脚本和详报只在 `review/`；代码侧留痕（worklog / report.json / handoff.md）与源码同目录（裁决 J3）。
arbiter 长期存在；programmer / reviewer 是**编号实例**，由 arbiter 通过 shell + `session` 文件调用，
**同一块代码复用同一实例的 session**（续用不重开）。

## 角色与写边界（完整治理）

| 角色 | 可写 | 只读 / 禁止 |
|---|---|---|
| 模块 arbiter（长期存在） | `module_docs/`（含其 worklog/report.json/handoff.md）、任务单、实例目录管理（`codeagent/<容器>/<编号>/` 的创建与 session）、自己 docs | 禁写 `code/`、`review/` |
| programmer 实例 | 其负责的 `code/<子文件夹>/`（含其中 worklog/report.json/handoff.md）、自己实例 docs | 契约只读；禁写 `review/`、别人的子文件夹 |
| programmer_reviewer（reviewer 实例） | `review/`（含整合级测试 `reviewcode/tests/`）、自己实例 docs | `code/` 只读；禁修业务代码 |
| module_reviewer | `review/reviewreport/`、自己 docs | 全模块只读；**只审规范面，不替 arbiter 做技术判断** |
| CFO arbiter | 项目规范、跨模块契约关系、协调台账 | 禁亲写模块业务代码 |
| consulter（**与 CFO 平级**） | 自己的评审留痕、必要的规范纠错、框架维护 | 禁执行业务实现、禁代替 CFO 裁决、**禁自审自己改的框架** |

**为什么 consulter 不放在 `agents/cfo/` 下面**：它对 CFO 做**模式监督**（CFO 的逐单例行
审查归 cfo_reviewer 子代理，卡在 `agents/cfo/reviewer/agent.md`；矩阵见 `agents/protocol/supervision.md`）。
挂在 CFO 名下等于结构上说它归 CFO 管，与「谁写的谁不审」直接打架。
三个项目级角色是**并列**的：CFO 分派、consulter 独立监督、人类裁决。

## 协作流程（完整治理）

模块 arbiter 开单 → worker 实现、自测、跑 `reviewcode` → commit 并形成固定 candidate → reviewagent 对 exact target 独立审核 → approved 后才由 `scripts/merge-to-integration.sh` 合并。同一任务最多打回 2 次，超限升级项目 arbiter。

跨模块、跨契约或越出当前写边界的变更必须停下，交项目 arbiter 裁决并留变更记录；不得顺手跨界。

## 通用铁律

1. **Git 边界清晰**：每个独立交付单元有明确仓根。完整治理使用 `feat/`、`fix/`、`chore/` 短分支；`main` 只经审批和合并门更新。
2. **密钥永不进 Git**：不进代码默认值、日志或 example 真值。运行时从 env / 专用密钥目录注入；关键配置缺失立即失败，禁止弱默认值。
3. **品牌与环境外置**：logo、名称、配色与部署差异走 theme/config，业务代码不硬编码。
4. **契约至上**：有外部消费方时，`module_docs/contract.md` 是对外行为唯一事实。完整治理下，改契约先走变更评审，批准后先改契约再改代码。
5. **文档与代码分离**：`codeagent/` 不放代码，`code/` 不放 agent 流程文档。
6. **客观验证优先**：任务单必须写清验收标准和确定命令；测试、lint、typecheck、build 的真实输出优先于主观结论。
7. **数据与代码分离**：运行数据、备份和缓存不进仓；每个模块只写自己的数据边界。
8. **模块只经契约依赖**：不直连其他模块内部数据库、文件或未公开 API；例外必须在双方契约中登记。
9. **单文件默认 ≤ 500 行**：超限则拆分；遗留例外必须有精确豁免和技术债。
    **适用范围只有源码文件**（人类裁决 2026-08-02）——`.md` / `.txt` / `.jsonl` 这类散文与数据不算：
    500 行的约束本意是**逼你拆职责**，那是代码的道理，长文档拆开反而伤可读性。
    源码由 `scripts/gates/run-gates.sh` 的 `c1_source_ext` 白名单显式列举（**唯一事实源**，
    增删扩展名只改那一处）；具体分档由项目宪法定（本项目见 `agents/CONSTITUTION.md` C1，
    台账 D12 定分档 / D16 定适用范围）。
10. **提示词唯一事实**：`AGENTS.md` 是规范源；如需 Claude Code 兼容，同目录 `CLAUDE.md` 只保留 `@AGENTS.md`。
11. **改动即同步文档**：架构、目录、接口或配置变化必须在同一逻辑变更中更新直接文档和所有导航/索引，不得只改一处。
12. **canonical report（完整治理）**：每个 agent 在 `agents/protocol/report-schema.md` 指定的路径产出已提交、可重放的 `report.json`；人类详报不替代 canonical report。**未被 Git 跟踪、工作区脏、或落在仓外/临时目录的 report 一律按「未产出」处理。** 派活方（CFO / arbiter）回收子代理时**必须用 Git 核验**（文件在仓内、已 commit、内容与交接一致），**不接受口头「已写报告」**，也不接受指向仓外路径的报告引用。
13. **文档四件套（完整治理，落点见 report-schema）**：worklog（**简短**）/ report.json / comm.jsonl（**每 agent 一份**，裁决 J4）/ handoff.md（一页纸，**每改必核**）分别表达过去叙事、当下交接、agent 沟通和未来接手，各一写属主，不重复。全仓 `logs/diary.jsonl` 是脚本事件账本，不承担 agent 沟通。
14. **commit 唯一归属（完整治理）**：每个新 commit 有且只有一个 `Agent-Attribution: <role>@<module>+<task_id>` trailer，三段使用可解析的小写 slug。
15. **一分支一活跃写者（完整治理）**：派活前基于已验证的远端 tip；交接前确认 candidate 真实落地；push 采用 fetch-then-push，非 fast-forward 拒绝是最后兜底。
16. **P0 隐患必须绑定 fix owner**：安全、数据、未受控写入或破坏回滚级问题，必须指定属主并阻断受影响工作，直到修复或有效止血；归档不等于缓解。
17. **审核代码化优先 + 测试分层（完整治理，裁决 J2/J5）**：可机械核验的事实一律脚本化，肉眼只审判断题。**测试分层**：programmer 写 pytest 级单元测试（新增代码必须同批新增测试）；reviewer 写整合级测试（跨整个代码文件，落 `review/reviewcode/tests/`）+ 审核检测脚本（`review/reviewcode/`）+ 规范性审核。**arbiter push 前跑本模块全量 + 消费方契约测试**（`scripts/gates/run-tests.sh`）；全仓跨模块全量归合入 dev/main 的 CI（J5）。要肉眼审必在报告写出「为何不能代码化」的具体理由。arbiter 开单时主动标出「应代码化的验收项」交 reviewer，审后发现该代码化却肉眼看的记进 worklog，下一轮任务单把补脚本列为硬验收项。
18. **留痕强制 · 落点必须在仓内**：每个任务一条 worklog（`<落点>/worklog/YYYY-MM-DD-<角色>-<任务>.md`，落点＝arbiter `module_docs/`、programmer `code/<子文件夹>/`、项目级角色自己 `docs/`），每次审核一份 reviewreport。**无留痕 = 审核直接打回。** 留痕的落点由 `agents/protocol/report-schema.md` 的 canonical path 表规定；**临时目录、`/tmp`、会话工作目录不是留痕**——会话结束即蒸发的东西不能当证据。派活方在 `sub_reports[].path` 里引用的路径必须是**仓内相对路径**，引用仓外绝对路径 = 该子报告按未产出计。
19. **审核可稀疏，自核必须可重放**：审核轮次不必每轮都开——由 arbiter 或人类按边际收益裁量，**减少审核轮次是被允许的**。但**每一轮免掉的审核，必须以确定性脚本自核顶上**：脚本落 `review/reviewcode/` 并 commit，报告里给出脚本路径与真实输出。"我跑了几条命令核过了"而命令没入仓 = 等于没核。**稀疏化换的是 reviewer 的时间，不是证据强度。**
20. **框架根仓自身也受治理**：clone 下来的框架根不是"配置目录"，它是一个真实的受治理 Git 仓。根仓的任何工作同样适用铁律 1（走 `feat/`/`fix/`/`chore/` 分支，`main` 只经合并门更新）、14（Agent-Attribution）、18（留痕）。**clone 后第一件事是 `./ci/install-ci.sh .` 给根仓自己装门禁**，否则根仓处于"有规范、无门禁"的裸奔状态。

21. **归档与迁移必须走脚本，且归档窗口内禁止回写源仓**：退役任何仓/目录，必须先产出可验证存档（bundle + 校验 + 冒烟还原 + 指纹），再改名留墓碑。**存档生成后到源仓退役前，禁止回写源仓**；确需回写，回写后必须重新生成受影响存档。（已实证事故：归档窗口内回写导致 bundle 少一个 commit，"删除前唯一的保险"是假的。）
22. **不可再生的上游输入必须在第一次被引用时即入仓固化**：铁律 2/7 管的是「不该进仓的东西别进」，本条管的是**「必须进仓的东西别丢」**——上游手册、无法重新生成的资产、迁移源指纹，只要本机只有一份且不可再生，第一次引用时就入仓并留 SHA256。登记为技术债而不入仓 = 违规。
    **且必须落在有远端的仓**——把「本机唯一一份」放进另一个只存在于本机的仓（例如 `new_module.sh`
    刚 `git init` 出来、还没配 remote 的模块仓），保险等于没上。

23. **修缺陷必须一般化到类，并留下断言**：发现一个缺陷时，先问**「这个形状还出现在哪」**——
    只修实例、不扫同类 = 未修完。修完必须在同一逻辑变更里留下**能重现拦住它的断言**
    （`scripts/checks/` 一条、`scripts/gates/` 一条，或 `selftest.sh` 一条），
    否则同一个坑会在下一次重构里原样复活。（已实证：非 ASCII 路径 C 转义的 bug
    在同一轮里被撞见过一次、只修了那一处计数、没扫同类，于是在九个 check 里躺了整轮，
    直到 CFO 撞上才暴露。）**没有断言的修复不算修复，只算这次没炸。**
    **一个高频形状**：`if [ -x <脚本> ]; then …; fi` —— 关键路径上这么写，
    脚本改名或丢失就**静默跳过、然后照常报成功**（比崩溃更糟：崩溃会停下，撒谎让人以为装好了）。
    通则：**关键路径缺文件必须 die；可选件缺失必须打印「已跳过」；唯一不许的是静默跳过后报成功。**

## 文档体系（完整治理）

| 文档 | 时间维度 | 唯一写属主 |
|---|---|---|
| worklog（简短） | 回顾过去：做了什么、为什么 | arbiter → `module_docs/worklog/`；programmer → `code/<子文件夹>/worklog/`；项目级角色 → 自己 `docs/worklog/` |
| report.json | 当下交接：任务终态与升级面（programmer 的＝工作任务书） | 各角色写自己 canonical path（见 report-schema） |
| comm.jsonl | agent 间沟通：发任务/报完成，append-only，**每 agent 一份**（J4） | 实例 `codeagent/<容器>/<编号>/docs/comm.jsonl`；arbiter `codeagent/arbiter/docs/arbiter.jsonl` |
| handoff.md（一页纸） | 面向未来接手者，**每改必核**（checks/14） | 模块级归 arbiter `module_docs/handoff.md`；代码侧归 programmer `code/<子文件夹>/handoff.md` |

全仓 `logs/diary.jsonl`＝脚本事件账本（override 记账、门禁事件），由 `scripts/log_event.sh` append-only，与上表的 agent 沟通 jsonl 是两回事。
arbiter 在接单时设定 `tier`：`simple` 只需 report + commit；`normal` 加 comm.jsonl 沟通留痕；`hard` 再检查 handoff。

## 级联读取顺序

完整治理的读取顺序：根 `AGENTS.md`（一行指针）→ `agents/AGENTS.md` → `agents/CONSTITUTION.md` → 模块 `AGENTS.md` → 模块 `codeagent/<角色>/AGENTS.md`（由 `scripts/new_agent.sh` 生成，边界已填实）。角色卡通常不会被 harness 自动加载，任务单首行必须显式要求先读对应角色卡。
