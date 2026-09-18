# hive · 对外接口契约

> 本文件是 hive 对外行为的**唯一事实**。先改这里、再改代码，顺序不可颠倒。
> 破坏性变更必须先评估消费方并留变更记录，禁止悄悄删除既有承诺。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: hive.honeycomb-view.v1
    summary: >
      蜂巢视图页面。纯静态页，无构建工具、无 CDN 依赖。分区是圆心发散的角度扇区，
      项目是扇区内的六边形格子；鼠标悬停放大、点开展开为卡片、中心格承载计时。
      静态路由由组装层决定（默认 /），入口文件 code/frontend/index.html。
  - id: hive.zone-order.v1
    summary: >
      分区排序的本机记忆，键 `nexus.hex.zoneOrder.v1`（localStorage，JSON 数组，
      元素是 zoneId）。**仅本机偏好，不是事实**——后端不知道它的存在，清掉只会
      回到默认顺序。别的模块要读可以读，但不得依赖它存在。

consumes:
  - id: contracts.design-tokens.v1
    contract: ../../../contracts/design-tokens-v1.md
    purpose: >
      颜色一律 `var(--…)`，零裸 hex；唯一豁免是 tokens.css 里哨兵注释包着的兜底块。
      **分区色阶 `--zone-1..--zone-12` 目前还不在那份契约里**，hex.css 有一块临时
      兜底定义（同样被哨兵注释包着，整块可删）。hex-data.js 只产出变量名、不产出
      色值，所以色阶进契约并落到 tokens.css 之后，删掉那个兜底块即可，JS 一行都不用改。

  - id: contracts.timer-ring-visual.v1
    contract: ../../../contracts/timer-ring-visual-v1.md
    purpose: >
      **跨模块视觉规范，不是 API。** 中心格的圆环是这份规范的第二个实现（第一个是
      ring）。两处对不上就是全站不一致，规范说「实现与规范不一致时，改的是实现」。
      改中心格圆环之前先读那份规范。

  - id: nexus-core.views.tree.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      蜂巢的主数据源：分区 / 项目 / 任务三级结构与 id、name、progress、flags。
      **progress / progressSource 直接渲染，不在前端重算。**
      项目级颜色由前端按 zoneId 从 zone.color 派生——契约故意不提供 project.color。
      flags 只读不解释，本模块不对 flags 的值做任何 if 分支。

  - id: nexus-core.views.current.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: 中心格的计时状态：是否在跑、跑的是哪个项目/任务、会话开始时刻。

  - id: nexus-core.views.gantt.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      项目卡双轨（计划期已走比例 + 有事实天数比例）。这是既有只读投影，
      本模块只是多读一条已经存在的端点，不要求后端加任何东西。
      「今天」用服务端返回的 today，不用本地时钟——客户端时区/时钟不准会让红线飘。

  - id: nexus-core.views.next-actions.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: 六边形卡片里的「下一步行动」。前端只负责忠实渲染，不自己排序、不自己筛。

  - id: nexus-core.views.review.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: 回顾面板的数据源，只读。

  - id: nexus-core.events.read.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      GET /api/core/events，只读展示 `session.completed` 事实（计时档案、最近完成、热度）。
      事实里只有 opaque id（subject.zone/project/task），名字由本模块现查 views/tree
      得到——后端故意不 join 名字。**查不到（已删除）就展示 id 并标注「已删除」，不崩**：
      项目和任务两类 id 都可能被删，按同一套规则兜底，不要只补任务那一种情况。

  - id: nexus-core.planner.crud.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      分区 / 项目 / 任务的增删改，统一入口 `/api/core/planner/{zones|projects|tasks}`。
      **`views/tree`、`views/current` 不在 planner 命名空间下**，路径不同，
      别在"统一改前缀"的时候把两组路径揉到一起。
      PATCH 只带真正改动的字段——后端是 strict 模型，多带一个字段就是 422。
      写成功后由调用方重新 fetchTree 刷新，本模块不做本地乐观更新。

  - id: nexus-core.timer.v1
    contract: ../nexus-core/module_docs/contract.md
    purpose: >
      中心格的三个动作：完成 = POST /api/core/timer/stop，取消不记录 =
      POST /api/core/timer/cancel，继续 = POST /api/core/timer/start {taskId}。
      与 ring 的「停止并记录」/「取消，不记录」是同一批接口，不另开请求面。
      开始计时前若已有任务在跑，无条件先发一次 stop 再 start，**两步串行不并行**。
      **本模块绝不写 events**——事实账本只追加，由后端在 stop 之后自己投。
```

## 两条本模块自己定的语义

**暂停是纯前端的。** 后端没有「暂停」这个概念。暂停 = 一次 `timer/stop`（这一段照常
入账）+ 本机记住「停的是哪个任务」，键 `nexus.timer.paused.v1`，形状见
`contracts/timer-ring-visual-v1.md`。蜂巢页与 ring 同源、**共用这一个键**，
两边都能恢复同一个暂停态。取消则不产生任何事实。

**分区色不是主题色。** 用户显式设过色的分区原样用 `zone.color`（那是用户设定，
契约已豁免它留在 API 内）；没设过的按分区序号取 `var(--zone-N)`。
本模块只决定第几号色，不决定第几号色长什么样——在 JS 里现算 `hsl()`/`oklch()`
同样算运行时生成色值，绕过对比度校验，禁止。
