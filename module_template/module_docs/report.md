# {{MODULE_NAME}} · report.json 模块特色字段（③模块级）

> report.json 三部分之一。①项目级通用见 `{{FRAMEWORK_ROOT}}/roles/report-schema.md`，②角色级见 `{{FRAMEWORK_ROOT}}/roles/<角色>.md`，③本文件=本模块特有需要往报告里加的字段。默认可为空；有特殊需要时在此声明。

## 本模块特色字段
（填写：本模块报告需额外携带的字段。示例：）

- `pitfalls_checked`（迁移类必填）：冻结坑逐条勾选，把"我照搬了"变成可审证据。
  ```json
  "pitfalls_checked": [
    {"pitfall": "存储键必须使用稳定 ID", "ok": true, "evidence": "repository.py:31"},
    {"pitfall": "对外字段命名不可暗改", "ok": true, "evidence": "serializer.py:20"}
  ]
  ```
- `security_reviewed`（有对外端点/密钥的模块）：`true` 且说明覆盖了鉴权/无客户端密钥/输入校验。

（无特殊需要的模块，本文件保留但字段可空。）
