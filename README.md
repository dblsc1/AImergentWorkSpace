# AImergent workspace starter

这是一个 clone 即用的可迁移项目框架仓：内含项目级规范、架构文档、六份角色/协议基线、CFO 角色卡、模块脚手和无密钥确定性 CI。仓库不包含具体业务模块、项目 findings、运行数据或环境密钥。

## clone 后快速开始

依赖：Bash、Git、GNU coreutils；运行完整 gates 还需 `jq`。Node/Python 只在项目实际有对应代码时需要。

```bash
git clone <your-repository-url> AImergent-workspace
cd AImergent-workspace
./ci/new_module.sh modules/example_module
```

`new_module.sh` 会：

1. 从本仓 `module_template/` 复制骨架。
2. 替换 `{{MODULE_NAME}}` 和 `{{FRAMEWORK_ROOT}}`，使角色卡继续相对引用本框架。
3. 以 `main` 分支初始化模块自己的 Git 仓。

生成的模块是独立仓，starter 根 `.gitignore` 默认不跟踪它。

## 选择工作模式

- **轻量模式**：单人、无外部消费方。在模块 `module_docs/rules.md` 选 `lightweight`，填真实启动/测试命令后直接在 `code/` 工作。
- **完整治理模式**：多人、多模块、有消费方或需要独立审计。选 `governed`，填契约和规则，安装 CI，走 arbiter → worker → reviewagent → merge 流程。

完整说明见 [新模块与新项目开设指南.md](新模块与新项目开设指南.md)。

## 安装确定性 CI

```bash
./ci/install-ci.sh modules/example_module
```

默认以 starter Git 顶层为目标路径根；如模块创建在另一个工作区，显式设置 `AIMERGENT_WORKSPACE_ROOT`。可用 `AIMERGENT_TEMPLATE_ROOT` 覆盖模板源，用 `AIMERGENT_CI_SOURCE` 覆盖 CI 源。所有默认都从脚本所在 Git 顶层推导，不依赖 clone 的绝对路径。

`install-ci.sh` 会安装：

- `.github/workflows/ci.yml`
- `ci/gates/` 与 tracked `ci/hooks/commit-msg`
- 本地 `.git/hooks/{commit-msg,pre-push}`
- 模块缺少 `.gitignore` 时安装通用版

安装本身不要求 remote；未配置 `origin` 时会明确报告“本地模式”。

## 文档入口

- [项目级规范](AGENTS.md)
- [文档地图](docs/manual/文档地图.md)
- [架构与约定](docs/1%20structure/README.md)
- [角色与报告协议](roles/)
- [CI / Git 治理](ci/README.md)

## 迁移安全边界

- 只在 `.env*.example` 中提交变量名和无敏占位，不提交真实凭据。
- 数据、备份、缓存和密钥目录由 env 指定，不写死本机路径。
- starter 不携带任何原项目代码、部署实例、findings/worklog 或 nested `.git`。
