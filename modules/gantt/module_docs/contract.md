# gantt · 对外接口契约

> 本文件是 gantt 对外行为的**唯一事实**。先改这里、再改代码，顺序不可颠倒。
> 破坏性变更必须先评估消费方并留变更记录，禁止悄悄删除既有承诺。

## 契约索引声明（provides / consumes）

```yaml
provides:
  - id: gantt.timeline.v1
    summary: >
      双图层甘特图（计划 / 事实叠加同一行），静态路由 /gantt/。纯静态页，
      无构建工具，唯一的第三方依赖是入仓的 vis-timeline（见「第三方库」节）。

consumes:
  - id: contracts.design-tokens-v1
    contract: ../../../contracts/design-tokens-v1.md
    purpose: >
      颜色一律 `var(--…)`，零裸 hex；唯一豁免是 tokens.css 里哨兵注释包着的
      兜底块。视觉基线继承 hive：页面骨架（topbar/brand/board-head/btn/
      status-banner/dialog）沿用 hive 已有的规则与像素值（各模块独立部署，
      无法跨模块引用同一份 CSS，只能各自维护一份一致的抄本）。新增语义色
      （本模块的计划紫/事实青）从 hive 已有的 `--violet` / `--cyan` 取，
      不另发明调色板。

  - id: nexus-core.views.gantt.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      计划与事实两个图层的数据源（GET /api/core/views/gantt）。
      projects[].plan 画计划层；projects[].actual[] 按天聚合画事实层；
      today 由服务端给，画红线——**不用客户端时钟**（时钟/时区飘会让基准各人一个答案）。

  - id: nexus-core.planner.crud.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: >
      拖拽改期保存：PATCH /api/core/planner/projects/{id} {"plan": {...}}，
      同一入口也用于任务：PATCH /api/core/planner/tasks/{id}。
      **走统一入口，不为甘特开专用端点**。写成功后由调用方重新拉取刷新，
      不做本地乐观更新（与 hive/ring 一致）。
```

## 对外 API

| 方法 | 路径 | 入参 | 出参 | 备注 |
|---|---|---|---|---|
| GET | `/gantt/` | 无 | 静态 HTML/JS/CSS | 由组装层的反代/静态托管直接指向本模块的 `code/frontend/` |

## 双图层（规范性 · 本模块的核心）

计划与事实是**两个可独立开关的图层**，**叠加在同一行**（不是上下两轨）。

| | 计划层 | 事实层 |
|---|---|---|
| 数据 | `plan.start` / `plan.end` | `actual[]` 按天聚合 |
| **颜色** | `--violet` `#a985ff` | `--cyan` `#58e8e3` |
| **边框** | **虚线** | **实线** |
| **填充** | **20% 半透明** | 实心 |
| 层序 | 下 | 上 |
| 可编辑 | **可拖拽改期** | **锁死**，`cursor: not-allowed` |

### 为什么三个通道而不是只用颜色

**只靠颜色区分两个图层不合无障碍要求**（色觉障碍者分不出）。
所以同时用**颜色 + 线型 + 填充**三个通道，任意一个通道单独也能区分。
**图例必须写出"虚线=计划、实线=事实"**，不能只放两个色块。

### 为什么事实层锁死

事实来自 append-only 的流水账。拖它 = 改历史。
要修正只能追加修正事件。**UI 上这个区别必须一眼可见**——
不能让人以为拖了事实条就改了记录。

### 红线 = 今天

把时间轴分成有语义的两半：**左边已发生（两层可对照），右边未发生（只有计划）**。
颜色 `--danger` `#ff6578`。取值来自服务端的 `today` 字段，不用客户端时钟。

## 第三方库

`code/frontend/vendor/` 下的 vis-timeline 8.5.2（双许可 Apache-2.0 OR MIT）与
timeline-arrows 4.6.0（MIT），**入仓不走 CDN**——理由与清单见该目录 `README.md`。
只读，要改行为在本模块自己的代码里包一层，不改 vendor 文件（升级会连带更新
`SHA256SUMS` 指纹）。

## 数据与存储

本模块不持有数据，静态资源 + 上述接口即完整功能。无需额外配置 data root。

## 配置与密钥

本模块无需任何环境变量或密钥；`.env.staging.example` 仅作占位说明，供未来
新增配置时同步维护。
