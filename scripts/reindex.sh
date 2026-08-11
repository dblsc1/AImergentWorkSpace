#!/usr/bin/env bash
# 生成全量留痕索引 → logs/INDEX.md
# 留痕文件本身保持分散（人类 2026-07-28 裁决 2）；本脚本只做索引，不搬文件。
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || printf '⏭  已跳过事件上报（缺 scripts/lib/emit.sh；只影响控制台可见性，不影响本次结果）\n' >&2
root=$(git rev-parse --show-toplevel) || exit 2
cd "$root"

printf '# 留痕索引\n\n> 本文件由 `scripts/reindex.sh` 生成，请勿手改。\n'
printf '> 留痕文件保持分散在各角色目录下，本表只提供导航。\n\n'
printf '| 最后改动 | 类型 | 角色/模块 | 路径 | commit |\n|---|---|---|---|---|\n'

emit() {
  local type=$1 path=$2
  local d c who
  d=$(git log -1 --format=%ad --date=short -- "$path" 2>/dev/null)
  c=$(git log -1 --format=%h -- "$path" 2>/dev/null)
  [ -n "$d" ] || { d="未提交"; c="—"; }
  who=$(sed -E 's|^(agents/cfo\|code)/||; s|/docs/.*$||; s|/module_docs/.*$||; s|/review/.*$||' <<<"$path")
  printf '| %s | %s | %s | `%s` | %s |\n' "$d" "$type" "$who" "$path" "$c"
}

# worklog 落点 J3 之后分裂成四种形状（module_docs/worklog/、code/<子文件夹>/worklog/、
# */docs/worklog/ 的项目级 + 旧布局兼容）——同一个坑在 60-doc-paths-exist.sh 撞过一次
# （CFO 2026-08-11 实测），这里是同一形状的第二个实例：漏掉前两种会让 J3 落点的
# worklog 静默不出现在索引里，不报错、只是「查不到」，比死链误判更隐蔽。
git ls-files -z | tr '\0' '\n' | while IFS= read -r f; do
  case "$f" in
    module_docs/worklog/*.md|code/*/worklog/*.md|*/docs/worklog/*.md) emit worklog "$f" ;;
    */report.json)         emit report "$f" ;;
    */reviewreport/*)      [ -f "$f" ] && emit reviewreport "$f" ;;
    */subreports/*)        [ -f "$f" ] && emit subreport "$f" ;;
    */handoff.md)          emit handoff "$f" ;;
  esac
done | grep -v '\.gitkeep' | sort -r

printf '\n生成时间：%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
