# 共享顶栏注入片段（contracts/gateway.v1）。
#
# 在前端页面的 location 里 `include /etc/nginx/honeycomb/inject.inc;`，网关就往这个
# 前端的 HTML 里注入同一套顶栏、设计 tokens 与主题：
#
#   </head> 前：站点前缀 window.HONEYCOMB_BASE（$honeycomb_base）、页签配置
#              window.HONEYCOMB_NAV（$honeycomb_nav，按已装前端生成）+ 首帧主题脚本（防白闪）
#   </body> 前：<前缀>__cockpit/tokens.css、navbar.css、focus.js（此刻的焦点的共享文案件，navbar.js 要用，先于它）、clockface.js（时钟读数与悬停卡的共享件，同样先于 navbar.js）、navbar.js
#
# 前端自己什么都不用引，也不用复制顶栏代码。登录页不注入（还没进门就给导航是错的）。
#
# gzip 必须关：sub_filter 作用在响应体上，压缩过的响应体里找不到锚点。当前镜像
# 默认没开 gzip，照样显式关掉——哪天有人在 http 块开了 gzip，注入不能悄悄失效。

gzip off;
sub_filter_once on;
sub_filter '</head>' '<script>window.HONEYCOMB_BASE="$honeycomb_base";window.HONEYCOMB_NAV=$honeycomb_nav;</script><script>(function(){try{var d=document.documentElement,t=localStorage.getItem("cockpit-theme")||"auto";if(t==="auto")t=matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light";d.setAttribute("data-theme",t);var a=localStorage.getItem("cockpit-accent");if(a)d.setAttribute("data-accent",a)}catch(e){}})();</script></head>';
sub_filter '</body>' '<link rel="stylesheet" href="${honeycomb_base}__cockpit/tokens.css"><link rel="stylesheet" href="${honeycomb_base}__cockpit/navbar.css"><script src="${honeycomb_base}__cockpit/focus.js" defer></script><script src="${honeycomb_base}__cockpit/clockface.js" defer></script><script src="${honeycomb_base}__cockpit/navbar.js" defer></script></body>';

# 前端页面与它的 js / css 一律先问服务器有没有新版（带 ETag，没变回 304）。不加这行时浏览器按启发式缓存，
# 升级后好一阵还在用旧文件（2026-11-10 仓主实测「改了看不到区别」）。
add_header Cache-Control "no-cache";
