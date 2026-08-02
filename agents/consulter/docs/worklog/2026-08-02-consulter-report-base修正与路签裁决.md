# 2026-08-02 · consulter · 修 report.json 的 git.base + 裁决「完工即自动还签」落点

任务 id：`consulter-report-base-lease-ruling`
派活方：CFO（联络消息）。写区路签：`agents/consulter/`（本轮申领，与 CFO 的
`scripts/ agents/cfo/ logs/ .gitattributes .gitignore code/_template/` 不重叠）。

## 一、元数据修正（本轮唯一的实际改动）

### 1.1 `git.base`

`agents/consulter/docs/findings/report.json` 原填 `4ab1b36e9d81…`，那是
`feat/frontend-view-modules` 上的中间 commit，不是分叉点。改为：

```
211bdec5559ab4ab798a464272cde5dc27f64740   # git merge-base HEAD origin/main
```

判据原文在 `agents/protocol/report-schema.md`：「`base` 必须取自 **PR 目标分支**…
禁止把只存在于 feature 分支的中间 worker/arbiter commit 填入公共 `git.base`，
因为 squash 后该 commit 不在 `main` 祖先链上」。

**我为什么会填错**：上一轮我把 `git.base` 理解成「本次改动的起点」——那是直觉，
不是判据。CFO 这轮改了 `scripts/gates/check-report-schema.sh` 的报错文案、
把正确值直接印出来，我这次是抄的。这条改动有效，记一笔：把一道每次都要重新推理的
题变成复制粘贴，是对的方向（同一个错一个月内五次，说明判据表述而非人的问题）。

### 1.2 顺带查出并修正的第二处：`git.branch` 填的是远端目标名

原填 `"v5"`（CFO 推送的目标分支），实际本地分支是 `feat/frontend-view-modules`。
`scripts/merge-to-integration.sh` 的 `verify_report()` 里有一条
`.git.branch == $branch` 的**等值**比较（`$branch` 是被合并的那条分支），
填远端目标名会让合并门在真正合并时判否。对照 `agents/cfo/docs/report.json`：
它填的是 `feat/frontend-view-modules`，本仓约定是本地分支名。同批把
`review_target.branch` 一起改了。

**这条推送门查不出来**——`check-report-schema.sh` 只要求 `git.branch` 是非空字符串。
也就是说它会一直静默到真正合并那天。属于「今天不炸、合并日炸」的一类。

### 1.3 `review_target` 换成本轮真读过的区间

原值是 2026-07-29 的全景文档审查区间（`62c2f91..cbce175`），距今四天，
且是 commit `ee5806a`「恢复 review_target 最近审查区间——推送门 schema 要求」
专门回填进来的。本轮换成我确实读过的区间：

```
06cf052452612f5d2f28e86e7f9e8da33713cdad .. a0ac81af61f33e3f3be5447dbbd54687f078929f
```

即 CFO 本批的三个 commit（路签 TTL / 账本 union / 报错文案）。`changed_files`
用 `git -c core.quotePath=false diff --name-only --no-renames` 取（**不加
`core.quotePath=false` 会把三条非 ASCII 路径输出成 C 转义串**，与门禁的取法不一致，
逐字符比对必然不等——铁律 23 那条注释里点名的老坑，这次是绕过去了不是撞上）。

**刻意不带 `verdict` 键**：对 CFO 逐单产出的例行裁决归 cfo_reviewer（裁决 J6），
不归我。我读这三个 commit 是为了回答 CFO 交下来的架构问题，不是替 cfo_reviewer 收审。

## 二、裁决：「完工即自动还签」的落点

### 2.1 结论：驳回 `scripts/dispatch.sh`

CFO 的倾向是「dispatch.sh 出完提示词自动还派活方的签」。驳回，三条：

**(a) `dispatch.sh` 不知道派活方是谁。** 它的 argv 是 `<接收方角色> <任务单>`；
`mission_start.sh` 末行 `exec dispatch.sh "$role" "$task"` 传的也是**接收方**。
要实现「还派活方的签」必须新加 `--as <角色>` 或读 `AIMERGENT_ROLE`——而该值缺省时
的行为是「静默地什么都不还」。这正是 2026-07-31 已经付过学费的形状（判例库
「恒定答案类」第五条：调用方漏传环境变量，子检查恒放行）。同一个坑，第二次。

**(b) 派活方的签常常不是闲置的。** arbiter / CFO 的正常节奏是「写任务单 → 派单 →
写下一张任务单」，它对 `module_docs/` 或 `agents/cfo/` 的持有跨越多次派单是合理的。
dispatch 时还签，会让它下一次提交撞上 `scripts/checks/_common/05-write-lease.sh`
的「角色 X 没有写区路签」，而那句错误文案**不会说明是上一次派单还掉的**。
更糟的是它会加速下面 §2.2 那条棘轮：持有者看到 05 报错的标准反应是放宽自己的签。

**(c) 它答的不是层②那个问题。** 层②的原文是「释放不在关键路径上」。派单不是完工；
落在 dispatch 里的释放依然是自愿动作，只是换了个触发器。

### 2.2 但 CFO 的直觉指向了一个更大的问题：写区粒度棘轮

`logs/diary.jsonl` 五天连续证据，cfo 的签：

```
07-29 05:54  agents/
07-29 13:51  agents/ contracts/                       ← 13:51:28 mission_blocked failed:"write-lease"
07-29 13:52  agents/ contracts/ .gitignore/           ← 13:52:41 mission_blocked failed:"write-lease"
07-29 14:16  agents/ contracts/ .gitignore
07-30 06:02  … blockers/ …                            ← 06:02:19 mission_blocked failed:"write-lease"
08-01 09:34  scripts/ .gitignore agents/ contracts/ blockers/ code/_template/ logs/
08-02 07:46  .gitattributes .gitignore code/_template/ agents/ logs/ scripts/ contracts/ blockers/
08-02 07:49  scripts/ agents/cfo/ logs/ .gitattributes .gitignore code/_template/   ← 手动收窄
```

每一次膨胀前 90 秒内都紧跟一条 `mission_blocked failed:"write-lease"`。
**05 号拦下越区提交 → 持有者放宽自己的签 → 下次更宽。这条棘轮没有反向齿**
（唯一一次收窄是 08-02 07:49 CFO 手动做的）。终点是一个角色持有整仓，那时路签
不再是碰撞探测器，而是派活方持有的全局互斥锁——CFO 今天卡子代理三次正是这个终点。

`scripts/lib/lease.sh` 自己写了判据：「**一旦签开始串行化正常并发，说明写区划分
错了或粒度太粗**」。已经触发。在这个循环里加自动释放 = 在棘轮末端装缓冲垫。

### 2.3 层②真要落，落在 `scripts/mission_start.sh` 的拒发分支

关键路径的定义是「有人被阻塞在这里，不解决走不下去」。拒发那一刻符合定义
（被卡方就在现场）；dispatch 时刻没有任何人在等，不符合。

做法（**留给实现轮，本轮不动 `scripts/`**）：拒发时若持有者的写区在工作树里干净、
且其声明的文档变更已全部落地，就把判据连同一条可直接粘贴的强收命令印给被卡方。

**不做自动强收。** `scripts/lib/lease.sh` 自己写了红线：「静默回收会让双写发生
（安静）」——那条红线对拒发路径同样成立。

### 2.4 实现顺序：F4 排在层②之前

三条本轮查出的缺陷有同一个形状——**机制的输入或输出有一段是黑的**：

| 编号 | 缺陷 | 黑在哪 |
|---|---|---|
| F3 | `scripts/mission_complete.sh` 的角色推断正则只认旧布局 `codeagent/<角色>/docs/`；J3 五条 canonical worklog 路径**全部**推断为空 → `role=unknown` → 05 号去找 `unknown.lease` 判失败 | 推断不出角色 |
| F4 | `scripts/mission_start.sh` 只在发签成功后 `emit_event lease_grant`；重叠拒发走 `cl_bad` 后直接 exit，**不产生任何事件** | 拒发不留痕 |
| F5 | `scripts/merge-to-integration.sh` 对 `review_target.head` 只断言「是候选的祖先」——**审得越旧越容易过**，从不断言审核覆盖了候选 | 审核新鲜度不可判 |

层②的方案再好，装在一个看不见自己状态的机制上也验不出效果。所以：
**F4（补 `lease_denied` 事件）→ 层② → F3 → F2 → F5/F6**。F4 排第一，
因为它是唯一能验证后面几步有没有效果的度量（CFO 报的「卡了三次」现在查无实据）。

### 2.5 F3 的实证（本轮机械核实，非推断）

把五条 J3 布局的 canonical worklog 路径逐条喂给 `scripts/mission_complete.sh`
里那条推断正则 `s|^codeagent/([^/]+)/docs/.*|\1|p`：

```
agents/consulter/docs/worklog/x.md      → 空 → unknown
agents/cfo/docs/worklog/x.md            → 空 → unknown
module_docs/worklog/x.md                → 空 → unknown
code/backend/worklog/x.md               → 空 → unknown
review/reviewreport/x.md                → 空 → unknown
codeagent/programmer/docs/report.json   → programmer      ← 唯一命中，旧布局
```

**为什么 2026-07-31 那次修复没抓到**：`scripts/selftest.sh` 第 50 项的沙箱暂存的
正是 `codeagent/programmer/docs/worklog/x.md`（该文件第 550 行）——唯一还能命中正则的
那条路径。断言恒绿，现实全是 unknown。判例库「被测对象是哪一份」第三次实证：
红/绿都点了火，点的是唯一还能亮的那盏灯。

活证据：本轮 `scripts/mission_start.sh` 出的派单提示词里，判据表标题自己印的就是
`（角色 = unknown；开工时就该读到）`——框架在自己的派单提示词里承认推断失败了。

## 二之补 · F7：推送门全绿时吐 `comm` 排序警告（已反向验证非假绿）

修完跑 `./scripts/gates/run-gates.sh`，退出码 0，但 stderr 混着三行：

```
comm: file 1 is not in sorted order
comm: file 2 is not in sorted order
comm: input is not in sorted order
```

来自 `scripts/gates/check-report-schema.sh` 里 `review_target.changed_files`
的逐条比对：`sort -u` 走 locale 排序（`LANG=en_US.UTF-8`），本仓路径大量含非 ASCII，
排出的序与 `comm` 期望的不一致。

**必须先问「这是不是假绿」**，不能因为退出码 0 就放过（判例库第一条）。
反向验证，五种输入喂同一条比对逻辑：

| 输入 | 期望 | 实测 |
|---|---|---|
| 少一条非 ASCII 路径 | 检出 | 🔴 检出 |
| 少一条 ASCII 路径 | 检出 | 🔴 检出 |
| 多一条伪造路径 | 检出 | 🔴 检出 |
| 非 ASCII 换成 C 转义形态 | 检出 | 🔴 检出 |
| 完全一致 | 判一致 | 🟢 判一致 |

**未观察到假绿**，判定为噪音而非漏判。但仍建议修（两处 `sort` 加 `LC_ALL=C`，
已验证警告随即消失）：绿灯里混着红色 stderr 会训练人忽略门禁输出，
而这道门恰恰是靠人读输出的。**只报不修**——`scripts/` 本轮只读。

（另记一笔自己踩的坑：本轮我核「改动有没有越出 `agents/consulter/`」时，
第一版命令用了默认的 `git diff --cached --name-only`，非 ASCII 路径被输出成
带引号的 C 转义串，`grep -v '^agents/consulter/'` 的锚点被开头的 `"` 顶掉，
于是两条**本来合规**的路径被报成越界。加 `-c core.quotePath=false` 后归零。
同一个 C 转义形状，今天在我这里出现第二次——一次在写 `review_target` 时避开了，
一次在验证命令里撞上了。验证命令本身也是代码。）

## 三、边界与自我监督

- 本轮改动**全部落在 `agents/consulter/` 内**，未动 `scripts/` 一个字符
  （`scripts/` 同时是 CFO 当前路签覆盖面，动它同时越写边界与越路签）。
- F1–F6 全部**只写报告，不顺手修**——consulter 三条红线之一。
- 裁决是**建议级**：层②落点、F2 的阈值、F5/F6 的 schema 改动都动共享协议，
  按 `agents/protocol/supervision.md` 归 CFO 或人类拍板，不由我落条文。
- 本轮未改框架实现，因此不存在自审自批（裁决 J7）。若后续由我实现 F3/F4，
  那部分须由人类复核，CFO 只辅助举证。

## 四、验证

```
./scripts/gates/run-gates.sh   → 退出码 0（直取 $?，不看管道回显）
```

提交走 `AIMERGENT_ROLE=consulter git commit`——见 F3，本仓角色自动推断对
`agents/consulter/docs/worklog/` 失效，不显式传会被判成 `unknown` 卡在 05 号。
**这一步本身就是 F3 的第三份证据**：连 consulter 提交自己的留痕都得手动绕过推断。
