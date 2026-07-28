#!/usr/bin/env bash
# 加一条闸门 —— 脚手，不用手写。
#   scripts/new_check.sh <层> <编号-名字>
#     层: _common | arbiter | programmer | programmer_reviewer | module_reviewer | module:<角色>
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true
die() { printf '❌ %s\n' "$*" >&2; exit 1; }
root=$(git rev-parse --show-toplevel) || die "不在 Git 仓内"; cd "$root"
layer=${1:?用法: new_check.sh <层> <编号-名字>}; name=${2:?}
case "$layer" in
  module:*) dir="codeagent/${layer#module:}/checks" ;;
  *)        dir="scripts/checks/$layer" ;;
esac
mkdir -p "$dir"; f="$dir/$name.sh"
[ -e "$f" ] && die "已存在: $f"
cat > "$f" <<'TPL'
#!/usr/bin/env bash
# 判据：<一句话写清这条拦什么>
[ "${1:-}" = --describe ] && { echo "<编号> <名字>：<一句话判据，会出现在 --list 与派单提示词里>"; exit 0; }
set -uo pipefail
# 通过 exit 0；失败 exit 非 0 并在 stderr 打印**可执行的**原因（说清怎么修，不只说哪里错）
staged=$(git diff --cached --name-only --diff-filter=ACMRD)
exit 0
TPL
chmod +x "$f"
"$f" --describe >/dev/null || die "新检查不满足 --describe 契约"
"$f" >/dev/null 2>&1 || die "新检查空跑就失败，先修好再提交"
printf '✅ 已生成: %s\n   改完跑一次 scripts/mission_complete.sh --list 确认它出现在判据里\n' "$f"
emit_event check_added "$f"
