# nginx-docker · 网关公用件

> 网关的对外接口规范在 `contracts/gateway.v1/contract.md`，本文件只说本模块提供了
> 什么、网关怎么用它。网关的配置本身不在这里：手写的默认组装在
> `deploy/nginx/templates/`，`install.sh add` 生成的在 `deploy/generated/nginx/templates/`，
> 两份都 include / 挂载本模块的文件。

```yaml
provides:
  - id: gateway.v1
    summary: 门片段、顶栏注入片段、共享顶栏与设计 tokens（网关挂载后即满足 gateway.v1）
consumes:
  - id: auth.gate.v1
    contract: ../../../contracts/auth.gate.v1/contract.md
    purpose: 门片段调 /__auth_verify，verify 的 204 可带租户头
  - id: contracts.design-tokens.v1
    contract: ../../../contracts/design-tokens-v1.md
    purpose: static/tokens.css 的数值唯一事实源
  - id: nexus-core.views.current.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 顶栏计时芯片（经网关的 /__cockpit/current）
  - id: nexus-core.views.lanes.v1
    contract: ../../nexus-core/module_docs/contract.md
    purpose: 计时芯片悬停 / 聚焦时弹出的泳道精简预览（v0.3，契约先行，见下「泳道预览」）
  - id: contracts.timer-ring-visual.v1
    contract: ../../../contracts/timer-ring-visual-v1.md
    purpose: 只读 hive / ring 共用的两个本机键 nexus.timer.paused.v1 / nexus.timer.carry.v1（见下「暂停与累计」）
```

## 文件

| 文件 | 挂到网关容器的哪里 | 作用 |
|---|---|---|
| `nginx/gate.inc` | `/etc/nginx/honeycomb/gate.inc` | 登录门片段：未登录跳登录页，转发租户头并覆盖客户端自带的 |
| `nginx/inject.inc` | `/etc/nginx/honeycomb/inject.inc` | 往前端 HTML 注入页签配置、首帧主题脚本、tokens 与顶栏 |
| `static/navbar.js` `navbar.css` | `/__cockpit/` | 共享顶栏：页签、计时芯片、主题面板、退出 |
| `static/tokens.css` | `/__cockpit/tokens.css` | 全站设计 tokens |
| `static/favicon.*` | `/__cockpit/`，另有 `/favicon.ico` `/favicon.svg` | 站点图标（SVG 是几何的唯一事实源） |

## 顶栏页签

`navbar.js` 不写死任何路由：站点前缀读网关注入的 `window.HONEYCOMB_BASE`（缺省 `/`，
计时芯片、登出、登录页地址都从它拼，见 `contracts/gateway.v1` 第七节），页签读
`window.HONEYCOMB_NAV`（地址已含前缀）：

```js
{ home: '/hive/', timer: '/ring/', tabs: [{ href: '/hive/', label: '任务' }, ...] }
```

来自各前端 `module.yaml` 的 `static[].nav`（页签文字）、`home`、`timer`。没装的前端没有页签。
页签顺序 = 安装时的模块顺序。缺省组装（手写的 `deploy/` 与 `install.sh add hive ring assistant`）是三个：
**任务、计时、AI助理**（v0.3 追加第三个，`modules/assistant`；两份组装的 `$honeycomb_nav` 逐字相同，`tools/test_install.py` 核对）。

## 计时芯片即时刷新（v0.2.1）

芯片每 10 秒拉一次 `<前缀>__cockpit/current`。页面自己开始 / 停止 / 取消计时成功后，
在 `window` 上发一个 `honeycomb:timer-changed` 事件（无 detail），顶栏立刻重拉一次，
不必等下一个 10 秒。hive 与 ring 都发；自己接进来的前端要芯片跟得上，也发这一个。

## 暂停与累计（v0.2.5）

后端没有暂停：暂停时 `views/current` 是空闲。芯片**只读**两个本机键（形状见
`contracts/timer-ring-visual-v1.md`，读不到 / 解析失败一律当不存在）：

- 空闲 + 有 `nexus.timer.paused.v1` → 「已暂停」，读数 = 其 `carriedSeconds`；
  否则「未在计时」，读数 `00:00`（此前读数停在上一段最后一秒）。
- 计时中且 `nexus.timer.carry.v1.taskId` 等于在计的任务 → 读数 = `carriedSeconds + 本段`，
  与 hive / ring 一致。每秒现读，不只在拉状态时读一次（「继续」时计时台写累计记忆晚于刷新事件）。
- 芯片从不写这两个键。
- 已暂停时顶栏加属性 `data-ckpt-paused`（v0.2.7，追加；`data-ckpt-timer` 仍是 idle），圆点换成 `--accent` 空心：
  窄屏把字藏了也能和空闲的灰空心点分开。

## 泳道预览（v0.3，契约先行，前端待建）

每个页面都有同一个顶栏，所以「人一条线、代理多条线」的精简版挂在计时芯片上，全站一个样：

- **内容**：人一条线 + **至多 4 条**代理线，最近约 **1 小时**。代理线的挑法：在跑且当前相位是
  `waiting_input`/`waiting_permission` 的在前，其次在跑且 `working`，再次在跑的其他相位，最后按结束时刻最近的已结束运行；
  同档按最近一次相位转入时刻倒序。配色、段的推法、`reply`/`attend` 连线与计时页泳道（`modules/ring` 契约
  `nexus-core.views.lanes.v1` 条）**同一套**，颜色取 `tokens.css`；超出 4 条时末尾一行「还有 N 个 → 计时页」。
  不画在场细带、不显示任何秒数合计（标记，不是时长）。
- **数据**：打开时拉 `<前缀>api/core/views/lanes?date=<今天>`（1 小时窗口跨零点时 `?from=<昨天>&to=<今天>`），
  本地按响应的 `now` 裁出最近 1 小时。**只在预览打开时**约每 15 秒轮询一次，关上即停；页面不可见时也停。
  直接走 `api/core`（用户已登录、在看页面），**不新开 `__cockpit/*` 路由**。非 2xx（含 404：后端早于 v2.4；401）
  → 不弹预览，芯片照旧，不跳登录页。`label`/`agent`/`detail` 只当文本渲染。
- **计时页上不弹**：当前页就是 `HONEYCOMB_NAV.timer` 时芯片悬停什么都不加——那一页已经画了全部泳道，再弹一份是重复。
- **交互与无障碍**：
  - 鼠标：悬停约 150 ms 后打开，移出芯片与预览约 300 ms 后关上（防闪）；点击芯片照旧去计时页。
  - 键盘：芯片获得焦点即打开，`Esc` 关上并把焦点留在芯片；芯片带 `aria-expanded`/`aria-controls`，预览里另有一段
    文字摘要（如「garden：等你批准」）给读屏用，不只靠颜色。
  - 触屏（没有悬停）：第一下点芯片切换预览开 / 关，预览里放一个「去计时页」链接；点预览外面关上。
  - `prefers-reduced-motion` 时不闪。
  - 预览是浮层（不占文档流），打开 / 关上**不引起布局位移**；宽度 `min(360px, 100vw - 32px)`，320px 宽的屏上左右各留 16px、不出横向滚动。
- **共享渲染件**（v0.3 实现时追加）：泳道的画法放在 `static/lanes.js` + `static/lanes.css`，网关照旧在
  `<前缀>__cockpit/` 下服出（不设门、不含数据）。顶栏在预览**第一次打开**时才加载它；计时页（`modules/ring`
  的 `ring-lanes.js`）按 `HONEYCOMB_BASE` 加载同一份——两处的配色、段的推法、连线画法因此只有一处实现。
  对外只挂 `window.HoneycombLanes`；本文件不发请求，全部 `textContent`。
- **在场与人此刻的状态**（2026-10-03 追加，**取代**上面「不画在场细带」那半句；仓主：「我正在干活，为什么不显示」）：
  预览也画 `human.presence`——人那条线分上下两半，上半是计时记下的段（实心），下半是在场（同色系浅填，离开画斜线），
  图例加「在电脑前」「离开」。预览最上加一行人此刻的状态，与计时页人那张卡同一个判定（共享件纯函数 `humanStatus`）：
  各设备里 `to` 落在 `[now − 90 秒, now + 60 秒]` 的在场段有一段不是离开 →「我：在电脑前 · 程序 · 标题」，只有离开 →「我：离开」，
  都没有 →「我：不在线」；在计时时改写「计时中 · HH:MM 起」，优先于前台程序。仍不写任何秒数合计；挑法、至多 4 条、
  列表式（不是计时页的卡片）都不变。共享件另导出 `sortByActivity`/`activeSeconds`/`humanStatus` 与 `render` 的
  `opts.cards`/`opts.top`（计时页卡片式用，见 `modules/ring` 契约 2026-10-03 条），预览不传、行为不变。

## AI 桥路由（v0.3）

网关新增 `<前缀>api/agent/`（→ `AGENT_UPSTREAM`，`agent.chat.v1`）与 `<前缀>api/mcp/`（→ MCP 服务，
`mcp.tools.v1`）两条受保护路由，规范写在 `contracts/gateway.v1` 第八节：`include gate.inc`、清掉转发的
`Cookie`/`Authorization`、关缓冲（SSE）、上游运行期解析（缺了这两个服务网关照常起）、新内部网
`honeycomb-agent-net`。手写组装与 `tools/generate.py` 两份都要加，`NGINX_ENVSUBST_FILTER` 放行
`AGENT_UPSTREAM`。本模块的 `gate.inc` 不变。已实现：手写组装两条常驻；生成的组装由模块清单的
`bridge: true` / `upstreamEnv` 产出（见 gateway.v1 第八节「实现落定」）。

## 对比度校验

`scripts/check-contrast.py` 按 `contracts/design-tokens-v1.md` 的对比度矩阵机械核对
`static/tokens.css`。测试：`python -m pytest -q modules/nginx-docker/tests`（CI 的「安装器」任务里跑）。
