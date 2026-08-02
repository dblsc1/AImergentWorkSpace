#!/usr/bin/env bash
# 「我改了这些，要连带看哪些文档？」—— 随时可问，不用等到着陆被拦。
#
#   scripts/doc_impact.sh                 看当前改动（暂存 + 未暂存）
#   scripts/doc_impact.sh --staged        只看暂存
#   scripts/doc_impact.sh --range A..B    看一段区间（推送/合并前用）
#   scripts/doc_impact.sh --scope <前缀…> 看某片写区**可能**牵动什么（起飞前预告）
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/paths.sh" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/docmap.sh" || { printf '❌ %s：载入 docmap.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }

mode=${1:---all}; shift 2>/dev/null || true
declare -a changed=()
case "$mode" in
  --staged) mapfile -t -d '' changed < <(staged_paths ACMRD) ;;
  --range)  r=${1:?用法: --range A..B}; mapfile -t -d '' changed < <(git diff --name-only -z --no-renames "$r") ;;
  --scope)  changed=("$@") ;;
  *)        mapfile -t -d '' changed < <(git diff --name-only -z --no-renames HEAD 2>/dev/null)
            mapfile -t -d '' _st < <(staged_paths ACMRD); changed+=("${_st[@]}") ;;
esac

# 只留"非文档"的改动 —— 文档改文档不牵动治理关系
declare -a code=()
for f in "${changed[@]}"; do
  [ -n "$f" ] || continue
  case "$f" in *.md|*/docs/worklog/*|*/docs/findings/*|*/docs/decisions/*|logs/*) continue ;; esac
  code+=("$f")
done

if [ "${#code[@]}" -eq 0 ]; then
  printf '\n（本次没有非文档改动，不牵动任何长期文档）\n\n'; exit 0
fi

printf '\n┌─ 文档影响面 · %d 处改动\n│\n' "${#code[@]}"
printf '│  改了这些：\n'; printf '│    %s\n' "${code[@]}"
printf '│\n│  所以必须逐份确认这些长期文档还对得上：\n'
n=0
while IFS=$'\t' read -r doc why; do
  [ -n "$doc" ] || continue
  n=$((n+1))
  printf '│    %d. %-44s ← 因为改了 %s\n' "$n" "$doc" "$why"
done < <(dm_impact "${code[@]}")
if [ "$n" -eq 0 ]; then
  printf '│    （治理关系表里没有匹配项 —— 若你觉得该有，是 agents/cfo/doc-map.tsv 缺一行，报 CFO）\n'
fi
cat <<'TAIL'
│
│  每一份只有两种处置，没有第三种：
│    · 改到位  → 和代码同批提交
│    · 不用改  → report.json 里写 {"path":"…","action":"no-change-needed","reason":"…"}
│                 写「不用改」完全可以，**写不出理由才是问题**
│
└─ 着陆时 checks/_common/11 会逐条核这张表；推送与合并前会按整段区间再核一次
TAIL
printf '\n'
