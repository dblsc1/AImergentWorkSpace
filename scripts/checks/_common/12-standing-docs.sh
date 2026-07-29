#!/usr/bin/env bash
# 判据：长期文档本身的规范体检 —— 每次提交都跑，与本次改动无关。
#
# 长期文档＝常驻的项目级/模块级文档（规范、契约、导航、总目标）。
# 它们是所有 agent 的判案依据，**烂掉是静默的**：没人会因为文档过时而报错，
# 只会照着过时的文档做出错的东西。所以要每次体检，不能等谁想起来。
[ "${1:-}" = --describe ] && { echo "12 长期文档体检：项目级/模块级常驻文档必须存在、无占位残留、导航与实际结构一致"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" && pwd -P)/paths.sh"
fail=0
bad() { echo "$*" >&2; fail=1; }

# ① 长期文档必须存在 —— 按仓型取判据（仓型类第 3 例：模块仓跑根仓清单恒红）
if repo_is_module; then
  for d in AGENTS.md module_docs/contract.md module_docs/rules.md module_docs/handoff.md 文档地图.md; do
    [ -f "$d" ] || bad "模块级长期文档缺失: $d"
  done
else
  for d in agents/AGENTS.md agents/reference/manual/文档地图.md scripts/README.md; do
    [ -f "$d" ] || bad "项目级长期文档缺失: $d"
  done
fi

# ② 模块级长期文档：每个模块都得有全套
while IFS= read -r -d '' m; do
  mod=${m%/AGENTS.md}
  case "$mod" in code/_template) continue ;; esac
  for d in module_docs/contract.md module_docs/rules.md module_docs/handoff.md 文档地图.md; do
    [ -f "$mod/$d" ] || bad "模块 $mod 缺长期文档: $d"
  done
done < <(git ls-files -z 'code/*/AGENTS.md')

# ③ 已启用的长期文档不得残留模板占位（模板自己除外）
while IFS= read -r -d '' f; do
  case "$f" in code/_template/*|*/TEMPLATE-*|*.template.md) continue ;; esac
  # 还带着模板启用说明 = 该项目尚未启用它，不算"已启用却没填"
  grep -q '启用方式' "$f" 2>/dev/null && continue
  grep -qE '（迁移时填写|<例：|\{\{MODULE_NAME\}\}|\{\{FRAMEWORK_ROOT\}\}' "$f" 2>/dev/null &&
    bad "长期文档仍有未填的占位: $f"
done < <(git ls-files -z 'agents/AGENTS.md' 'agents/CONSTITUTION.md' 'code/*/AGENTS.md' 'code/*/module_docs/*.md')

# ④ 导航与实际结构一致：文档地图列出的长期文档必须真的存在
map=agents/reference/manual/文档地图.md
repo_is_module && map=文档地图.md
if [ -f "$map" ]; then
  while read -r p; do
    [ -n "$p" ] || continue
    case "$p" in *'<'*|*'{'*|*'*'*) continue ;; esac
    p=${p%/}
    resolves_here_or_framework "$p" && continue
    grep -qxF -- "$p" scripts/gates/doc-path-exempt.txt 2>/dev/null && continue
    grep -qxF -- "$p" scripts/gates/doc-path-exempt.local.txt 2>/dev/null && continue
    bad "文档地图列出的 $p 不存在（本仓与框架根都没有——导航与实际结构脱节）"
  done < <(grep -oE '`(agents|code|logs|scripts|control-panel)/[^`]+`' "$map" | tr -d '`' | sort -u)
fi
# ⑤ 孤儿留痕树：改名/迁移后旧目录还在，里面还有内容 ——
# **旧树看起来是活的**，人打开它以为"没更新"，而新树其实一直在写。
# 这比丢文件更坏：丢了会被发现，两棵并存不会。
canon_docs="agents/cfo/docs agents/consulter/docs"
while IFS= read -r -d '' d; do
  case "$d" in */.git/*) continue ;; esac
  d=${d%/}
  # 只看 <角色目录>/docs 形状的
  case "$d" in *"/docs") ;; *) continue ;; esac
  case " $canon_docs " in *" $d "*) continue ;; esac
  case "$d" in codeagent/*/docs|code/*/codeagent/*/docs) continue ;; esac   # 模块内是合法的
  n=$(find "$d" -type f ! -name '.gitkeep' 2>/dev/null | wc -l)
  [ "$n" -eq 0 ] && continue
  bad "孤儿留痕树：$d 还有 $n 个文件，但 canonical 路径已不是它"
  bad "   → 迁移没做完。搬到 canonical 路径后删掉旧目录；两棵并存时人会打开旧那棵，以为「没更新」"
done < <([ -d agents ] && find agents -type d -name docs -print0 2>/dev/null; :)

exit $fail
