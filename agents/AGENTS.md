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
│   ├── roles/                #   六份项目级角色卡
│   ├── cfo/                  #   CFO arbiter / consulter：角色卡 + 各自 docs/
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

## 模块统一模板

```text
<module>/
├── AGENTS.md  CLAUDE.md
├── codeagent/
│   ├── arbiter/     AGENTS.md CLAUDE.md docs/
│   ├── frontend/    AGENTS.md CLAUDE.md docs/
│   ├── backend/     AGENTS.md CLAUDE.md docs/
│   └── reviewagent/ AGENTS.md CLAUDE.md docs/
├── module_docs/
│   ├── contract.md
│   ├── rules.md
│   ├── reviewlog.md
│   ├── report.md
│   └── handoff.md
├── code/
│   ├── backend/
│   └── frontend/
└── review/
    ├── reviewcode/
    └── reviewreport/
```

核心原则：`codeagent/` 只放规范与文档，代码只在 `code/`，审核脚本和详报只在 `review/`。

## 角色与写边界（完整治理）

| 角色 | 可写 | 只读 / 禁止 |
|---|---|---|
| 模块 arbiter | `module_docs/`（`reviewlog.md` 除外）、任务单、自己 docs | 禁写 `code/`、`review/` |
| programmer | `code/`、自己 docs | 契约只读；禁写 `review/` |
| programmer_reviewer | `review/`、自己 docs | `code/` 只读；禁修业务代码 |
| module_reviewer | `review/reviewreport/`、自己 docs | 全模块只读；**只审规范面，不替 arbiter 做技术判断** |
| CFO arbiter | 项目规范、跨模块契约关系、协调台账 | 禁亲写模块业务代码 |
| CFO consulter | 自己的评审留痕、必要的规范纠错 | 禁执行业务实现或代替 CFO 裁决 |

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
10. **提示词唯一事实**：`AGENTS.md` 是规范源；如需 Claude Code 兼容，同目录 `CLAUDE.md` 只保留 `@AGENTS.md`。
11. **改动即同步文档**：架构、目录、接口或配置变化必须在同一逻辑变更中更新直接文档和所有导航/索引，不得只改一处。
12. **canonical report（完整治理）**：每个 agent 在 `agents/protocol/report-schema.md` 指定的路径产出已提交、可重放的 `report.json`；人类详报不替代 canonical report。**未被 Git 跟踪、工作区脏、或落在仓外/临时目录的 report 一律按「未产出」处理。** 派活方（CFO / arbiter）回收子代理时**必须用 Git 核验**（文件在仓内、已 commit、内容与交接一致），**不接受口头「已写报告」**，也不接受指向仓外路径的报告引用。
13. **文档四件套（完整治理）**：worklog / report.json / diary / handoff 分别表达过去叙事、当下交接、机器事件和未来接手，各一写属主，不重复。
14. **commit 唯一归属（完整治理）**：每个新 commit 有且只有一个 `Agent-Attribution: <role>@<module>+<task_id>` trailer，三段使用可解析的小写 slug。
15. **一分支一活跃写者（完整治理）**：派活前基于已验证的远端 tip；交接前确认 candidate 真实落地；push 采用 fetch-then-push，非 fast-forward 拒绝是最后兜底。
16. **P0 隐患必须绑定 fix owner**：安全、数据、未受控写入或破坏回滚级问题，必须指定属主并阻断受影响工作，直到修复或有效止血；归档不等于缓解。
17. **审核代码化优先（完整治理）**：可机械核验的事实一律脚本化，肉眼只审判断题。reviewer 第一职责（永久）＝写审核检测脚本（`review/reviewcode/`）并运行、分析输出下判；要肉眼审必在报告写出「为何不能代码化」的具体理由。arbiter 开单时主动标出「应代码化的验收项」交 reviewer，审后发现该代码化却肉眼看的记进 worklog，下一轮任务单把补脚本列为硬验收项。
18. **留痕强制 · 落点必须在仓内**：每个任务一条 worklog（`docs/worklog/YYYY-MM-DD-<角色>-<任务>.md`），每次审核一份 reviewreport。**无留痕 = 审核直接打回。** 留痕的落点由 `agents/protocol/report-schema.md` 的 canonical path 表规定；**临时目录、`/tmp`、会话工作目录不是留痕**——会话结束即蒸发的东西不能当证据。派活方在 `sub_reports[].path` 里引用的路径必须是**仓内相对路径**，引用仓外绝对路径 = 该子报告按未产出计。
19. **审核可稀疏，自核必须可重放**：审核轮次不必每轮都开——由 arbiter 或人类按边际收益裁量，**减少审核轮次是被允许的**。但**每一轮免掉的审核，必须以确定性脚本自核顶上**：脚本落 `review/reviewcode/` 并 commit，报告里给出脚本路径与真实输出。"我跑了几条命令核过了"而命令没入仓 = 等于没核。**稀疏化换的是 reviewer 的时间，不是证据强度。**
20. **框架根仓自身也受治理**：clone 下来的框架根不是"配置目录"，它是一个真实的受治理 Git 仓。根仓的任何工作同样适用铁律 1（走 `feat/`/`fix/`/`chore/` 分支，`main` 只经合并门更新）、14（Agent-Attribution）、18（留痕）。**clone 后第一件事是 `./ci/install-ci.sh .` 给根仓自己装门禁**，否则根仓处于"有规范、无门禁"的裸奔状态。

21. **归档与迁移必须走脚本，且归档窗口内禁止回写源仓**：退役任何仓/目录，必须先产出可验证存档（bundle + 校验 + 冒烟还原 + 指纹），再改名留墓碑。**存档生成后到源仓退役前，禁止回写源仓**；确需回写，回写后必须重新生成受影响存档。（已实证事故：归档窗口内回写导致 bundle 少一个 commit，"删除前唯一的保险"是假的。）
22. **不可再生的上游输入必须在第一次被引用时即入仓固化**：铁律 2/7 管的是「不该进仓的东西别进」，本条管的是**「必须进仓的东西别丢」**——上游手册、无法重新生成的资产、迁移源指纹，只要本机只有一份且不可再生，第一次引用时就入仓并留 SHA256。登记为技术债而不入仓 = 违规。
    **且必须落在有远端的仓**——把「本机唯一一份」放进另一个只存在于本机的仓（例如 `new_module.sh`
    刚 `git init` 出来、还没配 remote 的模块仓），保险等于没上。

## 文档体系（完整治理）

| 文档 | 时间维度 | 唯一写属主 |
|---|---|---|
| worklog | 回顾过去：做了什么、为什么 | 各角色写自己 `docs/worklog/` |
| report.json | 当下交接：任务终态与升级面 | 各角色写自己 canonical path |
| diary / jsonl | 机器事件流水 | `scripts/log_event.sh` append-only |
| handoff | 面向未来接手者 | 模块 arbiter 独占 `module_docs/handoff.md` |

arbiter 在接单时设定 `tier`：`simple` 只需 report + commit；`normal` 加 diary；`hard` 再检查 handoff。

## 级联读取顺序

完整治理的读取顺序：根 `AGENTS.md`（一行指针）→ `agents/AGENTS.md` → `agents/CONSTITUTION.md` → 模块 `AGENTS.md` → 模块 `codeagent/<角色>/AGENTS.md`（由 `scripts/new_agent.sh` 生成，边界已填实）。角色卡通常不会被 harness 自动加载，任务单首行必须显式要求先读对应角色卡。
