#!/usr/bin/env bash
# 引用完整性 —— 穷举，不抽样。
#
# 病根：改名/搬家会留下悬空引用，而它们**只在运行时炸**。
# 本轮六次重组（ci→scripts、roles→agents/roles、merge-to-main→merge-to-integration、
# install-ci→install-gates …）每次都留下引用，每次都没人查，
# 直到有人真去跑那条路径才发现。`install-ci.sh` 更恶劣：调用点是 `if [ -x … ]`，
# 文件不存在就**静默跳过，然后照样打印「门禁 已安装」**。
#
# selftest 的手写断言是「我被烧过的地方」；本脚本是「所有可能烧的地方」。
# 两者不重叠：断言查语义，本脚本查引用。
set -uo pipefail
root=$(git rev-parse --show-toplevel) || exit 2
cd "$root"
exempt=scripts/gates/doc-path-exempt.txt
fail=0
bad() { printf '  ❌ %s\n' "$*" >&2; fail=1; }

is_exempt() { grep -qxF -- "$1" "$exempt" 2>/dev/null; }
# 占位/通配/模板记法不是真实路径
is_literal() { case "$1" in *'}'*|*'<'*|*'{'*|*'*'*|*'$'*|*' '*|*'…'*) return 1 ;; esac; return 0; }

printf '── 引用完整性 ──\n'

# ① 脚本引用的仓内脚本/文件必须存在（install-ci.sh 就是死在这里）
while IFS= read -r -d '' f; do
  case "$f" in *.sh|*/hooks/*|*/commit-msg|*/pre-push|*/pre-commit) ;; *) continue ;; esac
  [ -f "$f" ] || continue
  while read -r p; do
    [ -n "$p" ] || continue
    is_literal "$p" || continue
    p=${p%/}
    [ -e "$p" ] && continue
    is_exempt "$p" && continue
    bad "$f 引用不存在的仓内路径: $p"
    # 注释行里的例子不是引用（文档性质的路径由 checks/60 在 .md 里查），
    # 且前缀必须落在词边界上 —— 否则 reviewcode/run_all.sh 会被误切成 code/run_all.sh
  done < <(grep -v '^[[:space:]]*#' "$f" |
           grep -oE '(^|[^A-Za-z0-9._/-])(scripts|agents|code|control-panel)/[A-Za-z0-9._/*?{}<>-]+' |
           sed -E 's/^[^A-Za-z]//' | sort -u)
done < <(git ls-files -z)

# ② 报告协议点名的 canonical 路径，其目录必须存在（目录不在 = agent 不会写）
if [ -f agents/protocol/report-schema.md ]; then
  while read -r p; do
    [ -n "$p" ] || continue
    d=$(dirname "$p")
    case "$d" in codeagent/*) continue ;; esac   # 模块内路径，建模块后才有
    [ -d "$d" ] || bad "report-schema 点名的 canonical 路径，目录不存在: $d"
  done < <(grep -oE '`[a-zA-Z0-9._/-]+/report\.json`' agents/protocol/report-schema.md | tr -d '`' | sort -u)
fi

# ③ 脚本里提到的角色，必须有对应角色卡
for r in $(grep -rhoE '\b(arbiter|programmer|programmer_reviewer|module_reviewer|consulter)\b' \
            scripts/*.sh scripts/checks/*/*.sh 2>/dev/null | sort -u); do
  [ -f "agents/roles/$r.md" ] || [ -f "agents/cfo/$r/AGENTS.md" ] ||
    bad "脚本引用角色 $r，但既无 agents/roles/$r.md 也无 agents/cfo/$r/AGENTS.md"
done

# ④ checks 目录里的每个脚本都必须满足 --describe 契约且可执行
while IFS= read -r -d '' c; do
  [ -x "$c" ] || { bad "check 不可执行: $c"; continue; }
  "$c" --describe >/dev/null 2>&1 || bad "check 不满足 --describe 契约: $c"
done < <(find scripts/checks -name '*.sh' -print0 2>/dev/null)

[ "$fail" -eq 0 ] && printf '  ✅ 全部引用可解析\n'
exit $fail
