# 手册（manual）

新人 / 新 agent 从这里进入。[文档地图.md](文档地图.md) 回答“想改什么应读/改哪个文件”；[架构与约定](../1%20structure/README.md) 解释框架为什么这样设计；[通用经验教训.md](通用经验教训.md) 汇总跨项目通用的踩坑与纪律。

## 框架根目录速览

| 路径 | 作用 |
|---|---|
| `AGENTS.md` | 项目级铁律与级联根 |
| `README.md` | clone 后的安装、建模块与验收入口 |
| `docs/` | 文档导航与权威架构说明 |
| `roles/` | arbiter / backend / frontend / reviewagent 基线，以及 orchestration / report-schema 协议 |
| `CFO_agent/` | 项目 arbiter 与架构 consulter 的通用角色卡；不携带任何项目 findings |
| `ci/` | `new_module.sh`、`install-ci.sh`、gates、hooks、workflow 与合并脚本 |
| `module_template/` | 新模块骨架唯一事实源 |
| `<modules>/` | 项目自定的模块容器；示例占位：`modules/example_service/` |

## 模块内部结构

`AGENTS.md` + `codeagent/<角色>/` + `module_docs/` + `code/` + `review/`。完整骨架以 `module_template/` 为准，不在手册里维护第二份列表。

## 项目落地时要替换的占位

- 模块容器与模块清单，例如 `modules/<module-name>/`。
- 平台/共享能力契约的真实 ID，例如 `<capability>.v1`。
- 运行时 data / backup / secret 路径，通过 env 注入，不写死本机绝对路径。
- 如需跨模块依赖索引或变更请求台账，由项目在自己选定的目录中创建，不从 starter 携带实例数据。
