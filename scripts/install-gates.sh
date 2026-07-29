#!/usr/bin/env bash
# 将无外部密钥的 CI 和本地 Git hooks 安装到一个独立仓。
# 用法: install-ci.sh [--hook-only] <相对 AIMERGENT_WORKSPACE_ROOT 的仓路径|.>
set -euo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/lib/emit.sh" 2>/dev/null || true

die() { printf '❌ %s\n' "$*" >&2; exit 1; }

SCRIPT_PATH=$(readlink -f -- "${BASH_SOURCE[0]}") || die "无法解析脚本真实路径"
SCRIPT_DIR=$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)
PROJECT_ROOT=${AIMERGENT_PROJECT_ROOT:-$(git -C "$SCRIPT_DIR/.." rev-parse --show-toplevel 2>/dev/null || true)}
[ -n "$PROJECT_ROOT" ] && [ -d "$PROJECT_ROOT" ] || die "无法解析 starter Git 顶层；可设 AIMERGENT_PROJECT_ROOT"
PROJECT_ROOT=$(cd -- "$PROJECT_ROOT" && pwd -P)

WORKSPACE_ROOT=${AIMERGENT_WORKSPACE_ROOT:-$PROJECT_ROOT}
CI=${AIMERGENT_CI_SOURCE:-$SCRIPT_DIR}
[ -d "$WORKSPACE_ROOT" ] || die "目标工作区根不存在: $WORKSPACE_ROOT"
[ -d "$CI" ] || die "CI 源目录不存在: $CI"
WORKSPACE_ROOT=$(cd -- "$WORKSPACE_ROOT" && pwd -P)
CI=$(cd -- "$CI" && pwd -P)

hook_only=0
if [ "${1:-}" = --hook-only ]; then
  hook_only=1
  shift
fi
[ $# -eq 1 ] || die "用法: $0 [--hook-only] <相对仓路径|.>"
repo_arg=${1%/}
[ -n "$repo_arg" ] || die "仓路径不能为空"

path_slug='[a-z0-9]+([._-][a-z0-9]+)*'
if [ "$repo_arg" != . ]; then
  case "$repo_arg" in /*) die "仓路径必须相对于工作区根" ;; esac
  IFS='/' read -r -a repo_parts <<<"$repo_arg"
  for part in "${repo_parts[@]}"; do
    [[ "$part" =~ ^${path_slug}$ ]] || die "仓路径段不是小写 slug: $part"
  done
  mod="$WORKSPACE_ROOT/$repo_arg"
else
  mod=$WORKSPACE_ROOT
fi

mod_real=$(cd -- "$mod" 2>/dev/null && pwd -P || true)
case "$mod_real" in
  "$WORKSPACE_ROOT"|"$WORKSPACE_ROOT"/*) ;;
  *) die "仓路径越出工作区或经过 symlink: $mod" ;;
esac
mod_top=$(git -C "$mod_real" rev-parse --show-toplevel 2>/dev/null || true)
[ "$mod_top" = "$mod_real" ] &&
  [ "$(git -C "$mod_real" rev-parse --is-inside-work-tree 2>/dev/null || true)" = true ] ||
  die "不是独立 Git 工作树: $mod"

required=(
  hooks/pre-push hooks/commit-msg hooks/pre-commit hooks/cc-push-guard.sh
  arbiter-push.sh mission_complete.sh exam.sh dispatch.sh reindex.sh console.sh
)
if [ "$hook_only" -eq 0 ]; then
  required+=(
    workflows/ci.yml gates/run-gates.sh gates/run-tests.sh
    gates/check-report-schema.sh gates/check-references.sh gates/.gitleaks.toml
    gates/doc-path-exempt.txt gates/doc-map.tsv
    gates/legacy-path-exempt.txt gates/remote-test-exempt.txt
    gates/agent-attribution-activation module.gitignore
  )
fi
for source_file in "${required[@]}"; do
  [ -f "$CI/$source_file" ] || die "CI 源缺少文件: $CI/$source_file"
done

copy_file() {
  local source=$1 target=$2 source_real target_real
  source_real=$(readlink -f -- "$source")
  target_real=$(readlink -f -- "$target" 2>/dev/null || true)
  [ -n "$target_real" ] && [ "$source_real" = "$target_real" ] && return 0
  cp "$source" "$target"
}

if [ "$hook_only" -eq 0 ]; then
  mkdir -p "$mod_real/.github/workflows" "$mod_real/scripts/gates" "$mod_real/scripts/hooks"
  copy_file "$CI/workflows/ci.yml" "$mod_real/.github/workflows/ci.yml"
  for gate in run-gates.sh run-tests.sh check-report-schema.sh check-references.sh .gitleaks.toml \
    legacy-path-exempt.txt remote-test-exempt.txt agent-attribution-activation \
    doc-path-exempt.txt doc-map.tsv; do
    copy_file "$CI/gates/$gate" "$mod_real/scripts/gates/$gate"
  done
  for hook in pre-push commit-msg cc-push-guard.sh; do
    copy_file "$CI/hooks/$hook" "$mod_real/scripts/hooks/$hook"
  done
  copy_file "$CI/arbiter-push.sh" "$mod_real/scripts/arbiter-push.sh"
  chmod +x "$mod_real/scripts/gates/run-gates.sh" "$mod_real/scripts/gates/run-tests.sh" \
    "$mod_real/scripts/gates/check-references.sh" \
    "$mod_real/scripts/gates/check-report-schema.sh" "$mod_real/scripts/hooks/pre-push" \
    "$mod_real/scripts/hooks/commit-msg" "$mod_real/scripts/hooks/cc-push-guard.sh" \
    "$mod_real/scripts/arbiter-push.sh"
  if [ ! -f "$mod_real/.gitignore" ]; then
    copy_file "$CI/module.gitignore" "$mod_real/.gitignore"
  fi
fi

hooks_dir=$(git -C "$mod_real" rev-parse --path-format=absolute --git-path hooks)
mkdir -p "$hooks_dir"
for hook in pre-push commit-msg pre-commit; do
  copy_file "$CI/hooks/$hook" "$hooks_dir/$hook"
  chmod +x "$hooks_dir/$hook"
  cmp -s "$CI/hooks/$hook" "$hooks_dir/$hook" || die "$hook 安装核验失败"
done

origin=$(git -C "$mod_real" remote get-url origin 2>/dev/null || true)
# 控制台服务（零依赖，失败不阻断安装）
# 面板是可选件：可以跳过，但**必须明说跳过了**。
# 通则（铁律 23 的一个具体形状）：关键路径缺文件必须 die；可选件缺失必须打印"已跳过"。
# 唯一不许的是**静默跳过后照常报成功**。
if [ -x "$PROJECT_ROOT/control-panel/install.sh" ]; then
  "$PROJECT_ROOT/control-panel/install.sh" "${AIMERGENT_PANEL_INSTALL_ARGS:-}" 2>&1 | sed 's/^/   /' || true
else
  printf '   ⏭  控制台未安装（缺 control-panel/install.sh，可选件）\n'
fi

printf '✅ CI/Git 本地治理已安装入 %s\n' "$repo_arg"
if [ -n "$origin" ]; then
  printf '   origin: %s\n' "$origin"
else
  printf '   origin: 未配置（本地模式，安装仍成功）\n'
fi
printf '   pre-push + commit-msg + pre-commit: 字节一致且可执行\n'
if [ "$hook_only" -eq 0 ]; then
  printf '   tracked CI: gates + test + attribution marker\n'
else
  printf '   --hook-only: 保留仓内已有 CI 文件\n'
fi
