#!/usr/bin/env sh
# HoneyComb 安装器入口。真逻辑在 tools/install.py。
#
# 为什么是 sh 而不是 bash：这层只做一件事——找到 python3 再把参数递过去。
# 不需要 bash 特性，那就别要求 bash。
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 需要 python3（只用标准库；list/plan/doctor 零第三方依赖）" >&2
  exit 1
fi

exec python3 "$here/tools/install.py" "$@"
