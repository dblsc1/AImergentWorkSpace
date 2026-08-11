#!/usr/bin/env bash
# NUL 安全的路径取用 —— 所有 check 必须经此，不要直接 git diff --cached --name-only。
#
# 病根：`git diff --cached --name-only` 对非 ASCII 路径会加引号并 C 转义
#   "docs/worklog/2026-07-28-\344\273\273\345\212\241\345\215\225.md"
# 于是 `grep '\.md$'` 匹配不到（结尾是 .md"），中文留痕天然中招。
# 后果分两种，静默的那种更危险：
#   · 吵闹假阳性：合法文件被判越界（有人会来问）
#   · 静默假阴性：该查的文件被跳过，门禁照样报绿（没人会知道）
#
# 用法（一律用数组，不要用 $(...) 捕获——命令替换会吞掉 NUL）：
#   mapfile -t -d '' files < <(staged_paths ACMRD)
#   for f in "${files[@]}"; do ... done
staged_paths() {   # staged_paths [diff-filter]
  if [ -n "${1:-}" ]; then
    git diff --cached -z --name-only --diff-filter="$1"
  else
    git diff --cached -z --name-only
  fi
}
tracked_paths() { git ls-files -z ${1:+-- "$1"}; }

# 从数组里挑出匹配正则的项（替代对 NUL 流用 grep）
filter_paths() {   # filter_paths <正则> "${arr[@]}"
  local re=$1; shift
  local p; for p in "$@"; do [[ "$p" =~ $re ]] && printf '%s\n' "$p"; done
  return 0
}

# ── 仓型感知与框架根解析（判据仓型不匹配类的公共修法）────────────────
# 病根（已四次实证）：按框架仓写的判据装进模块仓恒红，反之恒不适用。
# 同一份 check 会被 install-gates 拷进模块仓——判据必须先问「我在哪种仓」。
repo_is_module() { [ -d codeagent ] && [ -d module_docs ]; }   # 在仓根调用

# 框架根：env 优先，其次模块仓根的 .aimergent-framework 指针；框架仓自身返回 "."
framework_root() {
  if [ -n "${AIMERGENT_FRAMEWORK_ROOT:-}" ] && [ -d "${AIMERGENT_FRAMEWORK_ROOT}" ]; then
    (cd -- "$AIMERGENT_FRAMEWORK_ROOT" && pwd -P); return 0
  fi
  if [ -f .aimergent-framework ]; then
    (cd -- "$(cat .aimergent-framework)" 2>/dev/null && pwd -P) && return 0
  fi
  repo_is_module && return 1
  printf '.'
}

# ── 从留痕路径推断角色（F3，2026-08-02）────────────────────────────
# 病根：原判据只有一条正则 `s|^codeagent/([^/]+)/docs/.*|\1|p`，只认 J3 迁移**前**
# 的旧布局。裁决 J3 之后的五条 canonical 留痕路径实测**全部**推成空 → role=unknown，
# 而 unknown 是非空字符串，checks/05 于是去找 unknown.lease、找不到就判失败 ——
# 常规提交路径（不手动传 AIMERGENT_ROLE）被一条与自己无关的理由拦住。
#
# 为什么上一轮的修复没抓到：selftest 的沙箱暂存的恰好是
# `codeagent/programmer/docs/worklog/x.md` —— 五条里唯一还能命中旧正则的那条，
# 所以断言恒绿而现实全是 unknown。判例库「被测对象是哪一份」第三次实证。
#
# **两层优先级，不是一张平表**：
#   层① 显式角色目录  agents/<角色>/docs/…、codeagent/<角色>[/<编号>]/docs/…
#   层② 布局推导      module_docs/→arbiter、code/<子文件夹>/→programmer（**仅模块仓**）、
#                     review/→programmer_reviewer
# 层①命中就不看层②。理由：module_reviewer 的 canonical 报告在
# `codeagent/module_reviewer/docs/`，而它同时会碰 `review/reviewreport/`（共写区）——
# 平表会判成「两个候选、歧义」，把一个本来说得清的角色推成 unknown。
#
# **同层出现两个不同角色 = 歧义，返回空**。不许 `head -1` 挑第一个：
# 那是「静默给一个可能错的答案」，比说不知道更糟。
role_from_trace_path() {   # role_from_trace_path <仓根相对路径> → "<层><TAB><角色>"，认不出则空
  local p=${1#./}
  if [[ $p =~ ^agents/([^/]+)/docs/ ]]; then printf '1\t%s' "${BASH_REMATCH[1]}"; return 0; fi
  if [[ $p =~ ^codeagent/([^/]+)/([^/]+/)?docs/ ]]; then printf '1\t%s' "${BASH_REMATCH[1]}"; return 0; fi
  case "$p" in
    module_docs/*)          printf '2\tarbiter' ;;
    review/reviewcode/*|review/reviewreport/*) printf '2\tprogrammer_reviewer' ;;
    code/*)  repo_is_module && printf '2\tprogrammer' ;;   # 框架根的 code/ 装的是模块仓，不是代码侧
  esac
  return 0
}

# 从一批暂存路径定角色。歧义或认不出 → 退 1 且不输出（调用方负责响亮，不许静默放行）。
role_from_staged() {   # role_from_staged < <(路径，一行一个)
  local line lvl r t1="" t2="" amb=0
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    IFS=$'\t' read -r lvl r < <(role_from_trace_path "$line")
    [ -n "${r:-}" ] || continue
    if [ "$lvl" = 1 ]; then
      if [ -z "$t1" ]; then t1=$r; elif [ "$t1" != "$r" ]; then amb=1; fi
    else
      if [ -z "$t2" ]; then t2=$r; elif [ "$t2" != "$r" ]; then amb=2; fi
    fi
  done
  if [ -n "$t1" ]; then [ "$amb" = 1 ] && return 1; printf '%s' "$t1"; return 0; fi
  if [ -n "$t2" ]; then [ "$amb" = 2 ] && return 1; printf '%s' "$t2"; return 0; fi
  return 1
}

# 路径可解析＝本仓有，或框架根有（模块仓引用框架文档/脚本是常态，不是死链）
resolves_here_or_framework() {   # resolves_here_or_framework <仓内相对路径>
  [ -e "$1" ] && return 0
  local fr; fr=$(framework_root 2>/dev/null) || return 1
  [ -n "$fr" ] && [ -e "$fr/$1" ]
}

# ── 历史叙事类文档判据（worklog / findings / decisions）—— **唯一实现** ──────
# 判"这条仓内相对路径是不是记录过去状态的留痕文档"。强制叙事类文档指向现存
# 路径 = 强制篡改历史，与「留痕不可篡改」直接冲突，所以多处判据要把它排除在外
# （死链检查、文档同步反查、留痕索引……）。
#
# 病根（2026-08-11 CFO 实测）：这些地方原来各自写一份 `*/docs/worklog/*` 式的
# case 猜测，锚定的是 J3 迁移**前**的旧布局（`codeagent/<角色>/docs/worklog/`）。
# 裁决 J3 之后 canonical worklog 落点分裂成四种形状（`agents/protocol/report-schema.md`
# canonical path 表 + J3/J4 配套落点唯一事实）：
#   · module_docs/worklog/                  —— 模块 arbiter（J3，无 docs/ 中段）
#   · code/<子文件夹>/worklog/                —— programmer（J3，无 docs/ 中段）
#   · agents/<角色>/docs/{worklog,findings,decisions}/  —— 项目级角色（cfo/consulter）
#   · codeagent/<角色>[/<编号>]/docs/{worklog,findings,decisions}/  —— 旧布局兼容 + reviewer 实例
# 只排除 `*/docs/worklog/*` 只命中后两种；前两种没有 `docs/` 中段，
# 于是「如实记录已删除路径」的 J3 落点 worklog 被判成引用了死链——
# 惩罚的恰恰是准确留痕（已实证：nexus-core 模块 arbiter 只能靠模块本地
# doc-path-exempt.local.txt 逃生口绕过，J3 之后的每个模块都会撞上同一个坑）。
#
# 新增落点只改这一处；不要在别的判据里重新长出一份 case 猜测。
is_narrative_doc_path() {   # is_narrative_doc_path <仓根相对路径>
  local p=${1#./}
  case "$p" in
    module_docs/worklog/*) return 0 ;;   # J3 模块级（无 docs/ 中段）
    code/*/worklog/*) return 0 ;;        # J3 代码侧（code/<子文件夹>/worklog/，无 docs/ 中段）
    */docs/worklog/*|*/docs/findings/*|*/docs/decisions/*) return 0 ;;  # 项目级角色 + 旧布局兼容
  esac
  return 1
}
