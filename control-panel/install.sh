#!/usr/bin/env bash
# 安装任务控制台服务。由 scripts/install-gates.sh 自动调用，也可单独跑。
#
#   control-panel/install.sh            装 systemd --user 服务并启动
#   control-panel/install.sh --no-service   只校验，不装服务（CI/无 systemd 环境）
#
# 零依赖：只用 python3 标准库。不装 pip 包、不碰系统目录。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../scripts" && pwd -P)/lib/emit.sh" 2>/dev/null || true

root=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "❌ 不在 Git 仓内" >&2; exit 1; }
port=${AIMERGENT_PANEL_PORT:-8787}
host=${AIMERGENT_PANEL_HOST:-127.0.0.1}
command -v python3 >/dev/null || { echo "⚠️  没有 python3，跳过面板安装" >&2; exit 0; }
python3 -c "import http.server" 2>/dev/null || { echo "⚠️  python3 标准库不完整，跳过" >&2; exit 0; }

if [ "${1:-}" = --no-service ] || ! command -v systemctl >/dev/null; then
  printf '✅ 控制台就绪（未装服务）\n   手动启动: python3 %s/control-panel/server.py\n   地址: http://127.0.0.1:%s\n' "$root" "$port"
  exit 0
fi

unit_dir="$HOME/.config/systemd/user"; mkdir -p "$unit_dir"
slug=$(basename "$root" | tr -c 'a-zA-Z0-9' '-')
unit="aimergent-panel-$slug.service"
cat > "$unit_dir/$unit" <<UNIT
[Unit]
Description=AImergent 任务控制台 ($root)

[Service]
Type=simple
WorkingDirectory=$root
Environment=AIMERGENT_PANEL_PORT=$port
Environment=AIMERGENT_PANEL_HOST=$host
ExecStart=$(command -v python3) $root/control-panel/server.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT

if systemctl --user daemon-reload 2>/dev/null &&
   systemctl --user enable --now "$unit" 2>/dev/null; then
  shown=$host
  [ "$host" = tailscale ] && shown=$(tailscale ip -4 2>/dev/null | head -1 || echo 127.0.0.1)
  printf '✅ 控制台服务已启动: %s\n   地址: http://%s:%s\n   停止: systemctl --user stop %s\n' "$unit" "$shown" "$port" "$unit"
  [ "$host" != 127.0.0.1 ] && printf '   ⚠️  面板无鉴权：凡能访问该地址的设备都能读全部留痕与报告。\n'
  true
else
  printf '⚠️  systemd --user 不可用（无 session bus？）。unit 已写入 %s\n' "$unit_dir/$unit"
  printf '   手动启动: python3 %s/control-panel/server.py\n' "$root"
fi
