# 额外路由（缺省为空）

网关会把 `HONEYCOMB_EXTRA_ROUTES_DIR` 指向的目录里每个 `*.conf.template` 渲染后
`include` 进 server 块。本目录是它的缺省值，故意为空。

不要往这里加自己的文件——在仓外放一个自己的目录，用 `.env` 指过去。写法见
`contracts/gateway.v1/contract.md`。
