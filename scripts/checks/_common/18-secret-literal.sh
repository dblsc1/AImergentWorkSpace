#!/usr/bin/env bash
# 判据：明文凭据不进 Git（铁律 2 的本地执行者之一）。
#
# ── 定位：它**不是**密钥扫描的全部 ────────────────────────────
# `scripts/gates/.gitleaks.toml` 是 `[extend] useDefault = true`，
# 继承 gitleaks 上游一百多条默认规则，**并且 CI 里一直在跑**
# （`scripts/workflows/ci.yml` 的 gitleaks-action）。AWS 密钥 / GitHub PAT /
# PEM 私钥这类**有固定指纹**的东西归它，本判据不重复造。
#
# 真实缺口只有本地这一段：gitleaks 在本机既没装二进制、又被 `RUN_GITLEAKS_LOCAL=0`
# 默认关着 —— 于是**提交时刻**没有任何检查，要等推到 CI 才知道。
# 本判据补的就是这一段，专攻 gitleaks 默认规则**天然抓不到的那类**：
# **项目自定义的、没有固定指纹的口令**（形如「词+年份」的弱口令，
# 任何基于熵或指纹的规则都会放过它）。
#
# ⚠️ 立项时我把 `.gitleaks.toml` 误判成「空壳零规则」。按那个诊断，下一步是往本文件
#    里塞更多正则；按真实诊断，下一步是**本地把 gitleaks 装上**。
#    修对了不等于诊断对了 —— 诊断错会把下一步引偏。
#
# ── 实证 ─────────────────────────────────────────────────────
# 2026-08-02（`agents/cfo/docs/2026-08-02-P0-明文口令进模块仓.md`）：
# 开发口令被原样贴进三个模块的 worklog / reviewreport / handoff / report.json，
# **而那些文件正是在论证「口令没有进测试文件」**。报告逐字写着「grep 零命中」——
# 那句话是真的，测试文件确实干净，紧接着的下一行就是 `export ..._PASSWORD=<真值>`。
# 病根：**判据盯的是 tests/ 目录，而证明合规的那份文档本身不在判据范围内。**
#
# ── 判据形状（v2，2026-08-02 被 consulter 判 rejected 后重写）────
# **判断的对象是右值，不是整行。** v1 用 `grep -v` 链过滤整行，于是
# `DB_PASSWORD=<真值>   # 参考 $HOME` 这种同一行别处出现 `$VAR` 的写法直接放行 ——
# 那不只是漏，是**可主动利用的绕过**。
#
# 键名：大小写不敏感、认词边界，覆盖 `PGPASSWORD` `AWS_SECRET_ACCESS_KEY`
#       小写 `password=` `api_key:` 等 v1 全部漏掉的形态；另认连接串 `://user:pw@host`。
# 右值：只有**整体**匹配占位符形态、或是变量引用、或是读 env 的调用才放行。
#
# **判据里不含任何真实凭据。** 认的是「赋值 + 右值是字面量」，不是「等于某个已知口令」；
# 把口令写进判据本身等于为了防泄露而泄露，而且只拦得住那一个。
#
# ── 逃生口 ───────────────────────────────────────────────────
# 不自设。统一逃生口由 `mission_complete.sh` 提供并记账，每条 check 都适用。
# （v1 在帮助文本里复述了一遍，selftest #51 当场判成「新开了个没接账本的逃生口」。）
[ "${1:-}" = --describe ] && { echo "18 明文凭据：暂存文件里 password/secret/token/api_key 类键不得赋成字面量右值（铁律 2 的提交时刻执行者；有指纹的密钥归 CI 的 gitleaks）"; exit 0; }
set -uo pipefail
_here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# 公共件缺失必须响亮死掉，不许静默退 0（S1 类：删掉 lib/paths.sh 会让
# 十二条 check 全部静默变绿，界面与真验过一模一样）。
. "$_here/../../lib/paths.sh" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "❌ 18-secret-literal: 需要 python3（判据要解析右值，不是行级 grep）" >&2; exit 2; }

mapfile -t -d '' _files < <(staged_paths ACMR)
[ "${#_files[@]}" -gt 0 ] || exit 0

printf '%s\0' "${_files[@]}" | python3 -c '
import re, sys, os

# 不扫的类：逐条列举，**没有 catch-all 兜底**。跳过数量会打印出来。
SKIP_SUFFIX = (".png",".jpg",".jpeg",".gif",".ico",".svg",".pdf",
               ".gz",".zip",".tar",".archive",".min.js",".min.css",
               ".lock",".sha256",".woff",".woff2",".ttf")
SKIP_PART   = ("/vendor/", "/node_modules/", "/.venv/", "/dist/", "/build/")

KEY = re.compile(
    r"(?i)(?<![A-Za-z0-9])"
    r"(?P<key>[A-Za-z0-9_]*"
    r"(?:pass(?:word|wd)|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)"
    r"[A-Za-z0-9_]*)"
    # 键名可能被引号包着（JSON / YAML 的引号键）——**闭引号夹在键名和冒号之间**，
    # 不吃掉它就整条 JSON 形态全漏。v2 第一版就漏在这里，是喂样本喂出来的。
    # （本行原来写了个带引号的键名做例子，结果被本判据自己拦下。判据在教什么，
    #   自己就得先遵守：文档举例用变量名，不写形如赋值的样子。）
    r"[\"\x27]?"
    # `=` 但**不是** `==` / `===`：`if (e.pass === false)` 里 `pass =` 会被读成赋值。
    # 这类「普通标识符恰好含 pass/token」的误报，靠下面的键名形态再滤一道。
    r"\s*(?:=(?!=)|:)\s*"
    r"(?P<val>.*)$"
)

# 键名必须像**配置键**：全大写下划线（env 约定）或被引号包着（JSON/YAML）。
# 不这样收，`e.pass` `passthrough` `detail = …` 这类普通代码全是误报，
# 而误报到了逼人用逃生口的程度，判据就等于没有。
# 代价是漏掉小写裸键 `password=x`——那类交给 CI 的 gitleaks（默认规则覆盖）。
KEYSHAPE = re.compile(r"^[A-Z][A-Z0-9_]*$")

# 文件名不是键名。`18-secret-literal.sh:36` 会被上面的正则读成
# 「键 `18-secret-literal.sh` = 值 `36`」——**讨论这条判据的文档会被它自己拦下**。
# 判据：带扩展名或含斜杠 = 路径，不是变量名。
LOOKS_LIKE_PATH = re.compile(r"(?i)(/|\.(sh|md|py|js|ts|json|ya?ml|toml|txt|html|css|jsonl|conf|log)$)")
# 连接串：scheme://user:pw@host
CONN = re.compile(r"(?i)[a-z][a-z0-9+.-]*://[^\s:/@]+:(?P<val>[^\s@/]+)@")

# 读 env 的调用出现在右值里 => 不是字面量
ENVCALL = re.compile(r"(?i)(os\.environ|getenv|process\.env|ENV\[|System\.getenv|Deno\.env)")

# 占位符：必须**整体**像占位符，不是「行里出现过」。
PLACEHOLDER_SUBSTR = (
    "changeme","change-me","placeholder","dummy","example","sample",
    "your","redacted","inject","runtime","setme","set-me","fillme",
    "fill-in","replaceme","goes-here","notareal","test-only",
)
# 短词必须**整体相等**才算占位符 —— 用子串会把 `hunter2pw` 判成占位（含 "pw"）。
PLACEHOLDER_EXACT = (
    "xxx","xxxx","pw","pass","password","secret","token","todo","tbd",
    "none","null","nil","empty","here","replace","x","...","****","redacted",
)

def value_is_literal(v: str) -> bool:
    v = v.strip()
    # 去掉尾部行内注释（# 或 // 开头，且前面有空白）
    v = re.sub(r"\s+(#|//).*$", "", v).strip()
    v = v.rstrip("\\").rstrip(",;").strip()   # 续行反斜杠 / JSON 逗号
    # 剥一层引号
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"\x27`":
        v = v[1:-1].strip()
    if not v:
        return False                      # 空值不是泄露
    if v[0] in "$%":
        return False                      # $VAR / ${VAR} / %VAR%
    if v.startswith("{{") or v.startswith("<") or v.startswith("{") or v.startswith("("):
        return False                      # <你的口令> / {{TOKEN}} / (fill in)
    if ENVCALL.search(v):
        return False
    low = v.lower()
    if low in PLACEHOLDER_EXACT:
        return False
    if any(w in low for w in PLACEHOLDER_SUBSTR):
        return False
    # 真凭据不含空白。带空白的右值几乎必然是散文（注释里在讨论这件事），
    # 而误报到了逼人用逃生口的程度，判据就等于没有。
    if re.search(r"\s", v):
        return False
    return True

data = sys.stdin.buffer.read().split(b"\0")
paths = [p.decode("utf-8","surrogateescape") for p in data if p]
hits, skipped = [], 0
for p in paths:
    if p.endswith(SKIP_SUFFIX) or any(s in "/"+p for s in SKIP_PART):
        skipped += 1
        continue
    if not os.path.isfile(p):
        continue
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        continue
    if b"\0" in open(p,"rb").read(8192):
        skipped += 1
        continue                          # 二进制
    for n, line in enumerate(lines, 1):
        line = line.rstrip("\n")
        for rx in (KEY, CONN):
            m = rx.search(line)
            if not m:
                continue
            k = m.groupdict().get("key")
            if k is not None:
                if LOOKS_LIKE_PATH.search(k):
                    continue              # 是路径不是键名
                quoted = re.search(r"[\"\x27]\s*" + re.escape(k) + r"\s*[\"\x27]", line)
                if not (KEYSHAPE.match(k) or quoted):
                    continue              # 不是配置键形态
            if value_is_literal(m.group("val")):
                hits.append(f"{p}:{n}:{line.strip()[:160]}")
                break

if hits:
    print("明文凭据疑似进仓（铁律 2）：", file=sys.stderr)
    for h in hits:
        print("  " + h, file=sys.stderr)
    print(f"""
  → 取值改成读 env（os.environ["X"] / $X），文档里写**变量名**不写值
  → 已经推上去的：**轮换那个凭据**。只删当前文件没用，历史里还在
  → 判据看的是**右值**不是整行（v1 只看整行，于是同行别处出现 $VAR 就整行放行，
    那是可主动利用的绕过，不只是漏）
  → 占位符请写成整体可辨的形态：$VAR / <你的口令> / changeme / inject-at-runtime
  → 本次跳过 {skipped} 个二进制或第三方文件""", file=sys.stderr)
    sys.exit(1)
sys.exit(0)
'
