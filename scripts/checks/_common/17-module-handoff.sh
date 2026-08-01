#!/usr/bin/env bash
# 判据：模块级 handoff（`module_docs/handoff.md`，arbiter 独占的冷启动接手便条）
# 必须**填实**，且契约变更时必须同批更新。
#
# ── 为什么单独一条，而不是并进 checks/14 ──────────────────────
# 14 管的是 **programmer 侧**的 `code/<子文件夹>/handoff.md`（代码怎么跑）。
# 本条管的是 **arbiter 侧**的 `module_docs/handoff.md`（模块是什么、对外给什么）。
# 两份文件、两个写属主、两种失效方式，合成一条会让报错说不清该谁去改。
#
# ── 2026-08-01 A-2 实证事故 ──────────────────────────────────
# 规范一直写着「handoff 每改必核」，而 14 只扫 code/ 那份、**从不看 module_docs/**。
# 结果：**六个模块的 module_docs/handoff.md 全是 15 行空模板**（与 code/_template/
# 逐字相同），建仓至今一次都没红过。新人按规范去查 canonical 交接文档 → 拿到空白页。
# 这是「不报错所以没人知道它没生效」的典型——闸门在撒谎。
[ "${1:-}" = --describe ] && { echo "17 模块 handoff：module_docs/handoff.md 必须填实（非空模板）；契约变更时须同批更新或在 report.json 表态"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"

[ -d codeagent ] && [ -d module_docs ] || exit 0   # 非模块仓（框架仓）不适用
H=module_docs/handoff.md
fail=0

# ① 必备件：不存在 = 红
if [ ! -f "$H" ]; then
  echo "缺少 $H —— 模块级一页纸是必备件，不是可选件" >&2
  echo "  → cp ../_template/module_docs/handoff.md $H 然后填实" >&2
  exit 1
fi

# ② 填实：占位符还在 = 红。
# 判据用**模板里的字面占位串**，不是行数——行数会因为随手加一行注释就变绿，
# 那种判据只能证明「有人动过」，不能证明「填实了」。
_ph=0
grep -q '{{'"MODULE_NAME"'}}' "$H" && _ph=1
grep -qE '^（.*是什么、对外提供什么、依赖谁）$' "$H" && _ph=1
grep -qE '^（启动命令、测试命令、自检门是什么）$' "$H" && _ph=1
grep -qE '^（指向 .module_docs/contract\.md.；关键端点/模块职责一句话）$' "$H" && _ph=1
grep -qE '^（踩过的坑、不能动的冻结点、已知技术债——让接手者别重踩）$' "$H" && _ph=1
if [ "$_ph" -eq 1 ]; then
  echo "$H 仍是未填实的模板（占位文字还在）" >&2
  echo "  → 这份是**冷启动接手便条**：新人只读它就该能上手。空模板 = 交接文档不存在。" >&2
  echo "     四小节都要填：一句话 / 怎么跑怎么测 / 接口 / 避坑与冻结点。" >&2
  fail=1
fi

# ③ 契约变更时必须同批更新（或在暂存的 report.json 里显式表态）
mapfile -t -d '' staged < <(staged_paths ACMR)
_contract_changed=0; _handoff_changed=0
for f in "${staged[@]}"; do
  [ "$f" = module_docs/contract.md ] && _contract_changed=1
  [ "$f" = "$H" ] && _handoff_changed=1
done
if [ "$_contract_changed" -eq 1 ] && [ "$_handoff_changed" -eq 0 ]; then
  declared=""
  for f in "${staged[@]}"; do
    [ "${f##*/}" = report.json ] || continue
    [ -f "$f" ] || continue
    declared+=$(command -v jq >/dev/null 2>&1 && jq -r '.docs_reviewed[]?.path // empty' "$f" 2>/dev/null || true)$'\n'
  done
  if ! grep -qxF "$H" <<<"$declared"; then
    echo "改了 module_docs/contract.md，但 $H 既没同批更新、也没在暂存 report.json 的 docs_reviewed 里表态" >&2
    echo "  → 契约变了而交接便条没变，下一个接手的人会照着过期的描述干活。" >&2
    fail=1
  fi
fi

exit $fail
