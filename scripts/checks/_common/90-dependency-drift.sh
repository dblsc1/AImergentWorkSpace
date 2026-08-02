#!/usr/bin/env bash
# 判据：改动不得悄悄改变依赖关系。依赖是最容易劣化、又最难事后看出来的东西。
[ "${1:-}" = --describe ] && { echo "90 依赖漂移：新增跨模块引用 / 包依赖增删 / 契约字段变动，必须在 worklog 说明"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
mapfile -t -d '' staged_arr < <(staged_paths ACMRD)
staged=$(printf '%s\n' "${staged_arr[@]}")
[ -n "$staged" ] || exit 0
# J3 后 worklog 落点多处（module_docs/worklog、code/<子文件夹>/worklog、各角色 docs/worklog）
# ——取值范围必须跟着迁（实证：写死旧路径让新落点的说明「不可见」，恒判未说明）
fail=0; note=$(git diff --cached -- '*worklog/*.md' 2>/dev/null)

# ① 包依赖增删
deps=$(grep -E 'package\.json|requirements\.txt|go\.mod|pyproject\.toml|Cargo\.toml' <<<"$staged" || true)
if [ -n "$deps" ] && ! grep -qE '依赖|dependency|新增包|升级' <<<"$note"; then
  echo "包依赖文件有改动但 worklog 未说明理由：$(tr '\n' ' ' <<<"$deps")" >&2; fail=1
fi
# ② 新增跨模块引用（指向本模块之外）
xmod=$(git diff --cached -U0 -- 'code/*' 2>/dev/null |
       grep -E '^\+' | grep -vE '^\+\+\+' |
       grep -oE "(import|require|from)[^\"']*[\"'][^\"']*\.\./\.\./[^\"']*[\"']" | head -5 || true)
if [ -n "$xmod" ]; then
  echo "新增疑似跨模块引用（模块只能经契约依赖，铁律 8）：" >&2
  sed 's/^/  /' <<<"$xmod" >&2; fail=1
fi
# ③ 契约字段变动
if grep -qE 'contract\.md|openapi|\.proto$' <<<"$staged" &&
   ! grep -qE '契约|contract|字段|consumer|消费方' <<<"$note"; then
  echo "契约有改动但 worklog 未说明影响哪些消费方" >&2; fail=1
fi
exit $fail
