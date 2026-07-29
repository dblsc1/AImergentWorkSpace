#!/usr/bin/env bash
# 判据（路签同形，2026-07-30 用户点破「过期便条该自堵不该巡逻」）：
# blockers/<id>.md = 一张「在等裁决/在阻塞」的便条，必须写明「解除标志：」——
# 一个仓内路径，或 <文字>@<文件>。标志一出现，本检查变红：**障已解未销**，
# 做完决定的那笔提交必须同批删掉便条，否则冷启动 agent 会停等一个已做完的决定
# （实证三例：§4.1 待改清单、nexus-core「等 CFO 裁决」、阻塞标记——全是解了没销）。
[ "${1:-}" = --describe ] && { echo "15 障签：blockers/ 便条的解除标志已出现则必须同批销障（过期便条自堵，不靠人巡逻）"; exit 0; }
set -uo pipefail
fail=0
while IFS= read -r -d '' b; do
  [ -f "$b" ] || continue
  mark=$(grep -m1 '^解除标志：' "$b" | sed 's/^解除标志：//; s/^[[:space:]]*//; s/[[:space:]]*$//')
  if [ -z "$mark" ]; then
    echo "$b 没写「解除标志：」——没有解除标志的障永远销不掉，等于把便条钉死" >&2
    fail=1; continue
  fi
  met=0
  case "$mark" in
    *@*)  _pat=${mark%@*}; _file=${mark##*@}
          [ -f "$_file" ] && grep -qF -- "$_pat" "$_file" && met=1 ;;
    *)    [ -e "$mark" ] && met=1 ;;
  esac
  if [ "$met" -eq 1 ]; then
    echo "障已解未销：$b 的解除标志（$mark）已出现——随本次提交删除这张便条" >&2
    echo "  → 过期便条比没便条更糟：下一个人会停等一个已做完的决定" >&2
    fail=1
  fi
done < <(git ls-files -z 'blockers/*.md' 2>/dev/null)
exit $fail
