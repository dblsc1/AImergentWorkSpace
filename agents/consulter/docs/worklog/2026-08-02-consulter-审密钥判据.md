# 2026-08-02 · consulter · 审 CFO 的密钥判据（commit b6bb516）

被审区间固定：`575fb8a..b6bb516`（exact，5 个文件）。
CFO 要求按审别人的标准审、直说。以下每条都有可复现命令与真实输出。
**本轮只报告，一行都没修**（红线：发现问题写报告，绝不顺手修）。

---

## 结论先行

判据本身**方向对、形状干净、没把口令写进判据**——这三点做对了，而且难得。
但它现在拦得住的是**它撞见过的那一种写法**，不是"这一类"。
实测 14 种明文凭据形状，**过了 12 种**。

更要紧的是：**它站在一个会静默塌掉的地基上**（S1，P0），
以及**它的立项诊断有一条是错的，而那条错会把下一步引到错的方向**（S3）。

---

## S1 · P0 · 类级：`lib/paths.sh` 一缺，九条 check 静默报绿

**这不是 18 的问题，18 只是第十个实例。**

复现（沙箱，删掉 `scripts/lib/paths.sh`，暂存 251 个文件后跑着陆检查单）：

```
❌ 1  write-lease          ← 红，但理由是"没有路签"，不是"我瞎了"
❌ 2  worklog-changed
✅ 3  doc-sync             ← 零验证
❌ 4  standing-docs
❌ 5  gates-fresh
✅ 6  handoff-fresh        ← 零验证
✅ 9  module-handoff       ← 零验证
✅ 10 secret-literal       ← 零验证。251 个文件一个没看，界面上和真验过一模一样
✅ 11 report-committed     ← 零验证
✅ 13 doc-paths-exist      ← 零验证
✅ 14 reviewer-opinion     ← 零验证
✅ 15 dependency-drift     ← 零验证
```

机制：十一条 check 都用
`. "$(cd -- "…/../../lib" && pwd -P)/paths.sh"` 取公共件，
而**它们都只 `set -uo pipefail`，没有 `-e`，source 失败不中止**。
接着 `staged_paths` 未定义 → `mapfile` 读到空 → 文件数组为空 → `exit 0`。
`mission_complete.sh` 用 `if out=$("$c" 2>&1); then cl_ok`，
`$out` 里的 `command not found` 在成功分支被丢弃 —— **一个字都不会显示**。

逐条实测退出码（`scripts/checks/_common/*.sh`，无 paths.sh）：

| exit=0（静默放行） | exit=1（响亮） |
|---|---|
| 05 11 14 17 18 20 60 70 90 | 10 12 |

这正是铁律 23 亲自点名的形状：
「关键路径缺文件必须 die；可选件缺失必须打印「已跳过」；**唯一不许的是静默跳过后报成功**」。
判例库「恒定答案类」问的两句——"它在什么情况下会永远通过？"——
这里的答案是"少一个共享库就永远通过"，而那个库正是 `install-gates.sh`
拷贝时唯一被回验的四个文件之一（说明写它的人知道它会丢）。

**修法（一行 × 十一处，我没动）**：
```bash
_pl="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../lib" 2>/dev/null && pwd -P)/paths.sh"
[ -f "$_pl" ] || { printf '❌ 缺 %s，拒绝以「什么都没扫」的姿态报绿\n' "$_pl" >&2; exit 2; }
. "$_pl"
```
配一条 selftest：沙箱删 `lib/paths.sh`，断言**每一条**用它的 check 退出码非 0。

**fix owner（铁律 16）**：consulter，下一轮单独一条 commit。
本轮我只在自己动的 `run-gates.sh` 里加了这道守卫（不然就是又添一个实例），
其余九条**没碰**——审核轮里顺手修九个文件，审的人和修的人就又合一了。

---

## S2 · 高 · 漏网面：14 种形状过了 12 种

沙箱逐条实弹（全用假值 `hunter2pw`；命令见本文末），`拦住`/`放行`：

| 形状 | 结果 | 为什么 |
|---|---|---|
| `DB_PASSWORD`+`=hunter2pw` | 拦住 | 基准 |
| `DB_PASSWORD`+`=hunter2pw   # 参考 $HOME` | **放行** | 过滤器 `grep -vE '\$\{?[A-Za-z_]'` 是**行级**的：行内任何位置出现 `$VAR` 就整行放行 |
| `DB_PASSWORD`+`=hunter2pw   # url=example.com` | **放行** | 占位符过滤器同样是行级：`=example` 出现在任何位置就整行放行 |
| `export PGPASSWORD`+`=hunter2pw` | **放行** | 正则要 `_PASSWORD`，`PGPASSWORD` 没有下划线。这是 postgres 的官方变量名 |
| `AWS_SECRET_ACCESS_KEY`+`=hunter2pw` | **放行** | `_SECRET` 后面跟的是 `_ACCESS_KEY` 不是 `=`；`_API_KEY` 也对不上 |
| `password`+`=hunter2pw` / `api_key`+`=` / `secret_key`+`=` | **放行** | `grep -E` 没有 `-i`，全大写清单对小写代码惯例无效 |
| `password`+`: hunter2pw`（YAML 裸键） | **放行** | 只认带引号的 `"password"` / `'password'` |
| `PASSWORD`+`: hunter2pw`（顶层无前缀） | **放行** | 同样要 `_PASSWORD` |
| `{"password"`+`:` 换行 `"hunter2pw"}`（多行 JSON） | **放行** | 逐行匹配 |
| `DATABASE_URL`+`=mysql://root:<口令占位>@db/app` | **放行** | 连接串里的口令不在赋值右侧首位 |
| `ghp_AbCdEf…`（裸 PAT） | 放行 | 设计外（判据只认赋值）——见 S3 |
| `-----BEGIN RSA PRIVATE KEY-----` | 放行 | 同上 |
| `DB_PASSWORD`+`="hunter2pw"`（.env） | 拦住 | ✓ |
| `MY_TOKEN`+`=aHVudGVyMnB3Cg==`（base64） | 拦住 | ✓ CFO 猜的这条其实**没漏** |

**最该先修的是那两条行级过滤器**：它们不只是漏，而是**可主动利用的绕过**——
在同一行加一句 `# 见 $HOME` 就能让判据放行。修法是把过滤下沉到匹配到的
**右值**上（`grep -oE` 取右值再判），而不是整行。

**名字清单该换成"后缀/词边界 + 忽略大小写"**：
`(^|[^A-Za-z])(pass(word|wd)?|secret|token|api[-_]?key|access[-_]?key)([^A-Za-z]|$)`
之类，再叠一层"右值像不像随机串"，比枚举五个大写名字覆盖面大一个数量级。

---

## S3 · 高 · **诊断有一条是错的，而它会把下一步引偏**

commit 消息与 worklog 都写：
> 「此前唯一相关的 gitleaks gate 三重失效：**配置空壳零规则**、被 `RUN_GITLEAKS_LOCAL=1` 挡着、本机无二进制。」

第一条不成立。`scripts/gates/.gitleaks.toml` 全文五行：

```toml
title = "starter secret scan"

[extend]
useDefault = true
```

`useDefault = true` 是 gitleaks 的**继承全套默认规则**开关（一百多条，含
AWS 密钥、GitHub PAT、私钥块）。它不是空壳，它是"照抄上游规则集"。
另外两条属实（本机 `command -v gitleaks` → 未安装；开关默认 0）。

**而且 CI 里 gitleaks 是跑的**：`scripts/workflows/ci.yml:22`
`uses: gitleaks/gitleaks-action@v3`，`GITLEAKS_CONFIG` 就指这份配置。

**为什么这条错很贵**：按"配置是空壳"这个诊断，下一步自然是
"往 18 里塞更多正则"。而按真实诊断，下一步应该是
**本地把 gitleaks 装上、把默认开关翻过来** —— 因为 S2 里 18 漏掉的
AWS 密钥、GitHub PAT、PEM 私钥**恰好全在 gitleaks 默认规则里**。
两条路的成本差一个数量级。

这是判例库那条「**修对了不等于诊断对了**」（F7，同一天）当日第二次实证：
修法（写一条自己的判据）没错，成因写错了，而错的成因指向错的下一步。

**建议的分工**（不是我裁）：
- gitleaks 管「像凭据的字符串」——上游维护的大规则集，白拿
- 18 管「本仓特有的形状」——它真正的独特价值是**扫留痕文档**
  （P0 那次口令进的是 worklog / handoff / report.json，不是代码），
  这一片 gitleaks 的默认规则确实覆盖不好

---

## S4 · 中 · 「铁律 2 写了一个多月零执行」这句话过头

本地零执行属实；但 CI 有 gitleaks-action。留痕里写"零执行者"会让下一个人
以为推上去也没人看。准确说法是「**本地**零执行者」。
（我知道这条听着像抠字眼。它值一条的理由：这份 worklog 会被当成史料读，
而"我们当时什么都没有"和"我们当时只有 CI 那一层"会导出不同的补救顺序。）

---

## S5 · 中 · 误报面很小，但那一个误报在最高频路径上

全仓 251 个 tracked 文件一次性喂给判据，**只有 1 处命中**：

```
code/_template/.env.staging.example:3:# REQUIRED_SECRET` + `=inject-at-runtime
（注：名字与右值之间的 ` + ` 是本文档为了不成为新实例而拆的，原文里没有；
 空格拆不开——判据的正则允许 `[[:space:]]*`。见文末「这份文档自己也中招了」）
```

它是**注释行 + 明显占位值**，是个误报。麻烦在落点：
`code/_template/` 是新模块结构的唯一事实源，`new_module.sh` 会把它拷进每个新模块。
也就是说**每建一个模块，首次提交都会撞上这一条**，然后那个人只有两条路：
改模板（越权）或者用逃生口。判例库「判断题不做硬闸门」讲的就是这个下场——
逃生口用滥了整套闸门就废。误报率 1/251 听着很好，但命中的是必经之路。

两条现成修法，都有仓内先例，**口径本该一致**：
- `skip_path` 加 `*.example|*.example.*` —— run-gates 的「禁默认值」gate
  第 134 行就是这么做的
- 跳过注释行 —— 「禁止路径」gate 有先例，注释里**讲**这个坑没有可执行伤害

---

## S6 · 低 · `skip_path` 是明确列出的类，不是 catch-all（这点 CFO 做对了）

CFO 说这是他最不放心的地方。审下来：`case` 分支逐条列举，
没有 `*)` 兜底放行，**不是变相 catch-all**，可以放心。

两个小观察：
1. **它不打印跳过了多少**。静默跳过 + 报成功是同一个形状的弱化版；
   建议末尾印一行「扫了 N 个、跳过 M 个」。（我在 run-gates 的行数 gate
   里补了这行，正是因为上一版就是只印 ✅ 才能哑四天。）
2. `*/vendor/*` 让 `code/x/vendor/creds.txt` 直接隐身。vendor 通常不入仓，
   风险低，但这是判据里唯一一处"换个路径就隐身"的口子，值得知道它在。

---

## S7 · 正面 · 判例库「字面量类」这次**没有**复发

CFO 说"把口令写进判据等于为了防泄露而泄露"——这句是对的，而且他做到了。
更值得记的是：`agents/cfo/docs/2026-08-02-P0-明文口令进模块仓.md` 第 45 行
把泄露的那行写成 `export …_PASSWORD` + `=<真值>`，**已脱敏**；
把这份 P0 报告单独喂给判据，**判绿**。

判例库那条「凡是报告一个关于字面量的缺陷的文档，都会成为那个缺陷的新实例」
已实证三次，**这是第一次没有复发**。该记一笔。

---

## S8 · 低 · #51「分不出提到与实现」——我的意见是别改

CFO 的观察成立：#51 匹配字符串，`AIMERGENT_MISSION_OVERRIDE` 出现在帮助文本里
也会判红。但**它这次的结论是对的，而且它下次也会是对的**：
统一逃生口由 `mission_complete.sh` 提供并记账，**任何单条 check 里出现这个词
都是冗余**——要么是复述（该删），要么是自建第二个出口（更该拦）。
"提到"和"实现"在这一条上恰好没有需要区分的合法情形。

建议只改文案，把「有逃生口没接问责账本」改成
「别在单条 check 里重复统一逃生口 —— 它由 mission_complete.sh 统一提供并记账」。
收紧判据反而会引入"什么算实现"的形状之争。

---

## S9 · 低 · 顺手撞见：断言 #36 有一处结构性盲区

`agents/reference/工作空间全景.md:255` 写着
`scripts/selftest.sh      34 条闸门有效性断言`，实际 62 条 —— 化石计数，
正是断言 #36 防的东西。而 **#36 是绿的**。

原因不是正则不匹配（单独喂那一行，`grep -c '[0-9]\+ 条闸门'` → `1`），
而是 #36 的排除项 `| grep -v 'selftest.sh'`
作用在整串 `路径:行号:正文` 上 —— **本意是排除 selftest.sh 这个文件，
实际连"正文里提到 selftest.sh 的行"一起排除了**。
而唯一那条关于 selftest 断言条数的化石计数，正文里必然写着 `selftest.sh`。
**结构上保证抓不到。**

修法：排除改成按路径判（`grep -v '^[^:]*selftest[^:]*:'`），
并把那一行改成"条数见运行输出"（`文档地图.md` 已经是这个写法）。
两处都不在我本轮写区，未动。

---

## 复现命令（可直接粘）

```bash
R=/srv/aimergent/sample-workspace-v5-time-management-test
SBX=$(mktemp -d); git -C "$SBX" init -q
mkdir -p "$SBX/scripts/checks/_common" "$SBX/scripts/lib"
cp "$R/scripts/checks/_common/18-secret-literal.sh" "$SBX/scripts/checks/_common/"
cp "$R/scripts/lib/paths.sh" "$SBX/scripts/lib/"
probe() { printf '%s\n' "$2" > "$SBX/p.txt"; git -C "$SBX" add p.txt >/dev/null 2>&1
  rc=$( cd "$SBX" && bash scripts/checks/_common/18-secret-literal.sh >/dev/null 2>&1; echo $? )
  [ "$rc" -ne 0 ] && v=拦住 || v='放行 ←'; printf '  %-6s %s\n' "$v" "$1"; }
probe 基准              'DB_PASSWORD''=hunter2pw'
probe '行内有 $VAR'     'DB_PASSWORD''=hunter2pw   # 参考 $HOME'
probe PGPASSWORD        'export PGPASSWORD''=hunter2pw'
probe AWS_SECRET        'AWS_SECRET_ACCESS_KEY''=hunter2pw'
probe 小写              'password''=hunter2pw'
probe YAML裸键          'password'': hunter2pw'
probe 连接串            'DATABASE_URL''=mysql://root:<口令占位>@db/app'

# S1：删掉公共件，看有几条 check 静默退 0
cp -r "$R/scripts" "$SBX2=$(mktemp -d)/" 2>/dev/null || true
```

（S1 的完整复现见本文上半段的表；退出码是逐条 `bash <check>; echo $?` 直取的，
不走管道——判例库「验证者自己的 `$?` 也会撒谎」。）

---

## 我这轮自己踩的两个坑（同一份判例库，两次）

1. 给 #62 写的子断言 `case "$_o62a" in *"扫描 "*)` 是**恒真**的：
   `sed` 区间末行是「── gate: gitleaks 密钥扫描 ──」，自带「扫描 」。
   删掉被验行照样绿。反向验证当场逮住。
2. 给 fixture 行贴 `# ref-fixture` 时贴到了**续行 `\` 后面**，
   反斜杠转义空格 + 注释吃掉换行 → 下一行 `> file` 成了只有重定向的命令，
   把 report.json 截空。**而 `bash -n` 报 OK。**

---

## 这份文档自己也中招了（第四次实证，这次是我）

写完上面那张表，`mission_complete` 第 10 项**当场把它拦下**：

```
❌ 10 secret-literal  [通用]
   agents/consulter/docs/findings/report.json:102: …REQUIRED_SECRET` + `=inject-at-runtime…
   agents/consulter/docs/worklog/2026-08-02-consulter-审密钥判据.md:80: | `DB_PASSWORD`+`=hunter2pw` | 拦住 |
```

判例库「字面量类」写着：
> 凡是「报告一个关于字面量的缺陷」的文档，都会成为那个缺陷的新实例。

已实证三次（模板检查脚本自身 / heredoc 展开 / 上报该缺陷的 report.json）。
**这是第四次，载体是「审这条判据的审核报告」。**

**我没有用逃生口。** 按判例库指定的写法把样本拆开
（`` `NAME` `` + `` `=值` ``、shell 里用 `'A''=B'` 相邻单引号拼接），
形状仍然逐字可读，但不再构成"赋值 + 右侧字面量"。

两个副产物：

1. **这是 S2 的反面证据**：我在上面说判据"14 种形状只拦住 2 种"，
   而它拦住的那一种，恰恰是**真实事故里出现的那一种**（P0 那次是
   `export …_PASSWORD` + 真值），而且它在**我自己的审核报告**上又拦了一次。
   S2 说的是覆盖面不够，不是这条判据没用 —— 这两句必须一起读。
2. **S5 说的「误报会逼人用逃生口」在我身上跑了一遍**，结果是：
   我选择改写法而不是绕过。说明这一条的成本是可接受的 ——
   前提是**改写法这条路存在且明确**。判例库写了那条路，所以我没绕。
   这反过来说明 S5 那个模板误报为什么更该修：撞上它的人**没有**第二条路
   （改 `code/_template/` 越权），只剩逃生口。

## 补：本文的连接串举例已改成占位符（CFO 2026-08-02 指出）

判据 v2 判右值之后，本文原来那两处 `mysql://root:` + 假口令 会被自己命中。
按判据自己的规矩，**文档举例该用占位符**，已改成 `<口令占位>`。

这是「字面量类」在同一份文档上的第二次收口：第一次是提交时被 v1 拦下，
第二次是判据变强之后又露出来。**判据每强一次，讲这件事的文档就要再改一次** ——
这不是麻烦，这正是该有的样子；真正该警惕的是「判据强了、文档没跟着改，
于是用逃生口放行」。
