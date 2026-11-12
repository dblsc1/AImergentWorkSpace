#!/usr/bin/env bash
# hive/frontend 单元测试入口。
#
# 纯逻辑测试：data.js + rails.js + gtd-data.js + hex-data.js / hex-audit.js /
# hex-ring.js 都没有 DOM 依赖，可以在 Node 里 require() 直接跑 ——
# 不需要浏览器、不需要无头环境、没有 npm 依赖（只用 node 内置 assert）。
#
# 缺 node 时**报错退出**，不是静默跳过：一个永远绿的测试入口比没有入口更糟。
set -euo pipefail
cd "$(dirname -- "${BASH_SOURCE[0]}")"
command -v node >/dev/null 2>&1 || { echo "❌ 需要 node 才能跑测试" >&2; exit 1; }
node data.test.js
node data-views.test.js
node rails.test.js
node gtd-data.test.js
node hex-audit.test.js
node hex-data.test.js
node hex-timer.test.js
node hex-decide.test.js
node hex-tiers.test.js
node hex-tiers-cards.test.js
node hex-minihive.test.js
node hex-claim-card.test.js
node hex-pet-pick.test.js
node hex-stage.test.js
node hex-record.test.js
node hex-project-ruler.test.js
node hex-centre-stats.test.js
node hex-agent-page.test.js
node hex-mini.test.js
node hex-onboard.test.js
node hex-source-health.test.js
node hex-clock-menu.test.js
node hex-human-page.test.js
node --check hex-stage-page.js   # 接线层碰 DOM，只查语法；行为由 tests/test_hive_stage.py 管
# 共享件 focus.js（此刻的焦点的文案：顶栏 / 计时页 / 本页中心格共用；文件在 nginx-docker，本页是它的使用方之一）
node ../../../nginx-docker/tests/focus.test.js
