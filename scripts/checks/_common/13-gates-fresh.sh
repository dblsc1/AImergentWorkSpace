#!/usr/bin/env bash
# 判据：模块里装的门禁脚本必须与框架当前版本一致。
#
# 病根：`install-gates.sh` 是**拷贝**，不是链接。框架修了门禁，模块里的副本原样不动 ——
# **模块的门禁停在装它那天的版本，而且没有任何提示**。
# 后果比"没装门禁"更隐蔽：门禁在跑、在绿，但跑的是旧判据。
# 本轮已实证同形：模块 run-gates 引用的 check-references 根本没被装进去，模块恒红半天没人知道。
[ "${1:-}" = --describe ] && { echo "13 门禁新鲜度：模块里装的门禁脚本必须与框架当前版本一致（拷贝会静默过期）"; exit 0; }
set -uo pipefail
root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$root"

# ── 活钩子自比（一切仓型都查，框架 checkout 也不豁免）──────────────
# 病根（2026-07-30 实证）：pull 只更新仓内 scripts/hooks/ 副本，**不更新 .git/hooks 里
# 真正在跑的那份**。根仓的 pre-commit 曾停在 306 字节旧版——停线旗在它自己仓里插不住，
# 而本检查当时把框架型仓整个豁免，看不见。活钩子 vs 本仓 tracked 副本，字节比对。
_hooks_dir=$(git rev-parse --path-format=absolute --git-path hooks)
_live_stale=()
for _h in pre-commit pre-push commit-msg; do
  [ -f "scripts/hooks/$_h" ] || continue
  git ls-files --error-unmatch "scripts/hooks/$_h" >/dev/null 2>&1 || continue
  if [ ! -f "$_hooks_dir/$_h" ] || ! cmp -s "scripts/hooks/$_h" "$_hooks_dir/$_h"; then
    _live_stale+=("$_h")
  fi
done
if [ ${#_live_stale[@]} -gt 0 ]; then
  echo "活钩子过期或缺失（.git/hooks 里真正在跑的与仓内 scripts/hooks/ 不一致）：${_live_stale[*]}" >&2
  echo "  → pull 更新的是仓内副本，不是活钩子——重跑 install-gates 刷新" >&2
  echo "  → 实证：旧 pre-commit 让停线旗在本仓插不住" >&2
  exit 1
fi

# 框架仓自己往下不适用（它就是门禁源头，无「副本 vs 框架」可比）
[ -d agents/roles ] && [ -d code/_template ] && exit 0

fw=${AIMERGENT_FRAMEWORK_ROOT:-}
[ -z "$fw" ] && [ -f .aimergent-framework ] && fw=$(cd "$(cat .aimergent-framework)" 2>/dev/null && pwd -P || true)
if [ -z "$fw" ] || [ ! -d "$fw/scripts" ]; then
  echo "解析不到框架根，无法核门禁新鲜度（缺 .aimergent-framework 或 AIMERGENT_FRAMEWORK_ROOT）" >&2
  echo "  → 模块与框架失联时，门禁会悄悄停在装它那天的版本" >&2
  exit 1
fi

stale=(); missing=(); untracked=()
while IFS= read -r rel; do
  [ -n "$rel" ] || continue
  case "$rel" in *.local.*) continue ;; esac      # *.local.* 是模块自有件（如本地豁免名单），内容本该不同
  [ -f "$rel" ] || continue                       # 模块没装这个文件，跳过（只装子集是正常的）
  [ -f "$fw/$rel" ] || continue
  # 副本必须已入仓（2026-07-30 实证：17 个 checks 全在工作区没提交，本检查算工作区
  # 哈希照样报绿——「被测的是没入仓的副本」，clone/换机即回到事故现场而 worklog 写着已修）
  git ls-files --error-unmatch "$rel" >/dev/null 2>&1 || { untracked+=("$rel"); continue; }
  a=$(sha256sum "$rel" 2>/dev/null | cut -d' ' -f1)
  b=$(sha256sum "$fw/$rel" 2>/dev/null | cut -d' ' -f1)
  [ "$a" = "$b" ] || stale+=("$rel")
done < <(cd "$fw" && find scripts/gates scripts/checks scripts/hooks scripts/lib -type f 2>/dev/null
         cd "$fw" && ls scripts/*.sh 2>/dev/null)

# 框架有、模块该有却没有的（本轮 check-references 就是这么漏的）
for must in scripts/gates/run-gates.sh scripts/gates/check-references.sh \
            scripts/gates/check-report-schema.sh scripts/mission_complete.sh; do
  [ -f "$fw/$must" ] || continue
  [ -f "$must" ] || missing+=("$must")
done

[ ${#stale[@]} -eq 0 ] && [ ${#missing[@]} -eq 0 ] && [ ${#untracked[@]} -eq 0 ] && exit 0
[ ${#missing[@]} -gt 0 ]   && { echo "模块缺这些门禁文件（框架有，这里没有）：" >&2; printf '  %s\n' "${missing[@]}" >&2; }
[ ${#stale[@]} -gt 0 ]     && { echo "模块的门禁副本已过期（框架已改，这里还是旧的）：" >&2; printf '  %s\n' "${stale[@]}" >&2; }
[ ${#untracked[@]} -gt 0 ] && { echo "门禁副本**未入仓**（在工作区跑着、在绿，但 clone/换机即消失）：" >&2; printf '  %s\n' "${untracked[@]}" >&2; echo "  → git add 提交它们——被测对象必须是仓里那份" >&2; }
echo "  → 跑：\$框架根/scripts/install-gates.sh <本模块相对路径>" >&2
echo "  → 门禁在跑、在绿，但跑的是旧判据 —— 这比没装更隐蔽" >&2
exit 1
