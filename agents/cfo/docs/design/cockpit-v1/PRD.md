# PRD · 时间驾驶舱前端升级（cockpit-v1）

- 日期：2026-08-08 · 属主：CFO · 状态：**待用户批准**
- 上游：`BRD.md`（目标与范围）、`index.html` 设计稿 v1（视觉与行为基线，已批）
- 本文是**派活的唯一需求源**：任务单只引用本文条目编号，不复述需求。

## 0. 术语

| 词 | 义 |
|---|---|
| 计划紫 / 事实青 | 语义色：planner 台账（意图）/ events 台账（已发生）。全站不可挪用 |
| 双轨 | 同一实体的计划轨 + 事实轨并置，全站签名件 |
| tokens | `/__cockpit/tokens.css` 承载的 CSS 变量集，唯一事实源在契约表 |
| 预设主题色 | 一组完整 tokens 覆盖块（含亮暗两态），用户可在界面选择 |
| 排期 | 给项目/任务设 `plan {start,end}`（日粒度） |
| 依赖 | 任务 A `dependsOn` 任务 B：A 应在 B 之后进行（纯表达，不排程） |

## 1. 信息架构与跨页联动

```
登录 ──► /（302 → 任务页）
顶栏（注入三页）：任务 · 计时 · 甘特 ＋ 录制胶囊 ＋ 主题控件 ＋ 退出
联动：
  L1 录制胶囊全站常显，点击 → 计时页
  L2 任务行播放钮 → 计时页?task=<id>（预选，不自动起表；开始只有计时页一个入口）
  L3 甘特「未定计划」空态 → 任务页
  L4 主题（明暗 + 预设色）localStorage + storage 事件 → 多窗即时同步
  L5 甘特点某天事实块 → 当日档案弹层（读现有档案读端）
```

## 2. 主题系统（F-THEME）

- **F-THEME-1** 明暗：默认跟随系统（`prefers-color-scheme`）；手动切换写
  `localStorage["cockpit-theme"]`（`light|dark|auto`），根元素 `data-theme`。
- **F-THEME-2** 预设主题色：**3 套预设**，每套 = 完整 tokens 覆盖块（亮暗双态都有），
  根元素 `data-accent`，`localStorage["cockpit-accent"]`。
  - 预设不许破坏语义：每套里计划/事实两轨必须可区分，且全组合过对比度表。
  - 预设 v1：`teal`（默认，即设计稿 v1 配色）、`violet`、`amber`（具体值进 tokens 契约表）。
- **F-THEME-3** 多窗同步：监听 `storage` 事件，theme/accent 变更即时生效，无刷新。
- **F-THEME-4** 选择器 UI：顶栏主题按钮弹出小面板：明/暗/跟随 + 三色圆点。
  登录页不注入顶栏 → 登录页只跟随 localStorage/系统，不提供切换。
- **F-THEME-5** 机械校验：对比度脚本按**预设 × 明暗 = 6 态**跑契约对比度表，
  任一组合 <4.5:1（正文）/ <3:1（大字、图形）即红。

## 3. 共享样式架构（F-TOKENS，方案 C，已批）

- **F-TOKENS-1** 唯一事实源：`agents/contracts/design-tokens-v1.md` 的 token 表
  （含 3 预设 × 亮暗全部值 + 对比度下限表）。
- **F-TOKENS-2** nginx-docker 按契约产出 `/__cockpit/tokens.css`，随顶栏注入
  ring/table/gantt 三页（`sub_filter` 现机制加一行）；login 不注入。
- **F-TOKENS-3** 四个前端仓各留 `:root` 兜底块（照抄契约默认预设），注入版后加载覆盖。
  各仓一条 check：兜底块 diff 契约表，漂移 commit 即拦。
- **F-TOKENS-4** **禁裸 hex**：模块 CSS/JS 里颜色只准 `var(--…)`；唯一豁免是兜底块本身。
  机械判据随 M2 落地，进各仓 reviewcode。

## 4. 顶栏 v2（F-NAV，属主 nginx-docker）

- **F-NAV-1** 结构：品牌 · 三页签（当前页事实青下划线）· 录制胶囊 · 主题控件 · 退出。
- **F-NAV-2** 录制胶囊：读 `/__cockpit/current`（10s 轮询不变）。
  **先判 `degraded` 再读 `running`**：降级 → 琥珀「状态未知」；running → 青点脉动 + 任务名 + 时长。
  点击 → 计时页。reduced-motion 时不脉动。
- **F-NAV-3** 窄容器（<560px）：胶囊缩为点+时间，品牌缩为图标，页签保留短词，不折行。
- **F-NAV-4** 退出：POST `/api/auth/logout` → 跳登录页。
- **F-NAV-5** 主题控件：见 F-THEME-4。

## 5. 登录页（F-LOGIN，属主 auth）

- **F-LOGIN-1** 设计稿 §2 落地：品牌 + 单口令框 + 「进入」。
- **F-LOGIN-2** 错误只一句「口令不对，再试一次。」，出现在字段下方；成功回跳来路页。
- **F-LOGIN-3** 自带兜底 tokens（不依赖注入）；跟随 localStorage/系统主题。

## 6. 计时页（F-RING，属主 ring）

- **F-RING-1** 仪表圆环合并（设计稿 §3）：环分段 = 今日各任务事实（同色深浅，深 = 当前任务），
  表芯 = 计时器。空闲态表芯显「今天 · 总分钟」+ 大按钮「开始计时」。
- **F-RING-2** 控件：两级选择（项目 → 任务，零任务项目灰提示行，保留现行为）；
  录制中锁定选择器；「停止并记录」= timer.stop；「取消，不记录」= `POST /api/core/timer/cancel`
  （后端已上线，本轮补按钮 + 确认弹层：取消不可恢复，需确认）。
- **F-RING-3** 秒跳本地自增；环分段仅数据变化时重绘（保持已修行为）；
  分段动画仅首载 350ms，reduced-motion 直接就位。
- **F-RING-4** URL 预选：`?task=<id>` 预选项目与任务；id 无效则忽略并按空闲态展示。
- **F-RING-5** 布局：≥640px 容器左表右控；窄容器上下叠，环 78cqw。

## 7. 任务页（F-TABLE，属主 table）

- **F-TABLE-1** 项目卡双轨（设计稿 §4）：紫轨 = 计划期已走比例；青轨 = 有事实天数比例。
  读现有投影，无后端增量。计划过期 → 琥珀「已结束」章，不标红。
- **F-TABLE-2** 任务行播放钮 → L2 跳转。
- **F-TABLE-3** 任务编辑弹窗增量：任务排期字段（`plan.start/end`，可空）
  + 「前置任务」多选（写 `dependsOn`）——**拖线的兜底编辑路径**（触屏/键盘可达性）。
- **F-TABLE-4** 现有功能全保留只换皮：CRUD 弹窗、断网横幅、flags 展示、计时档案。
- **F-TABLE-5** ≥700px 容器项目卡两列。

## 8. 甘特页（F-GANTT，属主 gantt；引擎 vis-timeline 不换）

- **F-GANTT-1** 行结构：嵌套分组 时区 → 项目 → 任务行；默认折叠到项目层，展开见任务条。
- **F-GANTT-2** 图层：项目计划条（紫）、项目事实块（青，高度=当日时长分档）、
  任务计划条（细紫）、任务事实块（细青）。项目层现有拖拽改排期保留；
  任务计划条同样可拖（PATCH task.plan）；事实层一律锁死（现行为）。
- **F-GANTT-3** 依赖箭头：vendor `timeline-arrows`（MIT，SHA256 固化）。
  箭头 = 任务级 `dependsOn`。样式走 tokens（禁裸 hex）。
  - 正常：次级墨色细线；**冲突（A.plan.start < B.plan.end）：琥珀**；
    任一端未排期：灰虚线（不判冲突）。
  - **M4 第一件事**：与 vis-timeline 8.5 兼容冒烟，不合就 fork 入仓。
- **F-GANTT-4** 拖线编辑：任务条 hover/长按出连接手柄，拖至目标任务条建立依赖；
  点击箭头 → 弹层（显示 A→B、冲突说明、删除按钮）。跨项目允许。
  兜底路径 = F-TABLE-3 弹窗多选。
- **F-GANTT-5** 缩放：日/周/月三档按钮 + 滚轮平滑缩放（vis 原生）；今天线常显。
- **F-GANTT-6** L5 当日档案弹层：点项目/任务的事实块 → 当天该对象的计时段列表。
- **F-GANTT-7** 未定计划空态：行内灰字 + 链到任务页（L3）。

## 9. 数据与契约增量（F-API，属主 nexus-core；**契约先行**）

- **F-API-1** `TaskOut` 增字段（planner 台账，不碰 events）：
  ```jsonc
  { …现有字段…,
    "plan": { "start": "YYYY-MM-DD", "end": "YYYY-MM-DD" } /* 可为 null */,
    "dependsOn": ["t_…"] /* 默认 [] */ }
  ```
- **F-API-2** 校验（PATCH/POST，全部指名道姓报错，禁静默回退）：
  | 情形 | 响应 |
  |---|---|
  | `plan.end` < `plan.start` | 400 |
  | `dependsOn` 含不存在的任务 id | 400，说明哪个 |
  | `dependsOn` 含自身 | 400 |
  | `dependsOn` 成环（DFS 全任务图） | 400，给出环路径 |
  | 删除被依赖的任务 | **409** + 指名有哪些任务依赖它（与既有「拒绝级联」语义一致） |
- **F-API-3** 排期冲突**不校验不阻止**（用户裁决：琥珀警示是展示层的事，无自动排程）。
- **F-API-4** 任务级日事实读端：甘特任务行需要「任务 × 日 → 分钟」。
  只读增量（新投影或扩展 `proj_daily_stats`，形状由 nexus-core arbiter 定），
  改投影必须带重建脚本口径（契约「投影重建」既有条款）。
- **F-API-5** 契约版本：nexus-core 契约 bump 一版收录 F-API-1..4；
  gantt/table 契约的 consumes 声明同步。**批准后先改契约再动代码**（铁律 4）。

## 10. 非功能需求

| 类 | 要求 |
|---|---|
| 可达性 | 对比度矩阵机械校验（F-THEME-5）；触控 ≥44px；键盘焦点环全程可见；拖线必有弹窗兜底；reduced-motion 全站尊重 |
| 性能 | 轮询间隔不变（navbar 10s / ring 7s）；无 CDN、零外部字体；甘特任务多时用折叠 + cluster 控密度 |
| 离线 | 第三方库 vendor + SHA256SUMS（现例：vis-timeline；新增：timeline-arrows） |
| 许可 | MIT / Apache-2.0；禁 GPL、禁商业 PRO 依赖 |
| 安全 | 会话/口令机制不动；无新密钥；写入口仍只经注册入口（前端不涉足后端） |
| 兼容 | 容器查询与 :has 可用（自家设备现代浏览器）；390px 容器为最窄一等公民 |

## 11. 验收清单（机械优先，任务单引用编号）

| # | 判据 | 方式 |
|---|---|---|
| A1 | 对比度矩阵 6 态全绿 | 脚本（nginx-docker reviewcode） |
| A2 | 模块 CSS/JS 无裸 hex（兜底块豁免） | 脚本（各仓 reviewcode） |
| A3 | 兜底块 == 契约表 | 脚本（各仓 check） |
| A4 | E2E 零 console 错误、`</body>` 锚点唯一 | 既有判据保持 |
| A5 | 390px 容器四页不横滚、顶栏不折行 | Playwright 视口脚本 |
| A6 | 依赖 CRUD：建/删/环拒绝/删除 409 | nexus-core pytest + gantt 整合测试 |
| A7 | 拖线建依赖 + 弹窗删依赖 + 冲突琥珀 | gantt E2E |
| A8 | 主题三控件（明暗/预设/跟随）多窗同步 | E2E 双页签 storage 断言 |
| A9 | 取消按钮：确认后无新事实记录 | ring E2E 对档案计数 |
| A10 | timer 全链路回归（停止并记录 → 三页数字一致） | 既有 E2E 扩展 |

## 12. 派活切分与档位（M 对应 BRD §7）

| 模块 | 条目 | 档位 |
|---|---|---|
| CFO | 契约三份（tokens / nginx v0.6 / nexus-core 增量） | —— |
| nginx-docker | F-TOKENS-2、F-NAV-*、F-THEME-4/5、A1 | normal |
| nexus-core | F-API-*、A6 | normal |
| ring | F-RING-*、A9 | normal |
| table | F-TABLE-*、A2/A3 自查 | normal |
| gantt | F-GANTT-*、A6/A7 整合侧 | **hard**（拖线 + 箭头 + 嵌套分组） |
| auth | F-LOGIN-* | simple |

派单模型档位遵既有裁决：默认 Sonnet，纯机械给 codex，判断类才升 Opus。

## 13. 开放问题（实现轮再定，不阻塞批准）

- O1 任务级日投影具体形状（nexus-core arbiter 定，契约先行）。
- O2 拖线触屏手柄的具体交互（长按时长、手柄大小）——gantt agent 出两案 CFO 择一。
- O3 预设 violet/amber 的具体色值（CFO 出值，对比度矩阵说了算）。
