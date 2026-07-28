#!/usr/bin/env bash
# 判据：本次改动必须包含至少一条 worklog（*/docs/worklog/*.md）。
[ "${1:-}" = --describe ] && { echo "10 worklog：本次改动必须包含至少一条 */docs/worklog/*.md"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"
mapfile -t -d '' staged < <(staged_paths ACMR)
for f in "${staged[@]}"; do [[ "$f" == *docs/worklog/*.md ]] && exit 0; done
echo "本次改动没有任何 worklog（*/docs/worklog/*.md）——铁律 18 留痕强制" >&2
exit 1
