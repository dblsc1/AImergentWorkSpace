#!/usr/bin/env bash
# 把扩展复制到用户目录并启用。可重复运行，不需要 sudo。
set -euo pipefail

UUID="agent-traffic-light@local"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"

mkdir -p "$DEST"
cp -f "$SRC/extension.js" "$SRC/metadata.json" "$SRC/stylesheet.css" "$DEST/"
echo "已复制到 $DEST"

if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable "$UUID" || echo "启用失败：Wayland 下请先注销再登录，然后运行 gnome-extensions enable $UUID"
else
    echo "没有 gnome-extensions 命令，请手动启用 $UUID"
fi
echo "Wayland 下首次安装或升级后需要注销再登录；X11 可按 Alt+F2 输入 r 重启 Shell。"
