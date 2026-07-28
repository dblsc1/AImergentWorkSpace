#!/usr/bin/env bash
# 判据：每份 report.json 必须带 reviewer_opinion —— 谁审的、什么结论、详报在哪。
#
# 与「审核门在 merge 不在 commit」的关系（重要，别搞反）：
#   · 提交时：字段必须**存在且格式正确**，verdict 允许是 pending —— 否则 programmer
#     永远无法先提交形成 candidate，审核就无从谈起。
#   · 合并时：verdict 必须是 approved —— 那才是审核门。
# 本检查管的是前者：**不许出现"根本没人审、也没打算让人审"的交付**。
[ "${1:-}" = --describe ] && { echo "70 审核意见：report.json 必须带 reviewer_opinion{reviewer,verdict,path}；提交可 pending，合并须 approved"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"
command -v jq >/dev/null || { echo "缺少 jq，无法校验 reviewer_opinion" >&2; exit 1; }
fail=0
while IFS= read -r -d '' f; do
  [ -n "$f" ] || continue
  if ! jq -e '
      (.reviewer_opinion | type == "object") and
      (.reviewer_opinion.reviewer | type == "string" and length > 0) and
      (.reviewer_opinion.verdict  | . == "pending" or . == "approved" or . == "rejected") and
      (.reviewer_opinion.path     | type == "string")
    ' "$f" >/dev/null 2>&1; then
    echo "$f 缺少合规的 reviewer_opinion{reviewer,verdict,path}" >&2
    echo "  例: \"reviewer_opinion\":{\"reviewer\":\"programmer_reviewer\",\"verdict\":\"pending\",\"path\":\"review/reviewreport/2026-07-28-x.md\",\"round\":1}" >&2
    fail=1; continue
  fi
  p=$(jq -r '.reviewer_opinion.path' "$f")
  case "$p" in
    /*) echo "$f: reviewer_opinion.path 指向仓外（按未产出计）：$p" >&2; fail=1 ;;
  esac
  v=$(jq -r '.reviewer_opinion.verdict' "$f")
  [ "$v" = pending ] && echo "  ℹ $f: 审核意见仍为 pending —— 可以提交，但合并门会拦" >&2
done < <(find . -path ./.git -prune -o -name report.json -print0 2>/dev/null | sed -z 's|^\./||')
exit $fail
