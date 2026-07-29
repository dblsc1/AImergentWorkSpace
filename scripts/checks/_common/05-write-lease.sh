#!/usr/bin/env bash
# 判据：暂存的每个文件必须落在本角色持有的写区路签内（验签）。
# 没跑 mission_start.sh = 没有签 = 一提交就被拦 —— 这就是「无法绕过」的落点。
[ "${1:-}" = --describe ] && { echo "05 写区路签：暂存文件必须落在本角色 mission_start.sh 申领的写区内（防未受控双写）"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"
role=${AIMERGENT_ROLE:-}
lease_dir=$(git rev-parse --path-format=absolute --git-common-dir)/aimergent-leases
[ -n "$role" ] || exit 0                      # 角色未知时不拦（由 10/40 等条兜底）
f="$lease_dir/$role.lease"
if [ ! -f "$f" ]; then
  echo "角色 $role 没有写区路签 —— 先跑 scripts/mission_start.sh $role <任务单> <写区>" >&2
  exit 1
fi
mapfile -t leases < "$f"
bad=0
while IFS= read -r -d '' p; do
  [ -n "$p" ] || continue
  case "$p" in
    codeagent/"$role"/*|logs/*|review/reviewreport/*) continue ;;   # 自己的留痕区永远允许
  esac
  ok=0
  # 前缀命中（目录签）或精确相等（单文件签；也兼容旧签带尾斜杠的形态）
  for l in "${leases[@]}"; do
    [ -n "$l" ] || continue
    case "$p" in "$l"*) ok=1; break ;; esac
    [ "${l%/}" = "$p" ] && { ok=1; break; }
  done
  [ "$ok" -eq 1 ] || { echo "越出写区路签：$p（持有 ${leases[*]}）" >&2; bad=1; }
done < <(staged_paths ACMRD)
exit $bad
