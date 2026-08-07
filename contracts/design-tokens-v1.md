# design-tokens-v1 · 驾驶舱设计 tokens 跨模块契约

- 版本：v1.0（2026-08-08）· 属主：CFO · 依据：PRD cockpit-v1 §2/§3（用户已批准）
- 消费方：nginx-docker（产出并注入 `/__cockpit/tokens.css`）、ring / table / gantt / auth
  （各留兜底块）、对比度校验脚本（A1）、兜底漂移校验（A3）
- **本表是全站颜色与主题行为的唯一事实源。** 改色先改这里，再改 tokens.css，
  最后机械同步各仓兜底块（铁律 4 / 11）。

## 1. 三条轴

| 轴 | 取值 | 载体 | 持久化 |
|---|---|---|---|
| 明暗 | `light` / `dark`（默认跟随系统） | `html[data-theme]` | `localStorage["cockpit-theme"]`（`light|dark|auto`） |
| 预设主题色 | `teal`（默认）/ `violet` / `amber` | `html[data-accent]` | `localStorage["cockpit-accent"]` |
| （模块自留） | 甘特「显示已完成」 | —— | `localStorage["cockpit-gantt-showdone"]`（归 gantt，登记于此防撞名） |

多窗同步：三者变更均靠 `storage` 事件即时生效，无刷新。

## 2. 语义规则（规范性）

- **计划紫 / 事实青是信息，不是装饰**：`--plan*` / `--fact*` 表达两本台账，
  **不随预设主题色变**。换预设换的是 `--accent*` 族——按钮、页签下划线、品牌记号、
  焦点这类「机身颜色」。把台账色做成可换，等于允许把数据重新贴标签。
- 默认预设 `teal` 的 accent 取值 == 事实青族（保留「按下去就是在产生事实」的语义）；
  `violet` / `amber` 预设下 accent 与图表中的计划紫 / 警示琥珀同族——
  **机身 ≠ 数据标记**，上下文不同，允许同族（本裁决记档，夜班 CFO 自决）。
- 模块 CSS/JS **禁裸 hex**，只准 `var(--…)`；唯一豁免：兜底块本身（A2）。

## 3. token 表（规范性 · 数值即法律）

对比度列 = 实测值（WCAG 相对亮度法），门限列 = 校验脚本红线。
面板亮 `#FFFFFF` / 面板暗 `#151C2E` 为文本对比的基准底。

### 3.1 中性（随明暗轴，与预设无关）

| token | light | dark | 用途 |
|---|---|---|---|
| `--bg` | `#F3F5F8` | `#0D1220` | 页底 |
| `--panel` | `#FFFFFF` | `#151C2E` | 卡片 |
| `--panel-2` | `#EDF0F5` | `#1D2639` | 输入框、轨道底 |
| `--line` | `#D9DFE8` | `#26314A` | 边线 |
| `--line-2` | `#E7EBF1` | `#202A40` | 弱分隔线 |
| `--ink` | `#17202E` | `#E8ECF4` | 正文（16.37 / 14.33 ≥4.5） |
| `--ink-2` | `#55627A` | `#9DA9BF` | 次级（6.15 / 7.16 ≥4.5） |
| `--ink-3` | `#8B96A9` | `#5F6C84` | 禁用、占位（不承诺对比度） |
| `--focus` | `#1E5FD0` | `#6FA8FF` | 焦点环（5.34 / 7.76 于 --bg ≥3） |
| `--shadow` | `0 1px 2px rgba(23,32,46,.06), 0 4px 16px rgba(23,32,46,.07)` | `0 1px 2px rgba(0,0,0,.4), 0 4px 20px rgba(0,0,0,.35)` | 卡片投影 |

### 3.2 语义（随明暗轴，与预设无关）

| token | light | dark | 对比度（亮/暗 于 --panel） | 门限 |
|---|---|---|---|---|
| `--plan` | `#6D5AE6` | `#9B8CFF` | 4.93 / 6.13 | ≥4.5 |
| `--plan-soft` | `#E9E6FB` | `#2A2A4E` | 条底，不承诺 | —— |
| `--fact` | `#0B7A6F` | `#2FD4C2` | 5.21 / 9.15 | ≥4.5 |
| `--fact-soft` | `#DCF0ED` | `#123B38` | 条底，不承诺 | —— |
| `--fact-ink` | `#FFFFFF` | `#04211D` | 5.21 / 9.12 于 --fact | ≥4.5 |
| `--fact-s1` | `#0B7A6F` | `#2FD4C2` | 图形档 | ≥3 |
| `--fact-s2` | `#37948A` | `#6FB8AE` | 3.64 / 7.39 | ≥3 |
| `--fact-s3` | `#9CCFC8` | `#46706B` | 仅与 s1/s2 相邻使用，装饰档 | —— |
| `--warn` | `#B45309` | `#F0B36A` | 5.02 / 9.18 | ≥4.5 |
| `--danger` | `#C2333B` | `#FF8189` | 5.48 / 7.07 | ≥4.5 |

> ⚠️ v1.0 修正记录：设计稿 v1 头注释里「事实青亮 4.6」是**估算错值**，实测 `#0E8C7F`
> 只有 4.14。本表 `--fact` 亮色已压暗至 `#0B7A6F`（5.21）。估算不是判据，脚本才是。

### 3.3 accent 预设（随预设轴 × 明暗轴）

| 预设 | token | light | dark | 对比度 | 门限 |
|---|---|---|---|---|---|
| `teal`（默认） | `--accent` | `#0B7A6F` | `#2FD4C2` | 5.21 / 9.15 | ≥4.5 |
|  | `--accent-ink` | `#FFFFFF` | `#04211D` | 5.21 / 9.12 于 --accent | ≥4.5 |
|  | `--accent-soft` | `#DCF0ED` | `#123B38` | 不承诺 | —— |
| `violet` | `--accent` | `#6D5AE6` | `#9B8CFF` | 4.93 / 6.13 | ≥4.5 |
|  | `--accent-ink` | `#FFFFFF` | `#14102E` | 4.93 / 6.64 于 --accent | ≥4.5 |
|  | `--accent-soft` | `#E9E6FB` | `#2A2A4E` | 不承诺 | —— |
| `amber` | `--accent` | `#9A5B00` | `#F0B36A` | 5.43 / 9.18 | ≥4.5 |
|  | `--accent-ink` | `#FFFFFF` | `#2A1A04` | 5.43 / 9.10 于 --accent | ≥4.5 |
|  | `--accent-soft` | `#F5E8D5` | `#3A2B12` | 不承诺 | —— |

使用规则：交互件（按钮实心、页签当前下划线、录制胶囊、品牌记号、开关选中态）用
`--accent*`；**图表里的条与块永远用 `--plan*` / `--fact*`**，不用 accent。

### 3.4 字与距（与主题无关，抄设计稿 v1 tokens.css §字/型/距/角/触控/动，不复述）

## 4. 校验（规范性 · A1/A2/A3 的口径）

- **A1 对比度矩阵**：脚本解析本表「对比度/门限」列出现的全部组合 ×（3 预设 × 明暗），
  实算 WCAG 比值，任一低于门限即红。脚本落 `nginx-docker review/reviewcode/`。
- **A2 禁裸 hex**：模块 CSS/JS 颜色字面量零容忍，兜底块（带哨兵注释
  `/* tokens-fallback:begin */ … /* tokens-fallback:end */`）豁免。落各仓 reviewcode。
- **A3 兜底漂移**：兜底块必须与本表生成的标准块**逐字节一致**（含全部 3 预设）；
  diff 脚本落各仓 `scripts/checks/`，commit 时拦。

## 5. 注入与兜底（行为契约，nginx-docker v0.6 收录细节）

- nginx 向 ring/table/gantt 三页注入：`</head>` 前 boot 脚本（读两个 localStorage 键、
  设 `data-theme`/`data-accent`，**不发请求、不摸 documentElement 以外的 DOM、≤10 行**），
  `</body>` 前 `tokens.css` + navbar 资产。锚点判据：每页 `</head>`、`</body>` 各恰好一个。
- login 不注入：自带兜底块（**含全部 3 预设**）+ 自带同款 boot 逻辑。
- 覆盖顺序：注入的 tokens.css 后加载，层叠必胜兜底块；兜底允许滞后于本表一个版本，
  但 A3 会把「滞后」变成显式红——同步是机械活，派 codex。

## 6. 变更流程

1. 改本表（CFO，版本号 bump + 修正记录）
2. nginx-docker 同步 `/__cockpit/tokens.css`（A1 重跑）
3. 四仓兜底块机械同步（A3 变绿）
- 新增预设 = 3.3 加一节 + 全链路重跑，禁止跳过 A1 直接加色。
