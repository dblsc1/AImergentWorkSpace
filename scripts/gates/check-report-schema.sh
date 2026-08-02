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
# 记下基线的**引用名**（origin/main 之类）。下面 check_one 会把 report_base 复用成
# 「这份报告自己填的 base」，名字被覆盖后就没法在报错里告诉人该拿什么去算了。
report_base_ref=$report_base
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
  local claimed_count unique_count rt_none
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
    # 本项目最高频的一个错：2026-08 一个月内四个不同角色犯了五次。
    # 病根是**直觉与判据不一致**：人想的是「我这次改动是从哪个 commit 开始的」，
    # 判据要的是「PR 目标基线」——在本仓 main 从不移动，那个值几乎恒定。
    # 五次同一个错 = 判据没说清，不是五个人都笨。所以这里**直接把正确值印出来**，
    # 把一道每次都要重新想的推理题变成一次复制粘贴。
    bad "git.base 不是 PR 目标 merge-base 的祖先（疑似 feature-only base）: $path"
    bad "  你填的 $(git rev-parse --short "$report_base" 2>/dev/null) 在 feat 分支上，不是分叉点。"
    bad "  判据要的不是「我的改动从哪开始」，是「这条分支从目标基线的哪一点分出去的」。"
    bad "  本仓当前的正确值（直接抄）：$(git rev-parse "$merge_base")"
    bad "  自己算：git merge-base HEAD $report_base_ref"
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

  # ── F6（2026-08-02）：consulter 的非审查轮次不必回填 review_target ──────
  # 病根：schema 把 consulter 与 programmer_reviewer / module_reviewer 同等对待，
  # 缺 review_target 即判否；而 consulter 的多数轮次（框架维护、架构裁决、调研入仓）
  # 根本不产生审查区间。commit ee5806a 的标题就是证据：「恢复 review_target
  # 最近审查区间——推送门 schema 要求」。**一个为满足门禁而回填的字段不再承载信息**，
  # 更糟的是它会被合并门当成真的审核凭据用。
  #
  # 修法不是「可以不写」，是「**必须显式声明**」：写 "review_target": null。
  # 省略仍然判否 —— **省略是疏忽，null 是决定，两者必须能区分开**。
  # reviewer 两角色不给这个口子：它们的每一轮按定义都产生审查区间。
  rt_none=0
  case "$expected_role" in
    programmer_reviewer|module_reviewer|consulter)
      if ! jq -e 'has("review_target")' >/dev/null <<<"$blob"; then
        bad "缺 review_target 键: $path"
        [ "$expected_role" = consulter ] &&
          bad "  非审查轮次也要显式写 review_target 为 null —— 省略是疏忽，null 是决定"
        return
      fi
      if jq -e '.review_target == null' >/dev/null <<<"$blob"; then
        if [ "$expected_role" != consulter ]; then
          bad "review_target 不得为 null: $path（reviewer 的每一轮按定义都产生审查区间）"
          return
        fi
        rt_none=1
      fi ;;
  esac

  if [ "$rt_none" -eq 0 ] &&
     { [ "$expected_role" = programmer_reviewer ] || [ "$expected_role" = module_reviewer ] ||
       [ "$expected_role" = consulter ]; } &&
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
  if [ "$rt_none" -eq 0 ] &&
     { [ "$expected_role" = programmer_reviewer ] || [ "$expected_role" = module_reviewer ] ||
       [ "$expected_role" = consulter ]; }; then
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
    # F7：两边都用 LC_ALL=C 排序。sort 走 LC_COLLATE，comm 按字节比 —— 两者不一致
    # 就会在**全绿时**往 stderr 吐三行 "comm: not in sorted order"。
    #
    # ⚠️ 成因不是非 ASCII（上一轮我判错了，实测证伪）。en_US.UTF-8 的排序规则
    #    **忽略前导标点、且不分大小写**，所以触发它的是最普通的两类路径：
    #      .gitignore  vs  code/x       —— 点被忽略 → locale 把 code/x 排前面
    #      scripts/README.md vs scripts/gates/a.sh —— 不分大小写 → README 排后面
    #    中文路径反而**不**触发（实测同序）。判成非 ASCII 会让人以为「本仓特有」，
    #    实际上任何有 dotfile 或大写文件名的仓都会中。
    #
    # 反向验证过这是噪音不是漏判（五种输入全部检出差异），但**绿灯里混着红色
    # stderr 会训练人忽略门禁输出**，而这道门恰恰是靠人读输出的。
    mismatch=$(comm -3 \
      <(sed '/^$/d' <<<"$actual_files" | LC_ALL=C sort -u) \
      <(sed '/^$/d' <<<"$claimed_files" | LC_ALL=C sort -u))
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
    # ── J3 新布局（2026-07-29 裁决）：留痕迁代码旁 ──
    module_docs/report.json) validate_common "$path" arbiter ;;
    code/*/report.json|code/*/*/report.json) validate_common "$path" programmer ;;
    review/reviewreport/report.json)
      canonical_review_changed=1
      validate_common "$path" programmer_reviewer
      ;;
    # ── 旧布局（过渡期兼容，仅存量模块；新模块一律新落点）──
    codeagent/programmer/docs/report.json) validate_common "$path" programmer ;;
    codeagent/programmer_reviewer/docs/report.json)
      canonical_review_changed=1
      validate_common "$path" programmer_reviewer
      ;;
    codeagent/module_reviewer/docs/report.json)
      canonical_review_changed=1
      validate_common "$path" module_reviewer
      ;;
    codeagent/arbiter/docs/report.json) validate_common "$path" arbiter ;;
    agents/cfo/docs/report.json) validate_common "$path" arbiter ;;
    agents/consulter/docs/findings/report.json) validate_common "$path" consulter ;;
    # 骨架占位不是审核产物，不触发 mirror-only 检查
    review/reviewreport/.gitkeep|review/reviewreport/*/.gitkeep) ;;
    review/reviewreport/*) review_artifact_changed=1 ;;
  esac
done < <(git -c core.quotePath=false diff --name-only --diff-filter=ACMRD \
  "$merge_base..$report_head")

if [ "$review_artifact_changed" -eq 1 ] && [ "$canonical_review_changed" -ne 1 ]; then
  bad "review/reviewreport/* 有变更，但本任务未同步 canonical review report（review/reviewreport/report.json，或存量模块的 codeagent/*_reviewer/docs/report.json）"
fi

[ "$fail" -eq 0 ] || exit 1
ok "仅当前任务变更的 canonical reports 全部合规"
