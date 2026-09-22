#!/usr/bin/env bash
# gantt/code/frontend 单元测试入口。
# 新增功能代码要求同批新增测试。
# 纯逻辑测试，只测 gantt-data.js（无 DOM 依赖），不需要浏览器/无头环境。
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")"
command -v node >/dev/null 2>&1 || { echo "❌ 需要 node 才能跑 gantt-data.test.js" >&2; exit 1; }
node gantt-data.test.js
