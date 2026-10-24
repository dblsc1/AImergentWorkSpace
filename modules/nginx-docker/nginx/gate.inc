# 登录门片段（contracts/gateway.v1 冻结项）。
#
# 在任何 location 里 `include /etc/nginx/honeycomb/gate.inc;`，这条路由就受同一道门
# 保护，并把认证服务给出的租户转给后端：
#
#   · 未登录 → 302 到登录页（@to_login）
#   · 已登录 → X-Nexus-Tenant 设为 verify 204 响应里的同名头；认证服务没给就是空，
#     空值的头 nginx 不转发。**无论哪种，客户端自己带来的 X-Nexus-Tenant 都被覆盖掉**，
#     后端只能看到网关认定的租户。
#   · 调用方范围（gateway.v1 第九节，auth.gate v1.4）：X-Nexus-Scope / X-Nexus-Anonymous 同样取自 verify 的
#     响应头、同样覆盖客户端自带的——设备令牌的范围、「这个请求什么凭据都没带」只能由认证服务说了算。
#
# 注意 proxy_set_header 的继承规则：一个 location 里只要写了任意一条
# proxy_set_header，外层的就全部失效。所以本片段只放在 location 里用，
# 和那条 location 自己的 proxy_set_header 写在同一层。

auth_request /__auth_verify;
auth_request_set $honeycomb_tenant $upstream_http_x_nexus_tenant;
error_page 401 = @to_login;
proxy_set_header X-Nexus-Tenant $honeycomb_tenant;
auth_request_set $honeycomb_scope $upstream_http_x_nexus_scope;
auth_request_set $honeycomb_anonymous $upstream_http_x_nexus_anonymous;
proxy_set_header X-Nexus-Scope $honeycomb_scope;
proxy_set_header X-Nexus-Anonymous $honeycomb_anonymous;
