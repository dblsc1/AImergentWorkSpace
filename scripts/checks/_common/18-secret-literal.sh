#!/usr/bin/env bash
# 判据：明文凭据不进 Git（铁律 2 的机械执行者）。
#
# ── 为什么需要它 ─────────────────────────────────────────────
# 铁律 2「密钥永不进 Git」写了一个多月，**执行者一直是零**：
# `gates/.gitleaks.toml` 是空壳（零规则），且被 `RUN_GITLEAKS_LOCAL=1` 挡着，
# 本机也没装 gitleaks 二进制 —— 那条 gate 在任何一次运行里都不做事。
#
# 2026-08-02 实证（`agents/cfo/docs/2026-08-02-P0-明文口令进模块仓.md`）：
# 开发口令被原样贴进三个模块的 worklog / reviewreport / handoff / report.json，
# **而那些文件正是在论证「口令没有进测试文件」**。reviewer 的报告逐字写着
# 「`grep -rn <口令> review/reviewcode/tests/` 零命中」——**这句话是真的**，
# 测试文件确实干净。紧接着的下一行就是 `export ..._PASSWORD=<真值>`。
#
# 病根一句话：**判据盯的是 tests/ 目录，而证明合规的那份文档本身不在判据范围内。**
#
# ── 判据 ─────────────────────────────────────────────────────
# 命中：`<名字>_PASSWORD|_TOKEN|_SECRET|_APIKEY|_API_KEY` 或 JSON 的 "password"
#       后面跟 `=` / `:` 且右侧是**字面量**。
# 放行：取值是变量（`$X` / `${X}` / `%X%`）、读 env 的调用（`os.environ` /
#       `getenv` / `process.env` / `ENV[`）、占位符（`<…>` / `xxx` / `changeme` /
#       `your-*` / `dummy`）、或右侧为空。纯散文提及变量名（没有 `=`）也放行。
#
# **判据里不硬编码任何真实凭据。** 认的是「形状是赋值且右侧是字面量」，
# 不是「等于某个已知口令」——把口令写进判据本身，等于为了防泄露而泄露；
# 而且那样只拦得住这一个口令，下一个不同的照样漏。
#
# ── 逃生口 ───────────────────────────────────────────────────
# 不自设。误报确实可能（文档里举一个明显是假的例子却没用上面的占位符形态），
# 但**统一逃生口由 mission_complete.sh 提供并记账**，每条 check 都适用 ——
# 在这里再印一遍不但冗余，还会被 selftest #51 判成「新开了一个没接账本的逃生口」。
# （本条实证：第一版在帮助文本里提了 MISSION_OVERRIDE，#51 当场变红。
#   判据分不出「提到」和「实现」，那是它的弱点；但这行本来也不该有。）
[ "${1:-}" = --describe ] && { echo "18 明文凭据：暂存文件里不得出现 *_PASSWORD/_TOKEN/_SECRET 赋成字面量（铁律 2 此前零执行者）"; exit 0; }
set -uo pipefail
. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)/../../lib/paths.sh"

# 二进制与第三方产物不扫：vendor 里的压缩 JS 常有 `token=` 之类的字面量，
# 那是别人的代码，不是我们的凭据。**跳过必须是明确列出的类，不是静默 catch-all。**
skip_path() {
  case "$1" in
    *.png|*.jpg|*.jpeg|*.gif|*.ico|*.gz|*.zip|*.archive|*.min.js|*.min.css) return 0 ;;
    */vendor/*|*/node_modules/*|*/.venv/*) return 0 ;;
    *) return 1 ;;
  esac
}

mapfile -t -d '' files < <(staged_paths ACMR)
hits=""
for f in "${files[@]}"; do
  [ -n "$f" ] && [ -f "$f" ] || continue
  skip_path "$f" && continue
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    hits="${hits}${f}:${line}
"
  done < <(
    grep -nIE '(_PASSWORD|_TOKEN|_SECRET|_APIKEY|_API_KEY|"password"|'"'"'password'"'"')[[:space:]]*[:=][[:space:]]*"?[^[:space:]"'"'"'<$%{]' "$f" 2>/dev/null |
    grep -vE '(os\.environ|getenv|process\.env|ENV\[|\$\{?[A-Za-z_])' |
    grep -vE '[:=][[:space:]]*"?(xxx+|changeme|your[-_]|placeholder|dummy|example|<)'
  )
done

[ -n "$hits" ] || exit 0

printf '明文凭据疑似进仓（铁律 2）：\n' >&2
printf '%s' "$hits" | sed 's/^/  /' >&2
cat >&2 <<'TXT'
  → 取值改成读 env（os.environ["X"] / $X），文档里写**变量名**不写值
  → 已经推上去的：**轮换那个凭据**。只删当前文件没用，历史里还在
  → 本判据认的是「赋值 + 右侧字面量」，不认任何具体口令 ——
    所以它拦得住下一个不同的凭据，不只是这一次撞见的那个
TXT
exit 1
