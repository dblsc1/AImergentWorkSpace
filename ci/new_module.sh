#!/usr/bin/env bash
# 从当前 starter 的 module_template 创建独立模块仓。
# 用法: new_module.sh <相对 AIMERGENT_WORKSPACE_ROOT 的目标路径>
set -euo pipefail

die() { printf '❌ %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PROJECT_ROOT=${AIMERGENT_PROJECT_ROOT:-$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null || true)}
[ -n "$PROJECT_ROOT" ] && [ -d "$PROJECT_ROOT" ] || die "无法解析 starter Git 顶层；可设 AIMERGENT_PROJECT_ROOT"
PROJECT_ROOT=$(cd -- "$PROJECT_ROOT" && pwd -P)

WORKSPACE_ROOT=${AIMERGENT_WORKSPACE_ROOT:-$PROJECT_ROOT}
TEMPLATE=${AIMERGENT_TEMPLATE_ROOT:-$PROJECT_ROOT/module_template}
[ -d "$WORKSPACE_ROOT" ] || die "目标工作区根不存在: $WORKSPACE_ROOT"
[ -d "$TEMPLATE" ] || die "模板不存在: $TEMPLATE"
WORKSPACE_ROOT=$(cd -- "$WORKSPACE_ROOT" && pwd -P)
TEMPLATE=$(cd -- "$TEMPLATE" && pwd -P)

[ $# -eq 1 ] || die "用法: $0 <相对路径>  例: $0 modules/example_module"
target_arg=${1%/}
case "$target_arg" in
  ''|.|/*) die "目标必须是非空相对路径: $1" ;;
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

git -C "$dest" init -q -b main
printf '✅ 模块 %s 已创建: %s\n' "$name" "$dest"
printf '   framework: %s\n' "$framework_ref"
printf '   Git: 已以 main 初始化；模板改动只回到 %s\n' "$TEMPLATE"
