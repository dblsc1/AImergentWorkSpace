# AImergent workspace starter

这是一个 clone 即用的可迁移项目框架仓：内含项目级规范、架构文档、六份角色/协议基线、CFO 角色卡、模块脚手和无密钥确定性 CI。仓库不包含具体业务模块、项目 findings、运行数据或环境密钥。

## clone 后 15 分钟跑通

依赖：Bash、Git、GNU coreutils、`jq`。

```bash
git clone <your-repository-url> workspace && cd workspace
./scripts/install-ci.sh .              # ① 先给根仓自己装门禁 —— 别跳过
git checkout -b feat/<你的第一个主题>    # ② 根仓工作也走分支，main 只经合并门更新
./scripts/selftest.sh                  # ③ 确认 12 条闸门全绿
./scripts/new_module.sh my_module      # ④ 一个参数建模块，建完即可开工
./scripts/dispatch.sh backend code/my_module/codeagent/backend/docs/<任务单>.md
```

> **①②不是可选步骤。** 框架根是受治理仓（`AGENTS.md` 铁律 20），不是配置目录。
> 跳过 ① 则 hooks/CI 全程不生效；跳过 ② 会让报告门禁在空区间上假绿。

## 顶层四分区

```
agents/   AI 工作区：角色卡、CFO、项目级审核、参考文档
code/     代码区：模块骨架 + 各模块
logs/     日志区：INDEX.md、diary.jsonl、console.html
scripts/  脚本区：脚手、门禁、hooks、检查项
```

根目录只有三份文件：`AGENTS.md`、`CONSTITUTION.md`、`README.md`。

## 闸门长什么样

- **派单层**：`scripts/exam.sh <角色>` 考通用流程，不过退回 arbiter 重新派单。
- **提交层**：`scripts/mission_complete.sh` 挂 pre-commit，六条检查任一不过拒绝提交。
  判据用 `--list` 可枚举，`dispatch.sh` 会把它嵌进派单提示词 —— **开卷，不让人猜**。
  逃生口 `AIMERGENT_MISSION_OVERRIDE="<理由>"`：放行但强制记账，绕过在台账上可见。
- **推送层**：`scripts/arbiter-push.sh` / `scripts/merge-to-main.sh` 一次性握手，绕不过去。

## 控制台

```bash
./scripts/console.sh > logs/console.json   # mission_complete 通过后会自动刷新
python3 -m http.server                     # 然后访问 /logs/console.html
```

## 文档入口

- [项目级规范](AGENTS.md)
- [项目宪法模板（加严条款 + 裁决台账）](CONSTITUTION.template.md)
- [文档地图](agents/reference/manual/文档地图.md)
- [架构与约定](docs/1%20structure/README.md)
- [角色与报告协议](agents/roles/)
- [CI / Git 治理](scripts/README.md)

## 迁移安全边界

- 只在 `.env*.example` 中提交变量名和无敏占位，不提交真实凭据。
- 数据、备份、缓存和密钥目录由 env 指定，不写死本机路径。
- starter 不携带任何原项目代码、部署实例、findings/worklog 或 nested `.git`。
