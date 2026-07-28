#!/usr/bin/env bash
# 判据：契约类文件有改动时，对应契约文档必须同一次改动内同步。
[ "${1:-}" = --describe ] && { echo "50 契约同步：改 contracts/ 或 *openapi* 时，contract.md 须同批改动（无契约的仓自动跳过）"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"
mapfile -t -d '' staged < <(staged_paths ACMRD)
touched=$(filter_paths '^contracts/|openapi' "${staged[@]}")
[ -n "$touched" ] || exit 0
[ -n "$(filter_paths 'contract\.md|contracts/.*\.md' "${staged[@]}")" ] && exit 0
echo "改了契约实现但未同步契约文档（铁律 4/11）：" >&2
sed 's/^/  /' <<<"$touched" >&2
exit 1
