# 任务单 · 上游前端原件入仓固化（铁律 22）

- 任务号：`root-upstream-vault-260728-204450-674`
- 执行角色：CFO arbiter（本任务不外派：只做入仓固化，不产生业务代码）
- tier：`normal`
- 分支：`chore/upstream-vault`

## 目标

把 `/home/xia/Downloads/` 下两份**本机唯一、不可再生**的上游前端原件固化进受治理的 Git 仓，
并留下可机械复核的 SHA256 指纹。

本任务**不做部署**。部署方案（建哪个模块、怎么改造）另开任务，前置条件是本任务已完成。
排序理由：原件在入仓前只存在于一个非受控目录，任何"先想方案"的时间都是敞口。

## 可触碰目录（写边界）

- `agents/`（本任务申领的写区路签）

明确不碰：`code/`、`scripts/`、`logs/`、根级文件。

## 自检门

1. `scripts/mission_complete.sh` 全绿（pre-commit 强制）
2. `scripts/gates/run-gates.sh` 全绿
3. `cd agents/reference/upstream && sha256sum -c SHA256SUMS` 全 OK
4. 仓内副本与源文件逐字节相同：`sha256sum` 两侧比对一致
5. `git ls-files` 能列出两份原件（.gitignore 是白名单，会静默吞掉未登记文件）

## 验收标准

| # | 判据 | 确定命令 |
|---|---|---|
| A1 | 两份原件已被 Git 跟踪 | `git ls-files agents/reference/upstream/` 列出 4 个文件 |
| A2 | 指纹自洽 | `cd agents/reference/upstream && sha256sum -c SHA256SUMS` → 2×OK |
| A3 | 与上游源逐字节相同 | 仓内 sha256 == `/home/xia/Downloads/` 侧 sha256 |
| A4 | 行数超限已登记技术债 | `agents/cfo/arbiter/docs/decisions/` 下有对应裁决单 |
| A5 | 导航同步（铁律 11） | `agents/reference/manual/文档地图.md` 含 upstream 一行 |
| A6 | 门禁绿 | `scripts/gates/run-gates.sh` 输出 🟢 |

## 升级条件

- 仓内副本与源文件 SHA256 不一致 → 立即停，报人类（复制过程损坏 = 保险失效）
- 行数门禁在根仓变红 → 停，不得自行新增根级目录绕过（根目录结构受 `.gitignore` 白名单约束）
