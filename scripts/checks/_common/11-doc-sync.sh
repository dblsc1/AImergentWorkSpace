#!/usr/bin/env bash
# 判据：改了东西，提到它的文档要么同批改，要么在报告里声明「已读·无需改 + 理由」。
#
# 关系不手维护，从文档里反查：某份 .md 用反引号写了 `scripts/foo.sh`，
# 它就依赖 foo.sh。改 foo.sh 时自动查出「谁提到了我」。
# **手维护的映射表本身会腐烂，而且新文件忘登记就是静默漏掉** —— 又回到老问题。
#
# 与 gates/check-references.sh 的分工，别搞混：
#   · check-references 管「引用的东西还在不在」（改名/删除留下的死链，机器能判）
#   · 本条管「引用还在，但描述已经不准了」（内容变了，只能人判 → 所以要声明）
#
# 声明写在自己的 report.json：
#   "docs_reviewed": [
#     {"path":"scripts/README.md","action":"updated"},
#     {"path":"agents/AGENTS.md","action":"no-change-needed","reason":"只改内部实现，文档只描述接口"}
#   ]
[ "${1:-}" = --describe ] && { echo "11 文档同步：改动波及的文档须同批更新，或在 report.json 的 docs_reviewed 里声明「已读·无需改+理由」"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"

mapfile -t -d '' staged < <(staged_paths ACMRD)
[ "${#staged[@]}" -gt 0 ] || exit 0

# 本次改了哪些「非文档」文件
changed=()
for f in "${staged[@]}"; do
  case "$f" in
    *.md|*/docs/worklog/*|*/docs/findings/*|*/docs/decisions/*|logs/*) continue ;;
  esac
  changed+=("$f")
done
[ "${#changed[@]}" -gt 0 ] || exit 0

# 本次已经一起改了的文档
declare -A staged_docs=()
for f in "${staged[@]}"; do case "$f" in *.md) staged_docs["$f"]=1 ;; esac; done

# 报告里已声明的文档
declare -A declared=()
if command -v jq >/dev/null; then
  while IFS= read -r -d '' rep; do
    while read -r d; do [ -n "$d" ] && declared["$d"]=1; done < <(
      jq -r '.docs_reviewed[]? | select(.action=="updated" or .action=="no-change-needed") | .path' "$rep" 2>/dev/null)
  done < <(find . -path ./.git -prune -o -name report.json -print0 2>/dev/null | sed -z 's|^\./||')
fi

# 可选补充表：自动推不出来的语义关联（文档讲某功能但没写路径）
deps_table=scripts/gates/doc-deps.txt

fail=0
declare -A reported=()
for c in "${changed[@]}"; do
  # ① 从文档反查：谁用反引号提到了我
  while IFS= read -r -d '' doc; do
    case "$doc" in *.md) ;; *) continue ;; esac
    case "$doc" in */docs/worklog/*|*/docs/findings/*|*/docs/decisions/*) continue ;; esac
    grep -qF -- "\`$c\`" "$doc" 2>/dev/null || continue
    [ -n "${staged_docs[$doc]:-}" ] && continue
    [ -n "${declared[$doc]:-}" ] && continue
    [ -n "${reported[$doc]:-}" ] && continue
    reported["$doc"]=1
    echo "改了 $c，但提到它的 $doc 既没同批改、也没在 report.json 声明" >&2
    fail=1
  done < <(git ls-files -z '*.md')
  # ② 补充表里的语义关联
  if [ -f "$deps_table" ]; then
    while read -r pat doc; do
      case "$pat" in ''|'#'*) continue ;; esac
      [ -n "$doc" ] || continue
      # shellcheck disable=SC2053
      [[ "$c" == $pat ]] || continue
      [ -n "${staged_docs[$doc]:-}" ] && continue
      [ -n "${declared[$doc]:-}" ] && continue
      [ -n "${reported[$doc]:-}" ] && continue
      reported["$doc"]=1
      echo "改了 $c（匹配 $pat），但 $doc 既没同批改、也没声明" >&2
      fail=1
    done < "$deps_table"
  fi
done

if [ "$fail" -ne 0 ]; then
  cat >&2 <<'HINT'
  → 两条路，选一条：
     ① 同批把文档改到位
     ② 在自己的 report.json 里声明已读：
        "docs_reviewed":[{"path":"<文档>","action":"no-change-needed","reason":"<为什么不用改>"}]
     声明是结构化的、可审计的、会进控制面板 —— 但必须写理由。
HINT
fi
exit $fail
