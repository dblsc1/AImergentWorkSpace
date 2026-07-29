#!/usr/bin/env bash
# 判据：文档里用反引号写出的仓内路径必须真实存在（防链接腐烂）。
[ "${1:-}" = --describe ] && { echo "60 路径可解析：文档中反引号内的仓内路径必须真实存在；有意的前向引用须登记 scripts/gates/doc-path-exempt.txt"; exit 0; }
set -uo pipefail
exempt=scripts/gates/doc-path-exempt.txt
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"
fail=0
while IFS= read -r -d '' f; do
  [ -n "$f" ] || continue
  case "$f" in *.md) ;; *) continue ;; esac
  # 历史叙事类不查：worklog / findings 记录的是当时的状态，
  # 强制它们指向现存路径 = 强制篡改历史，与「留痕不可篡改」直接冲突。
  case "$f" in */docs/worklog/*|*/docs/findings/*|*/docs/decisions/*) continue ;; esac
  [ -f "$f" ] || continue
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    case "$p" in *'<'*|*'{'*|*'*'*|*' '*) continue ;; esac
    p=${p%/}
    # 严格判据（人类 2026-07-28 裁决）：文件必须真实存在。
    # 「目录存在即可」放过了 scripts/merge-to-integration.sh 这类重组后的死链接 —— 已实证漏检。
    # 有意的前向引用（如报告协议里点名、但按任务才产出的 report.json）必须**显式登记**，
    # 登记本身就是可审计的：没登记的死链一律红。
    # 仓型类第 4 例：模块仓引用框架文档（agents/…、scripts/…）是级联的常态不是死链，
    # 判据＝「本仓或框架根可解析」（同 check-references 的口径）。
    resolves_here_or_framework "$p" && continue
    grep -qxF -- "$p" "$exempt" 2>/dev/null && continue
    echo "$f 引用了不存在的路径：$p" >&2
    echo "  → 若是有意的前向引用，登记进 $exempt；否则修正它" >&2
    fail=1
  done < <(grep -oE '`(agents|code|logs|scripts)/[^`]+`' "$f" | tr -d '`' | sort -u)
done < <(staged_paths ACMR)
exit $fail
