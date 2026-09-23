# 共享顶栏注入片段（contracts/gateway.v1）。
#
# 在前端页面的 location 里 `include /etc/nginx/honeycomb/inject.inc;`，网关就往这个
# 前端的 HTML 里注入同一套顶栏、设计 tokens 与主题：
#
#   </head> 前：页签配置 window.HONEYCOMB_NAV（取自 server 块的 $honeycomb_nav，
#              按已装前端生成）+ 首帧主题脚本（防白闪）
#   </body> 前：/__cockpit/tokens.css、navbar.css、navbar.js
#
# 前端自己什么都不用引，也不用复制顶栏代码。登录页不注入（还没进门就给导航是错的）。
#
# gzip 必须关：sub_filter 作用在响应体上，压缩过的响应体里找不到锚点。当前镜像
# 默认没开 gzip，照样显式关掉——哪天有人在 http 块开了 gzip，注入不能悄悄失效。

gzip off;
sub_filter_once on;
sub_filter '</head>' '<script>window.HONEYCOMB_NAV=$honeycomb_nav;</script><script>(function(){try{var d=document.documentElement,t=localStorage.getItem("cockpit-theme")||"auto";if(t==="auto")t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";d.setAttribute("data-theme",t);var a=localStorage.getItem("cockpit-accent");if(a)d.setAttribute("data-accent",a)}catch(e){}})();</script></head>';
sub_filter '</body>' '<link rel="stylesheet" href="/__cockpit/tokens.css"><link rel="stylesheet" href="/__cockpit/navbar.css"><script src="/__cockpit/navbar.js" defer></script></body>';
