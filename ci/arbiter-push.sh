#!/usr/bin/env bash
# arbiter-only push：唯一 sanctioned 路径。铸一次性 push-lease → fetch-then-push → 清 lease。
# 用法: ci/arbiter-push.sh <git push 参数...>   例: ci/arbiter-push.sh -u origin feat/x
set -euo pipefail
if [ "$#" -eq 0 ]; then echo "用法: arbiter-push.sh <git push 参数...>" >&2; exit 2; fi

common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
nonce=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
umask 077
token="$common_dir/aimergent-push-lease.$$"
printf '%s' "$nonce" > "$token"; chmod 600 "$token"
trap 'rm -f -- "$token"' EXIT

# fetch-then-push（铁律15④）：不猜 remote，取默认/跟踪 remote；失败不致命，server 端非 ff 兜底仍在
git fetch --quiet || true

AIMERGENT_PUSH_LEASE=1 \
AIMERGENT_PUSH_LEASE_NONCE="$nonce" \
AIMERGENT_PUSH_LEASE_TOKEN_FILE="$token" \
git push "$@"
