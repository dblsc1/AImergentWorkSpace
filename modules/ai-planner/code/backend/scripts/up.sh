#!/usr/bin/env bash
# ai-planner 起服务 —— 读 env、前置校验、起 uvicorn（组装根：ai_planner.bootstrap:build_app）。
#
#   code/backend/scripts/up.sh
#
# 关键路径缺文件/缺凭据必须 die，不许静默跳过后报成功。
# 本脚本只做「起不来就别装起来了」的前置校验；Config.from_env 里同样的判断
# (env 缺失即 die) 不因为脚本先查过一遍就删掉——两处独立生效，脚本查是为了
# 给出更早、更好读的报错，Python 里的判断是最后一道防线。
set -euo pipefail

die() { printf '❌ %s\n' "$*" >&2; exit 1; }

here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
backend=$(cd -- "$here/.." && pwd -P)
workspace_root=$(cd -- "$backend/../../../.." && pwd -P)
cd "$backend"

py="$backend/.venv/bin/python"
[ -x "$py" ] || die ".venv 不存在或未装依赖：$py（先 python3 -m venv .venv && .venv/bin/pip install -r requirements.txt）"

: "${AI_PLANNER_NEXUS_BASE:?❌ 缺 AI_PLANNER_NEXUS_BASE（nexus 基址，禁弱默认值）}"

if [ -z "${AI_PLANNER_NEXUS_PASSWORD:-}" ] && [ -z "${AI_PLANNER_NEXUS_COOKIE:-}" ]; then
  die "缺 nexus 凭据：AI_PLANNER_NEXUS_PASSWORD 或 AI_PLANNER_NEXUS_COOKIE 二选一必填（口令走 env，绝不进仓）"
fi

bind="${AI_PLANNER_BIND:-127.0.0.1:8700}"
case "$bind" in
  0.0.0.0*) die "AI_PLANNER_BIND 不得绑定 0.0.0.0" ;;
esac
host=${bind%%:*}
port=${bind##*:}

default_guide="$workspace_root/contracts/ai-planner-guide-v1.md"
export AI_PLANNER_GUIDE_PATH="${AI_PLANNER_GUIDE_PATH:-$default_guide}"
[ -f "$AI_PLANNER_GUIDE_PATH" ] || die "F-GUIDE 说明书不存在：$AI_PLANNER_GUIDE_PATH（system prompt 唯一事实，缺了不启动）"

# 驱动可插拔（module_docs/contract.md「驱动可插拔」）：默认 deepseek —— codex 复用的
# 宿主 ChatGPT 登录态当前被吊销，deepseek 已实调验证可用。Config.from_env 里
# 有同款判断（最后一道防线），这里查是为了给出更早、更好读的报错。
driver="${AI_PLANNER_DRIVER:-deepseek}"
case "$driver" in
  codex)
    codex_bin="${AI_PLANNER_CODEX_BIN:-codex}"
    command -v "$codex_bin" >/dev/null 2>&1 || die "codex 可执行不存在：$codex_bin（AI_PLANNER_DRIVER=codex 时必须能找到 codex，不许静默跳过后报成功）"
    ;;
  deepseek)
    [ -n "${AI_PLANNER_DEEPSEEK_API_KEY:-}" ] || die "缺 AI_PLANNER_DEEPSEEK_API_KEY（AI_PLANNER_DRIVER=deepseek 时必填，口令走 env，绝不进仓）"
    ;;
  *)
    die "AI_PLANNER_DRIVER 非法：$driver（合法值：codex|deepseek）"
    ;;
esac

printf '启动 ai-planner：driver=%s bind=%s:%s nexus_base=%s guide=%s\n' \
  "$driver" "$host" "$port" "$AI_PLANNER_NEXUS_BASE" "$AI_PLANNER_GUIDE_PATH" >&2

exec "$py" -m uvicorn ai_planner.bootstrap:build_app --factory --host "$host" --port "$port"
