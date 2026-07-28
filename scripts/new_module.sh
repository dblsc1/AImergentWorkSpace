#!/usr/bin/env bash
# 从当前 starter 的 code/_template 创建模块。
# 用法: new_module.sh <相对 AIMERGENT_WORKSPACE_ROOT 的目标路径>
set -euo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true

die() { printf '❌ %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=${AIMERGENT_PROJECT_ROOT:-$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null || true)}
[ -n "$PROJECT_ROOT" ] && [ -d "$PROJECT_ROOT" ] || die "无法解析 starter Git 顶层；可设 AIMERGENT_PROJECT_ROOT"
PROJECT_ROOT=$(cd -- "$PROJECT_ROOT" && pwd -P)

WORKSPACE_ROOT=${AIMERGENT_WORKSPACE_ROOT:-$PROJECT_ROOT}
TEMPLATE=${AIMERGENT_TEMPLATE_ROOT:-$PROJECT_ROOT/code/_template}
[ -d "$WORKSPACE_ROOT" ] || die "目标工作区根不存在: $WORKSPACE_ROOT"
[ -d "$TEMPLATE" ] || die "模板不存在: $TEMPLATE"
WORKSPACE_ROOT=$(cd -- "$WORKSPACE_ROOT" && pwd -P)
TEMPLATE=$(cd -- "$TEMPLATE" && pwd -P)

[ $# -eq 1 ] || die "用法: $0 <相对路径>  例: $0 modules/example_module"
target_arg=${1%/}
case "$target_arg" in
  ''|.|/*) die "模块名不能为空、不能是绝对路径: $1" ;;
  */*) ;;                       # 显式相对路径，按原样用
  *) target_arg="code/$target_arg" ;;   # 只给模块名 → 落到 code/ 下
esac

path_slug='[a-z0-9]+([._-][a-z0-9]+)*'
IFS='/' read -r -a target_parts <<<"$target_arg"
for part in "${target_parts[@]}"; do
  [[ "$part" =~ ^${path_slug}$ ]] || die "目标路径段不是小写 slug: $part"
done

dest="$WORKSPACE_ROOT/$target_arg"
[ ! -e "$dest" ] || die "已存在，拒绝覆盖: $dest"
name=${target_parts[${#target_parts[@]}-1]}

mkdir -p "$(dirname -- "$dest")"
cp -R "$TEMPLATE" "$dest"
framework_ref=$(realpath --relative-to="$dest" "$PROJECT_ROOT")

replace_token() {
  local token=$1 value=$2 escaped file_path
  escaped=${value//\\/\\\\}
  escaped=${escaped//&/\\&}
  escaped=${escaped//|/\\|}
  while IFS= read -r -d '' file_path; do
    sed -i "s|$token|$escaped|g" "$file_path"
  done < <(grep -rIlZ -F -- "$token" "$dest" 2>/dev/null || true)
}

replace_token '{{MODULE_NAME}}' "$name"
replace_token '{{FRAMEWORK_ROOT}}' "$framework_ref"

if grep -rIl -F -- '{{MODULE_NAME}}' "$dest" 2>/dev/null | grep -q . ||
   grep -rIl -F -- '{{FRAMEWORK_ROOT}}' "$dest" 2>/dev/null | grep -q .; then
  die "骨架占位符替换不完整: $dest"
fi

# ── 角色实例：交给 new_agent.sh 统一生成（角色卡填实 + system prompt + 出生考卷）──
for r in arbiter programmer programmer_reviewer; do
  AIMERGENT_FRAMEWORK_ROOT="$PROJECT_ROOT" "$PROJECT_ROOT/scripts/new_agent.sh" "$r" "$dest" >/dev/null ||
    die "角色生成失败: $r"
done


# 记下框架根的相对位置，供 dispatch.sh 解析角色卡（不硬编码绝对路径）
printf '%s\n' "$framework_ref" > "$dest/.aimergent-framework"

# ── Git：初始 commit，让模块交付时就处于「可直接跑门禁」状态 ──
# 不做初始 commit 会让新模块首次跑 check-report-schema.sh 撞一个语义对不上的红。
git -C "$dest" init -q -b main
if [ -x "$PROJECT_ROOT/scripts/install-gates.sh" ]; then
  AIMERGENT_WORKSPACE_ROOT="$WORKSPACE_ROOT" "$PROJECT_ROOT/scripts/install-gates.sh" "$target_arg" >/dev/null ||
    die "门禁安装失败: $dest"
fi
git -C "$dest" add -A
git -C "$dest" -c core.hooksPath=/dev/null commit -q -m "chore: 脚手生成 $name 模块骨架

Agent-Attribution: arbiter@$name+scaffold"
git -C "$dest" checkout -q -b feat/init

printf '✅ 模块 %s 已就绪: %s\n' "$name" "$dest"
printf '   framework : %s\n' "$framework_ref"
printf '   Git       : main 已有脚手 commit，当前在 feat/init 分支\n'
printf '   门禁      : 已安装（scripts/gates + hooks + pre-commit）\n'
printf '   角色      : arbiter / programmer / programmer_reviewer 已生成（含 .claude/agents 与出生考卷）\n'
printf '   下一步    : ① 填 module_docs/{contract,rules}.md（此前 reviewcode 会红，那是待办不是故障）\n'
printf '               ② scripts/dispatch.sh <角色> <任务单>\n'
printf '   模板改动只回到 %s\n' "$TEMPLATE"
