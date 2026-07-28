#!/usr/bin/env bash
# NUL 安全的路径取用 —— 所有 check 必须经此，不要直接 git diff --cached --name-only。
#
# 病根：`git diff --cached --name-only` 对非 ASCII 路径会加引号并 C 转义
#   "docs/worklog/2026-07-28-\344\273\273\345\212\241\345\215\225.md"
# 于是 `grep '\.md$'` 匹配不到（结尾是 .md"），中文留痕天然中招。
# 后果分两种，静默的那种更危险：
#   · 吵闹假阳性：合法文件被判越界（有人会来问）
#   · 静默假阴性：该查的文件被跳过，门禁照样报绿（没人会知道）
#
# 用法（一律用数组，不要用 $(...) 捕获——命令替换会吞掉 NUL）：
#   mapfile -t -d '' files < <(staged_paths ACMRD)
#   for f in "${files[@]}"; do ... done
staged_paths() {   # staged_paths [diff-filter]
  if [ -n "${1:-}" ]; then
    git diff --cached -z --name-only --diff-filter="$1"
  else
    git diff --cached -z --name-only
  fi
}
tracked_paths() { git ls-files -z ${1:+-- "$1"}; }

# 从数组里挑出匹配正则的项（替代对 NUL 流用 grep）
filter_paths() {   # filter_paths <正则> "${arr[@]}"
  local re=$1; shift
  local p; for p in "$@"; do [[ "$p" =~ $re ]] && printf '%s\n' "$p"; done
  return 0
}
