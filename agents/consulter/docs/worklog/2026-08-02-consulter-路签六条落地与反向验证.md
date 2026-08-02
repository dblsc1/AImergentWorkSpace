# 路签机制六条缺陷落地（F4 / 层② / F3 / F2 / F5·F6 / F7）

日期：2026-08-02 · 角色：consulter · 任务单：`agents/consulter/docs/worklog/2026-08-02-任务单-F4层2F3F2F5F6F7落地.md`

派活方 CFO 已对上一轮六条发现逐条裁决，本轮只做实现 + 断言 + 反向验证。
**每一条都按铁律 23 要求：修到类、留断言、且把断言改红过一次再复绿。**

---

## F4 · 路签拒发不记账

**病**：`mission_start.sh` 只在发签成功后 `emit_event lease_grant`；拒发走 `cl_bad` 后
`cl_footer … || exit 1` 直接退出，全程零事件。账上 18 条发签、**0 条拒发** ——
CFO 报的「今天为此卡了三次」查无实据，且任何优化路签的方案都没有基线可比。

**修**：`scripts/mission_start.sh` 的 `cl_footer` 失败分支改成显式 `if !` 块，
退出前 `emit_event lease_denied "<角色>: 申领 <写区> 被拒（<失败项>）" 1`。
事件带**失败项清单**而不只是「被拒」——只记「拒了」数得出次数、数不出原因分布。

**断言**：`scripts/selftest.sh` 第 54 项「路签拒发是否记账」。
两侧都验（防恒真）：
- 重叠申领 → rc≠0 且 diary 有 `lease_denied`
- 不重叠申领 → rc=0、有 `lease_grant`、**且一条 `lease_denied` 都没有**

沙箱 `_mk_lease_sandbox` 造的是「只有第 2 项会失败、其余七项全过」的仓，
所以断言变红时红的原因唯一，不会被别的检查项顺带拖红。

**反向验证**（把修好的改坏）：
删掉 `emit_event lease_denied` 那两行后：

```
54. 路签拒发是否记账
  ❌ FAIL  拒发没有落 lease_denied 事件 —— 拒发次数不可数，路签优化没有基线可比（F4）
── 小结: PASS 53 · FAIL 1 · N/A 0 ──
```

从备份副本恢复后（不用 `git checkout --`，判例库「红/绿扰动前先看工作区」）：

```
54. 路签拒发是否记账
  ✅ PASS  拒发落 lease_denied（rc=1）、正常发签只落 lease_grant 不误记拒发
── 小结: PASS 54 · FAIL 0 · N/A 0 ──
```

**顺带**：写断言时自己踩了一次判例库「验证者自己的 $? 也会撒谎」的同族坑 ——
`grep -c … || echo 0` 在无命中时会输出两行 `0`（grep 自己印一个、`echo` 再印一个），
喂给 `[ ]` 直接语法错。已抽成 `_cnt()` 并在注释里写明，防下一个人重踩。
