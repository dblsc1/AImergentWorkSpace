#!/usr/bin/env bash
# 判据：逃生口问责账本 logs/ledger.jsonl 必须被 Git 跟踪，且新增条目须随本次提交。
#
# ── 为什么这条无逃生口 ──────────────────────────────────────
# 逃生口的**全部合法性**建立在一句话上：「绕了会被记下来，事后可追」。
# 账本不入仓 = 这句话是假的 = **所有逃生口实际上是静默的**，而人还以为闸门在那儿。
# 这是「不报错所以没人知道它没生效」的最严重形态（2026-08-01 A-1 实证事故：
# 当天 15 次逃生口使用，因 diary 被 .gitignore 吞，按铁律18 全部等于没记账）。
#
# 允许用逃生口绕过「记录逃生口」的检查，是自指的荒谬。故本条不设逃生口。
#
# ── 两个账本，别混（见 lib/emit.sh 头部）─────────────────────
#   logs/diary.jsonl   事件流水，高频，**不入仓**（每写一行就动 git status，
#                      会把「活落盘了」类检查骗成恒真）。给控制台看，不是证据。
#   logs/ledger.jsonl  只记逃生口放行，低频，**必须入仓**。正常运行一行都不写。
[ "${1:-}" = --describe ] && { echo "16 问责账本：logs/ledger.jsonl 须被 Git 跟踪，新增逃生口记录须随本次提交（账本不入仓＝逃生口全是静默的）"; exit 0; }
set -uo pipefail
L=logs/ledger.jsonl

# 账本还没产生过（从未用过逃生口）——这是好事，不是缺件
[ -e "$L" ] || exit 0

if ! git ls-files --error-unmatch "$L" >/dev/null 2>&1; then
  echo "问责账本未被 Git 跟踪：$L" >&2
  echo "  → 逃生口的合法性全靠「绕了会被记下来」。账本不入仓 = 所有逃生口都是静默的。" >&2
  echo "     git add $L 并随本次提交；同时确认 .gitignore 没有忽略它" >&2
  exit 1
fi

# 有未提交的账本增量 = 这次绕过还没落地成证据。
# 允许它随**本次**提交一起走，但不许一直挂着——挂着的证据等于没有证据。
if [ -n "$(git status --porcelain -- "$L" 2>/dev/null)" ]; then
  if git diff -z --cached --name-only 2>/dev/null | tr '\0' '\n' | grep -qxF "$L"; then
    n=$(git diff --cached --numstat -- "$L" | awk '{print $1}')
    printf '  ℹ 本次提交带入 %s 条逃生口记录（已在暂存区，会随本次落地）\n' "${n:-?}" >&2
    exit 0
  fi
  echo "问责账本有未提交的新增记录：$L" >&2
  echo "  → 你（或本轮某个脚本）用过逃生口，但那条记录还没入仓。" >&2
  echo "     git add $L —— 未提交的证据不是证据" >&2
  exit 1
fi
exit 0
