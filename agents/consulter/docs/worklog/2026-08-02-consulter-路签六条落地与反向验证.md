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

---

## 插曲 · F3 的断言把引用完整性门顶红了（顺手修了类，不是修那五条）

**病**：F3 的断言必须把 J3 canonical 路径写成字面量喂进角色推断，
而 `check-references.sh` 把这些**测试输入**全判成悬空引用（CFO 发现并提醒）。

CFO 的建议是登记进 `doc-path-exempt.txt`。我没照做，理由是**那只修实例**：
下一个写路径类断言的人照样撞；而且那张表会慢慢长成一份混着
「真的还没建」和「按定义永远不会建」两种东西的名单，再也分不开。

**修**：给 `check-references.sh` 加**行级** `ref-fixture` 标记
（行尾注释里出现这个词，该行不参与引用提取）。为什么是行级不是文件级：
文件级豁免等于「这个文件里的死链我全不查了」，那是把门变哑。
`doc-path-exempt.txt` 头部同批写清这一类该走哪条路。

**断言 #57**，两侧都验：
① 带标记的测试输入被放行 ② **同一个文件里**没带标记的真悬空引用照样红。

**反向验证**：

| 改坏 | 结果 |
|---|---|
| A 标记不生效（去掉那段 `grep -v`） | `❌ FAIL 带 ref-fixture 标记的测试输入仍被判成悬空引用 —— 类没修掉` |
| B 豁免改成整文件（文件里出现一次就全跳过） | `❌ FAIL 没带标记的真悬空引用也没被抓 —— 豁免机制把整道门变哑了` |

恢复后 `PASS 57 · FAIL 0`。B 那一发是这条最要紧的：**任何豁免机制都必须验它没顺手把门关掉**。

---

## F2 · 写区粒度棘轮（只警告不拦，警告带签史）

**病**（三条里 CFO 判为最要紧的一条）：五天 diary 的形状不是「谁忘了还签」，
而是 **05 拦下越区提交 → 持有者放宽自己的签 → 覆盖面越来越大**，
每次膨胀前 90 秒内都紧跟一条 `mission_blocked failed:write-lease`。
棘轮没有反向齿，终点是一个角色持有整仓——那时路签不再是碰撞探测器，
而是派活方持有的全局互斥锁。唯一一次收窄是 07:49 人手做的。

**修**：`lib/lease.sh` 新增 `lease_widening` + `lease_grant_history`，
`mission_start.sh` 加第 9 项。**只警告不拦**——「多宽算太宽」是判断题，
硬拦会逼人用逃生口（判例库明文）。

放宽的两种形状都认：
- **放宽**：旧签落在新签里（`docs/a/` → `docs/`）
- **新增**：新签不落在任何旧签里

**签史是这条的关键**。光说「你的签有点宽」没有信息量；
`lease_grant_history` 从 `logs/diary.jsonl` 取该角色的 `lease_grant`，
打印次数 + 每次的片数，**且永远带上第一条**（中间的略）——
棘轮比的是起点和现在，只看最近几次看不出单调性。
diary 不入仓，所以这是本机视角：新克隆上没有历史就不打印，**不假装有**。

**当场自证**：写完之后我给自己申领 `scripts/ agents/`，第 9 项立刻报：

```
│  ⏭  9  写区比上一次放宽了（只提醒，不拦）
│       放宽：agents/consulter/ → agents/
│     本机记到的 consulter 签史（2 次发签；diary 不入仓，新克隆上没有）：
│        2026-08-02T07:55  1 片  agents/consulter/
│        2026-08-02T08:12  2 片  scripts/ agents/consulter/
```

我按它的提示把签改回窄的（`scripts/ agents/consulter/ agents/protocol/report-schema.md`，
单文件签），第 9 项转 ✅。**这条警告在它落地的第一分钟就拦下了自己作者的一次棘轮。**

**断言 #58**，四件一起验：① 放宽检出并指名道姓 ② 只警告不拦（rc 必须为 0）
③ 签史带第一条 ④ 收窄/不变不误报（误报会训练人忽略警告）。

**反向验证**：

| 改坏 | 结果 |
|---|---|
| A `lease_widening` 恒不命中 | `❌ FAIL 写区从 docs/a/ 放宽到 docs/ 没被检出 —— 棘轮转了没人看见` |
| B 第 9 项改成 `cl_bad`（硬拦） | `❌ FAIL 放宽被硬拦了（rc=1）—— 判断题不做硬闸门` |
| C 签史略掉第一条 | `❌ FAIL 签史略掉了第一条 —— 只看最近几次看不出单调性` |

恢复后 `PASS 58 · FAIL 0`。
