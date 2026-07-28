#!/usr/bin/env bash
# 判据：programmer 不得改 review/ 与 module_docs/（契约只读，改要上报 arbiter）。
[ "${1:-}" = --describe ] && { echo "81 写边界：programmer 不得改 review/ 与 module_docs/（契约只读）"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"
mapfile -t -d '' staged < <(staged_paths ACMRD)
bad=$(filter_paths '^(review/|module_docs/)' "${staged[@]}")
[ -z "$bad" ] && exit 0
echo "越出 programmer 写边界（契约与审核区只读，需求变更上报 arbiter）：" >&2
sed 's/^/  /' <<<"$bad" >&2
exit 1
