#!/usr/bin/env bash
# 将无外部密钥的 CI 和本地 Git hooks 安装到一个独立仓。
# 用法: install-ci.sh [--hook-only] <相对 AIMERGENT_WORKSPACE_ROOT 的仓路径|.>
set -euo pipefail

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
  hooks/pre-push hooks/commit-msg hooks/cc-push-guard.sh arbiter-push.sh
)
if [ "$hook_only" -eq 0 ]; then
  required+=(
    workflows/ci.yml gates/run-gates.sh gates/run-tests.sh
    gates/check-report-schema.sh gates/.gitleaks.toml
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
  mkdir -p "$mod_real/.github/workflows" "$mod_real/ci/gates" "$mod_real/ci/hooks"
  copy_file "$CI/workflows/ci.yml" "$mod_real/.github/workflows/ci.yml"
  for gate in run-gates.sh run-tests.sh check-report-schema.sh .gitleaks.toml \
    legacy-path-exempt.txt remote-test-exempt.txt agent-attribution-activation; do
    copy_file "$CI/gates/$gate" "$mod_real/ci/gates/$gate"
  done
  for hook in pre-push commit-msg cc-push-guard.sh; do
    copy_file "$CI/hooks/$hook" "$mod_real/ci/hooks/$hook"
  done
  copy_file "$CI/arbiter-push.sh" "$mod_real/ci/arbiter-push.sh"
  chmod +x "$mod_real/ci/gates/run-gates.sh" "$mod_real/ci/gates/run-tests.sh" \
    "$mod_real/ci/gates/check-report-schema.sh" "$mod_real/ci/hooks/pre-push" \
    "$mod_real/ci/hooks/commit-msg" "$mod_real/ci/hooks/cc-push-guard.sh" \
    "$mod_real/ci/arbiter-push.sh"
  if [ ! -f "$mod_real/.gitignore" ]; then
    copy_file "$CI/module.gitignore" "$mod_real/.gitignore"
  fi
fi

hooks_dir=$(git -C "$mod_real" rev-parse --path-format=absolute --git-path hooks)
mkdir -p "$hooks_dir"
for hook in pre-push commit-msg; do
  copy_file "$CI/hooks/$hook" "$hooks_dir/$hook"
  chmod +x "$hooks_dir/$hook"
  cmp -s "$CI/hooks/$hook" "$hooks_dir/$hook" || die "$hook 安装核验失败"
done

origin=$(git -C "$mod_real" remote get-url origin 2>/dev/null || true)
printf '✅ CI/Git 本地治理已安装入 %s\n' "$repo_arg"
if [ -n "$origin" ]; then
  printf '   origin: %s\n' "$origin"
else
  printf '   origin: 未配置（本地模式，安装仍成功）\n'
fi
printf '   pre-push + commit-msg: 字节一致且可执行\n'
if [ "$hook_only" -eq 0 ]; then
  printf '   tracked CI: gates + test + attribution marker\n'
else
  printf '   --hook-only: 保留仓内已有 CI 文件\n'
fi
