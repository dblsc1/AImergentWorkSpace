#!/usr/bin/env bash
# ring/frontend 自核套件入口（2026-08-03 U1–U5）。
#
# 与真网关 E2E 套件的分工：
#   真网关 E2E  打真网关 / 真后端 / 真 Mongo，**会往库里写计时记录**，要口令。
#   本脚本    只起本地静态服务器 + 浏览器侧桩，**一个字节都不写库**，不要口令。
#
# 依赖不全时**响亮 skip 并 exit 0**（打印怎么装），不是静默通过——
# 关键路径缺文件必须 die，可选件缺失必须打印「已跳过」，
# 唯一不许的是静默跳过后报成功。
set -uo pipefail
cd "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

banner() { printf '\n──── %s ────\n' "$*"; }
skip() { printf '\n⏭  已跳过 ring/frontend 自核套件：%s\n' "$*"; exit 0; }

# 找一个装了 playwright 的 python：
#   ① RING_TEST_PYTHON 显式指定  ② 本目录自己的 .venv
pick_python() {
  local candidates=(
    "${RING_TEST_PYTHON:-}"
    "$PWD/.venv/bin/python"
  )
  local py
  for py in "${candidates[@]}"; do
    [ -n "$py" ] && [ -x "$py" ] || continue
    if "$py" -c 'import playwright, pytest' >/dev/null 2>&1; then
      printf '%s' "$py"; return 0
    fi
  done
  return 1
}

# 下面这行是装法提示里要打印的 venv 目录。它是**运行时目录**（被 .gitignore 吞掉，
# 按定义永远不入仓），不是文档引用。
# 提示串里改引用 $venv_dir，免得同一个悬空字面量在三行里各写一遍。
venv_dir="code/frontend/tests/.venv"

PY=$(pick_python) || skip \
"找不到装了 playwright+pytest 的 python。装法（任选其一）：
   python3 -m venv $venv_dir && \\
     $venv_dir/bin/pip install -r code/frontend/tests/requirements.txt && \\
     $venv_dir/bin/playwright install chromium
   或复用已有的：RING_TEST_PYTHON=<某个装好的 python> bash code/frontend/tests/run.sh"

banner "用 $PY 跑 U1–U5"
if ! "$PY" -c 'from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    p.chromium.launch(headless=True).close()' >/dev/null 2>&1; then
  skip "playwright 的 chromium 没装好（launch 失败）。装：$PY -m playwright install chromium"
fi

"$PY" -m pytest . -v -rs "$@"
rc=$?
if [ $rc -eq 0 ]; then
  banner "✅ ring/frontend 自核套件全过（U1–U5）"
else
  banner "❌ ring/frontend 自核套件有失败（不是 skip，是断言真的红了）"
fi
exit $rc
