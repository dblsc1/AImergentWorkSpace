# {{MODULE_NAME}} · 对外接口契约

> 本文件是 {{MODULE_NAME}} 对外行为的**唯一事实**。改本文件 = 先提 CR 给项目 arbiter，批准后先改这里、再改代码。只能追加与修订条目，禁止悄悄删除既有承诺。

## 对外 API

| 方法 | 路径 | 入参 | 出参 | 备注 |
|---|---|---|---|---|
| （迁移时填写） | | | | |

## 依赖的外部契约

| 依赖 | 契约位置 | 用途 |
|---|---|---|
| llm_services | `0/llm_services/module_docs/contract.md` | （按需填写） |
| auth_services | `0/auth_services/module_docs/contract.md` | （按需填写） |

## 数据与存储

（本模块拥有哪些数据、落在 `/srv/aimergent/runtime/data/<模块名>/` 何处、备份与清理策略。新增数据文件必须声明属主/备份/清理三件事。）

## 变更记录

| 日期 | CR | 变更 |
|---|---|---|
