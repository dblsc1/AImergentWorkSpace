#!/usr/bin/env bash
# 判据：本次改动必须包含至少一条 worklog（*/docs/worklog/*.md）。
[ "${1:-}" = --describe ] && { echo "10 worklog：本次改动必须包含至少一条 */docs/worklog/*.md"; exit 0; }
set -uo pipefail
staged=$(git diff --cached --name-only --diff-filter=ACMR)
if grep -qE 'docs/worklog/.*\.md' <<<"$staged"; then exit 0; fi
echo "本次改动没有任何 worklog（*/docs/worklog/*.md）——铁律 18 留痕强制" >&2
exit 1
