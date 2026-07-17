# aimergent starter — 框架与思路

可迁移的 web 开发框架 DNA（**框架 + 思路，不含任何项目特定架构**）。clone 下来即可起新模块 / 新项目。

## 内容
- `module_template/` —— 新模块骨架**唯一事实源**（`{{MODULE_NAME}}` 占位）。
- `新模块与新项目开设指南.md` —— 该知会 agent 哪些资料 + 7 条验收清单 + 轻量/重量模式。

## 配套 DNA（当前在母仓 `0/`，迁移 starter 时一并纳入，见指南）
`0/AGENTS.md`（铁律/思路）、`0/docs/manual`（文档地图）、`0/docs/1 structure`（设计说明）、`0/roles/`（四角色基线）、`0/ci/`（门禁工具）、`0/CFO_agent/*/AGENTS.md`（监管角色卡）。

## 用法
```
0/ci/new_module.sh <相对 /srv/aimergent 的路径>   # 复制骨架+填名+git init
```
> 每个项目的**具体架构不同**，starter 只给骨架与方法，不给某个项目的实现。
