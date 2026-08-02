#!/usr/bin/env bash
# 项目级确定性门禁：任何模块 CI 都跑这一套（零 LLM，纯脚本）。
# 只扫描 Git 已跟踪文件，避免本地 node_modules、缓存与构建物造成假红。
set -uo pipefail

fail=0
say() { printf '%s\n' "$*"; }
bad() { printf '❌ %s\n' "$*"; fail=1; }
ok()  { printf '✅ %s\n' "$*"; }

# 仓型判断走公共件（判例库「判据仓型不匹配」已五次实证，一律不再各自手写）。
# **缺了必须响亮死**：`. lib/paths.sh` 失败而脚本继续跑，就成了铁律 23 点名的
# 「静默跳过后报成功」——下面每一道 gate 都靠 tracked，tracked 空就全体假绿。
_pl="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../lib" 2>/dev/null && pwd -P)/paths.sh"
[ -f "$_pl" ] || { printf '❌ 缺 scripts/lib/paths.sh —— 门禁没有仓型判断能力，拒绝以「什么都没扫」的姿态报绿\n' >&2; exit 2; }
. "$_pl" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }

# 扫描范围：**框架仓与模块仓不是同一件事**（2026-08-02 实证）。
#   · 模块仓：业务代码在 code/，只扫它——codeagent/ 装的是留痕，不该按代码判。
#   · 框架仓：code/ 装的是**模块仓**（各自独立 Git 仓，git ls-files 本来就看不见），
#     真正的源码在 scripts/。原来的判据是 `[ -d code ]`，而框架仓的 code/ 也存在，
#     于是三道 gate（禁默认值 / 禁止路径 / 单文件行数）实际只看 code/_template 下
#     26 个文件，对 1129 行的 scripts/selftest.sh 连着四天打印 ✅「无超限文件」。
#     **恒定答案 = 没有这道门**，而且比没有更糟：它还在发绿灯。
tracked=()
if repo_is_module; then
  mapfile -d '' tracked < <(git ls-files -z -- code)
else
  mapfile -d '' tracked < <(git ls-files -z)
fi

is_exempt() {
  local list=$1 path=$2
  [ -f "$list" ] && grep -qxF -- "$path" "$list"
}

say "── gate: commit agent 归属 ──"
attribution_marker=scripts/gates/agent-attribution-activation
attribution_hook=scripts/hooks/commit-msg
attribution_base=${AIMERGENT_ATTRIBUTION_BASE:-}
attribution_head=HEAD
trusted_pr_event=0
pr_event_base=''
pr_event_head=''
if [ "${GITHUB_ACTIONS:-}" = true ] &&
   [ "${GITHUB_EVENT_NAME:-}" = pull_request ] &&
   [[ "${GITHUB_REF:-}" =~ ^refs/pull/[1-9][0-9]*/merge$ ]] &&
   [ -n "${GITHUB_EVENT_PATH:-}" ] && [ -f "$GITHUB_EVENT_PATH" ] &&
   [ -n "${GITHUB_REPOSITORY:-}" ] &&
   [ -n "${GITHUB_SHA:-}" ] && [ -n "${GITHUB_HEAD_REF:-}" ] &&
   [ -n "${GITHUB_BASE_REF:-}" ]; then
  pr_event_repo=$(jq -er '.repository.full_name' "$GITHUB_EVENT_PATH" 2>/dev/null || true)
  pr_event_base=$(jq -er '.pull_request.base.sha' "$GITHUB_EVENT_PATH" 2>/dev/null || true)
  pr_event_head=$(jq -er '.pull_request.head.sha' "$GITHUB_EVENT_PATH" 2>/dev/null || true)
  pr_event_base_ref=$(jq -er '.pull_request.base.ref' "$GITHUB_EVENT_PATH" 2>/dev/null || true)
  pr_event_head_ref=$(jq -er '.pull_request.head.ref' "$GITHUB_EVENT_PATH" 2>/dev/null || true)
  if [ "$pr_event_repo" = "$GITHUB_REPOSITORY" ] &&
     [ "$pr_event_base_ref" = "$GITHUB_BASE_REF" ] &&
     [ "$pr_event_head_ref" = "$GITHUB_HEAD_REF" ] &&
     [[ "$pr_event_base" =~ ^[0-9a-f]{40}$ ]] &&
     [[ "$pr_event_head" =~ ^[0-9a-f]{40}$ ]] &&
     [[ "$GITHUB_SHA" =~ ^[0-9a-f]{40}$ ]] &&
     git cat-file -e "$pr_event_base^{commit}" 2>/dev/null &&
     git cat-file -e "$pr_event_head^{commit}" 2>/dev/null; then
    trusted_pr_event=1
  fi
fi

if [ -z "$attribution_base" ] && [ "$trusted_pr_event" -eq 1 ]; then
  attribution_base=$pr_event_base
elif [ -z "$attribution_base" ] && [ -n "${GITHUB_BASE_REF:-}" ] &&
   git rev-parse --verify --quiet "origin/$GITHUB_BASE_REF^{commit}" >/dev/null; then
  attribution_base="origin/$GITHUB_BASE_REF"
elif [ -z "$attribution_base" ] &&
     git rev-parse --verify --quiet 'origin/main^{commit}' >/dev/null; then
  attribution_base=origin/main
elif [ -z "$attribution_base" ] &&
     git rev-parse --verify --quiet 'main^{commit}' >/dev/null; then
  attribution_base=main
fi

if [ "$trusted_pr_event" -eq 1 ] &&
   [ "$(git rev-parse HEAD 2>/dev/null || true)" = "$GITHUB_SHA" ] &&
   [ "$(git rev-parse "$attribution_base^{commit}" 2>/dev/null || true)" = "$pr_event_base" ]; then
  synthetic_parents=()
  read -r -a synthetic_parents <<<"$(git show -s --format=%P HEAD 2>/dev/null || true)"
  if [ "${#synthetic_parents[@]}" -eq 2 ] &&
     [ "${synthetic_parents[0]}" = "$pr_event_base" ] &&
     [ "${synthetic_parents[1]}" = "$pr_event_head" ]; then
    attribution_head=$pr_event_head
    say "（可信 pull_request merge ref + event SHA + 双亲拓扑已确认；排除 GitHub synthetic merge commit，仅核真实 feature commits）"
  fi
fi

if [ -z "$attribution_base" ]; then
  if [ -f "$attribution_marker" ]; then
    bad "归属 marker 已存在，但无法解析 PR/main 基线"
  else
    say "（归属 marker 尚未安装，历史仓跳过）"
  fi
else
  attribution_merge_base=$(git merge-base "$attribution_head" "$attribution_base" 2>/dev/null || true)
  attribution_base_has_marker=0
  if git cat-file -e "$attribution_base:$attribution_marker" 2>/dev/null; then
    attribution_base_has_marker=1
  fi
  if [ -z "$attribution_merge_base" ]; then
    bad "无法计算归属校验 merge-base: $attribution_base"
  elif [ "$attribution_base_has_marker" -eq 1 ] &&
       ! git cat-file -e "$attribution_merge_base:$attribution_marker" 2>/dev/null; then
    bad "目标 main 已激活归属 marker；旧 feature 分支必须 rebase 后再验"
  elif git cat-file -e "$attribution_merge_base:$attribution_marker" 2>/dev/null; then
    if [ ! -f "$attribution_marker" ]; then
      bad "归属 marker 已在 main 激活，feature 分支不得删除"
    elif [ ! -x "$attribution_hook" ]; then
      bad "缺少可执行归属校验器: $attribution_hook"
    else
      attribution_hit=0
      while IFS= read -r commit; do
        [ -n "$commit" ] || continue
        if ! git show -s --format=%B "$commit" | "$attribution_hook" /dev/stdin; then
          subject=$(git show -s --format=%s "$commit")
          bad "commit 缺少合法唯一归属: $commit $subject"
          attribution_hit=1
        fi
      done < <(git rev-list --reverse "$attribution_merge_base..$attribution_head")
      [ "$attribution_hit" -eq 0 ] && ok "merge-base 后全部 feature commits 均有唯一合法归属"
    fi
  else
    if [ "$attribution_base_has_marker" -eq 0 ] && [ -f "$attribution_marker" ]; then
      say "（marker 由当前启用分支引入；本次不追溯历史，合入 main 后强制）"
    else
      say "（归属 marker 尚未安装，历史仓跳过）"
    fi
  fi
fi

say "── gate: canonical report schema ──"
if [ ! -x scripts/gates/check-report-schema.sh ]; then
  bad "缺少可执行 report schema gate: scripts/gates/check-report-schema.sh"
elif scripts/gates/check-report-schema.sh; then
  ok "当前任务 canonical reports 合规"
else
  bad "canonical report schema 未通过"
fi

say "── gate: 禁默认值 ──"
weak_hit=0
for f in "${tracked[@]}"; do
  case "$f" in *.example|*.example.*) continue ;; esac
  case "$f" in *.py|*.js|*.mjs|*.cjs|*.ts|*.tsx|*.json) ;; *) continue ;; esac
  [ -f "$f" ] || continue
  if grep -qiE '(change[-_]?me|replace[-_]?me|example[-_]?(secret|password)|dev[-_]?(secret|password))' "$f"; then
    printf '  matched: %s\n' "$f"
    weak_hit=1
  fi
done
[ "$weak_hit" -eq 0 ] && ok "无弱默认值/泄露值" || bad "已跟踪业务文件出现弱默认值/泄露值"

say "── gate: 项目声明的禁止路径 ──"
forbidden_prefix=${AIMERGENT_FORBIDDEN_PATH_PREFIX:-}
path_hit=0
path_exempt=scripts/gates/legacy-path-exempt.txt
if [ -z "$forbidden_prefix" ]; then
  say "（未设 AIMERGENT_FORBIDDEN_PATH_PREFIX，跳过项目特定路径扫描）"
else
  case "$forbidden_prefix" in /*) ;; *) bad "禁止路径前缀必须是绝对路径"; path_hit=1 ;; esac
  if [ "$path_hit" -eq 0 ]; then
    for f in "${tracked[@]}"; do
      [ -f "$f" ] || continue
      is_exempt "$path_exempt" "$f" && continue
      # 注释行豁免（CFO 2026-07-30 实报，字面量类）：判据防的是「用到的绝对路径
      # 换台机器就废」——注释/文档行里**讲**这个坑的路径没有可执行伤害，
      # 逼人改文案 = 判据不许人讨论问题本身。只扫非注释行。
      if grep -nF -- "$forbidden_prefix" "$f" | grep -vE '^[0-9]+:[[:space:]]*(#|//)'; then
        printf '  at %s\n' "$f"
        path_hit=1
      fi
    done
  fi
  [ "$path_hit" -eq 0 ] && ok "无未豁免禁止路径" || bad "已跟踪文件残留项目禁止路径"
fi

say "── gate: 单文件行数（C1 三档）──"
# C1（人类裁决 2026-07-29，台账 D12）原文三档，这里一档不改地实现：
#   ≤500          常态
#   501–1000      **必须在 report.json 里记一笔**（不是警告——记不到就是红）
#   >1000         没有说明余地，硬拦
# **治理对象只有源码**（人类裁决 2026-08-02，台账 D16）：500 行的约束本意是逼你拆职责，
# 那是代码的道理；长文档拆开反而伤可读性。散文与数据（.md/.txt/.jsonl…）不进这三档。
# D12 定的是分档，D16 定的是适用范围，两条各管一维、不冲突。
# D2/D14 保留唯一例外：**保险柜派生的存量导入文件**，顶部写明「下次重构拆掉」的
# 只警告不拦（偿还条件＝谁第一个为功能需求实质修改它，谁负责拆）。
#
# ── 这道 gate 以前为什么形同虚设（2026-08-02 实证，两个独立原因，任一个都足以让它哑）
#   ① tracked 在框架仓只装 code/ 下 26 个模板文件（见文件头部的修复）
#   ② 扩展名白名单只认 py/js/ts/html/css/vue/svelte —— `.sh` `.md` `.json` 全不看
# 两条叠加，1129 行的 scripts/selftest.sh 从来没进过它的视野，
# 而它每次都打印 ✅「无超限文件」。C1 从 2026-07-29 落地起就是散文。
#
# ── 豁免只有两类，都不是名单 ────────────────────────────────────
# 判例库教训：名单会慢慢长成一份混着「真的还没弄」与「按定义永远不适用」两种东西的
# 清单，再也分不开（doc-path-exempt 踩过）。所以这里**不读任何豁免名单**：
#   · 判据不适用：二进制 / 第三方产物 / 保险柜原件 —— 由**路径类与内容**自动判定
#   · D2 存量导入：由**文件自己顶部的标记**声明，改动的人一眼看得见
# 旧的 review/reviewcode/size_exempt.txt 不再被读；若它还在且里面有上面两类都
# 覆盖不到的条目，下面会逐条报红要求迁移——**不许静默作废别人的豁免**。
size_hit=0; size_scanned=0; size_skipped=0; size_d2=0; size_prose=0

# ── C1 的源码扩展名白名单：**唯一事实源，就这一处** ─────────────────
# 改判据 = 改这张表。别在别的脚本里另起一份——这条规矩是用两次事故买来的，方向相反：
#   · 表太窄 → 门变哑：上一版只认 py/js/ts/html/css/vue/svelte，`.sh` 不在里面，
#     1129 行的 scripts/selftest.sh 连着四天被打印成 ✅「无超限文件」。
#     **`.sh` 必须留在表里**（selftest 那次拆分正是这条判据该管的）。
#   · 表有两份 → 迟早分叉：模块模板 review/reviewcode/run_all.sh 原先另写一份
#     「全类型 >500 一律硬拦」，与 C1 三档相反、也与 D16 相反；那份已在本轮删除
#     （同一文件里就记着 abspath 判据两份副本分叉造成的 abspath-second-copy 事故）。
c1_source_ext=(sh bash zsh py js mjs cjs jsx ts tsx vue svelte html css scss less
               json yaml yml toml sql go rs rb java kt c h cc cpp hpp php)

c1_is_source() {   # 源码 → 0；散文/数据 → 1
  local f=$1 b ext e first=''
  b=${f##*/}
  case "$b" in
    *.*)
      ext=${b##*.}
      for e in "${c1_source_ext[@]}"; do [ "$ext" = "$e" ] && return 0; done
      return 1 ;;
  esac
  # 无扩展名但有 shebang 的可执行脚本同样是源码（scripts/aim、scripts/hooks/* 就是这样）。
  # 不认它，等于亲手挖一个和「.sh 不在表里」一模一样形状的新盲区。
  IFS= read -r first < "$f" 2>/dev/null || true
  case "$first" in '#!'*) return 0 ;; esac
  return 1
}

size_skip_reason() {   # 判据不适用 → 打印理由并返回 0
  local f=$1 d b
  case "$f" in
    */vendor/*|*/node_modules/*|*/.venv/*|*.min.*) printf '第三方产物'; return 0 ;;
  esac
  grep -Iq . -- "$f" 2>/dev/null || { printf '二进制'; return 0; }
  # 保险柜原件（铁律 22 固化的上游输入）：同目录 SHA256SUMS 点了它的名。
  # 这类按定义**不许动**——拆一行校验和就对不上，"删除前唯一的保险"当场变假。
  # 注意 C1 的例外说的是「保险柜**派生**的文件」，走下面的 D2 标记；
  # 原件根本不在 C1 的治理对象里，两者别混。
  d=$(dirname -- "$f"); b=$(basename -- "$f")
  if [ -f "$d/SHA256SUMS" ] && grep -qF -- " $b" "$d/SHA256SUMS"; then
    printf '保险柜原件（%s/SHA256SUMS 守着）' "$d"; return 0
  fi
  return 1
}

# 501–1000 那一档要能**核到那一笔**，所以先把全仓 report.json 声明过的路径收上来。
# 字段：`"oversize_files": [{"path":…, "lines":…, "why":…, "plan":…}]`（见 report-schema）。
declare -A size_declared=()
while IFS= read -r -d '' _rj; do
  while IFS= read -r _p; do
    [ -n "$_p" ] && size_declared["$_p"]=$_rj
  done < <(jq -r '(.oversize_files // [])[]? | .path // empty' "$_rj" 2>/dev/null)
done < <(git ls-files -z -- '*report.json')

# 行数 gate 的扫描集比 tracked 宽一点：模块仓里 review/reviewcode/ 的审核脚本与整合
# 测试也是源码，C1 一样管（模板里那条「全类型 >500」删掉之后，这里是它唯一的接盘人）。
# **不并进 tracked**：禁默认值/禁止路径两道 gate 扫 reviewcode 会把判据自己写的样例
# 字符串当成命中——模板的 abspath 判据就显式排除了 *reviewcode*，是同一个道理。
size_tracked=("${tracked[@]}")
if repo_is_module; then
  mapfile -d '' -O "${#size_tracked[@]}" size_tracked < <(git ls-files -z -- review)
fi

for f in "${size_tracked[@]}"; do
  [ -f "$f" ] || continue
  if _why=$(size_skip_reason "$f"); then size_skipped=$((size_skipped+1)); continue; fi
  if ! c1_is_source "$f"; then size_prose=$((size_prose+1)); continue; fi
  size_scanned=$((size_scanned+1))
  n=$(wc -l < "$f")
  [ "$n" -gt 500 ] || continue
  if head -20 -- "$f" | grep -q '下次重构拆掉'; then
    say "  ⚠️  存量导入（D2，只警告不拦）：$f ($n 行) —— 谁第一个实质改它，谁负责拆"
    size_d2=$((size_d2+1)); continue
  fi
  if [ "$n" -gt 1000 ]; then
    bad "超 1000 行必须分拆，C1 没有说明余地：$f ($n 行)"
    size_hit=1
  elif [ -n "${size_declared[$f]:-}" ]; then
    say "  ·  501–1000 已记一笔：$f ($n 行) ← ${size_declared[$f]}"
  else
    bad "超 500 行却没在任何 report.json 的 oversize_files 里记一笔：$f ($n 行)"
    say "     → 在自己的 report.json 加：{\"path\":\"$f\",\"lines\":$n,\"why\":\"…\",\"plan\":\"…\"}"
    size_hit=1
  fi
done

# 旧名单的迁移断言：不静默作废别人的豁免（判例库「半做比不做危险」）。
size_exempt_legacy=review/reviewcode/size_exempt.txt
if [ -f "$size_exempt_legacy" ]; then
  say "  ℹ️  $size_exempt_legacy 已停用（C1 的豁免改由路径类 + 文件顶部标记表达），逐条核它还有没有真在挡事："
  while IFS= read -r _p; do
    [ -n "$_p" ] || continue
    case "$_p" in \#*) continue ;; esac
    [ -f "$_p" ] || continue
    size_skip_reason "$_p" >/dev/null && continue
    c1_is_source "$_p" || { say "  ·  $_p 已不在 C1 治理范围（D16：只管源码），豁免自然作废"; continue; }
    bad "旧豁免名单里的 $_p 不属于「第三方产物/二进制/保险柜原件」——迁移前不许删名单：给它文件顶部加「下次重构拆掉」，或直接拆"
    size_hit=1
  done < "$size_exempt_legacy"
fi

# **必须打印被验对象**：只印一个 ✅ 而不说扫了什么，正是上一版能哑四天的原因。
# 白名单会**缩小**扫描集，所以被它挡下的数量必须和扫描数印在同一行——
# 否则下次表写窄了，界面上和「全都验过」一模一样。
say "  扫描 $size_scanned 个源码文件（跳过 $size_skipped 个：二进制/第三方产物/保险柜原件；$size_prose 个散文/数据按 D16 不在 C1 治理范围），存量导入 $size_d2 个"
[ "$size_hit" -eq 0 ] && ok "无超限文件（C1 三档：≤500 / 501–1000 已记一笔 / >1000 无）"

say "── gate: gitleaks 密钥扫描 ──"
if [ "${RUN_GITLEAKS_LOCAL:-0}" = 1 ] && command -v gitleaks >/dev/null 2>&1; then
  gitleaks detect --no-banner --config "$(dirname "$0")/.gitleaks.toml" || bad "gitleaks 命中"
else
  say "（GitHub Actions 由独立 action 扫新增变更；本地全历史扫描需 RUN_GITLEAKS_LOCAL=1）"
fi

say "── gate: 引用完整性 ──"
if [ -x scripts/gates/check-references.sh ]; then
  scripts/gates/check-references.sh || bad "引用完整性未通过（改名/搬家留下了悬空引用）"
else
  bad "缺少 scripts/gates/check-references.sh"
fi

say "── gate: hook 安装状态 ──"
hooks_dir=$(git rev-parse --path-format=absolute --git-path hooks 2>/dev/null || true)
missing_hooks=()
for h in pre-push commit-msg pre-commit; do
  [ -x "$hooks_dir/$h" ] || missing_hooks+=("$h")
done
if [ ${#missing_hooks[@]} -eq 0 ]; then
  ok "pre-push / commit-msg / pre-commit 均已安装"
else
  bad "hook 未安装: ${missing_hooks[*]} —— 本仓处于零保护状态，跑 scripts/install-gates.sh ."
fi

say "── gate: 模块 reviewcode ──"
if [ -x review/reviewcode/run_all.sh ]; then
  ./review/reviewcode/run_all.sh || bad "reviewcode/run_all.sh 未全绿"
else
  say "（无 run_all.sh，跳过）"
fi

printf '\n'
[ "$fail" -eq 0 ] && { say "🟢 全部门禁通过"; exit 0; }
say "🔴 门禁失败"
exit 1
