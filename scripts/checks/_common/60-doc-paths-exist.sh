#!/usr/bin/env bash
# 判据：文档里用反引号写出的仓内路径必须真实存在（防链接腐烂）。
[ "${1:-}" = --describe ] && { echo "60 路径可解析：文档中反引号内以 agents/ code/ logs/ scripts/ 开头的路径，其所在目录必须真实存在"; exit 0; }
set -uo pipefail
fail=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  [ -f "$f" ] || continue
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in *'<'*|*'{{'*|*'*'*|*' '*) continue ;; esac
    p=${p%/}
    # 判据是「家在不在」，不是「文件在不在」：
    #   · report.json 这类按任务产出的文件，此刻不存在是正常的——但它的目录必须存在
    #     （目录不存在 → agent 根本不会写，这正是本轮最大的坑）
    #   · code/backend 这类模块内相对路径，在框架根不存在也属正常
    [ -e "$p" ] && continue
    [ -d "$(dirname "$p")" ] && continue
    echo "$f 引用的路径连所在目录都不存在：$p" >&2; fail=1
  done < <(grep -oE '`(agents|code|logs|scripts)/[^`]+`' "$f" | tr -d '`' | sort -u)
done < <(git diff --cached --name-only --diff-filter=ACMR | grep -E '\.md$' || true)
exit $fail
