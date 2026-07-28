#!/usr/bin/env bash
# 判据：审核者的 report.json 必须带标准 review_target（exact 区间），否则审的是"当前工作区"而非固定 candidate。
[ "${1:-}" = --describe ] && { echo "82 审核区间：programmer_reviewer 的 report.json 必须带 review_target{branch,base,head,diff_mode=exact,changed_files}"; exit 0; }
set -uo pipefail
f=codeagent/programmer_reviewer/docs/report.json
[ -f "$f" ] || exit 0
command -v jq >/dev/null || { echo "缺少 jq" >&2; exit 1; }
jq -e '
  (has("target") | not) and
  (.review_target | type == "object") and
  (.review_target.base | test("^[0-9a-f]{40}$")) and
  (.review_target.head | test("^[0-9a-f]{40}$")) and
  .review_target.diff_mode == "exact" and
  (.review_target.changed_files | type == "array")
' "$f" >/dev/null 2>&1 && exit 0
echo "$f 缺少标准 review_target（禁止用 target 等别名；diff_mode 必须是 exact）" >&2
exit 1
