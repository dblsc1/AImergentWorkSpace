# worklog · 建 ring / table 两个前端视图模块 + 新增模块配方

- 日期：2026-07-28
- 角色：CFO arbiter
- 任务号：`root-frontend-view-modules-260728-224924-336`
- 分支：`feat/frontend-view-modules`
- tier：`normal`

## 人类裁决

「两个模块，而且需要考虑新增模块的情况。」

前半句定了划分：`code/ring` 与 `code/table` 各自独立仓，贴合 HANDOFF §4 的四条并列静态路由。
后半句定了本任务的真正交付物——**不是两个模块，是"再加第三第四个"的成本**。

## 做了什么

### ① 建了两个模块骨架

```
scripts/new_module.sh ring
scripts/new_module.sh table
```

模块名用视图名本身，与 nginx 路由（`/ring/` `/table/`）和 HANDOFF §1.2 视图表保持一条线。

**门禁做了实测而非采信打印**（`new_module.sh` 那句「门禁 已安装」有前科）：
两个模块的 `.git/hooks/` 下 pre-commit / commit-msg / pre-push 三个都在，
`scripts/gates/` 已就位，`run-gates.sh` 红在 `module_docs/contract.md 还没填实` 上——
**这是正确的失败形状**（待办，不是故障）。D2 的修复确认真实有效。

### ② 把"新增模块"做成配方，而不是留两个参照物

`agents/reference/新增前端视图模块.md`：八步 + 反面清单。

写它的理由：**抄前一个模块是最常见的腐坏方式**——抄的人分不清哪些是本质、
哪些是上一个模块的偶然，于是偶然被复制成惯例。配方里显式点名了这次踩到的三件事：
门禁要看不要信、保险柜只读不回写、豁免绑"谁动谁拆"不绑日期。

### ③ 建了项目级依赖索引

`agents/cfo/arbiter/docs/依赖索引.md`（CFO 角色卡职责 5）。含**反向索引**
「改这个契约要通知谁」——这是它唯一的用途，不是文书工作。

## 待裁决 / 已如实登记为未验证的事

### D4 · 两个模块都没有远端（未验证，不是豁免）

`new_module.sh` 建出来的模块是 `git init` 出来的本地仓，**没有 origin**。
按 `agents/reference/新模块与新项目开设指南.md` 第 71 行的口径：
**未配置远端 ≠ 该项豁免**——此时 `pre-push` hook 一次都不会触发，
铁律 15（fetch-then-push、非 fast-forward 拒绝）**从未被检验却以为已生效**。

拓扑本身不需要我发明：HANDOFF §2.3 已写明「每个可部署单元自己一个仓库/容器/数据库」，
所以是**每模块一个远端**，不是并进根仓。需要人类给的是**授权与落点**
（建在哪个账号/组织下），建远端是对外动作，我不自行执行。

已在依赖索引里把两个模块的远端列如实标成「本地模式·未验证」，不留空白。

### D5 · 供给侧契约还不存在，`consumes` 无处可指

两个前端模块要 consume 的是 nexus-core 的 `/api/core/views/current` 与 `/views/tree`，
但 **nexus-core 还没建模块，没有 `contract.md`**。

这不是形式问题：HANDOFF §7.1 虽然写了响应模型，但 **HANDOFF 是项目总目标、
指导性可改，不是契约**（它自己开头就这么定性）。让两个前端模块的 `consumes`
指向一份会随项目进度改动的指导性文档，等于把契约建在流沙上。

处置已写进依赖索引：在 nexus-core 的 `contract.md` 落地前，
两个前端模块的 `contract.consumes` **如实写成待定**，
不许指向一份还不存在的契约文件充数。

## 依赖漂移说明（检查项 90）

本次未新增/删除任何包依赖。新增了两条**跨模块依赖声明**（`code/ring` 与 `code/table`
各消费 nexus-core 一条 views 读路径），已全部登记进 `agents/cfo/arbiter/docs/依赖索引.md`
的正向表与反向索引。两个模块之间**零依赖**——HANDOFF §2.1 明确四个前端没有任何通信路径，
这是架构决定，不是"还没来得及连"。

未改动任何契约文件（供给侧契约尚不存在，见 D5）。

## 自检结果

| 判据 | 结果 |
|---|---|
| 两个模块骨架已建 | ✅ `code/ring` `code/table` |
| 门禁真实安装（看而非信） | ✅ 三个 hook + gates 就位；红在 contract 待填上 |
| 配方可复用 | ✅ `agents/reference/新增前端视图模块.md` |
| 依赖索引含反向索引 | ✅ `agents/cfo/arbiter/docs/依赖索引.md` |
| 导航同步（铁律 11） | ✅ 文档地图已加两行 |
| 远端状态如实登记 | ✅ 标「本地模式·未验证」，未当作豁免 |
