# vendor · 第三方库（入仓，不走 CDN）

## 为什么入仓而不是 CDN

CDN 挂了页面就废，而这套系统**设计成局域网自持**（nginx + 本机后端，
不假设有外网）。入仓还带来两个好处：版本确定、离线可用。

代价是仓库大了 535K。接受——这是一次性的，且远小于自己手写甘特的成本。

## 清单

| 文件 | 来源 | 版本 | 许可 |
|---|---|---|---|
| `vis-timeline-graph2d.min.js` | npm `vis-timeline` | 8.5.2 | Apache-2.0 OR MIT |
| `vis-timeline-graph2d.min.css` | 同上 | 8.5.2 | 同上 |
| `timeline-arrows.js` | `https://registry.npmjs.org/timeline-arrows/-/timeline-arrows-4.6.0.tgz`（`package/arrow.js`，原文件名改成更直白的 `timeline-arrows.js`，内容逐字节未改） | 4.6.0 | MIT（`LICENSE-timeline-arrows`） |

指纹见 `SHA256SUMS`，校验：`cd vendor && sha256sum -c SHA256SUMS`

## timeline-arrows 4.6.0 · 加载方式（唯二的非 UMD 例外，记清楚）

上游只发布 ES module 源码（`export default class Arrow`），**不像 vis-timeline 那样带
UMD/`standalone` 构建**——npm 包里没有 `dist/`，只有 `arrow.js` 本体。核实过 npm 页面与
tgz 内容，不是下载错文件。

选择：**原样入仓、不改内容**（只改了文件名，`arrow.js` → `timeline-arrows.js`，更贴模块名；
指纹按入仓后的文件名算，改名不算改内容），用浏览器原生 `<script type="module">` 加载——
这仍是**零构建**（ES module 是浏览器内置能力，不经打包器），不违反 D12。

`<script type="module">` 会被延迟执行（等价 `defer`），在经典 `<script>`（`gantt-view.js`
等）之后才跑，与"先声明先执行"的经典脚本顺序不同——所以桥接脚本（`index.html` 里的内联
`type="module"`）挂完 `window.TimelineArrow` 后必须显式 `dispatchEvent`，`gantt-view.js`
里消费方要么读到已就位的全局，要么监听那个事件再画箭头，不能假设"脚本标签顺序 = 执行顺序"
（真机验证过：如果不等事件，快速网络下箭头有时画不出来）。细节见 `gantt-view.js` 里
`ensureArrowsReady()` 的实现与注释。

## 为什么选 vis-timeline（核实过，不是拍脑袋）

对比 frappe-gantt（56K，更小）：

| 需求 | vis-timeline | frappe-gantt |
|---|---|---|
| 双图层**叠加同一行** | ✅ `stack: false` 原生 | ❌ 一任务一条，要自己 hack |
| 今天红线 | ✅ `showCurrentTime` 内置 | ❌ 无 |
| 拖拽改期 | ✅ | ✅ |
| 挂载 | UMD，`<script src>` 直接用，零构建 | 同 |

frappe-gantt 省 460K，但双图层与红线都得手写——**那正是重复造轮子**，
所以选 vis-timeline。

## 规矩

1. **只读**。要改行为在自己的代码里包一层，不改 vendor 文件——
   改了下次升级就冲突，而且 SHA256 对不上。
2. **升级要改 `SHA256SUMS` 与上表，同一个 commit 内完成**，避免指纹和清单不同步。
3. **不许加第二个做同一件事的库**。要换就换掉，不要并存——
   同一职责两个实现是这个项目今天栽过四次的坑。
