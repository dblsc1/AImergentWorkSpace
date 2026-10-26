#!/bin/sh
# 本机请求标记（gateway.v1 第十节）：挂到网关容器的 /docker-entrypoint.d/15-honeycomb-same-host.sh，
# 在模板渲染之前跑，生成 conf.d/00-honeycomb-same-host.conf（一个 geo，变量 $honeycomb_same_host）。
# 本机 = 回环地址，或本容器默认路由的网关地址（宿主机访问发布端口时对端是桥网关）；读不到网关就只认回环。
# HONEYCOMB_SAME_HOST_TRUSTED=false 时只写 default ""，一律置空。
# 本脚本被执行或被 source 都行，所以不用 exit。
{
  echo '# 由 same-host.sh 在容器启动时生成，不要手改。'
  echo 'geo $remote_addr $honeycomb_same_host {'
  echo '    default "";'
  case "${HONEYCOMB_SAME_HOST_TRUSTED:-true}" in
    false|False|FALSE|0|no|off)
      echo '    # HONEYCOMB_SAME_HOST_TRUSTED=false：不认本机' ;;
    *)
      echo '    127.0.0.0/8 1;'
      echo '    ::1 1;'
      # 只认默认路由 via 的确切地址，不猜子网的 .1；读不到就只剩回环（失败即关）
      ip -4 route show default 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="via" && $(i+1) ~ /^[0-9.]+$/) print $(i+1)}' \
        | sort -u | while read -r gw; do echo "    $gw 1;"; done ;;
  esac
  echo '}'
} > /etc/nginx/conf.d/00-honeycomb-same-host.conf
