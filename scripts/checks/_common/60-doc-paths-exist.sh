#!/usr/bin/env bash
# 判据：文档里用反引号写出的仓内路径必须真实存在（防链接腐烂）。
[ "${1:-}" = --describe ] && { echo "60 路径可解析：文档中反引号内的仓内路径必须真实存在；有意的前向引用须登记 scripts/gates/doc-path-exempt.txt；测试 fixture 在行尾写 ref-fixture"; exit 0; }
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
    # 模块本地豁免（CFO 实报的缺口）：框架全局名单是拷贝副本，模块改它=制造漂移；
    # 本地名单 .local 不在 install-gates 安装集，13 号新鲜度不比对它，模块可自由登记。
    grep -qxF -- "$p" "${exempt%.txt}.local.txt" 2>/dev/null && continue
    echo "$f 引用了不存在的路径：$p" >&2
    echo "  → 有意的前向引用：框架仓登记 $exempt；模块仓登记 ${exempt%.txt}.local.txt（不随框架同步，不算漂移）" >&2
    fail=1
    # 与 gates/check-references.sh **同一个约定**（2026-08-02）：行尾写 ref-fixture
    # 的那一行，其中的路径是喂给判据的合成输入，不是引用。
    # 为什么 .md 也需要：判例库里「讲某个路径类缺陷」的条目会逐字引用那些路径，
    # 于是**讲缺陷的文档自己成为该缺陷的新实例**（判例库「字面量类」，第四次复发）。
    # 两个检查器共用一个词，不要各造一套——否则下一个人得记两套规矩。
  done < <(grep -v 'ref-fixture' "$f" |
           grep -oE '`(agents|code|logs|scripts)/[^`]+`' | tr -d '`' | sort -u)
done < <(staged_paths ACMR)
exit $fail
