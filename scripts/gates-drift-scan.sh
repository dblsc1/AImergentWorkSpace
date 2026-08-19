#!/usr/bin/env bash
# 遍历所有「已装门禁」的模块仓，逐个比对其门禁脚本副本与框架根是否一致。
#
# 病根（2026-08-19 实证）：checks/_common/13-gates-fresh.sh 只在**模块自己跑门禁时**
# 生效——如果一个模块长期靠 MISSION_OVERRIDE 跳过着陆检查（本轮撞见的正是这个温水
# 状态：6/7 模块的门禁副本停在装它那天的版本，每次提交都红、每次都 override，
# 没人去看那条红），13 号判据形同虚设。本脚本从框架根主动扫，不依赖模块自己触发。
#
#   scripts/gates-drift-scan.sh [code 目录路径]   默认框架根下的 code/
#
# 判据：目录里有 scripts/mission_start.sh 且自身是独立 git 仓 = 「已装门禁」。
# 不写死模块名单——新增模块会被自动发现，不会因为没登记而漏扫。
set -uo pipefail
die() { printf '❌ %s\n' "$*" >&2; exit 2; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
FW=$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null) || die "无法解析框架根（本脚本必须放在框架仓的 scripts/ 下）"
CODE_DIR=${1:-$FW/code}
[ -d "$CODE_DIR" ] || die "code 目录不存在: $CODE_DIR"

drift=0
found_any=0
report=()

for mod_path in "$CODE_DIR"/*/; do
  [ -d "$mod_path" ] || continue
  mod_path=${mod_path%/}
  modname=$(basename "$mod_path")
  # 装没装门禁的判据：mission_start.sh 存在 —— 这是 install-gates.sh 必装的核心脚本
  [ -f "$mod_path/scripts/mission_start.sh" ] || continue
  # 只扫独立子仓（.git 目录或 gitfile，worktree/submodule 都算）；不是仓的目录跳过
  [ -e "$mod_path/.git" ] || continue
  found_any=1

  # 只装了子集是正常的（install-gates.sh 只拷一份固定名单，不是整棵 scripts/ 树）——
  # 与 13-gates-fresh.sh 同一原则：模块没有的文件跳过，不算漂移；
  # 「框架有、模块本该有却没有」单独用下面的 must 名单查。
  while IFS= read -r rel; do
    [ -n "$rel" ] || continue
    case "$rel" in *.local.*) continue ;; esac   # *.local.* 是模块自有件，内容本该不同
    [ -f "$FW/$rel" ] || continue                # 框架根都没有的文件不比
    [ -f "$mod_path/$rel" ] || continue           # 模块没装这个文件，跳过（只装子集是正常的）
    a=$(sha256sum "$FW/$rel" 2>/dev/null | cut -d' ' -f1)
    b=$(sha256sum "$mod_path/$rel" 2>/dev/null | cut -d' ' -f1)
    if [ "$a" != "$b" ]; then
      report+=("漂移: 模块 $modname 的 $rel 与框架根不一致")
      drift=1
    fi
  done < <(cd "$FW" && find scripts/gates scripts/checks scripts/hooks scripts/lib -type f 2>/dev/null
           cd "$FW" && ls scripts/*.sh 2>/dev/null)

  # 框架有、模块该有却没有的（与 13-gates-fresh.sh 的 must 名单同源）
  for must in scripts/gates/run-gates.sh scripts/gates/check-references.sh \
              scripts/gates/check-report-schema.sh scripts/mission_complete.sh \
              scripts/mission_start.sh scripts/doc_impact.sh; do
    [ -f "$FW/$must" ] || continue
    if [ ! -f "$mod_path/$must" ]; then
      report+=("缺文件: 模块 $modname 缺 $must（框架有，模块没有）")
      drift=1
    fi
  done
done

if [ "$found_any" -eq 0 ]; then
  printf '⏭  没有发现任何已装门禁的模块仓（%s 下找不到含 scripts/mission_start.sh 的独立子仓）\n' "$CODE_DIR"
  exit 0
fi

if [ "$drift" -eq 1 ]; then
  printf '门禁脚本副本与框架根不一致：\n'
  printf '  %s\n' "${report[@]}"
  printf '  → 修法：cd %s && ./scripts/install-gates.sh code/<模块名>\n' "$FW"
  printf '  → 门禁在跑、在绿，但跑的是旧判据 —— 这比没装更隐蔽\n'
  exit 1
fi

printf '✅ 全部已装门禁的模块仓门禁副本与框架根一致\n'
exit 0
