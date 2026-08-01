#!/usr/bin/env bash
# arbiter-only push：唯一 sanctioned 路径。铸一次性 push-lease → fetch-then-push → 清 lease。
# 用法: scripts/arbiter-push.sh <git push 参数...>   例: scripts/arbiter-push.sh -u origin feat/x
set -euo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true
if [ "$#" -eq 0 ]; then echo "用法: arbiter-push.sh <git push 参数...>" >&2; exit 2; fi

# 停线旗：旗在 = 冻结推送（同 pre-commit，判据见 agents/protocol/supervision.md）
_sl_root=$(git rev-parse --show-toplevel 2>/dev/null || echo .)
if [ -f "$_sl_root/logs/STOPLINE" ]; then
  printf '🛑 停线中，拒绝推送。原因：\n' >&2; sed 's/^/   /' "$_sl_root/logs/STOPLINE" >&2; exit 1
fi

common_dir=$(git rev-parse --path-format=absolute --git-common-dir)
nonce=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
umask 077
token="$common_dir/aimergent-push-lease.$$"
printf '%s' "$nonce" > "$token"; chmod 600 "$token"
trap 'rm -f -- "$token"' EXIT

# ── 先审后推（2026-07-28 改）──────────────────────────────
# 本地 commit 已是不可变、有 SHA 的对象，审核绑它足够，不必先推。
# 先审后推的好处：被打回的活永远不上远端，返修不用 force-push，远端历史干净。
if [ "${AIMERGENT_PUSH_UNREVIEWED:-0}" = 1 ]; then
  # 记账（2026-08-01 补）：此前这条分支**完全不记账**——本文件唯一的 emit 挂在
  # PUSH_SKIP_GATES 上，UNREVIEWED 走过去悄无声息。实测当天用了约 10 次，零留痕。
  emit_override PUSH_UNREVIEWED "${AIMERGENT_PUSH_UNREVIEWED_REASON:-未给理由（建议设 AIMERGENT_PUSH_UNREVIEWED_REASON）}" 2>/dev/null || true
fi
if [ "${AIMERGENT_PUSH_UNREVIEWED:-0}" != 1 ]; then
  _root=$(git rev-parse --show-toplevel 2>/dev/null || echo .)
  _ok=0
  for _f in "$_root"/codeagent/*/docs/report.json; do
    [ -f "$_f" ] || continue
    if command -v jq >/dev/null &&
       jq -e '.role|test("reviewer")' "$_f" >/dev/null 2>&1 &&
       jq -e '.status=="approved"' "$_f" >/dev/null 2>&1; then _ok=1; break; fi
  done
  if [ "$_ok" -ne 1 ]; then
    echo "⚠️  没有找到 approved 的审核报告。" >&2
    echo "   流程是：programmer 本地 commit → review_start.sh → review_complete.sh(approved) → 本脚本推。" >&2
    echo "   确需先推（例如要让 CI 先跑）：AIMERGENT_PUSH_UNREVIEWED=1 scripts/arbiter-push.sh ..." >&2
    exit 1
  fi
fi

# ── 推之前先跑门禁（2026-07-28 补）──────────────────────────
# 缺口实证：本脚本原先只校验有没有 approved 审核，**不跑 run-gates.sh**，
# 于是「门禁红了照样能推上去」。我自己就干过一次。
# 逃生口 AIMERGENT_PUSH_SKIP_GATES=<理由>：放行但记账，绝不静默。
if [ "${AIMERGENT_PUSH_SKIP_GATES:-}" = "" ]; then
  _r=$(git rev-parse --show-toplevel 2>/dev/null || echo .)
  [ -x "$_r/scripts/gates/run-gates.sh" ] ||
    { echo "❌ 缺少 scripts/gates/run-gates.sh —— 无法在推送前验门禁，拒绝推送。" >&2
      echo "   确需绕过：AIMERGENT_PUSH_SKIP_GATES=\"<理由>\"（放行但记账）" >&2; exit 1; }
  if true; then
    echo "▶ 推送前门禁…" >&2
    if ! (cd "$_r" && ./scripts/gates/run-gates.sh >/tmp/aimergent-push-gates.$$ 2>&1); then
      sed 's/^/   /' /tmp/aimergent-push-gates.$$ >&2; rm -f /tmp/aimergent-push-gates.$$
      echo "❌ 门禁未通过，拒绝推送。" >&2
      echo "   确需绕过：AIMERGENT_PUSH_SKIP_GATES=\"<理由>\" scripts/arbiter-push.sh ...（放行但记账）" >&2
      exit 1
    fi
    rm -f /tmp/aimergent-push-gates.$$
    echo "  🟢 门禁通过" >&2
  fi
else
  echo "⚠️  跳过推送前门禁：$AIMERGENT_PUSH_SKIP_GATES（已记账）" >&2
  emit_override PUSH_SKIP_GATES "$AIMERGENT_PUSH_SKIP_GATES" 2>/dev/null || true
fi

# ── 推送前按整段区间复核文档影响面 ──────────────────────────
# 着陆检查只看**单次暂存**：一个 commit 里声明了，另一个 commit 改了别的却没声明，
# 分支整体就有漏。推送是最后一次能廉价补救的时机。
if [ "${AIMERGENT_PUSH_SKIP_GATES:-}" = "" ] && [ -x "$_r/scripts/doc_impact.sh" ]; then
  _base=$(git merge-base HEAD "${AIMERGENT_INTEGRATION_BRANCH:-dev}" 2>/dev/null ||
          git merge-base HEAD main 2>/dev/null || true)
  if [ -n "$_base" ]; then
    echo "▶ 整段区间的文档影响面（$(git rev-parse --short "$_base")..HEAD）：" >&2
    (cd "$_r" && ./scripts/doc_impact.sh --range "$_base..HEAD" 2>&1 | sed 's/^/   /') >&2
    echo "   ↑ arbiter 复核：上面每一份，本分支是否都已改到位或已在报告里声明。" >&2
  fi
fi

# fetch-then-push（铁律15④）：不猜 remote，取默认/跟踪 remote；失败不致命，server 端非 ff 兜底仍在
git fetch --quiet || true

AIMERGENT_PUSH_LEASE=1 \
AIMERGENT_PUSH_LEASE_NONCE="$nonce" \
AIMERGENT_PUSH_LEASE_TOKEN_FILE="$token" \
git push "$@"
