#!/usr/bin/env bash
# 检查单渲染 —— 起飞单（mission_start）与着陆单（mission_complete）共用同一套。
#
# 为什么做成检查单而不是"第一条不过就 die"：
#   die-on-first 只告诉你**第一个**问题，修完再跑又撞下一个，来回三四趟。
#   检查单一次跑完全部、一次列全，人看一眼就知道还差几件。
#   飞行检查单就是这么设计的：不是为了拦住你，是为了让你一次看清全貌。
#
# 约定：每项三段 —— 编号+名称 / 判据（做什么才算过）/ 没过时怎么办。
# **没过时怎么办是必填的**：只说哪里错、不说该怎么办，人会去改错的地方
#（已实证三次：改 .gitignore、去找文件、改 replace_token 都是被错误信息引偏的）。

CL_PASS=0; CL_FAIL=0; CL_SKIP=0
declare -a CL_FAILED_ITEMS=()

cl_header() {  # cl_header <标题> <副标题>
  printf '\n┌─ %s\n' "$1"
  [ -n "${2:-}" ] && printf '│  %s\n' "$2"
  printf '│\n'
}

cl_ok()   { printf '│  ✅ %-2s %s\n' "$1" "$2"; CL_PASS=$((CL_PASS+1)); }
cl_skip() { printf '│  ⏭  %-2s %s\n' "$1" "$2"; [ -n "${3:-}" ] && printf '│       └ %s\n' "$3"; CL_SKIP=$((CL_SKIP+1)); }
cl_bad() {  # cl_bad <编号> <名称> <哪里不对> <怎么办>
  printf '│  ❌ %-2s %s\n' "$1" "$2"
  [ -n "${3:-}" ] && printf '│       ├ 现状：%s\n' "$3"
  [ -n "${4:-}" ] && printf '│       └ 怎么办：%s\n' "$4"
  CL_FAIL=$((CL_FAIL+1)); CL_FAILED_ITEMS+=("$1 $2")
}
cl_note() { printf '│     %s\n' "$1"; }
cl_table_row() { printf '│     %-34s %s\n' "$1" "$2"; }

cl_footer() {  # cl_footer <过了怎么说> <没过怎么说>
  printf '│\n'
  if [ "$CL_FAIL" -eq 0 ]; then
    printf '└─ 🟢 %s（%d 过 · %d 跳过）\n\n' "$1" "$CL_PASS" "$CL_SKIP"
    return 0
  fi
  printf '└─ 🔴 %s —— 还差 %d 件：\n' "$2" "$CL_FAIL"
  printf '     · %s\n' "${CL_FAILED_ITEMS[@]}"
  printf '\n'
  return 1
}
