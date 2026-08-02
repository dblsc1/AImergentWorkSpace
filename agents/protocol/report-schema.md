# 统一 agent 报告协议 · report.json（项目级 · 通用部分）

> **铁律**：每个 agent 完成任务，在其工作目录产出 `report.json`（人类可读结论仍写 worklog/reviewreport.md，两者一致）。report.json 必须在本逻辑任务内 commit；未跟踪或工作区脏的 report.json 按“未产出”处理。接收方必须按下述 v2 算法核验，不能只信口头回报。无已 git 化 report.json = 无留痕 = 审核打回。
> 报告由**三部分拼成**：①项目级通用（本文件）②角色特色（框架根 `agents/roles/<角色>.md`）③模块特色（`<module>/module_docs/report.md`）。保持简单——CFO/人要一眼能读。
> 这份 schema 即将来 LangGraph state 字段（协议先行，后装零返工）。

## ① 项目级通用字段（每份报告都必填）

```json
{
  "schema_version": 2,
  "module": "example_module",
  "role": "reviewagent",
  "task": "纯产品重写验收",
  "tier": "hard",                    // simple|normal|hard，arbiter 接单时定，驱动文档重量
  "status": "approved",              // approved|rejected|blocked|escalate
  "summary": "一句话结论",
  "git": { "protocol": "embedded-self-v2", "branch": "feat/migration",
           "base": "<任务/PR起点的40位sha>", "head": "SELF", "diff_mode": "contains",
           "changed_files": ["code/backend/app/trade.py", "codeagent/<角色>/docs/report.json"] },

  "contract": { "touched": false, "which": [], "consumes": ["shared.capability.v1"] },
  "cross_module_impact": [],
  "escalation": null,                // 或 {"reason":"打回上限|规则冲突|架构缺陷","detail":"…","blocker":"blockers/<id>.md"}
                                     // escalation != null 必须同批在 blockers/ 立障（一障一文件，
                                     // 写明「解除标志：<仓内路径 或 文字@文件>」，可选「波及：<路径前缀>」——
                                     // 带波及的便条会在 mission_start 硬拦压线写区的发签）。标志出现后
                                     // checks/15 会强制销障——过期便条自堵，不靠人巡逻。

  "docs_reviewed": [                 // 改动波及的文档，逐条表态（铁律 11 的机械落点）
    {"path": "scripts/README.md", "action": "updated"},
    {"path": "agents/AGENTS.md", "action": "no-change-needed",
     "reason": "只改内部实现，该文档只描述接口"}
  ],

  "reviewer_opinion": {              // 每份报告必填，任何角色都不例外
    "reviewer": "programmer_reviewer",   // 谁审的（arbiter 的报告写 module_reviewer）
    "verdict": "pending",                // pending | approved | rejected
    "path": "review/reviewreport/2026-07-28-x.md",  // 详报，仓内相对路径
    "round": 1
  }
}
```

## canonical report 路径（项目级唯一事实）

新任务只承认下列 agent 间交接路径；`review/reviewreport/` 是人类详报/镜像区，不能代替 canonical `report.json`：

| 层级 / 角色 | canonical path |
|---|---|
| 模块 arbiter | `module_docs/report.json`（模块级交接，裁决 J3） |
| programmer 实例 | `code/<子文件夹>/report.json`（其负责代码侧的交接任务书，裁决 J3） |
| reviewer 实例（programmer_reviewer） | `review/reviewreport/report.json` |
| module_reviewer | `codeagent/module_reviewer/docs/report.json` |
| CFO（项目 arbiter） | `agents/cfo/docs/report.json` |
| consulter（**与 CFO 平级**，不隶属于它） | `agents/consulter/docs/findings/report.json` |
| **临时执行者**（CFO/arbiter 直派、不属上述固定角色的一次性 implementer / reviewer / 归档工等） | `<派活方>/docs/subreports/<YYYY-MM-DD>-<task-id>-<role>.md` |
| *(过渡期兼容)* 旧模块布局 | `codeagent/<角色>/docs/report.json` 仍被门禁承认，仅限 J3 迁移前生成的存量模块；新模块一律新落点 |

**J3/J4 配套落点（与 report.json 同为留痕硬要求）**：

- **worklog（简短）**：arbiter 写 `module_docs/worklog/`；programmer 写 `code/<子文件夹>/worklog/`。
- **handoff.md（一页纸说明，每改必核）**：模块级 `module_docs/handoff.md`；代码侧 `code/<子文件夹>/handoff.md`。
  改了某侧代码，同一提交必须更新该侧 handoff.md 或在 report.json `docs_reviewed` 里给出站得住的
  `no-change-needed` 理由——由 `checks/_common/14-handoff-fresh.sh` 机器核。
- **comm.jsonl（每 agent 一份，裁决 J4）**：agent 间发任务/报完成的格式化沟通留痕，append-only。
  实例：`codeagent/<programmer|reviewer>/<编号>/docs/comm.jsonl`；arbiter：`codeagent/arbiter/docs/arbiter.jsonl`。
  全仓 `logs/diary.jsonl` 保留为**脚本事件账本**（override 记账、门禁事件），不承担 agent 沟通。

**临时执行者落点是硬要求，不是建议。** 固定六角色覆盖不了的一次性活（迁仓、归档、写契约、专项审核……）**同样必须把报告落进仓内**：由派活方在自己 `docs/subreports/` 下收编并**随本任务一起 commit**。派活方 `report.json` 的 `sub_reports[].path` **必须是仓根相对路径**；填 `/tmp/...`、会话工作目录或任何仓外绝对路径，该子报告按**未产出**计（铁律 12 / 18）。临时执行者不必单独出 `report.json`——它的报告由派活方收编进 `sub_reports`，但**文件本身必须可被后来者 `git show` 出来**。

框架根 `scripts/gates/check-report-schema.sh` 只检查当前任务相对 PR/main merge-base 新增、修改或删除的 canonical reports，不扫描未变的历史报告。**当 merge-base == head（例如直接在 `main` 上提交）时区间为空，门禁不会退化为绿灯而是响亮失败**——空区间意味着"无法确定任务区间"，不等于"验过了"。若本任务变更 `review/reviewreport/*` 却没有同步变更 `codeagent/reviewagent/docs/report.json`，gate 直接拒绝 mirror-only 交付。

## `docs_reviewed`：改了东西，提到它的文档要表态

铁律 11 说「改动即同步文档 + 关联文档全同步」，但它一直**没有机械执行者**——
本字段就是那个执行者的落点，由 `scripts/checks/_common/11-doc-sync.sh` 核验。

**两层核对，别搞混**：
① **派单时声明**（`mission_start.sh --docs update:<路径> create:<路径>`）——
   arbiter 开单就承诺要动哪些文档，完工时**机器逐条核对**：声明 create 的在不在、声明 update 的有没有进本次提交。
   **机器只判机械事实**，理由的成色不归它管。
② **反查兜底**——arbiter 没预见到的文档，由「谁提到了我」自动补上，要求同批改或声明。

**关系的三个来源，主干是人维护的治理关系**：
1. `agents/cfo/doc-map.tsv` —— 长期文档治理哪片代码区域（项目级，CFO 维护）
2. 约定推导 —— `code/<模块>/code/**` → 该模块 `contract.md` 与 `AGENTS.md`（模块级，不用登记）
3. 反引号反查 —— **只作提示，不硬拦**（抓到的多是偶然提及，粒度太细）

主干为什么不能是自动反查：`contract.md` 治理 `code/backend/**`，
**哪怕它正文里一个路径都没写**。关系是语义的，机器推不出来。

另有 `checks/_common/12-standing-docs.sh` **每次提交都体检长期文档本身**
（存在、无占位残留、导航与实际结构一致）—— 文档烂掉是静默的，
没人会因为文档过时而报错，只会照着过时的文档做出错的东西。

`action` 只有两个值：`updated`（同批改了）/ `no-change-needed`（**必须写 `reason`**）。
写「无需改」是完全可以的，**写不出理由才是问题**。

## `oversize_files`：501–1000 行的那一笔记在这里

`CONSTITUTION.md` 的 C1（人类裁决 2026-07-29，台账 D12）分三档：
**≤500 常态 / 501–1000 必须在 `report.json` 里记一笔 / >1000 必须分拆**。
中间那一档的「记一笔」就落在本字段，由 `scripts/gates/run-gates.sh` 的
「单文件行数」gate 机械核验 —— 它把全仓所有 tracked `report.json` 的
`oversize_files[].path` 收成一个集合，**超 500 行却不在集合里就是红**。
不是警告：C1 说的是"必须记"，只警告等于"不记也行"。

```json
"oversize_files": [
  {"path": "scripts/foo.sh", "lines": 700,
   "why": "为什么现在还没拆", "plan": "什么时候、按什么边界拆"}
]
```

- `path`：仓根相对路径，**必须与 gate 看到的路径逐字相等**（写错一个字＝没记）
- 只在真有 501–1000 行的文件时才需要；没有就不写这个字段
- `>1000` 没有本字段可言 —— C1 原文「1000 以上没有说明余地」，记多少笔都拦

**唯一的例外是 D2/D14 存量导入**：保险柜**派生**的文件，在**文件顶部 20 行内**
写明「下次重构拆掉」的，gate 只打印警告不拦（偿还条件＝谁第一个为功能需求
实质修改它，谁负责拆）。例外由**文件自己**声明，不走名单——
名单会慢慢长成一份混着「真的还没弄」与「按定义永远不适用」两种东西的清单，
再也分不开（`doc-path-exempt` 踩过，修法是行级标记）。
保险柜**原件**（同目录 `SHA256SUMS` 守着的上游固化物，铁律 22）不在 C1
治理范围内：拆一行校验和就对不上，"删除前唯一的保险"当场变假。

断言：`scripts/selftest.sh` 第 62 项（三档各喂一次，两个致哑原因各做一发红测）。

## `reviewer_opinion`：谁审的，写在报告里

**每份 `report.json` 都必须带**，没有例外——包括 arbiter 自己那份（它的审核者是 `module_reviewer`）。

与「审核门在 merge 不在 commit」的关系**别搞反**：

| 时机 | 要求 | 为什么 |
|---|---|---|
| **提交时** | 字段存在且格式正确，`verdict` 可以是 `pending` | 否则 programmer 永远无法先提交形成 candidate，审核就无从谈起 |
| **合并时** | `verdict` 必须是 `approved` | **这才是审核门** |

`scripts/checks/_common/70-reviewer-opinion.sh` 管提交时那一格。
它拦的是**「根本没人审、也没打算让人审」的交付**，不是拦未审完的中间状态。

> ⚠️ **合并时的 approved 强制目前只写在规范里，尚未接进 `scripts/merge-to-integration.sh`。**
> 在接进去之前，这一格靠人和 reviewer 守，不要当成已有机械保证。

## `sub_reports` 的两个可观测字段（arbiter 必填）

派活方在 `sub_reports[]` 每一项里除 `role/status/path/agent/task` 外，还必须填：

- **`resumed`**（布尔）：该子代理是**续用**已开 session（`true`）还是**新开**（`false`）。
  重开＝冷启动重读全部上下文，实测一次修复轮重开可占整轮子代理消耗三成以上。
  记下来，「重开率」才成为可看的数字。
- **`objections`**（字符串数组）：该执行者**对指令本身提出的异议**（指出任务单/裁决单里的错误、
  不可执行的命令、范围过宽的断言……）。为空写 `[]`。

`objections` 这条的用意要说清楚：**框架里没有任何角色审「指令」**——执行者的产出有 reviewer 看，
编排者的产出没人看，错误只能靠执行者顺手撞见。与其增设一道审指令的闸门（贵、慢），
不如把**已经发生的纠正变成数据**：「某个 arbiter 的指令异议率」一旦可见，
模式级问题会自己浮出来，不需要有人去审每一单。代价已经付过了，别让信息蒸发。

## reviewagent 的标准 `review_target`

reviewagent（以及承担 L0 独立审核的 CFO consulter）的公共 `.git` 只描述自己的审核产物；被审范围必须使用顶层 `review_target`，禁止用 `target`、`review`、`range` 等别名替代：

```json
{
  "review_target": {
    "branch": "feat/task-slug",
    "base": "<被审范围起点40位sha>",
    "head": "<被审任务head的40位sha>",
    "diff_mode": "exact",
    "changed_files": ["code/backend/example.py"]
  }
}
```

五个字段全部必填；`base` / `head` 均为 40 位小写 Git SHA且 `base` 必须是 `head` 的祖先，`diff_mode` 固定为 `exact`。

**consulter 的非审查轮次：写 `"review_target": null`，不要回填**（2026-08-02，F6）。
consulter 的多数轮次（框架维护、架构裁决、调研入仓）不产生审查区间；
此前 schema 与两个 reviewer 一视同仁、缺则判否，于是只能翻出一个旧区间填进去——
**为满足门禁而回填的字段不再承载信息**，还会被合并门当成真的审核凭据。
现在的判据是：**键必须在，值可以是 `null`；省略仍判否**。
省略是疏忽，`null` 是决定，两者必须能区分开。
`programmer_reviewer` / `module_reviewer` 不给这个口子——它们的每一轮按定义都产生审查区间。

**合并门另有一条覆盖断言**（2026-08-02，F5）：`review_target.base` 必须是集成基线
（`$AIMERGENT_INTEGRATION_BRANCH`，默认 `dev`）的祖先或就是它。
此前 `merge-to-integration.sh` **只读 `.head`、从不读 `.base`**：前向那头有守
（白名单卡 `reviewed..candidate`），后向那头完全没守，于是**审得越窄越容易过**——
分支上有 C1..C10 而只审最后一个，`reviewed..candidate` 为空、白名单空转放行，
前九个 commit 一次没被审就合进去了。判据落在 `scripts/lib/review.sh`
（唯一实现，`merge-to-integration.sh` source 它），断言见 `scripts/selftest.sh` 第 59 项。`changed_files` 不得重复，必须与 `git diff --name-only --no-renames base..head` 的路径集合**完全相等**（缺项、多项、重复都拒绝），不能只把 `exact` 当字符串标签。它与 `.git.base` 不同：`.git.base` 必须来自 PR 目标分支，而 `review_target.base/head` 精确描述 feature 分支上的被审区间。

squash 后的 main 终态复验必须显式传入“旧 main SHA”为 task base、“新 squash SHA”为 head；不得让 `main==origin/main==HEAD` 退化为空 diff。此时 `SELF` 解析为新 squash commit，feature-only `review_target` 对象须在即时复验期间仍可解析并按上述 exact 集合核实。

## Git 证据协议：`embedded-self-v2`

同一 commit 的最终 SHA 取决于 report 内容，report 又不能预先写入该 SHA；因此 v2 用 `head: "SELF"` 明示“最后修改这份 report 的 commit”，禁止继续填旧 HEAD 冒充任务 head。

字段规则：

- `base`：逻辑任务或 PR 起点的完整 40 位 commit SHA，必须取自 **PR 目标分支**（通常是建任务分支时的 `main`），并是解析后 head 的祖先。禁止把只存在于 feature 分支的中间 worker/arbiter commit 填入公共 `git.base`，因为 squash 后该 commit 不在 `main` 祖先链上；中间审核范围必须放在角色字段 `review_target`。
- `head`：新报告固定为字符串 `SELF`；接收方从本 report 路径在当前交接 ref 上的最近提交解析 `resolved_head`。
- `diff_mode`：固定 `contains`。`changed_files` 是本角色声称交付的仓根相对路径集合；每一项必须出现在 `git diff --name-only --no-renames base..resolved_head` 中。允许同一 PR 中其他角色文件也出现在 diff，但不得声称 diff 中不存在的文件。
- `changed_files` 必须包含本 report 路径；有 worklog 的任务也必须包含本角色 worklog。reviewagent 的公共 `git` 描述审核产物，另用 `review_target` 描述被审范围。
- report 必须被 Git 跟踪且工作区干净；其已提交 blob 必须与当前交接内容一致。每次后续任务更新 report，都会产生新的 `resolved_head`。

机械核验：

1. `git status --short -- <report路径>` 无输出，且 `git ls-files --error-unmatch <report路径>` 成功。
2. `resolved_head=$(git log -1 --format=%H -- <report路径>)`，从 `resolved_head:<report路径>` 读取 JSON，而非先信工作区。
3. `base`/`resolved_head` 均可解析，且 `git merge-base --is-ancestor base resolved_head` 成功。
4. `git diff --name-only --no-renames base..resolved_head` 必须包含 `changed_files` 每一项；report 自身和本角色 worklog 不得缺。
5. feature 分支交接与 CI 用上述算法；**仅当 `base` 来自 PR 目标分支时**，squash 后 report 中的 `SELF` 才能解析为新 squash commit 并继续通过 ancestor/contains 复核。接收方必须在 squash 后再跑一次机械验证，不得用 feature 分支绿灯代替 main 终态验证。

限制必须诚实记录：最终 squash 会合并各 agent 的原始提交，v2 在 `base` 符合上述条件时能长期证明“这些文件进入了该逻辑 PR/squash”，不能永久保存每个 agent 的原始 commit 边界。需要提交级永久审计时必须改用保留历史的 companion/attestation 方案，不能宣称 v2 已提供。

legacy report（无 `schema_version:2` / `protocol`）只做历史分级；range 可复现可标 `legacy-range-verified`，range 失真则标 `legacy-committed-unverifiable`，未跟踪/脏则标 `legacy-untracked-or-dirty`。legacy 不得批准新任务，必须由原角色按 v2 更新并 commit。

## 升级面（CFO 路由只看这三格）
- `contract.touched` + `which`（碰平台契约 → 项目级审 + 消费方契约测试）
- `cross_module_impact` 非空 → CFO 协调受影响模块
- `escalation` 非 null → CFO/consulter 介入

其余字段=模块级；升级面=项目级。字段全来自事实（git/测试输出），不是自评感觉。

## ②③ 拼接
角色特色字段见框架根 `agents/roles/<角色>.md` 的「报告特色字段」节；模块特色见模块 `module_docs/report.md`。三部分合成一个 json 对象。

## tier 驱动文档重量（见框架根 `AGENTS.md` 文档体系）
arbiter 接单时定：`simple`=只 report.json+commit；`normal`=加 diary 事件流水；`hard`=全套（diary + 做完检查是否更 handoff）。文档重量匹配任务重量。
