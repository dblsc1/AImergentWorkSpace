# CI / Git 治理

本目录只包含可迁移的确定性脚本、hooks、gates 和模块 workflow 模板。它不携带项目历史测试 fixture、部署实例或外部 AI 密钥工作流。

## 组成

- `new_module.sh`：从框架根 `code/_template/` 创建模块，替换模块名/框架相对路径并 `git init -b main`。
- `install-ci.sh`：安装 tracked workflow/gates/hooks 与本地 Git hooks；支持 `--hook-only`，不要求已配置 remote。
- `gates/run-gates.sh`：检查 agent 归属、canonical report、弱默认值、项目指定的禁止路径、文件行数和模块 reviewcode。
- `gates/check-report-schema.sh`：以 PR/main merge-base 为任务边界，检查 embedded-self-v2 与 exact `review_target`。
- `gates/run-tests.sh`：递归发现 `code/` 中的 Node/Python 测试、lint 和 build 入口。
- `hooks/commit-msg`：要求唯一 `Agent-Attribution: <role>@<module>+<task_id>` trailer。
- `hooks/pre-push`：拒绝绕过 merge gate 直推 main，并要求其他 ref 使用 push lease。
- `arbiter-push.sh`：铸造一次性 push lease，执行 fetch-then-push，退出时清理。
- `merge-to-main.sh`：绑定独立 approved 证据、本地 gates/tests 和 PR checks 后 squash 合并。
- `log_event.sh` / `new_task_id.sh`：生成 append-only diary 事件与稳定任务 ID。
- `workflows/ci.yml`：安装到模块 `.github/workflows/ci.yml`，产生 `gates` / `test` 两个 check。
- `module.gitignore`：只在目标模块缺少 `.gitignore` 时安装，不覆盖已有规则。

## 路径参数

脚本默认从自身所在 Git 顶层定位，可用下列 env 显式覆盖：

| env | 作用 |
|---|---|
| `AIMERGENT_PROJECT_ROOT` | starter / 治理框架顶层 |
| `AIMERGENT_WORKSPACE_ROOT` | 目标模块路径的相对根 |
| `AIMERGENT_TEMPLATE_ROOT` | `new_module.sh` 的模板源 |
| `AIMERGENT_CI_SOURCE` | `install-ci.sh` 的 CI 源 |

目标参数只接受 `.` 或由小写 slug 组成的相对路径；绝对路径、`..`、symlink escape 和普通子目录冒充 Git 顶层都会被拒绝。

## 用法

```bash
./ci/new_module.sh modules/example_module
./ci/install-ci.sh modules/example_module
./ci/install-ci.sh --hook-only modules/example_module
./ci/merge-to-main.sh modules/example_module feat/example-task
```

如模块位于另一工作区：

```bash
AIMERGENT_WORKSPACE_ROOT=/path/to/workspace ./ci/new_module.sh modules/example_module
AIMERGENT_WORKSPACE_ROOT=/path/to/workspace ./ci/install-ci.sh modules/example_module
```

## 边界

- workflow 只使用 GitHub 自带的短期仓读取凭据，不需要业务密钥。
- 安装了 hooks/CI 不等于已启用远端 branch protection；服务端硬保护必须单独核实。
- `merge-to-main.sh` 会发生远端写入，只在用户已授权、remote 与审核证据都已配置时运行。
