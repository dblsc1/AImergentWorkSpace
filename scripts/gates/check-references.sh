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

# ── 仓类型感知 ───────────────────────────────────────────────
# 同一份脚本会被装进模块仓。模块里没有 agents/（那在框架仓），
# 也只装了门禁子集（没有 install-gates / merge-to-integration）。
# 判据应当是「这个引用能不能在**够得着的地方**解析」，
# 而不是「一定在本仓」—— 否则装进模块就恒红，等于没有这道门。
framework=${AIMERGENT_FRAMEWORK_ROOT:-}
if [ -z "$framework" ] && [ -f .aimergent-framework ]; then
  framework=$(cd "$(cat .aimergent-framework)" 2>/dev/null && pwd -P || true)
fi
is_module=0
[ -d codeagent ] && [ -d module_docs ] && is_module=1

# 引用可解析 = 本仓有 或 框架根有
resolves() {
  [ -e "$1" ] && return 0
  [ -n "$framework" ] && [ -e "$framework/$1" ] && return 0
  return 1
}
bad() { printf '  ❌ %s\n' "$*" >&2; fail=1; }

is_exempt() { grep -qxF -- "$1" "$exempt" 2>/dev/null; }
# 占位/通配/模板记法不是真实路径
is_literal() { case "$1" in *'}'*|*'<'*|*'{'*|*'*'*|*'$'*|*' '*|*'…'*) return 1 ;; esac; return 0; }

printf '── 引用完整性（%s）──\n' "$([ "$is_module" -eq 1 ] && echo 模块仓 || echo 框架仓)"

# ① 脚本引用的仓内脚本/文件必须存在（install-ci.sh 就是死在这里）
while IFS= read -r -d '' f; do
  case "$f" in *.sh|*/hooks/*|*/commit-msg|*/pre-push|*/pre-commit) ;; *) continue ;; esac
  [ -f "$f" ] || continue
  while read -r p; do
    [ -n "$p" ] || continue
    is_literal "$p" || continue
    p=${p%/}
    resolves "$p" && continue
    is_exempt "$p" && continue
    if [ "$is_module" -eq 1 ] && [ -z "$framework" ]; then
      bad "$f 引用 $p，本仓没有且解析不到框架根（缺 .aimergent-framework 或 AIMERGENT_FRAMEWORK_ROOT）"
    else
      bad "$f 引用不存在的路径: $p（本仓与框架根都没有）"
    fi
    # 注释行里的例子不是引用（文档性质的路径由 checks/60 在 .md 里查），
    # 且前缀必须落在词边界上 —— 否则 reviewcode/run_all.sh 会被误切成 code/run_all.sh
    #
    # ── 测试 fixture 路径不是引用（2026-08-02，F3 落地时撞出来的一整类）──
    # **喂给路径判据的合成输入**会被本判据全数误报。selftest 要验「角色推断
    # 认不认 J3 的那几条 canonical 留痕路径」，就必须把它们写成字面量喂进去——
    # 它们是测试输入，不是引用，而且**按定义不该存在**：要它们真存在才能过，
    # 等于要求 selftest 往仓里种垃圾文件。
    #
    # 逐条登记进 doc-path-exempt.txt 只修实例（铁律 23 的反面教材）：下一个人
    # 写断言时照样撞，而且登记表会慢慢长成一张没人敢删的名单——里面混着
    # 「真的还没建」和「永远不会建」两种东西，再也分不开。
    # 所以给一个**行级**标记：就近声明、可 grep、**不能整文件豁免**。
    #
    # 用法：把 ref-fixture 这个词写在**那一行**的行尾注释里。
    # （本段自己是 `#` 开头的整行注释，早被上一条 grep 滤掉了——否则
    #  判例库「讲某个字面量的文档会成为那个字面量的新实例」又要复发一次。）
  done < <(grep -v '^[[:space:]]*#' "$f" | grep -v 'ref-fixture' |
           grep -oE '(^|[^A-Za-z0-9._/-])(scripts|agents|code|control-panel)/[A-Za-z0-9._/*?{}<>-]+' |
           sed -E 's/^[^A-Za-z]//' | sort -u)
done < <(git ls-files -z)

# ② 报告协议点名的 canonical 路径，其目录必须存在（目录不在 = agent 不会写）
schema=agents/protocol/report-schema.md
[ -f "$schema" ] || schema="${framework:-.}/agents/protocol/report-schema.md"
if [ -f "$schema" ] && [ "$is_module" -eq 0 ]; then
  while read -r p; do
    [ -n "$p" ] || continue
    d=$(dirname "$p")
    case "$d" in codeagent/*|module_docs|module_docs/*|review|review/*) continue ;; esac   # 模块内路径，建模块后才有（J3 后含 module_docs/ 与 review/）
    [ -d "$d" ] || bad "report-schema 点名的 canonical 路径，目录不存在: $d"
  done < <(grep -oE '`[a-zA-Z0-9._/-]+/report\.json`' "$schema" | tr -d '`' | sort -u)
fi

# ③ 脚本里提到的角色，必须有对应角色卡
for r in $(grep -rhoE '\b(arbiter|programmer|programmer_reviewer|module_reviewer|consulter)\b' \
            scripts/*.sh scripts/checks/*/*.sh 2>/dev/null | sort -u); do
  # 角色卡两处：模块级在 agents/roles/；项目级 CFO 与 consulter 各自成目录（二者平级）
  resolves "agents/roles/$r.md" || resolves "agents/$r/AGENTS.md" ||
    bad "脚本引用角色 $r，但 agents/roles/$r.md 与 agents/$r/AGENTS.md 在本仓与框架根都不存在"
done

# ④ checks 目录里的每个脚本都必须满足 --describe 契约且可执行
while IFS= read -r -d '' c; do
  [ -x "$c" ] || { bad "check 不可执行: $c"; continue; }
  "$c" --describe >/dev/null 2>&1 || bad "check 不满足 --describe 契约: $c"
done < <(find scripts/checks -name '*.sh' -print0 2>/dev/null)

[ "$fail" -eq 0 ] && printf '  ✅ 全部引用可解析\n'
exit $fail
