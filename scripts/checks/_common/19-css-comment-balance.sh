#!/usr/bin/env bash
# 判据：CSS 注释开闭必须配平。
#
# ── 实证（2026-08-08，auth 模块，cockpit-v1 换皮夜班）─────────────
# 兜底 tokens 块的中文注释里写了 `--plan*/--fact*`——`*/` 把注释提前闭合，
# 后半段落进选择器位置，浏览器按「无效规则」静默丢弃，**整个 light 主题
# :root 块蒸发**。无报错、页面照常渲染、只是颜色全错。靠 cssRules 数量
# 对不上才抓到。四个前端仓都在按同一份契约写同款长注释兜底块，这是类，
# 不是实例（铁律 23）。
#
# 判据形状：数 `/*` 与 `*/` 出现次数，不等即红。
# 已知误报面：content:"*/" 这类字符串字面量——本仓样式无此用法；真撞上走
# 统一逃生口（mission_complete 的 override，记账）。宁可误报可绕，不可漏报静默。
#
# 测试口：--files <f...> 显式指定文件（绕过暂存区，供 selftest 喂 fixture）。
[ "${1:-}" = --describe ] && { echo "19 CSS 注释配平：暂存 .css 的 /* 与 */ 数量必须相等（注释里的 */ 会静默吞掉整段规则；实证 2026-08-08 auth）"; exit 0; }
set -uo pipefail
_here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)

_files=()
if [ "${1:-}" = --files ]; then
  shift; _files=("$@")
else
  # 公共件缺失必须响亮死掉（S1 类）。
  . "$_here/../../lib/paths.sh" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
  while IFS= read -r -d '' f; do
    case "$f" in *.css) _files+=("$f");; esac
  done < <(staged_paths ACMR)
fi

[ "${#_files[@]}" -gt 0 ] && [ -n "${_files[0]}" ] || exit 0

bad=0
for f in "${_files[@]}"; do
  [ -f "$f" ] || continue
  o=$(grep -o '/\*' -- "$f" | wc -l)
  c=$(grep -o '\*/' -- "$f" | wc -l)
  if [ "$o" -ne "$c" ]; then
    printf '❌ %s：注释开 %d 个、闭 %d 个 —— 注释里的 */ 会提前闭合并静默吞掉后续整段规则\n' "$f" "$o" "$c" >&2
    bad=1
  fi
done
if [ "$bad" -ne 0 ]; then
  printf '  → 注释里别写裸 `*/`（「--plan*/--fact*」写成「--plan 族/--fact 族」）\n' >&2
  printf '  → 浏览器不报错，只是整块规则蒸发 —— 比崩溃更糟（实证：auth 丢了整个 light 主题）\n' >&2
  exit 1
fi
exit 0
