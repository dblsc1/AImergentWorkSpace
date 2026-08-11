#!/usr/bin/env bash
# 合并门的审核绑定判据 —— **唯一实现**，merge-to-integration.sh source 它。
#
# 为什么单独成文件：这段判据要能被 selftest 拿真实的 commit 链喂，
# 而 merge-to-integration.sh 从第一行就要 origin/PR号/dev 分支/工作区干净，
# 在沙箱里跑不到这个函数。判据留在那里 = 只能靠肉眼读，读不出方向反了
# （F5 就是这么躺了四天的）。搬出来之后 selftest #59 喂的是**这一份**，
# 不是抄一份等价逻辑去测（判例库「被测对象是哪一份」）。
#
# 依赖调用方先设好：repo / candidate / branch / main_sha；会写全局 reviewed。

reviewed=''
verify_report() {
  local path=$1 expected_role=$2 blob report_commit report_base changed bad diff_names current_blob committed_blob
  blob=$(git -C "$repo" show "$candidate:$path" 2>/dev/null) || return 1
  jq -e --arg role "$expected_role" --arg branch "$branch" --arg path "$path" '
    .schema_version == 2 and .status == "approved" and .role == $role and
    .git.protocol == "embedded-self-v2" and .git.head == "SELF" and
    .git.branch == $branch and
    .git.diff_mode == "contains" and
    (.git.base | type == "string" and test("^[0-9a-f]{40}$")) and
    (.git.changed_files | type == "array" and index($path) != null) and
    (if $role == "consulter" | not then
       any(.git.changed_files[]; startswith("codeagent/" + $role + "/docs/worklog/"))
     else
       any(.git.changed_files[]; startswith("agents/consulter/docs/worklog/"))
     end) and
    .review_target.branch == $branch and
    (.review_target.head | type == "string" and test("^[0-9a-f]{40}$")) and
    (.review_target.base | type == "string" and test("^[0-9a-f]{40}$"))
  ' >/dev/null <<<"$blob" || return 1
  reviewed=$(jq -r .review_target.head <<<"$blob")
  git -C "$repo" merge-base --is-ancestor "$reviewed" "$candidate" || return 1
  # ── F5（2026-08-02）：判据方向反了，不是不够严 ─────────────────────
  # 原来这里**只读 .review_target.head，从不读 .base**。前向那一头是有守的
  # （下面「未经审核的后续路径」按白名单卡 reviewed..candidate），
  # 但**后向那一头完全没守**：审得越窄越容易过。
  # 具体形状：分支上有 C1..C10，reviewer 只审最后一个（base=C9, head=C10），
  # 于是 reviewed..candidate 为空、白名单循环空转放行，C1..C9 九个 commit
  # 一次都没被审就合进去了。
  # 修法与下面 .git.base 那条对称：审核区间的起点必须在集成基线之前（或就是它），
  # 这样 review_base..reviewed 就覆盖了 main_sha..reviewed，
  # 前向的白名单再盖住 reviewed..candidate，两头合起来才是「审核覆盖了被合并的工作」。
  review_base=$(jq -r '.review_target.base // empty' <<<"$blob")
  [ -n "$review_base" ] || return 1
  if ! git -C "$repo" merge-base --is-ancestor "$review_base" "$main_sha"; then
    printf '  审核区间没覆盖被合并的工作：review_target.base=%s 不在集成基线 %s 之前\n' \
      "$(git -C "$repo" rev-parse --short "$review_base" 2>/dev/null || echo "$review_base")" \
      "$(git -C "$repo" rev-parse --short "$main_sha")" >&2
    printf '  即：这条分支上有一段 commit 从没被这次审核看过。审核区间要从分叉点起。\n' >&2
    return 1
  fi
  report_commit=$(git -C "$repo" log -1 --format=%H "$candidate" -- "$path")
  git -C "$repo" merge-base --is-ancestor "$reviewed" "$report_commit" || return 1
  report_base=$(jq -r .git.base <<<"$blob")
  git -C "$repo" merge-base --is-ancestor "$report_base" "$main_sha" || return 1
  git -C "$repo" merge-base --is-ancestor "$report_base" "$report_commit" || return 1
  diff_names=$(git -C "$repo" -c core.quotePath=false diff --name-only --no-renames "$report_base..$report_commit")
  while IFS= read -r changed; do
    [ -n "$changed" ] || continue
    grep -Fqx -- "$changed" <<<"$diff_names" || return 1
  done < <(jq -r '.git.changed_files[]' <<<"$blob")
  current_blob=$(git -C "$repo" rev-parse "$candidate:$path")
  committed_blob=$(git -C "$repo" rev-parse "$report_commit:$path")
  [ "$current_blob" = "$committed_blob" ] || return 1
  bad=0
  while IFS= read -r changed; do
    [ -n "$changed" ] || continue
    if [ "$expected_role" != consulter ]; then
      case "$changed" in
        codeagent/*/docs/*|review/*) ;;
        *) printf '  未经审核的后续路径: %s\n' "$changed" >&2; bad=1 ;;
      esac
    else
      case "$changed" in
        agents/consulter/docs/*|agents/cfo/docs/*) ;;
        *) printf '  未经审核的后续路径: %s\n' "$changed" >&2; bad=1 ;;
      esac
    fi
  done < <(git -C "$repo" -c core.quotePath=false diff --name-only --no-renames "$reviewed..$candidate")
  [ "$bad" -eq 0 ] || return 1
}
