# 2026-08-02 · consulter · 拆 selftest.sh（C1 执行，零行为改动）

## 做了什么

`scripts/selftest.sh` 1129 行 → 46 行入口 + `scripts/selftest.d/` 六个分片，
每片 118–314 行，全部 ≤500。**入口仍然只有 `scripts/selftest.sh`。**

| 分片 | 断言 | 行 |
|---|---|---|
| `10-false-green.sh` | 1–20 假绿灯 / 留痕落点 / 硬闸门是否存在 | 191 |
| `20-literals-and-boundary.sh` | 17′–35 字面量自毁 / 嵌套仓边界 / 诊断质量 / 监督分工 | 148 |
| `30-layout-and-instances.sh` | 38–37 J3 布局 / 实例模型 / 停线旗 / 化石计数 | 157 |
| `40-roles-and-hatches.sh` | 49–51 模型分档 / 角色 export / 逃生口记账 | 118 |
| `50-lease-and-handoff.sh` | 52–55 模块 handoff / 路签 TTL / 拒发记账 | 195 |
| `60-inference-and-exempt.sh` | 56–61 角色推断 / 行级豁免 / 审核覆盖 / 密钥判据 | 314 |

分片是**纯搬运**：用 `sed -n '<起>,<止>p'` 从原文件按行区间切出来，
切点全落在用例之间的空行上，一个字符没改。分片共享入口里的
`repo/S/P/F/N` 与 `pass/fail/na`，所以只能 source，不能单独执行（每片头部写明）。

## 零行为改动的证据（不是"我看着一样"）

```
$ bash scripts/selftest.sh > /tmp/selftest-after.txt 2>&1; echo EXIT=$?
EXIT=0
$ diff /tmp/selftest-before.txt /tmp/selftest-after.txt && echo "[完全一致]"
[完全一致]
before sha256 ceb2d460c5f33fa42f43aed457fb97e46981e16b0c52f28075e7c79514592cab
after  sha256 ceb2d460c5f33fa42f43aed457fb97e46981e16b0c52f28075e7c79514592cab
── 小结: PASS 61 · FAIL 0 · N/A 0 ──
```

**整份输出逐字节相同、SHA256 相同**，用例编号与 PASS/FAIL/N/A 计数全不变。

## 装载器刻意不写成 `[ -d "$D" ] && for …`

那正是铁律 23 点名的高频形状。如果分片目录被 gitignore 吞掉、或没随 clone 下来，
`for` 循环跑零次、脚本照常退 0 —— 界面上和「61 条全绿」**一模一样**。
拆分把「一个文件」变成「一个文件 + 一个目录」，等于新造了一个可以静默丢失的部件；
不给它配断言，这次拆分本身就是在制造下一个假绿。

三层守卫，三发反向验证都点了火：

| 情形 | 期望 | 实测 |
|---|---|---|
| `selftest.d/` 整个消失 | 响亮死 | `EXIT=2` + `❌ 缺 …/selftest.d` |
| 目录在但一个分片都没有 | 响亮死 | `EXIT=2` + `❌ … 里一个断言分片都没有` |
| 分片在但断言全体哑火 | 响亮死 | `EXIT=2` + `❌ 断言分片一条都没跑` |
| 恢复 | 复绿 | `PASS 61 · FAIL 0 · N/A 0` |

第三层（`pass+fail+na > 0`）是关键的一层：前两层只管"文件在不在"，
只有它管"跑了没跑"。**不写死条数** —— 写死的 N 是化石，断言 36 就是为这个存在的。

严格说三层守卫是行为改动，但它们只在**拆分前不可能出现的状态**下触发
（拆分前没有 `selftest.d/`），所以对任何既有输入行为完全一致。

## 为什么单独一条 commit

拆分会重排整个文件。和别的修复混在一个区间里，审的人没法把
"搬运"和"改动"分开 —— 那不是审得慢，是审不了。
CFO 2026-08-02 裁决：C1 必须执行、不给 selftest 开口子、但拆分单独成 commit。

## 顺带记一笔（不在本 commit 修）

`agents/reference/工作空间全景.md:255` 写着「34 条闸门有效性断言」，实际 61 条 ——
化石计数，与断言 36 防的是同一个形状，但 36 没盯到这一处。已进 findings。
