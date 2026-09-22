# gantt · handoff（冷启动接手便条）

> **面向未来接手**：只读这一份就能上手，不是历史流水。做完一个任务，检查这里要不要更新。

## 一句话

**双图层甘特图**：计划与事实叠加在同一行，一根红线（今天）把「已发生」和「未发生」
分开。静态路由 `/gantt/`。纯静态页，唯一的第三方依赖是入仓的 vis-timeline +
timeline-arrows。规范性定义见 `module_docs/contract.md`。

## 怎么跑 / 怎么测

任意静态服务器指向 `code/frontend/`，打开 `index.html`。
没有后端时 fetch 会失败、页面走错误分支展示——这是预期，不是坏了。
生产由组装层挂路由 `/gantt/` 并要求登录。

纯逻辑测试（无 DOM 依赖，只测 `gantt-data.js`），Node 直接跑：

```bash
cd code/frontend && bash run_tests.sh
# 等价于：node gantt-data.test.js
```

独立冒烟（验证 vendor 的 vis-timeline + timeline-arrows 兼容性，不接业务代码/
后端）：静态服务器指到 `code/frontend/` 再打开
`http://127.0.0.1:<port>/tests/smoke-arrows.html`（ES module 走 fetch，
`file://` 下浏览器多半会因 CORS 拒绝 module 脚本，不能直接双击打开）。

## 文件分层

**纯逻辑（无 DOM）**

| 文件 | 干什么 |
|---|---|
| `gantt-data.js` | 数据层：拉取/PATCH、groups/items 整理、依赖箭头判定，无 DOM 依赖，Node 可测 |

**渲染与交互（碰 DOM，只能在浏览器里跑）**

| 文件 | 干什么 |
|---|---|
| `gantt-view.js` | 视图层：绑定 `window.vis`（vendor 挂的全局）与 `window.GanttData`，拉数据 → 建 groups/items → 起 `vis.Timeline` → 图层开关 / 拖拽保存 / 排期弹窗 / 错误展示 |
| `index.html` | 页面骨架 + 图例（图例文字是静态内容，不是 JS 生成） |

**样式**：`tokens.css`（design tokens 兜底块）→ `style.css`。

**第三方**：`vendor/`（只读，见 `module_docs/contract.md`「第三方库」节与
`vendor/README.md`）。

## 接口

规范性定义见 `module_docs/contract.md`。消费两条：`nexus-core.views.gantt.v1`
（读计划+事实）与 `nexus-core.planner.crud.v1`（拖拽保存）。

## 避坑 / 冻结点 / 技术债

1. **两个图层叠在同一行，不是上下两轨。** vis-timeline 要 `stack: false`——
   默认 `true` 会把它们排成上下，那不是要的效果。
2. **三个视觉通道，不能只靠颜色**：颜色 + 线型（计划虚线/事实实线）+
   填充（计划 20% 半透明/事实实心）。色觉障碍者靠后两个也要能分。
   **图例必须写出线型含义**，不能只放色块。
3. **事实层锁死。** 它来自 append-only 流水账，拖它 = 改历史。
   `editable` 只给计划层。UI 上要一眼看出区别（`cursor: not-allowed`）。
4. **红线用服务端给的 `today`，不许用 `new Date()`。** 客户端时钟/时区飘
   会让"该干的干了没"这个判断基准各人一个答案。
5. **拖拽保存走 `PATCH /api/core/planner/projects/{id}`（任务同理走 tasks/{id}）**，
   **不许为甘特开专用端点**——就算某个需求听起来像"要个批量改日期的接口"也一样。
6. **写完重拉，不做乐观更新**（与 hive/ring 一致）。拖拽体验上会有一点延迟，
   但避免前端维护影子状态——这条纪律不为体验让步。
7. **vendor 只读**。要改行为在自己代码里包一层。改了 vendor 文件下次升级冲突，
   且 SHA256 对不上。
8. **配色沿用 hive**，不自创。多个前端模块该像一个系统。
9. **事实层相邻分段的"视觉连成一组"只合并真连续（0 天间隔）的天**，恰好隔
   1 天不合并——凭空画过渡段会让人误以为那天也有数据，与"事实层是流水账
   一比一映射，不能凭空多画"的原则冲突。
10. **timeline-arrows 用 `<script type="module">` 加载**（上游只发布 ES module
    源码，没有 UMD 构建），会被浏览器延迟到经典 `<script>` 之后执行——桥接脚本
    挂完 `window.TimelineArrow` 后要显式 `dispatchEvent`，消费方要么读到已就位
    的全局，要么监听那个事件再画箭头，不能假设"脚本标签顺序 = 执行顺序"。
    细节见 `gantt-view.js` 里 `ensureArrowsReady()` 的实现与注释。

## 技术债

- vendor 的 527K min.js：minified 后是单行，体积虽大但符合预期，不代表代码
  质量问题。
- 无自动化端到端测试（新模块）；只有 `gantt-data.js` 的纯逻辑单测与
  `smoke-arrows.html` 的第三方库兼容冒烟。
