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

## 对比度校验

`scripts/check-contrast.py` 按 `contracts/design-tokens-v1.md` 的对比度矩阵机械核对
`static/tokens.css`。测试：`python -m pytest -q modules/nginx-docker/tests`（CI 的「安装器」任务里跑）。
