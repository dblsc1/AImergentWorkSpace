# 2026-08-02 · consulter · 修自己的 review_target（并把报错文案记成一条 finding）

## 症状

本轮报告写完后 `check-report-schema.sh` 判红：

```
❌ report schema: 独立 reviewer 必须使用标准 review_target（exact），禁止 target 等别名:
   agents/consulter/docs/findings/report.json
```

**而我根本没用 `target` 这个别名。** 报错指向的方向是错的。

## 真实原因有两个，报错一个都没说

1. **缺 `review_target.branch`** —— 判据要求它是非空字符串，我只写了
   `base/head/diff_mode/changed_files`。
2. **`changed_files` 用了 C 转义的路径。** 我用
   `git diff --name-only --no-renames base..head` 取的，它对非 ASCII 路径会加引号并转义：
   `"agents/cfo/docs/worklog/2026-08-02-cfo-\345\257\206…md"`。
   而 gate 内部比对用的是 `git -c core.quotePath=false diff …`，拿到的是
   `agents/cfo/docs/worklog/2026-08-02-cfo-密钥判据.md`。两边永远对不上。

被审的那个区间恰好有两份中文文件名的 CFO worklog —— **在这个仓里这不是边缘情况，是常态。**

## 为什么一句报错说不清

那条判据是一个**七项的大 `and`**（`scripts/gates/check-report-schema.sh:158`），
任何一项为假都吐同一句话，而那句话只描述了七项里的一项（别名）。

这与 `checks/_common/05-write-lease.sh` 注释里记的教训**同源**：
> 「原来这里静默变成 unknown，然后报「角色 unknown 没有写区路签」——
> 被拦住的人会以为是路签的问题……**错误信息把人引向错的地方，判例库记过三次。**」

这是第四处。已记进 findings（medium）与 recommendations。

## 修法（本次只修我自己的报告，gate 留给下一轮）

```bash
git -c core.quotePath=false diff --name-only --no-renames <base>..<head>
```
加上 `"branch"` 字段。核实：

```
$ jq -e '<gate 那条七项判据>' agents/consulter/docs/findings/report.json && echo 七项全过
✅ 七项全过
```

**注意 gate 读的是已提交 blob（`resolved_head:<path>`），不是工作区** ——
所以改完必须先 commit 才能看到它转绿，这一点第一次撞上时会误以为"改了没用"。

## 留给下一轮的 gate 修法（未做）

把七项大 `and` 拆成逐项判、报出具体缺哪一项；
错误文案里点明 `changed_files` 必须是 `core.quotePath=false` 形态 ——
**非 ASCII 仓必踩，而现在的报错说的是「禁止用别名」。**
