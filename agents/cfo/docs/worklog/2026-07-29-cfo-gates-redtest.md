# worklog · 四模块补装门禁 + 裸提交红测

- 日期：2026-07-29
- 角色：CFO
- tier：`normal`

## 事故背景

`install-gates` 旧版只装 `gates/` + 3 个 hook，**没装 `mission_complete.sh`、
没装 `checks/`、没装 `lib/`**；而 `pre-commit` 写的是
`[ -x "$root/scripts/mission_complete.sh" ] || exit 0`。

**四个模块的每一次提交，着陆检查一次都没跑过** —— 包括我推上
`cockpit-nexus-core` 远端那一次。

这是 `[ -x … ] || exit 0` 那个形状的第 N 次出现，而这次它长在**提交闸门本身**上，
**守着所有别的闸门**。铁律 23 的通则（关键路径缺文件必须 die）正是为它写的。

## 做了什么

四个模块各重跑 `scripts/install-gates.sh`，`checks/13` 新鲜度全部 rc=0。

**注意一个坑**：我第一次装是在 rebase 到新框架 tip **之前**跑的，拷进去的是旧判据。
`checks/13` 当场报「门禁在跑、在绿，但跑的是旧判据 —— 这比没装更隐蔽」。
**必须先 rebase 再装。**

## 红测（没做红测 = 没装好，只是没炸）

每模块造一笔**裸提交**（无 worklog、无 report、无路签），判据取两个：

| 模块 | `git commit` rc | HEAD 是否移动 | 结论 |
|---|---|---|---|
| ring | 1 | 未动 | ✅ 被拦 |
| table | 1 | 未动 | ✅ 被拦 |
| nexus-core | 1 | 未动 | ✅ 被拦 |
| nginx-docker | 1 | 未动 | ✅ 被拦 |

**退出码直取，不走管道** —— `git commit … | tail` 之后的 `$?` 是 `tail` 的，
它永远是 0，会把"被拦"报成"成功"。本轮此前我自己就一直在用管道，
是这次的交接提示词点破的。**两个判据都取是有意的**：rc 可能被壳吞，
`git log` 不会——只认 HEAD 动没动才是硬证据。

## 依赖漂移说明（检查项 90）

无包依赖增删。本次是门禁文件补装 + 留痕，未触碰任何模块业务代码与契约。

## 顺带清掉的账

孤儿留痕树 `agents/cfo/arbiter/docs/`（4 份 worklog）已搬进 `agents/cfo/docs/`
并删除旧目录——**是 `standing-docs` 这条闸门自己把这笔账挂出来的**，
不是我记得要做。两棵树并存时人会打开旧那棵，以为「没更新」。

## 上报：单个文件申领不到写区路签

`mission_start.sh` 的 `norm()` 把写区统一加尾斜杠，`.gitignore` 变成 `.gitignore/`；
验签时 `checks/_common/05` 拿 `.gitignore` 去匹配 `.gitignore/*`，**永远匹配不上**。

后果：**根级单文件（`.gitignore`、`README.md`、`AGENTS.md`）无法合法进入任何提交**。
本次 `contracts/` 的 `.gitignore` 白名单因此没能随同一逻辑变更落地——
而白名单不加，新根级目录会被静默吞掉。这条是真缺陷，不是我越界，故未用逃生口。
