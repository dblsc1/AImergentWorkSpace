# sample-workspace

这里保存新模块 / 新项目的空白脚手架。`module_template/` 是唯一事实源；不要在已生成的副本里反向维护模板。

## 创建

```bash
/srv/aimergent/0/ci/new_module.sh <相对 /srv/aimergent 的目标路径>
```

例如：

```bash
/srv/aimergent/0/ci/new_module.sh 0/functions/foo
/srv/aimergent/0/ci/new_module.sh dev/my_idea
```

脚本会复制 `module_template/`、替换 `{{MODULE_NAME}}`，并以 `main` 分支初始化 Git。

## 选择工作模式

- 轻量模式：适合突发想法、单人开发、没有外部消费方的项目。保留 Git、`.gitignore` 和 `code/`，一个 agent 直接实现并自检；需要被其他模块调用时再补真实 `contract.md`。
- 完整治理模式：适合 `0/` 内正式模块或多人 / 多模块项目。填写契约和规则，安装 CI，按 arbiter → worker → reviewagent → merge 流程交付。

完整说明与验收清单见 [新模块与新项目开设指南.md](新模块与新项目开设指南.md)。
