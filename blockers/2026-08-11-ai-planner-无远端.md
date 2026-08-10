# 障签 · ai-planner 模块仓无远端，波2-A 全部交付只有本机一份

- 立障：2026-08-11
- 立障方：CFO（波2 里程碑文档审计 P0 发现，CFO 复核属实）
- 判定：**阻塞**——在建起远端并推送验证前，**不得再向 `code/ai-planner` 派新活**
- 波及：`code/ai-planner/` 全部；间接波及 GTD 波2 的 AI 写能力线（F-AI-*）
- fix owner：**CFO**（CONSTITUTION D3：建远端归 CFO）

## 现象

```
$ git -C code/ai-planner remote -v
（空）
```

其余 6 个模块（auth / gantt / nexus-core / nginx-docker / ring / table）都有 `origin`（`dblsc1/cockpit-*`）。
ai-planner 是 2026-08-11 新建的模块，`new_module.sh` 生成时 `git init` 了但没配 remote。

## 为什么是 P0 而不是"待办"

**铁律 22 追加条款明文点名这个形状**：

> 且必须落在有远端的仓——把「本机唯一一份」放进另一个只存在于本机的仓
> （例如 `new_module.sh` 刚 `git init` 出来、还没配 remote 的模块仓），保险等于没上。

ai-planner 里躺着的不是可再生的东西：
- 受控工具层（AI 安全边界的**全部实现**）+ 52 条测试
- contract v0.1（白名单命令面契约）
- codex 驱动适配器 + 真调冒烟的产出
- 两份 canonical report

这台机器掉了，波2-A 一整轮从零重来。**而且它恰好是安全边界那一轮** —— 最不该丢的那份。

## 我的失误（记下来，不粉饰）

这条我在 2026-08-11 凌晨**已经向用户报告过**（"模块仓无 remote，push 待你定"），
但**只报告、没立障、没绑属主、没阻断后续派活**。铁律 16 明说：

> P0 隐患必须绑定 fix owner ……**归档不等于缓解**。

报给人类 ≠ 有人负责。这张便条就是把它从"我说过了"变成"有人欠着"。

## 处置方案

1. 建私有远端 `dblsc1/cockpit-ai-planner`（私有——与其余 cockpit-* 一致；
   本模块不含真实时间安排数据，但仍按既定数据护栏走）
2. `git -C code/ai-planner remote add origin <url>` 并推 `feat/init`
3. **推成功后核验**：`git -C code/ai-planner ls-remote origin` 能看到该分支的 SHA，
   且与本地 `feat/init` 的 SHA 一致（不是"push 命令没报错"就算数）

## 当前拦路

**push 与建仓都被 Claude Code 权限分类器拒绝**（CFO 实测：`git push` 在 ring 模块上重试两次均被拦）。
所以这一步**必须人类执行**。要跑的命令：

```bash
cd /srv/aimergent/sample-workspace-v5-time-management-test
gh repo create dblsc1/cockpit-ai-planner --private
git -C code/ai-planner remote add origin git@github.com:dblsc1/cockpit-ai-planner.git
git -C code/ai-planner push -u origin feat/init
git -C code/ai-planner ls-remote origin   # 核验：SHA 要与本地 feat/init 一致
```

## 待人类裁决的分叉

PRD `gtd-v1` 的开放问题 O5 未决：**ai-planner 是走独立散仓，还是并入 X-cockpit monorepo**。
- 走散仓 = 上面的命令
- 并入 X-cockpit = 在 X-cockpit 里 `git subtree add`，本模块仓退役（走铁律 21 的归档脚本）

**两条路都能解除本障；不选＝继续裸奔。** 在人类选定前，按"阻塞"处理。

## 解除标志

解除标志：`origin` @ `code/ai-planner/.git/config`

（即该模块仓的 config 里出现 remote。现在没有，配好并推送核验后才会有。）

## 归属

CFO。是我建的模块、我发现的问题、我漏了立障。
