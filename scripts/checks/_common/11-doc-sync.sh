#!/usr/bin/env bash
# 判据：改了东西，提到它的文档要么同批改，要么在报告里声明「已读·无需改 + 理由」。
#
# 关系有两个来源，**主干是人维护的治理关系，不是自动反查**：
#   ① scripts/gates/doc-map.tsv —— 长期文档治理哪片代码区域（项目级，CFO 维护）
#   ② 约定推导 —— code/<模块>/code/** → 该模块的 contract.md 与 AGENTS.md（模块级，不用登记）
# 为什么主干必须是人维护的：`contract.md` 治理 `code/backend/**`，
# **哪怕它正文里一个路径都没写** —— 靠「文档提到了谁」反查永远抓不到这条，
# 而它恰恰是最该抓的。关系是语义的。
#
#   ③ 反引号反查 —— 只作**提示**，不硬拦：它抓到的多是偶然提及，粒度太细。
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

fail=0
declare -A staged_all=()
for f in "${staged[@]}"; do staged_all["$f"]=1; done

# ── ① 派单时声明的预期文档变更：逐条机械核对 ──────────────
# 机器只判机械事实：声明 create 的文件在不在、声明 update 的有没有出现在本次改动里。
# 理由的成色不归机器管，归 arbiter（模块内）/ CFO（跨模块）。
role_now=${AIMERGENT_ROLE:-}
lease_dir=$(git rev-parse --path-format=absolute --git-common-dir)/aimergent-leases
docs_file="$lease_dir/${role_now}.docs"
if [ -n "$role_now" ] && [ -f "$docs_file" ]; then
  while IFS=$'\t' read -r act path; do
    [ -n "$path" ] || continue
    case "$act" in
      create)
        if [ ! -e "$path" ]; then
          echo "派单声明要新建 $path —— 但它不存在" >&2; fail=1
        elif [ -z "${staged_all[$path]:-}" ]; then
          echo "派单声明要新建 $path —— 存在但没进本次提交" >&2; fail=1
        fi ;;
      update)
        [ -n "${staged_all[$path]:-}" ] ||
          { echo "派单声明要更新 $path —— 但本次改动里没有它" >&2; fail=1; } ;;
    esac
  done < "$docs_file"
fi

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

doc_map=scripts/gates/doc-map.tsv

declare -A reported=()
need() {   # need <文档> <因为改了什么>
  local doc=$1 why=$2
  [ -e "$doc" ] || return 0
  [ -n "${staged_all[$doc]:-}" ] && return 0
  [ -n "${declared[$doc]:-}" ] && return 0
  [ -n "${reported[$doc]:-}" ] && return 0
  reported["$doc"]=1
  printf '  %-40s → %s\n' "$why" "$doc" >&2
  fail=1
}

for c in "${changed[@]}"; do
  # ① 治理关系表（项目级，人维护）
  if [ -f "$doc_map" ]; then
    while IFS=$'\t' read -r area doc; do
      case "$area" in ''|'#'*) continue ;; esac
      [ -n "$doc" ] || continue
      # shellcheck disable=SC2053
      [[ "$c" == $area ]] && need "$doc" "$c"
    done < "$doc_map"
  fi
  # ② 模块级按约定推导，不用登记
  case "$c" in
    code/*/code/*)
      mod=${c%%/code/*}; mod=${mod#code/}; mod="code/$mod"
      need "$mod/module_docs/contract.md" "$c"
      need "$mod/AGENTS.md" "$c" ;;
  esac
  # ③ 反引号反查 —— 只提示，不计入 fail（偶然提及，粒度太细）
  while IFS= read -r -d '' doc; do
    case "$doc" in *.md) ;; *) continue ;; esac
    case "$doc" in */docs/worklog/*|*/docs/findings/*|*/docs/decisions/*) continue ;; esac
    grep -qF -- "\`$c\`" "$doc" 2>/dev/null || continue
    [ -n "${staged_all[$doc]:-}" ] && continue
    [ -n "${declared[$doc]:-}" ] && continue
    [ -n "${reported[$doc]:-}" ] && continue
    reported["$doc"]=1
    echo "  ℹ 提示：$doc 里提到了 $c（偶然提及，不硬拦；确有影响就一并改）" >&2
  done < <(git ls-files -z '*.md')
done

if [ "$fail" -ne 0 ]; then
  # 表头补在最前面（子 shell 里没法预知有没有条目，所以先打条目再补说明）
  cat >&2 <<'HINT'
  ↑ 左边是本次改动，右边是治理它的长期文档；这些文档既没同批改、也没声明。
  → 两条路，选一条：
     ① 同批把文档改到位
     ② 在自己的 report.json 里声明已读：
        "docs_reviewed":[{"path":"<文档>","action":"no-change-needed","reason":"<为什么不用改>"}]
     声明是结构化的、可审计的、会进控制面板 —— 但必须写理由。
     治理关系从哪来：scripts/gates/doc-map.tsv（项目级，CFO 维护）
                     + 模块级约定（code/<模块>/code/** → 该模块 contract.md 与 AGENTS.md）
HINT
fi
exit $fail
