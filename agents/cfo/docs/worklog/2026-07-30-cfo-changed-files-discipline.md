# worklog · changed_files 改为从 git 计算（修 M1）

- 日期：2026-07-30
- 角色：CFO
- tier：`simple`

## 目标

修 reviewer 第 3 轮打回的 M1，并把成因（手写字段）从根上去掉。

## 可触碰目录（写边界）

`agents/`

## 自检门

1. `./scripts/mission_complete.sh`
2. `changed_files` 与 `git show --name-only HEAD` 的集合一致

## 验收标准

| # | 判据 | 确定命令 |
|---|---|---|
| M1a | `changed_files` 覆盖本笔全部本角色文件 | 与 `git show -z --name-only` 对齐 |
| M1b | `task` 反映本轮而非上上轮 | 读 `report.json` |

## 问题

`changed_files` 声明的是**上一轮那 6 项**，本轮三个交付物一个没进；`task` 还停在上上轮。

reviewer 指出这是**第三轮连续落在同一格**：

```
R1 整轮无 report → R2 漏 docs_reviewed → R3 漏 changed_files + task
```

**形状一样：正文写得足，结构化字段落后于正文。**

它用了我自己在 N4 里写的那句话来判我：**下一个人读的不是 summary 散文，是 `changed_files`。**

而机械核验抓不到这个 —— `diff_mode: contains` 只校验「**声明的都在 diff 里**」，
**漏声明是抓不到的**。这正是需要人看的那一格，也是 reviewer 这个角色存在的理由。

## 处置：不是「这次记得写全」，是不再手写

三轮同一格，说明「下次记得」这条路已经走不通了。本轮起 `changed_files` **从 git 计算**：

```python
out = git show -z --name-only --format= HEAD
files = sorted(set(prev_commit_files) | {本笔新增的文件})
```

**为什么这算修到根**：手写是「凭记忆枚举」，而记忆恰恰是三轮都失效的那个环节。
从 git 取值把它变成机械操作 —— 与 reviewer 提的框架级修法
（加一条「changed_files 必须覆盖本次提交里属于本角色的全部文件」的检查）同一方向，
只是我先在自己这一侧做掉，不等框架。

## 一条更普适的读法

reviewer 把我 worklog 里那句「**教训只活在散文里就没有约束力，只有闸门不会忘**」
抄进了它的详报和清单，并指出它的适用范围比那一条大：

> 本框架里凡是「下次记得」都该读成「**尚未修复**」。

M1 正是这句话的又一个实例 —— 我在 N2 里刚写下这个道理，
同一轮的另一格就在犯它。

## 依赖漂移说明

无。只改报告字段取值方式与留痕。
