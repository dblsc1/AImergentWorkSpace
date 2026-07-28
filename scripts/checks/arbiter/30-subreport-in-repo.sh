#!/usr/bin/env bash
# 判据：所有 report.json 的 sub_reports[].path 必须是仓内相对路径。
[ "${1:-}" = --describe ] && { echo "30 子报告落点：sub_reports[].path 必须是仓内相对路径，禁止 /tmp 等仓外绝对路径"; exit 0; }
set -uo pipefail
command -v jq >/dev/null || { echo "缺少 jq，无法校验 sub_reports 落点" >&2; exit 1; }
fail=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in
      /*) echo "$f: sub_reports 落点在仓外（按未产出计）：$p" >&2; fail=1 ;;
      *)  [ -e "$p" ] || { echo "$f: sub_reports 指向的文件不存在：$p" >&2; fail=1; } ;;
    esac
  done < <(jq -r '.sub_reports[]?.path // empty' "$f" 2>/dev/null)
done < <(find . -path ./.git -prune -o -name report.json -print 2>/dev/null | sed 's|^\./||')
exit $fail
