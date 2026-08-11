# 可迁移项目框架 · 架构与约定

> 本文是设计原因的完整说明；项目根 `AGENTS.md` 是同一套规则的可执行精简版。两者冲突时以 `AGENTS.md` 为准。

## 1. 设计目标

这套框架将“项目方法”和“具体业务”分开：starter 只携带规范、模板、角色卡和确定性门禁；clone 后才由项目填写模块清单、契约、运行路径与部署编排。

四个核心目标：

1. clone 到任意路径都能建模块，脚本不依赖作者机器的绝对目录。
2. 契约、代码、agent 文档和审核产物各有唯一位置与写属主。
3. 审核与测试优先用可重现脚本，而不是对话里的主观声明。
4. 小项目可从轻量模式起步，需要时原地叠加角色、契约、审核和合并门，无需重建骨架。

## 2. 框架层与模块层

```text
<framework-root>/
├── AGENTS.md  README.md
├── docs/  agents/roles/  agents/cfo/  scripts/
├── code/_template/
└── <module-container>/
    └── <module>/              # 独立 Git 仓
```

框架层定义共性方法；模块层定义业务契约、技术栈、启动/测试命令和特有禁区。模块角色卡薄引用框架 `agents/roles/`，不重抄通用方法。

`scripts/new_module.sh` 从自身所在 Git 顶层定位 `code/_template/`，用相对路径创建模块，并把模块内的框架引用替换成可解析的相对路径。目标根、模板和 CI 源均可用脚本文档声明的 env 覆盖。

## 3. 模块内部分区

```text
<module>/
├── AGENTS.md  CLAUDE.md
├── codeagent/<role>/          # 角色规范与留痕
├── module_docs/              # contract / rules / report / handoff
├── code/backend/  frontend/  # 业务代码
└── review/reviewcode/  reviewreport/
```

无前端的模块仍保留 frontend 占位，以维持统一工具边界。空目录用 `.gitkeep` 跟踪，避免 clone 后结构丢失。

## 4. 规范级联

| 层级 | 文件 | 作用 |
|---|---|---|
| 框架/项目级 | `<framework-root>/AGENTS.md` | 模式、通用铁律和边界 |
| 项目角色基线 | `<framework-root>/roles/<role>.md` | 角色通用方法 |
| 模块级 | `<module>/AGENTS.md` | 模块职责、模式和流程 |
| 模块角色级 | `<module>/codeagent/<role>/AGENTS.md` | 薄引用 + 模块特有加严 |

任务单必须显式要求实现者先读角色卡，因为角色卡通常不在 harness 的自动祖先加载链上。

## 5. 契约与依赖

- `module_docs/contract.md` 是模块对外行为的唯一事实。
- 契约显式声明 `provides` 和 `consumes`；模块只依赖其他模块的已发布契约，不直连内部实现。
- 完整治理下，模块 arbiter 维护自身契约，项目 CFO 维护跨模块依赖关系和变更路由。
- 轻量模式可在没有消费方时暂缓契约，但首次开放调用前必须补真实内容。

## 6. 可审计交付

完整治理的开发与发布是两层：

1. session 内由 CFO / arbiter 派活，worker 产出代码、测试、worklog 和 canonical report。
2. Git 层由 hooks + gates + CI 检查归属、报告证据、密钥、文件大小与项目测试；独立 approved 后才 squash 到 `main`。

`report.json` 的 Git 证据使用 `embedded-self-v2`：`head="SELF"` 由接收方解析为最后修改该报告的 commit；`base` 取任务目标分支起点；reviewagent 另用 exact `review_target` 绑定被审差异。详细协议见 `agents/protocol/report-schema.md`。

## 7. 路径、密钥与数据

- 脚本从 `git rev-parse --show-toplevel` 和自身位置定位框架，不嵌入作者机器绝对路径。
- 密钥只通过 env / 专用密钥挂载注入；`.env*.example` 只放变量名和无敏占位。
- 运行数据、备份、缓存和构建产物不进 Git。路径由项目配置，不由 starter 假设某个挂载点。

## 8. 升级路径

轻量项目长大时，按以下顺序叠加：

1. 把 `contract.md` 与 `rules.md` 填成真实事实。
2. 激活模块角色卡，建立 canonical reports 和 worklog。
3. 运行 `scripts/install-gates.sh <module-path>` 安装 hooks、gates 和 workflow。
4. 引入独立 reviewagent、exact review target 和 `scripts/merge-to-integration.sh`。
5. 如有多模块契约，建立项目级依赖索引与变更请求台账。
