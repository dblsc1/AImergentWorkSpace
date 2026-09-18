# hive · handoff（冷启动接手便条）

> **面向未来接手**：只读这一份就能上手，不是历史流水。做完一个任务，检查这里要不要更新。

## 一句话

**蜂巢视图**：纯静态页，读 nexus-core 的 `views/tree|current|gantt|next-actions|review`
与 `events`，写 `planner/{zones,projects,tasks}` 与 `timer/start|stop|cancel`。
零后端逻辑、零构建工具、零 CDN 依赖。规范性定义见 `module_docs/contract.md`。

分区是圆心发散的**角度扇区**（张角 ∝ 项目数），项目是扇区内的六边形格子。
第 d 圈有 6d 个槽位，槽宽 60/d 度；扇区把整圆划开，所以第 d 圈的每个槽位
**只有一个可能的归属分区**——这条唯一性是整个布局的根，改角度相关的代码前先确认它还成立。

## 怎么跑

任意静态服务器指向 `code/frontend/`，打开 `index.html`。
没有后端时 fetch 会失败、页面显示连接失败——**这是预期**，走的是错误分支，不是坏了。
生产由组装层挂路由并要求登录（见 `deploy/`）。

纯逻辑层可以在 Node 里直接 `require()` 单测，不需要浏览器、不需要无头环境
（下面这段在模块根 `modules/hive/` 下跑）：

```js
const H = require("./code/frontend/hex-data.js");
H.buildHoneycomb(tree, ...);
```

UMD 包装、Node 里可 require 的有：`data.js` `gtd-data.js` `rails.js`
`hex-data.js` `hex-audit.js` `hex-ring.js`。其余文件碰 DOM，只能在浏览器里跑。

## 文件分层

**纯逻辑（无 DOM）**

| 文件 | 干什么 |
|---|---|
| `data.js` | 读 `views/tree` 整形；planner 增删改写请求；档案只读 |
| `hex-data.js` | 蜂巢模型：扇区划分、格子分配、分区取色、热度与取序 |
| `hex-audit.js` | 最近完成去重、热度计算、下一步行动归并、时间戳格式化 |
| `gtd-data.js` | 下一步行动 / 回顾两条读端 |
| `rails.js` | 项目卡双轨：把 `{plan, actual[]}` 换算成两条百分比 |
| `hex-ring.js` | 中心格圆环的几何，照 `timer-ring-visual-v1` 规范 |

**渲染与交互（碰 DOM）**

| 文件 | 干什么 |
|---|---|
| `hex-app.js` | 蜂巢主控：拉数据、持有 state、编排下面这一族 |
| `hex-layout.js` | **只做几何**：吃蜂巢模型吐 plan（谁在哪、多大、相机缩多少），再落到 transform |
| `hex-cards.js` | **只做内容**：拿数据吐 HTML 字符串，零 DOM 操作、零事件、零 state |
| `hex-crud.js` | 响应 `hex-cards.js` 生成的 `data-hex-action`，两边靠属性名对齐、不互相 import |
| `hex-borders.js` | 分区边界描边 |
| `hex-timer.js` | 长按开始计时 |
| `hex-center-ctl.js` | 中心格的 完成 / 暂停 / 取消不记录 / 继续 |
| `hex-zone-edit.js` | 分区拖动与编辑 |
| `hex-zone-plan.js` | 分区规划面板（分区改名删除、项目改名移动删除都在这里） |
| `hex-genie.js` `hex-spark.js` `hex-sprout.js` | 出场 / 完成 / 新建的动效 |
| `app.js` `crud.js` `review.js` | 页面外围：状态条、概览统计、计时档案、回顾面板 |

**样式**：`tokens.css`（设计变量兜底块）→ `style.css` → `hex.css` →
`hex-center-ctl.css` / `hex-zone-plan.css` / `review.css`。
`shared/` 是顶栏与站点图标。

## 避坑

1. **载入顺序：`hex-audit.js` 必须在 `hex-data.js` 之前。**
   后者把前者的名字整套 re-export 出去，调用方因此不用关心它们分了家；
   顺序反了就是 `undefined`。`index.html` 里已经排好，别重排。

2. **`maxRing` 是结果，不是上限。** 圈数由项目总数和分区形状自然长出来。
   兜底用 `ringCapFor(total)`（最小的 R 满足 `3R(R+1) ≥ total`，再加几圈余量），
   **不要写死常数**。写死过一次 60：窄扇区里排不上的格子被兜底扔到第 60 圈，
   plan 撑成 9118×8995px，其余格子挤成看不见的一点——表现是「新建一个分区，蜂巢直接消失」。

3. **冷区补贴必须是权重补贴，不是取序补贴。**
   取序版实测是 no-op（前后分配结果逐字节相同）。根因是几何不是顺序：
   一个 27° 的扇区里**根本没有第 1、2 圈的槽心**，再怎么调顺序也排不上。
   所以做法是给排不上第 2 圈的分区一个角度下限（`slotWidthDeg(2)` = 30°），
   迭代到不动点，且总补贴超过整圆的预算上限时整体放弃补贴。
   补贴后如果半径反而变大，回退到不补贴的那一份——这条回归保护别删。

4. **角度算术有两个踩过的浮点坑**（权重从整数变成小数之后才会撞上）：
   - 扇区的 `sweep` 必须从**共享的边界**推出来（`edges[i+1] - edges[i]`），
     不能各自独立算再累加——两条浮点路径会让 `start + sweep` 和下一个 `start` 不相等。
   - `inSector` 不要做 `((angle - start) % 360 + 360) % 360` 的取模往返，
     那会把 47.647058823529406 变成 47.64705882352939。直接加减一次 360 就够。

   两个坑各自都能让**两个分区同时认领 300°**，射线唯一性一破，布局就乱。

5. **「处理完就重写 DOM，同一个事件又命中新 DOM」。** 这个模块踩过三次：
   点一下折叠的格子，展开之后同一次点击又命中了新渲染出来的标题，直接进了改名。
   守卫是记住 `ev.timeStamp` 并在入口处比对。新加交互时想一下自己会不会是第四次。

6. **`localStorage` 在隐私窗口会直接抛。** 读写都包 try/catch，取不到就走默认值。

7. **暂停是纯前端的，取消不写任何事实。** 见 `module_docs/contract.md`。

8. **前端不写 `events`。** 事实账本只追加，由后端在 `timer/stop` 之后自己投。

## 已知技术债

- 运行时标识符仍是 `NexusTable*` / `window.NexusTableHex*`，注释里也还写着 `table`。
  改名会牵动全部互相引用的文件，收益只有可读性，目前**故意不动**——
  真要改就整批一次改干净，不要改一半。
- `hex.css` 978 行、`hex-data.js` 939 行、`hex-app.js` 885 行，都逼近 1000 行。
  下一次往这三个文件里加东西之前先看有没有天然的缝可以拆。
