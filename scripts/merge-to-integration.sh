#!/usr/bin/env bash
# 唯一合法合并通道：审批绑定、门禁/测试、PR checks 均通过才 squash。
#
# 目标分支 = $AIMERGENT_INTEGRATION_BRANCH（默认 dev），**不是 main**。
#   模块 feature → dev   ← arbiter 权限，走本脚本
#   dev         → main  ← 只有用户测试通过才推，人的动作，agent 不得代劳
# 直接以 main 为目标会被本脚本拒绝。
set -euo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true

die() { printf '❌ %s\n' "$*" >&2; exit 1; }
ok() { printf '✅ %s\n' "$*"; }

[ $# -eq 2 ] || die "用法: $0 <相对仓路径|.> <PR号|feat/fix/chore分支>"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=${AIMERGENT_PROJECT_ROOT:-$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null || true)}
[ -n "$PROJECT_ROOT" ] && [ -d "$PROJECT_ROOT" ] || die "无法解析 starter Git 顶层；可设 AIMERGENT_PROJECT_ROOT"
PROJECT_ROOT=$(cd -- "$PROJECT_ROOT" && pwd -P)
WORKSPACE_ROOT=${AIMERGENT_WORKSPACE_ROOT:-$PROJECT_ROOT}
[ -d "$WORKSPACE_ROOT" ] || die "目标工作区根不存在: $WORKSPACE_ROOT"
WORKSPACE_ROOT=$(cd -- "$WORKSPACE_ROOT" && pwd -P)
repo_arg=$1
selector=$2
path_slug='[a-z0-9]+([._-][a-z0-9]+)*'

if [ "$repo_arg" = . ]; then
  repo=$WORKSPACE_ROOT
else
  case "$repo_arg" in ''|/*) die "仓路径必须是非空相对路径" ;; esac
  IFS='/' read -r -a repo_parts <<<"$repo_arg"
  for part in "${repo_parts[@]}"; do
    [[ "$part" =~ ^${path_slug}$ ]] || die "仓路径段不是小写 slug: $part"
  done
  repo="$WORKSPACE_ROOT/$repo_arg"
fi
repo_real=$(cd -- "$repo" 2>/dev/null && pwd -P || true)
case "$repo_real" in
  "$WORKSPACE_ROOT"|"$WORKSPACE_ROOT"/*) ;;
  *) die "仓路径越出工作区或经过 symlink: $repo" ;;
esac
repo_top=$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null || true)
[ -n "$repo_real" ] && [ "$repo_top" = "$repo_real" ] && \
  [ "$(git -C "$repo" rev-parse --is-inside-work-tree 2>/dev/null || true)" = true ] || \
  die "不是 git 工作树: $repo"
[ "$repo_real" = "$PROJECT_ROOT" ] && level=project || level=module
[ -z "$(git -C "$repo" status --porcelain=v1 --untracked-files=all)" ] || die "工作区不干净: $repo"

# 读取配置中的原始 URL；fetch 仍由 Git 自己处理 insteadOf/credential 等传输规则。
origin=$(git -C "$repo" config --get remote.origin.url 2>/dev/null || true)
[ -n "$origin" ] || die "缺少 origin: $repo_arg"
slug=''
case "$origin" in
  https://github.com/*.git) slug=${origin#https://github.com/}; slug=${slug%.git} ;;
  git@github.com:*.git) slug=${origin#git@github.com:}; slug=${slug%.git} ;;
esac

pr=''
branch=''
candidate=''
pr_json=''
fetch_origin_refs() {
  local name refspecs=()
  for name in "$@"; do
    refspecs+=("+refs/heads/$name:refs/remotes/origin/$name")
  done
  git -C "$repo" fetch --no-tags origin "${refspecs[@]}"
}

branch_holder_path() {
  local wanted=$1 field current=''
  while IFS= read -r -d '' field; do
    case "$field" in
      worktree\ *) current=${field#worktree } ;;
      "branch refs/heads/$wanted") printf '%s\n' "$current"; return 0 ;;
    esac
  done < <(git -C "$repo" worktree list --porcelain -z)
  return 1
}

main_holder_path() { branch_holder_path main; }

local_sync_error=''
preflight_branch_holder() {
  local wanted=$1 expected=$2 label=$3
  local holder holder_real holder_top holder_head holder_branch
  local_sync_error=''
  holder=$(branch_holder_path "$wanted" || true)
  [ -n "$holder" ] || return 0
  holder_real=$(cd -- "$holder" 2>/dev/null && pwd -P || true)
  holder_top=$(git -C "$holder" rev-parse --show-toplevel 2>/dev/null || true)
  if [ -z "$holder_real" ] || [ "$holder_top" != "$holder_real" ]; then
    local_sync_error="$label 不是可用的 Git 工作树: $holder"
    return 1
  fi
  holder_branch=$(git -C "$holder" symbolic-ref --quiet HEAD 2>/dev/null || true)
  if [ "$holder_branch" != "refs/heads/$wanted" ]; then
    local_sync_error="$label 未保持在预期分支 $wanted: $holder"
    return 1
  fi
  if [ -n "$(git -C "$holder" status --porcelain=v1 --untracked-files=all)" ]; then
    local_sync_error="$label 工作区不干净: $holder"
    return 1
  fi
  holder_head=$(git -C "$holder" rev-parse HEAD 2>/dev/null || true)
  if [ "$holder_head" != "$expected" ]; then
    local_sync_error="$label 未停在预期提交 $expected: $holder"
    return 1
  fi
}

preflight_main_holder() {
  preflight_branch_holder main "$main_sha" 'main holder'
}

preflight_task_holder() {
  preflight_branch_holder "$branch" "$candidate" 'task branch holder'
}

sync_local_main() {
  local target=$1 holder
  preflight_main_holder || return 1
  holder=$(main_holder_path || true)
  if [ -n "$holder" ]; then
    if ! git -C "$holder" merge --ff-only "$target" >/dev/null; then
      local_sync_error="main holder 无法 fast-forward 到 $target: $holder"
      return 1
    fi
  elif ! git -C "$repo" branch -f main "$target" >/dev/null; then
    local_sync_error="本地 main 引用无法更新到 $target"
    return 1
  fi
}

finish_task_branch() {
  local target=$1 task_holder task_holder_real main_holder
  preflight_task_holder || return 1
  task_holder=$(branch_holder_path "$branch" || true)
  main_holder=$(main_holder_path || true)
  if [ -n "$task_holder" ]; then
    task_holder_real=$(cd -- "$task_holder" && pwd -P) || {
      local_sync_error="task branch holder 无法解析物理路径: $task_holder"
      return 1
    }
    if [ "$task_holder_real" = "$repo_real" ] && [ -z "$main_holder" ]; then
      git -C "$task_holder" switch main >/dev/null || {
        local_sync_error="本地 task branch holder 无法切换到已同步 main"
        return 1
      }
    else
      git -C "$task_holder" switch --detach "$target" >/dev/null || {
        local_sync_error="task branch holder 无法 detach 到已同步 main $target: $task_holder"
        return 1
      }
    fi
  fi
  if git -C "$repo" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$repo" branch -D "$branch" >/dev/null || {
      local_sync_error="已合并任务分支无法在本地清理"
      return 1
    }
  fi
}

if [[ "$selector" =~ ^[1-9][0-9]*$ ]]; then
  [ -n "$slug" ] || die "PR 号只适用于 GitHub origin"
  pr=$selector
else
  git check-ref-format --branch "$selector" >/dev/null 2>&1 || die "非法分支名: $selector"
  case "$selector" in feat/*|fix/*|chore/*) ;; *) die "只允许 feat/fix/chore 任务分支" ;; esac
  branch=$selector
  if [ -n "$slug" ]; then
    command -v gh >/dev/null 2>&1 || die "GitHub origin 缺少 gh CLI"
    prs=$(gh pr list --repo "$slug" --state open --base main --head "$branch" --json number)
    count=$(jq 'length' <<<"$prs")
    [ "$count" -le 1 ] || die "分支对应多个 open PR，拒绝猜测"
    [ "$count" -eq 0 ] || pr=$(jq -r '.[0].number' <<<"$prs")
    [ -n "$pr" ] || die "GitHub 仓的任务分支必须先开 PR，不能降级为本地直推"
  fi
fi

if [ -n "$pr" ]; then
  command -v gh >/dev/null 2>&1 || die "缺少 gh CLI"
  pr_json=$(gh pr view "$pr" --repo "$slug" --json number,state,title,headRefName,headRefOid,baseRefName,isCrossRepository,author)
  [ "$(jq -r .state <<<"$pr_json")" = OPEN ] || die "PR #$pr 不是 OPEN"
  [ "$(jq -r .baseRefName <<<"$pr_json")" = main ] || die "PR #$pr 目标不是 main"
  [ "$(jq -r .isCrossRepository <<<"$pr_json")" = false ] || die "暂不接受 fork PR"
  branch=$(jq -r .headRefName <<<"$pr_json")
  case "$branch" in feat/*|fix/*|chore/*) ;; *) die "PR head 不是 feat/fix/chore 分支" ;; esac
  candidate=$(jq -r .headRefOid <<<"$pr_json")
  fetch_origin_refs main "$branch"
  [ "$(git -C "$repo" rev-parse "origin/$branch")" = "$candidate" ] || die "PR head 与 origin/$branch 不一致"
else
  git -C "$repo" show-ref --verify --quiet "refs/heads/$branch" || die "本地分支不存在: $branch"
  candidate=$(git -C "$repo" rev-parse "$branch")
  fetch_origin_refs main
fi

attribution_slug=$path_slug
branch_task=${branch#*/}
[[ "$branch_task" =~ ^${attribution_slug}$ ]] || die "任务分支后缀必须是可归属的小写 slug: $branch"
if [ "$level" = project ]; then
  project_module=${AIMERGENT_PROJECT_SLUG:-$(basename -- "$PROJECT_ROOT")}
  project_module=${project_module,,}
  [[ "$project_module" =~ ^${attribution_slug}$ ]] || die "项目名不是可归属 slug；可设 AIMERGENT_PROJECT_SLUG"
  squash_attribution="cfo-arbiter@$project_module+$branch_task"
else
  repo_module=$repo_arg
  module_pattern="^${attribution_slug}(/${attribution_slug})*$"
  [[ "$repo_module" =~ $module_pattern ]] || die "仓模块不是可归属路径 slug: $repo_module"
  squash_attribution="arbiter@$repo_module+$branch_task"
fi

verify_squash_attribution() {
  local commit=$1 parsed
  git -C "$repo" show -s --format=%B "$commit" | "$PROJECT_ROOT/ci/hooks/commit-msg" /dev/stdin || return 1
  parsed=$(git -C "$repo" show -s --format=%B "$commit" | git interpret-trailers --parse)
  [ "$(grep -Fxc -- "Agent-Attribution: $squash_attribution" <<<"$parsed")" -eq 1 ]
}

verify_main_reports() {
  local old_main=$1 new_main=$2
  [ -x "$PROJECT_ROOT/scripts/gates/check-report-schema.sh" ] || return 1
  (cd "$repo" &&
    AIMERGENT_REPORT_BASE="$old_main" AIMERGENT_REPORT_HEAD="$new_main" \
      "$PROJECT_ROOT/scripts/gates/check-report-schema.sh")
}

TARGET=${AIMERGENT_INTEGRATION_BRANCH:-dev}
case "$TARGET" in
  main|master) die "禁止用本脚本合入 $TARGET。$TARGET 只由人在用户测试通过后推进；agent 合入目标是 dev。" ;;
esac
git -C "$repo" rev-parse --verify --quiet "$TARGET" >/dev/null ||
  die "集成分支不存在: $TARGET（先建：git branch $TARGET）"
main_sha=$(git -C "$repo" rev-parse "$TARGET")
remote_main=$(git -C "$repo" rev-parse "origin/$TARGET" 2>/dev/null || echo "$main_sha")
[ "$main_sha" = "$remote_main" ] || die "本地 $TARGET 与 origin/$TARGET 不同步"
git -C "$repo" merge-base --is-ancestor "$main_sha" "$candidate" || die "任务分支不是当前 main 的后代"

# 审核绑定判据（含 F5 的审核覆盖断言）搬进 lib/review.sh —— **这里不再有第二份实现**。
# 关键路径缺文件必须 die：source 失败就没有 verify_report，approved 恒 0，
# 下面直接 die「没有精确绑定的 approved 审核」——fail-closed，不会静默放行。
. "$SCRIPT_DIR/lib/review.sh" || { printf '❌ %s：载入 review.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }

approved=0
if [ "$level" = module ]; then
  # 代码类交付由 programmer_reviewer 审；arbiter 交付 CFO 的任务由 module_reviewer 审规范面。
  # 任一份精确绑定 candidate 的 approved 报告即可放行。
  for _r in programmer_reviewer module_reviewer; do
    if verify_report "codeagent/$_r/docs/report.json" "$_r"; then
      approved=1; break
    fi
  done
else
  if verify_report agents/consulter/docs/findings/report.json consulter; then
    approved=1
  elif [ -n "$pr" ]; then
    author=$(jq -r .author.login <<<"$pr_json")
    reviews=$(gh api --paginate --jq '.[]' "repos/$slug/pulls/$pr/reviews")
    if jq -s -e --arg author "$author" --arg head "$candidate" '
      sort_by(.submitted_at) | group_by(.user.login) | map(last) |
      any(.state == "APPROVED" and .user.login != $author and .commit_id == $head)
    ' >/dev/null <<<"$reviews"; then
      approved=1
    fi
  fi
fi
[ "$approved" -eq 1 ] || die "没有与 $branch@$candidate 精确绑定的独立 approved 审核"
ok "审批已与任务 head 绑定"

test_tree=$(mktemp -d)
rmdir "$test_tree"
cleanup_test() { git -C "$repo" worktree remove --force "$test_tree" >/dev/null 2>&1 || true; }
trap cleanup_test EXIT
git -C "$repo" worktree add --detach "$test_tree" "$candidate" >/dev/null
[ -x "$test_tree/scripts/gates/run-gates.sh" ] || die "缺少可执行 run-gates.sh"
[ -x "$test_tree/scripts/gates/run-tests.sh" ] || die "缺少可执行 run-tests.sh"
(cd "$test_tree" && ./scripts/gates/run-gates.sh)
(cd "$test_tree" && ./scripts/gates/run-tests.sh)
if [ "$level" = project ]; then
  while IFS= read -r -d '' shell_file; do
    bash -n "$shell_file"
  done < <(find "$test_tree/scripts" -type f -name '*.sh' -print0)
  while IFS= read -r -d '' json_file; do
    jq empty "$json_file"
  done < <(find "$test_tree" -path "$test_tree/.git" -prune -o -type f -name '*.json' -print0)
fi
ok "本地 gates/tests 全绿"
cleanup_test
trap - EXIT

if [ -n "$pr" ]; then
  checks=$(gh pr view "$pr" --repo "$slug" --json statusCheckRollup)
  jq -e '
    .statusCheckRollup as $checks |
    ($checks | length) > 0 and
    all($checks[];
      if .__typename == "CheckRun" then
        .status == "COMPLETED" and .conclusion == "SUCCESS"
      else
        .state == "SUCCESS"
      end
    ) and
    any($checks[]; (.name // .context) == "gates") and
    any($checks[]; (.name // .context) == "test")
  ' >/dev/null <<<"$checks" || die "PR #$pr checks 未全部通过 gates/test"
  ok "PR #$pr checks 全绿"
  preflight_main_holder || die "合并前 main holder 预检失败: $local_sync_error"
  preflight_task_holder || die "合并前 task branch holder 预检失败: $local_sync_error"
  squash_subject=$(jq -r .title <<<"$pr_json")
  AIMERGENT_MERGE_GATE=1 gh pr merge "$pr" --repo "$slug" --squash --delete-branch \
    --match-head-commit "$candidate" --subject "$squash_subject (#$pr)" \
    --body "Agent-Attribution: $squash_attribution"
  [ "$(gh pr view "$pr" --repo "$slug" --json state --jq .state)" = MERGED ] || die "PR #$pr 未处于 MERGED"
  post_merge_fail() {
    die "PR #$pr 已确认 MERGED；后续仅本地终态复验失败，禁止回滚或重试合并: $*"
  }
  fetch_origin_refs "$TARGET" || post_merge_fail "无法刷新 origin/$TARGET"
  merged_sha=$(git -C "$repo" rev-parse "origin/$TARGET") || post_merge_fail "无法解析 origin/$TARGET"
  verify_squash_attribution "$merged_sha" || \
    post_merge_fail "远端 squash commit 未生成预期 Agent-Attribution"
  verify_main_reports "$main_sha" "$merged_sha" || \
    post_merge_fail "远端 main squash 后 canonical reports 终态复验失败"
  ok "远端 main canonical reports 已按旧 main..新 squash 终态复验"
  sync_local_main "$merged_sha" || post_merge_fail "$local_sync_error"
  finish_task_branch "$merged_sha" || post_merge_fail "$local_sync_error"
  ok "PR #$pr 已 squash merge"
  exit 0
fi

# 非 GitHub 本地 fixture/自托管仓：在隔离 worktree squash，再用一次性握手 push。
merge_tree=$(mktemp -d)
rmdir "$merge_tree"
cleanup_merge() { git -C "$repo" worktree remove --force "$merge_tree" >/dev/null 2>&1 || true; }
trap cleanup_merge EXIT
git -C "$repo" worktree add --detach "$merge_tree" main >/dev/null
git -C "$merge_tree" merge --squash "$candidate"
git -C "$merge_tree" commit -m "chore: squash $branch via merge gate" \
  -m "Agent-Attribution: $squash_attribution"
merge_sha=$(git -C "$merge_tree" rev-parse HEAD)
verify_squash_attribution "$merge_sha" || die "本地 squash commit 未生成预期 Agent-Attribution"
verify_main_reports "$main_sha" "$merge_sha" || \
  die "本地 squash 后 canonical reports 终态复验失败"
ok "本地 squash canonical reports 已按旧 main..新 squash 终态复验"
git -C "$repo" diff --quiet "$branch" "$merge_sha" || \
  die "squash 后树内容与任务分支不一致"
preflight_main_holder || die "push 前 main holder 预检失败: $local_sync_error"
preflight_task_holder || die "push 前 task branch holder 预检失败: $local_sync_error"
common_dir=$(git -C "$merge_tree" rev-parse --path-format=absolute --git-common-dir)
nonce=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
token=$(mktemp "$common_dir/aimergent-merge-gate.XXXXXX")
chmod 600 "$token"
printf '%s' "$nonce" >"$token"
trap 'rm -f -- "$token"; cleanup_merge' EXIT
AIMERGENT_MERGE_GATE=1 AIMERGENT_MERGE_GATE_TOKEN_FILE="$token" \
  AIMERGENT_MERGE_GATE_NONCE="$nonce" git -C "$merge_tree" push origin HEAD:main
post_push_fail() {
  die "远端 main 已推进到 $merge_sha；后续仅本地终态复验失败，禁止回滚或重试合并: $*"
}
verify_main_reports "$main_sha" "$merge_sha" || \
  post_push_fail "远端 push 后 canonical reports 终态复验失败"
rm -f -- "$token"
cleanup_merge
trap - EXIT
sync_local_main "$merge_sha" || post_push_fail "$local_sync_error"
finish_task_branch "$merge_sha" || post_push_fail "$local_sync_error"
ok "本地分支已 squash 并经一次性握手推送 main"
