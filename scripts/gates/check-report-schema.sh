#!/usr/bin/env bash
# 只检查当前任务相对 PR/main merge-base 新增或修改的 canonical reports。
set -uo pipefail

fail=0
bad() { printf '❌ report schema: %s\n' "$*" >&2; fail=1; }
ok() { printf '✅ report schema: %s\n' "$*"; }

report_base=${AIMERGENT_REPORT_BASE:-}
report_head=${AIMERGENT_REPORT_HEAD:-HEAD}
if ! git rev-parse --verify --quiet "$report_head^{commit}" >/dev/null; then
  bad "无法解析 report head: $report_head"
  exit 1
fi
if [ -z "$report_base" ] && [ -n "${GITHUB_BASE_REF:-}" ] &&
   git rev-parse --verify --quiet "origin/$GITHUB_BASE_REF^{commit}" >/dev/null; then
  report_base="origin/$GITHUB_BASE_REF"
elif [ -z "$report_base" ] &&
     git rev-parse --verify --quiet 'origin/main^{commit}' >/dev/null; then
  report_base=origin/main
elif [ -z "$report_base" ] &&
     git rev-parse --verify --quiet 'main^{commit}' >/dev/null; then
  report_base=main
fi

if [ -z "$report_base" ]; then
  bad "无法解析 PR/main 基线"
  exit 1
fi
merge_base=$(git merge-base "$report_head" "$report_base" 2>/dev/null || true)
if [ -z "$merge_base" ]; then
  bad "无法计算 merge-base: $report_base"
  exit 1
fi

# 空区间 = 假绿灯陷阱：直接在 main（或与 base 同点的分支）上工作时，
# merge-base 就是 head 本身，diff 为空 → 一份 report 都不验却退 0。
# 这种情况下门禁不是"验过了"，而是"无法确定任务区间"，必须响亮失败。
if [ "$(git rev-parse "$merge_base")" = "$(git rev-parse "$report_head^{commit}")" ]; then
  bad "任务区间为空（merge-base == head）——本次运行不会核验任何 canonical report。"
  bad "  成因通常是直接在 main 上提交（未走 feat/ 分支），或 base 与 head 同点。"
  bad "  正解：在 feat/ 分支上工作并以 main 为 base；确需在同分支核验时显式指定："
  bad "  AIMERGENT_REPORT_BASE=<任务起点40位sha> $0"
  exit 1
fi

validate_common() {
  local path=$1 expected_role=$2 blob report_commit report_base changed diff_names
  local review_base review_head actual_files claimed_files mismatch
  local claimed_count unique_count
  git cat-file -e "$report_head:$path" 2>/dev/null || {
    bad "canonical report 被删除或不可读: $path"
    return
  }
  report_commit=$(git log -1 --format=%H "$report_head" -- "$path")
  [ -n "$report_commit" ] || {
    bad "无法解析 canonical report 的 resolved_head: $path"
    return
  }
  blob=$(git show "$report_commit:$path" 2>/dev/null) || {
    bad "resolved_head 中 canonical report 不可读: $path@$report_commit"
    return
  }
  if ! jq -e --arg path "$path" --arg role "$expected_role" '
    .schema_version == 2 and
    (.module | type == "string" and length > 0) and
    .role == $role and
    (.task | type == "string" and length > 0) and
    (.tier == "simple" or .tier == "normal" or .tier == "hard") and
    (.status == "approved" or .status == "rejected" or
      .status == "blocked" or .status == "escalate") and
    (.summary | type == "string" and length > 0) and
    .git.protocol == "embedded-self-v2" and
    (.git.branch | type == "string" and length > 0) and
    (.git.base | type == "string" and test("^[0-9a-f]{40}$")) and
    .git.head == "SELF" and .git.diff_mode == "contains" and
    (.git.changed_files | type == "array" and index($path) != null) and
    (.contract.touched | type == "boolean") and
    (.contract.which | type == "array") and
    (.contract.consumes | type == "array") and
    (.cross_module_impact | type == "array") and has("escalation") and
    (if has("process_issues") then
       (.process_issues | type == "array") and
       all(.process_issues[];
         (.issue_id | type == "string" and length > 0) and
         (.status == "open" or .status == "closed"))
     else true end)
  ' >/dev/null <<<"$blob"; then
    bad "缺少 embedded-self-v2 必填字段或 path/role 不匹配: $path"
    return
  fi

  report_base=$(jq -r .git.base <<<"$blob")
  if ! git rev-parse --verify --quiet "$report_base^{commit}" >/dev/null; then
    bad "git.base 不可解析: $path@$report_base"
    return
  fi
  if ! git merge-base --is-ancestor "$report_base" "$report_commit"; then
    bad "git.base 不是 resolved_head 祖先: $path@$report_commit"
    return
  fi
  if ! git merge-base --is-ancestor "$report_base" "$merge_base"; then
    bad "git.base 不是 PR 目标 merge-base 的祖先（疑似 feature-only base）: $path"
    return
  fi
  diff_names=$(git -c core.quotePath=false diff --name-only --no-renames "$report_base..$report_commit")
  while IFS= read -r changed; do
    [ -n "$changed" ] || continue
    if ! grep -Fqx -- "$changed" <<<"$diff_names"; then
      bad "git.changed_files 不在 base..resolved_head: $path 声称 $changed"
      return
    fi
  done < <(jq -r '.git.changed_files[]' <<<"$blob")

  if { [ "$expected_role" = reviewagent ] || [ "$expected_role" = consulter ]; } &&
     ! jq -e '
       (has("target") | not) and
       (.review_target | type == "object") and
       (.review_target.branch | type == "string" and length > 0) and
       (.review_target.base | type == "string" and test("^[0-9a-f]{40}$")) and
       (.review_target.head | type == "string" and test("^[0-9a-f]{40}$")) and
       .review_target.diff_mode == "exact" and
       (.review_target.changed_files | type == "array") and
       all(.review_target.changed_files[]; type == "string" and length > 0)
     ' >/dev/null <<<"$blob"; then
    bad "独立 reviewer 必须使用标准 review_target（exact），禁止 target 等别名: $path"
    return
  fi
  if [ "$expected_role" = reviewagent ] || [ "$expected_role" = consulter ]; then
    review_base=$(jq -r .review_target.base <<<"$blob")
    review_head=$(jq -r .review_target.head <<<"$blob")
    if ! git rev-parse --verify --quiet "$review_base^{commit}" >/dev/null ||
       ! git rev-parse --verify --quiet "$review_head^{commit}" >/dev/null; then
      bad "review_target base/head 不可解析: $path"
      return
    fi
    if ! git merge-base --is-ancestor "$review_base" "$review_head"; then
      bad "review_target base 不是 head 祖先: $path"
      return
    fi
    claimed_count=$(jq '.review_target.changed_files | length' <<<"$blob")
    unique_count=$(jq '.review_target.changed_files | unique | length' <<<"$blob")
    if [ "$claimed_count" -ne "$unique_count" ]; then
      bad "review_target.changed_files 含重复路径: $path"
      return
    fi
    actual_files=$(git -c core.quotePath=false diff --name-only --no-renames \
      "$review_base..$review_head")
    claimed_files=$(jq -r '.review_target.changed_files[]' <<<"$blob")
    mismatch=$(comm -3 \
      <(sed '/^$/d' <<<"$actual_files" | sort -u) \
      <(sed '/^$/d' <<<"$claimed_files" | sort -u))
    if [ -n "$mismatch" ]; then
      bad "review_target.changed_files 与 base..head no-renames diff 不完全一致: $path"
      return
    fi
  fi
  ok "$path"
}

review_artifact_changed=0
canonical_review_changed=0
while IFS= read -r path; do
  [ -n "$path" ] || continue
  case "$path" in
    codeagent/backend/docs/report.json) validate_common "$path" backend ;;
    codeagent/frontend/docs/report.json) validate_common "$path" frontend ;;
    codeagent/reviewagent/docs/report.json)
      canonical_review_changed=1
      validate_common "$path" reviewagent
      ;;
    codeagent/arbiter/docs/report.json) validate_common "$path" arbiter ;;
    agents/cfo/arbiter/docs/report.json) validate_common "$path" arbiter ;;
    agents/cfo/consulter/docs/findings/report.json) validate_common "$path" consulter ;;
    # 骨架占位不是审核产物，不触发 mirror-only 检查
    review/reviewreport/.gitkeep|review/reviewreport/*/.gitkeep) ;;
    review/reviewreport/*) review_artifact_changed=1 ;;
  esac
done < <(git -c core.quotePath=false diff --name-only --diff-filter=ACMRD \
  "$merge_base..$report_head")

if [ "$review_artifact_changed" -eq 1 ] && [ "$canonical_review_changed" -ne 1 ]; then
  bad "review/reviewreport/* 有变更，但本任务未同步 canonical codeagent/reviewagent/docs/report.json"
fi

[ "$fail" -eq 0 ] || exit 1
ok "仅当前任务变更的 canonical reports 全部合规"
