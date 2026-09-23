# nginx-docker · 交接

## 怎么验

- 单测：`python -m pytest -q modules/nginx-docker/tests tools`
- 真起：`deploy/smoke.sh <口令>`（缺省组装）；`deploy/test/gateway_contract.sh <口令>`
  （gateway.v1 契约，要按 `deploy/test/override.yml` 起）。CI 两样都跑。

## 避坑

1. **`proxy_set_header` 的继承**：location 里只要写了一条，外层的全部失效。
   `gate.inc` 因此只放在 location 里，和那条 location 自己的 `proxy_set_header` 同层。
   漏了 `proxy_set_header X-Nexus-Tenant $honeycomb_tenant;` 这一行，客户端就能冒充
   任意租户——`gateway_contract.sh` 有一条专门盯它（实测删掉这行即红）。
2. **sub_filter 遇上 gzip 就失效**：注入片段里显式 `gzip off;`。
3. **额外路由的 location 路径不能用变量**：nginx 不接受。所以额外路由文件也过
   envsubst，用 `${HONEYCOMB_BASE_PATH}` 在渲染时写成字面量。
4. **页签文字里不能有 `'` 或 `$`**：它被塞进 nginx 单引号字符串，`$` 会被当成变量。
   生成器遇到直接报错。

## 从 v0.1 带过来的名字

`/__cockpit/*` 路径、`ckpt-` CSS 前缀、`data-ckpt-*` 属性、localStorage 的
`cockpit-theme` / `cockpit-accent`：运行时标识符，改了会让已有用户的主题设置失效，
按「只改目录名、不改运行时标识符」保留。
