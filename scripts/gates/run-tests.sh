#!/usr/bin/env bash
# 递归发现模块内真实 Node/Python 测试与前端构建入口。
set -euo pipefail

ran=0
remote_exempt=scripts/gates/remote-test-exempt.txt
PYTHON=python
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python3
command -v "$PYTHON" >/dev/null 2>&1 || { echo "❌ 缺少 Python 3" >&2; exit 1; }

is_exempt() {
  local path=$1
  [ -f "$remote_exempt" ] && grep -qxF -- "$path" "$remote_exempt"
}

# J2/J5 测试分层：code/ 是 programmer 的单测；review/reviewcode/ 是 reviewer 的
# 整合级/契约测试（tests/）。push 前两层都跑 = 「本模块全量 + 契约测试」。
all=()
mapfile -d '' all < <(git ls-files -z -- code review/reviewcode 2>/dev/null || true)

# 项目若跟踪 scripts/tests/test-*.sh，在此作为可复现治理 fixture 运行。
ci_shell_tests=()
mapfile -d '' ci_shell_tests < <(git ls-files -z -- 'scripts/tests/test-*.sh' 2>/dev/null || true)
for script in "${ci_shell_tests[@]}"; do
  [ -x "$script" ] || { printf '❌ CI fixture 不可执行：%s\n' "$script" >&2; exit 1; }
  printf '▶️  bash %s\n' "$script"
  bash "$script"
  ran=1
done

for package in "${all[@]}"; do
  [ "${package##*/}" = package.json ] || continue
  dir=${package%/package.json}
  if is_exempt "$dir"; then
    printf '⏭️  远端测试豁免：%s（依赖本机只读遗留系统）\n' "$dir"
    continue
  fi

  deps=$(jq '((.dependencies // {}) + (.devDependencies // {})) | length' "$package")
  if [ -f "$dir/package-lock.json" ]; then
    (cd "$dir" && npm ci --no-audit --no-fund)
  elif [ "$deps" -gt 0 ]; then
    printf '❌ %s 声明依赖但没有 package-lock.json\n' "$dir" >&2
    exit 1
  fi

  for script in test lint build; do
    if jq -e --arg script "$script" '.scripts[$script] // empty' "$package" >/dev/null; then
      printf '▶️  %s: npm run %s\n' "$dir" "$script"
      (cd "$dir" && npm run "$script")
      ran=1
    fi
  done
done

shell_test_dirs=()
for script in "${all[@]}"; do
  [ "${script##*/}" = run_tests.sh ] || continue
  script_dir=${script%/run_tests.sh}
  printf '▶️  sh %s\n' "$script"
  sh "$script"
  shell_test_dirs+=("$script_dir")
  ran=1
done

requirements=()
python_files=()
python_tests=()
for f in "${all[@]}"; do
  [ "${f##*/}" = requirements.txt ] && requirements+=("$f")
  case "$f" in
    *.py) python_files+=("$f") ;;
  esac
  case "$f" in
    */test_*.py|*/tests/*.py|*_test.py) python_tests+=("$f") ;;
  esac
done

if [ "${#python_files[@]}" -gt 0 ]; then
  printf '▶️  Python compileall\n'
  "$PYTHON" -m compileall -q code
  ran=1
fi
if [ "${#python_tests[@]}" -gt 0 ]; then
  venv_dir=$(mktemp -d "${RUNNER_TEMP:-/tmp}/aimergent-ci-venv.XXXXXX")
  trap 'rm -rf -- "$venv_dir"' EXIT
  "$PYTHON" -m venv "$venv_dir"
  TEST_PYTHON="$venv_dir/bin/python"
  "$TEST_PYTHON" -m pip install --quiet --disable-pip-version-check pytest
  for req in "${requirements[@]}"; do
    printf '▶️  pip install -r %s\n' "$req"
    "$TEST_PYTHON" -m pip install --quiet --disable-pip-version-check -r "$req"
  done

  requirement_dirs=()
  for req in "${requirements[@]}"; do
    requirement_dirs+=("${req%/requirements.txt}")
  done

  for dir in "${requirement_dirs[@]}"; do
    has_tests=0
    for test_file in "${python_tests[@]}"; do
      case "$test_file" in "$dir"/*) has_tests=1 ;; esac
    done
    if [ "$has_tests" -eq 1 ]; then
      printf '▶️  pytest %s\n' "$dir"
      (cd "$dir" && "$TEST_PYTHON" -m pytest -q)
    fi
  done

  for test_file in "${python_tests[@]}"; do
    covered=0
    for dir in "${requirement_dirs[@]}"; do
      case "$test_file" in "$dir"/*) covered=1 ;; esac
    done
    for dir in "${shell_test_dirs[@]}"; do
      case "$test_file" in "$dir"/*) covered=1 ;; esac
    done
    if [ "$covered" -eq 0 ]; then
      test_dir=${test_file%/*}
      test_name=${test_file##*/}
      printf '▶️  pytest %s\n' "$test_file"
      (cd "$test_dir" && "$TEST_PYTHON" -m pytest -q "$test_name")
    fi
  done
  ran=1
fi

if [ "$ran" -eq 0 ]; then
  printf 'ℹ️  当前脚手架没有可在远端执行的测试/构建目标\n'
fi
