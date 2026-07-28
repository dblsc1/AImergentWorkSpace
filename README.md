# AImergent workspace starter

这是一个 clone 即用的可迁移项目框架仓：内含项目级规范、架构文档、六份角色/协议基线、CFO 角色卡、模块脚手和无密钥确定性 CI。仓库不包含具体业务模块、项目 findings、运行数据或环境密钥。

## clone 后 15 分钟跑通

依赖：Bash、Git、GNU coreutils、`jq`。

```bash
git clone <your-repository-url> workspace && cd workspace
./scripts/install-gates.sh .              # ① 先给根仓自己装门禁 —— 别跳过
git checkout -b feat/<你的第一个主题>    # ② 根仓工作也走分支，main 只经合并门更新
./scripts/selftest.sh                  # ③ 确认 12 条闸门全绿
./scripts/new_module.sh my_module      # ④ 一个参数建模块，建完即可开工
./scripts/dispatch.sh backend code/my_module/codeagent/backend/docs/<任务单>.md
```

> **①②不是可选步骤。** 框架根是受治理仓（`AGENTS.md` 铁律 20），不是配置目录。
> 跳过 ① 则 hooks/CI 全程不生效；跳过 ② 会让报告门禁在空区间上假绿。

## 顶层四分区

```
agents/         AI 工作区：规范正文 · 四份角色卡 · 两份协议 · CFO · 参考文档
code/           代码区：模块骨架 + 各模块
logs/           日志区：INDEX.md、diary.jsonl（事件流水）
scripts/        脚本区：脚手、门禁、hooks、检查项
control-panel/  任务控制台：零依赖 Python 服务，实时看每个 sh 的一举一动
```

根目录只有 `README.md` + 两个**一行指针**（`AGENTS.md` 给 Codex、`CLAUDE.md` 给 Claude Code，
都指向 `agents/AGENTS.md`）。指针必须留在根上——那是两个 harness 的自动发现点，移走等于开工不加载规范。

## 四个角色

| 角色 | 谁建 | 干什么 |
|---|---|---|
| `arbiter` | CFO | 模块仲裁：拆单、派活、维护契约，不写代码 |
| `programmer` | arbiter | 实现，边界由脚手按分区填实 |
| `programmer_reviewer` | arbiter | 审代码 |
| `module_reviewer` | CFO | **审 arbiter 的规范面**：文档规范、有无越权、大任务报告是否合规 |

**派活两种方式**：

```bash
# ① 父 agent 在自己 session 内开子代理（两层，最省）
scripts/dispatch.sh programmer <任务单>          # 生成提示词，用 Agent 工具派

# ② 起独立进程（arbiter 自己是子代理、开不了子代理时用这个）
scripts/run_agent.sh programmer <任务单>         # claude -p --agent，不受嵌套限制
scripts/run_agent.sh programmer <任务单> --resume  # 打回-修复循环用这个，绝不重开
```

建角色一律走脚本，不手写：`scripts/new_agent.sh <角色>` —— 它填实写边界，
并生成 `.claude/agents/<角色>.md`（该子代理的 system prompt），**其中写死了"出生第一条输出必须是考卷"**。

## 闸门长什么样

- **派单层**：`scripts/exam.sh <角色>` 考通用流程，不过退回 arbiter 重新派单。
- **提交层**：`scripts/mission_complete.sh` 挂 pre-commit，六条检查任一不过拒绝提交。
  判据用 `--list` 可枚举，`dispatch.sh` 会把它嵌进派单提示词 —— **开卷，不让人猜**。
  逃生口 `AIMERGENT_MISSION_OVERRIDE="<理由>"`：放行但强制记账，绕过在台账上可见。
- **推送层**：`scripts/arbiter-push.sh` / `scripts/merge-to-main.sh` 一次性握手，绕不过去。

## 控制台（每个 sh 的一举一动）

```bash
python3 control-panel/server.py    # http://127.0.0.1:8787
```

`install-gates.sh` 会自动装成 systemd --user 服务。所有脚本都 source 了
`scripts/lib/emit.sh`，**激活与退出自动上报**，面板用 SSE 实时显示：
时间、脚本名、激活者、简短报告、退出码。**「逃生口使用」那格要盯着** ——
它是唯一一个「规矩被绕过」的可见入口。

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
