#!/usr/bin/env bash
# 判据：本次改动必须包含至少一条 worklog（任意 */worklog/*.md）。
# J3 后合法落点：module_docs/worklog/（arbiter）、code/<子文件夹>/worklog/（programmer）、
# 各角色 docs/worklog/（项目级与存量布局）。判据统一为「路径里有 worklog/ 目录段」。
[ "${1:-}" = --describe ] && { echo "10 worklog：本次改动必须包含至少一条 */worklog/*.md（铁律 18；J3 后含 module_docs/ 与 code/<子文件夹>/ 落点）"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"
mapfile -t -d '' staged < <(staged_paths ACMR)
for f in "${staged[@]}"; do [[ "$f" == */worklog/*.md || "$f" == worklog/*.md ]] && exit 0; done
echo "本次改动没有任何 worklog（*/worklog/*.md）——铁律 18 留痕强制" >&2
exit 1
