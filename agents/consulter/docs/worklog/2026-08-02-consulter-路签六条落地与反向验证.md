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

---

## 层② · 「完工即自动还签」的落点 = 拒发这一刻

**裁**：CFO 接受上一轮驳回 `dispatch.sh` 的理由，落 `mission_start.sh` 拒发分支，
**不自动强收**，只印可粘贴的强收命令。

**修**：`scripts/lib/lease.sh` 新增 `lease_takeover_advice <持有者> [重试命令]`，
`mission_start.sh` 第 2 项 `cl_bad` 之后逐行 `cl_note` 印出来。三条判据：

| | 判据 | 为什么是它 |
|---|---|---|
| A | 持有者写区在工作树里干净（`--untracked-files=no`） | 有未提交改动 = 人还在里面干活，收了就是抢 |
| B | 起飞 `--docs` 声明的文档变更已落地（入仓 + 发签之后真被改过） | 声明了没做完 = 任务没结束 |
| C | 签龄已知 | 无 meta 的签判不出走没走，只能人看（`lease_expired` 同一条原则：未知 ≠ 过期） |

三条全过 → 印「结论：可强收」+ 两行可直接粘贴的命令（还签 + 原样重试）；
任一条不过 → 印「结论：不建议强收」并指出打 ✗ 的那条。**函数从不调用 `lease_release`。**

真实拒发输出（沙箱实跑）：

```
│  ❌ 2  写区重叠
│       ├ 现状：scripts/lib/ ⊂ scripts/ ← 持有者 arbiter（已持有 25 分钟，任务：module_docs/任务单-A.md）
│     强收判据（三条全过才建议收；本脚本永远不自动收）：
│       ✓ A 持有者写区在工作树里干净
│       ✓ B 起飞时声明的文档变更都已落地（或没声明）
│       ✓ C 签龄已知：25 分钟
│     结论：可强收。直接粘贴——
│       scripts/mission_start.sh --release arbiter
│       scripts/mission_start.sh consulter task.md scripts/lib/
```

**断言**：`selftest.sh` 第 55 项，一次验三件事（缺一件机制就退化）：
① 干净时判「可强收」且给可粘贴命令 ② 脏时判「不建议」且指出原因 ③ **两种情况下签都还在**。

沙箱这次**真做了一次 commit**：没有 commit 的仓里「写区干净」是白捡的
（未跟踪文件不算脏），那样判据 A 就成了恒真——正是判例库「恒定答案类」的形状。

**反向验证**（三种改坏法，各自红在不同的断言上）：

| 改坏 | 结果 |
|---|---|
| A 拆掉 `lease_takeover_advice` 调用 | `❌ FAIL 拒发只说「重叠」，不给强收判据` · PASS 54 FAIL 1 |
| B 判据 A 改成恒真（不看写区脏不脏） | `❌ FAIL 持有者写区有未提交改动时仍建议强收 —— 判据恒真，等于没有判据` · PASS 54 FAIL 1 |
| C 拒发时顺手 `lease_release` 自动强收 | `❌ FAIL 拒发时把别人的签自动收了（clean=0 dirty=0）` · PASS 54 FAIL 1 |

三处从备份副本恢复后：`PASS 55 · FAIL 0 · N/A 0`。

---

## F3 · 角色自动推断对 J3 五条 canonical 路径全失效

**病**：`mission_complete.sh:32` 只有一条正则 `s|^codeagent/([^/]+)/docs/.*|\1|p`，
只认 J3 迁移**前**的旧布局。实测把 J3 之后的 canonical 留痕路径逐条喂进去：

```
agents/consulter/docs/worklog/x.md            -> [<空>]
agents/cfo/docs/worklog/x.md                  -> [<空>]
module_docs/worklog/x.md                      -> [<空>]
code/backend/worklog/x.md                     -> [<空>]
review/reviewreport/x.md                      -> [<空>]
codeagent/programmer/docs/worklog/x.md        -> [programmer]     ← 唯一命中的
```

`role=unknown` 是非空字符串 → checks/05 去找 `unknown.lease` → 找不到判失败。
**本轮开工时这个 bug 当场演示了一次**：`mission_start.sh` 出的派单提示词里嵌的
`mission_complete --list` 打印的是「角色 = unknown」；我第一次 commit 也被
「角色 unknown 没有写区路签」拦下，只能手动传 `AIMERGENT_ROLE=consulter`。

**修**：判据搬进 `scripts/lib/paths.sh` 的 `role_from_trace_path` / `role_from_staged`
（按判例库「一律用公共件，不再各自手写路径判断」），两层优先级：

- **层① 显式角色目录**：`agents/<角色>/docs/…`、`codeagent/<角色>[/<编号>]/docs/…`
- **层② 布局推导**：`module_docs/`→arbiter、`review/`→programmer_reviewer、
  `code/<子文件夹>/`→programmer（**仅模块仓**，用 `repo_is_module` 判——框架根的
  `code/` 装的是模块仓，不是代码侧）

层①命中就不看层②：module_reviewer 的 canonical 报告在 `codeagent/module_reviewer/docs/`，
而它同时会碰共写区 `review/reviewreport/`——平表会把它判成歧义、推成 unknown。

**同层出现两个不同角色 = 返回空**，不许 `head -1` 挑第一个：静默给一个可能错的答案
比说不知道更糟。推不出来时 `mission_complete` 现在**说清为什么**，
而不是让人对着「角色 unknown 没有写区路签」去给 unknown 申领一块签。

**断言两条**：
- **#56** 纯判据逐条喂：十条 canonical 路径 + 两条反面（框架仓 `code/gantt/` 不许
  推成 programmer；`agents/cfo/` 与 `agents/consulter/` 同批必须判歧义）。
- **#50 扩测**：原来只喂旧布局那一条 —— **五条里唯一还能命中旧正则的那条**，
  所以它长期恒绿而现实全是 unknown。现在**三种布局逐个端到端跑**
  （旧布局 / J3 模块级 / J3 项目级），验的是「推断→export→05 真拦住」整条链。

**反向验证**：

| 改坏 | 结果 |
|---|---|
| A 把 `mission_complete.sh` 换回修复前的旧正则（历史复现） | #50 `❌ FAIL [J3模块级 …exit=1] [J3项目级 …exit=1]`，**旧布局那条照样绿** —— 正是「断言只覆盖粗心的一半」的现场 |
| B 歧义时挑第一个 | #56 `❌ FAIL 同层出现 cfo 与 consulter 两个角色却给了「cfo」` · PASS 55 FAIL 1 |
| C 删掉 J3 项目级规则 | #50 `❌ FAIL [J3项目级 …]` + #56 `❌ FAIL agents/consulter/… 实得「<空>」` · PASS 54 FAIL 2 |

三处从备份副本恢复后：`PASS 56 · FAIL 0 · N/A 0`。

**红测 A 是这条最要紧的证据**：它证明旧断言在 bug 存在时是绿的。
