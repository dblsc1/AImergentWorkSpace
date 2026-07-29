#!/usr/bin/env bash
# 「改了这个 → 要看哪些长期文档」的唯一算法。
#
# 抽成库是有原因的：这套关系要在**五个时点**被问到 ——
#   派活 / 起飞 / 干活中随时 / 着陆 / 推送与合并。
# 每个时点各写一份，五份迟早互相漂移；漂移之后哪一份是对的没人说得清。
# **同一个问题只允许有一个答案的来源。**
#
# 关系三来源（主干是人维护的治理关系，不是自动反查）：
#   ① agents/cfo/doc-map.tsv          项目级，CFO 维护
#   ② 模块级约定（不用登记）           code/<模块>/code/** → 该模块 contract.md + AGENTS.md
#   ③ 反引号反查                       只作提示，不硬拦（偶然提及，粒度太细）

_dm_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)

dm_table() {   # 打印治理关系表的路径（模块仓回落到框架根）
  local t="$_dm_root/agents/cfo/doc-map.tsv"
  [ -f "$t" ] && { printf '%s' "$t"; return; }
  local fw=${AIMERGENT_FRAMEWORK_ROOT:-}
  [ -z "$fw" ] && [ -f "$_dm_root/.aimergent-framework" ] &&
    fw=$(cd "$_dm_root" && cd "$(cat .aimergent-framework)" 2>/dev/null && pwd -P || true)
  [ -n "$fw" ] && [ -f "$fw/agents/cfo/doc-map.tsv" ] && printf '%s' "$fw/agents/cfo/doc-map.tsv"
}

docs_for() {   # docs_for <改动路径> → 每行一个治理它的长期文档
  local c=$1 t area doc mod
  t=$(dm_table)
  if [ -n "$t" ]; then
    while IFS=$'\t' read -r area doc; do
      case "$area" in ''|'#'*) continue ;; esac
      [ -n "$doc" ] || continue
      # shellcheck disable=SC2053
      [[ "$c" == $area ]] && printf '%s\n' "$doc"
    done < "$t"
  fi
  case "$c" in
    code/*/code/*)
      mod=${c%%/code/*}; mod=${mod#code/}; mod="code/$mod"
      printf '%s\n' "$mod/module_docs/contract.md" "$mod/AGENTS.md" "$mod/文档地图.md" ;;
    codeagent/*|module_docs/*|code/*|review/*)
      # 模块仓内部（cwd 就是模块根）
      [ -d codeagent ] && printf '%s\n' "module_docs/contract.md" "AGENTS.md" "文档地图.md" ;;
  esac
}

dm_impact() {  # dm_impact <改动路径…> → "文档<TAB>因为改了什么(逗号分隔)"
  local -A why=()
  local c d
  for c in "$@"; do
    [ -n "$c" ] || continue
    while read -r d; do
      [ -n "$d" ] || continue
      [ -e "$d" ] || continue
      if [ -n "${why[$d]:-}" ]; then why["$d"]="${why[$d]}, $c"; else why["$d"]="$c"; fi
    done < <(docs_for "$c")
  done
  for d in "${!why[@]}"; do printf '%s\t%s\n' "$d" "${why[$d]}"; done | sort
}
