#!/usr/bin/env bash
# 判据：新增功能代码必须带测试。旧功能被测试锁住，才敢把 token 花在新功能上。
[ "${1:-}" = --describe ] && { echo "83 新功能带测试：新增 code/ 功能文件时，必须同批新增/修改测试或回归用例"; exit 0; }
set -uo pipefail
new_code=$(git diff --cached --name-only --diff-filter=A -- 'code/*' 2>/dev/null |
           grep -E '\.(py|js|mjs|cjs|ts|tsx|vue|svelte|go|rs)$' |
           grep -vE '(test|spec|__tests__|\.d\.ts)' || true)
[ -n "$new_code" ] || exit 0
tests=$(git diff --cached --name-only --diff-filter=ACMR |
        grep -E '(test|spec|__tests__|review/regression/|review/reviewcode/)' || true)
[ -n "$tests" ] && exit 0
echo "新增功能代码但本批没有任何测试/回归用例：" >&2
sed 's/^/  /' <<<"$new_code" >&2
echo "  → 放 review/regression/（永久回归，每次全跑）或模块测试目录" >&2
exit 1
