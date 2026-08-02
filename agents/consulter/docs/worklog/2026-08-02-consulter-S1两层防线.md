# 2026-08-02 · consulter · S1（P0）公共件一缺、半套门禁静默变绿 —— 两层防线

## 病根三段（CFO 复核后确认，我报九条、他实测十二条）

差异来自暂存状态不同：某些 check 在空暂存区时本来就走 `exit 0`。
**CFO 的数字更保守也更对**，按十二条记。

1. check 只 `set -uo pipefail`（**没有 `-e`**），`. <公共件>` 失败不中止
2. 函数未定义 → `mapfile` 读到空 → 数组空 → 走到 `exit 0`
3. **`mission_complete.sh` 的 `if out=$("$c" 2>&1); then cl_ok` 在成功分支把 `$out` 整个丢弃**
   —— `paths.sh: No such file or directory` 与 `staged_paths: command not found` 一个字都不显示

CFO 的判断我同意并采纳：**第 3 段最要害。** 只给每个文件加 `|| exit 2` 是修今天这批实例；
明天新写的一条照样可能「函数没定义 → 数组空 → exit 0」，而主脚本仍然会判它 pass。

## 层① · source 失败必须响亮死（18 处，统一一个惯用法）

```bash
. "…/paths.sh" || { printf '❌ %s：载入 paths.sh 失败 —— 拒绝以「什么都没验」的姿态退 0\n' "${BASH_SOURCE[0]}" >&2; exit 2; }
```

覆盖面比 CFO 点的十二条**更宽**——原报告只数了 `_common`，实际同形状还在：

| 位置 | 处数 |
|---|---|
| `scripts/checks/_common/` | 10 |
| `scripts/checks/arbiter/`、`scripts/checks/programmer/` | 4 |
| `mission_complete.sh`、`mission_start.sh`、`doc_impact.sh`、`merge-to-integration.sh` | 5 |
| `gates/run-gates.sh` | 1（本轮把它从「上一行 `[ -f ]` 预检」改成同一个行尾惯用法） |

**措辞统一不是洁癖**：断言 63 要能分辨「守卫拦下的」和「碰巧因别的理由红的」，
靠的就是守卫独有的那句「拒绝以」。两套措辞 = 断言要认两套规矩，而两套迟早变三套。
CFO 在 18 里写的 `❌ 18-secret-literal: 无法载入 lib/paths.sh` 意思对、措辞不同，已对齐。

`lib/emit.sh` 保持 `2>/dev/null || true` —— 它是**声明过的可选件**，不在本类内（见文末）。

## 层② · 主脚本不许信一个自相矛盾的成功

`mission_complete.sh` 新增：

```bash
interpreter_blew_up() {
  grep -qE ': line [0-9]+: .*(command not found|No such file or directory|unbound variable|syntax error|Permission denied|bad substitution)' <<<"${1:-}"
}
...
if out=$("$c" 2>&1) && ! interpreter_blew_up "$out"; then cl_ok ... else ... fi
```

**判据钉在 bash 自己的诊断前缀 `<脚本>: line N: …` 上，不是裸关键词。**
check 的正常业务输出里完全可能出现「找不到文件」这类字样；
只有解释器级错误才会带 `: line N:`。常态复跑十五条零误伤（见下）。

## 断言与反向验证（真实输出）

新增分片 `scripts/selftest.d/70-lib-bootstrap.sh`，三条：

| # | 验什么 |
|---|---|
| 63 | 删掉 `lib/paths.sh` 后**逐条**跑所有 check，凡用到它的都必须**由守卫本身**拦下 |
| 64 | 主脚本对「退 0 + 解释器级错误」的 check 判失败，且正常 check 零误伤 |
| 65 | 结构断言：`scripts/` 下任何 source 必需公共件的行都必须带守卫（挡住**下一个**新写的） |

绿测：`PASS 65 · FAIL 0 · N/A 0`，`run-gates EXIT=0`，输出无噪音。

**反向验证三发，证明两层各自有效（CFO 明确要求的那一点）**：

| 拆什么 | #63 | #64 | #65 |
|---|---|---|---|
| A 只撤层①（14 条守卫全撤），层②在 | ❌ 点名 11 条 | ✅ 仍绿 | ❌ 点名 14 条 |
| B 只拆层②（守卫函数改恒假），层①在 | ✅ 仍绿 | ❌「退0+解释器错误被判成了 pass」 | ✅ 仍绿 |
| C 只撤层①里的**一条**（05-write-lease） | ❌ 点名 05 | — | ❌ 点名 05 |

**A 与 B 互为对照**：拆哪层就只有哪层的断言变红，另一层照旧 —— 这就是"两层各自有效"的证据。

## 反向验证第 C 发抓出我自己写的一个弱断言

C 第一次跑是**绿的**。原因：#63 原本只断言「退出码非 0」，而
`05-write-lease.sh` 在沙箱里本来就会因「角色 x 没有路签」而红 ——
**红对了，但理由是错的**。真出事时人会去申领路签，而实际病因是判据瞎了。
（这正是我在 S1 findings 里写过的那句「❌ 1 write-lease ← 红，但理由是"没有路签"，不是"我瞎了"」
—— 我把它写进了报告，却没把它写进断言。）

改成「退出码非 0 **且**输出里有守卫独有的措辞」之后，C 发正常变红。

**判据（已进判例库）**：断言一个守卫时，只验"结果对不对"不够，
要验"是不是这个守卫造成的"。否则一个碰巧同向的旁因就能把断言变成恒绿。

顺带第二个：#65 原来的 grep 还接了 `| grep '\.sh"'`，
于是 `. "$_lease_lib"` 这种**变量形式的 source 全部溜过去** —— 我自己的断言开了个洞，
也是反向验证逼出来的。已去掉那层过滤，并给两处测试内 source 加了真守卫
（**没另造豁免机制**）。

第三个：`P "… 带 \`|| exit 2\` …"` 里的反引号在双引号中是命令替换，
全绿路径上多吐一行 `syntax error near unexpected token '||'`。
和 F7 的 locale 噪音同类：断言过了，但输出有噪音，下一个人分不清哪行是真报错。已改。

## 明确**没有**纳入本类的一件事，以及理由

`lib/emit.sh` 的十五处 `. "…/emit.sh" 2>/dev/null || true` 保持原样。
它是**显式声明过的可选件**，`|| true` 是作者的有意选择，不是遗漏。

但按铁律 23 的原话——「可选件缺失必须打印「已跳过」；唯一不许的是静默跳过后报成功」——
**它现在是静默的**。缺 `emit.sh` 时 `emit_override` 没了，逃生口记账会不会跟着静默停掉，
我没有在本轮验（那是另一条断言、另一个区间）。**列为下一轮候选，不在这条 commit 里顺手改。**

## 顺带（CFO 指出的两件里的第一件）

审核 worklog 里两处 `mysql://root:` + 假口令的举例会被 v2 判据命中。
已改成 `<口令占位>`。这是「字面量类」在同一份文档上的**第二次**收口 ——
判据每强一次，讲这件事的文档就要再改一次。真正该警惕的是判据强了、文档没跟着改，
于是用逃生口放行。

## 顺带（第二件）

`logs/ledger.jsonl` 有一条 CFO 推送时写的 override 记录未入仓，
`checks/16` 正确拦住了本轮提交。按既有做法（commit `aeb4bc3` 同型）随本 commit 一起入仓 ——
**账本不入仓 = 逃生口全是静默的**，这条不能拖到下一轮。
