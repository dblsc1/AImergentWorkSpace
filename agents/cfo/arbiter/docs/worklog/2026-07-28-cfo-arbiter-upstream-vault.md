# worklog · 上游前端原件入仓固化

- 日期：2026-07-28
- 角色：CFO arbiter
- 任务号：`root-upstream-vault-260728-204450-674`
- 分支：`chore/upstream-vault`
- tier：`normal`
- 任务单：`agents/cfo/arbiter/docs/worklog/2026-07-28-任务单-upstream-vault.md`

## 做了什么

把两份本机唯一、不可再生的上游前端原件固化进根仓，并留 SHA256 指纹（铁律 22）。

| 仓内落点 | 源 | SHA256 | 行数 |
|---|---|---|---|
| `agents/reference/upstream/index.html` | `/home/xia/Downloads/index.html` | `74c70abc…13ee9e` | 1280 |
| `agents/reference/upstream/project-task-contribution-ring.html` | `/home/xia/Downloads/project-task-contribution-ring.html` | `a70abc27…50af0e` | 251 |

同时产出 `agents/reference/upstream/SHA256SUMS`（可 `sha256sum -c` 复核）与
`agents/reference/upstream/README.md`（来源台账 + 只读规矩）。

## 为什么先做这个，而不是先想部署方案

原件入仓前只存在于 `/home/xia/Downloads/` 一处非受控目录。
"先设计模块划分再入仓"意味着整个设计讨论期间敞口一直开着，
而设计讨论的时长是不可预测的。入仓花了十分钟，且**不需要任何尚未拍板的决策**——
凡是不依赖未决问题的工作都应该先做完，这也是本框架铁律 22 写"第一次被引用时"
而不是"用到时"的原因。

## 为什么落在根仓而不是模块仓

三条理由：

1. **模块划分尚未拍板**（见下方「待裁决」）。等它 = 继续敞口。
2. **根仓是本机唯一有远端的仓**（`origin`，含 `main`/`dev/v4` 等分支）。
   `.gitignore` 首行注释写明：`code/` 下的模块实例若为独立 Git 仓，默认不纳入 starter。
   模块仓由 `scripts/new_module.sh` `git init` 生成、**没有远端**——
   把"本机唯一一份"的原件放进一个同样只存在于本机的仓里，保险等于没上。
3. **保险柜与派生物本就该分开。** 保险柜存只读原件，模块 `code/` 存可改造的派生文件。
   两者分离后，改造多少轮，原件指纹都还对得上。

## 行数门禁：为什么没红

`index.html` 1280 行，超铁律 9 的 500 行上限。**但根仓门禁本次是真绿，不是被绕过的绿。**

机制：`scripts/gates/run-gates.sh` 的行数 gate 在仓内存在 `code/` 目录时，
扫描范围是 `git ls-files -- code`（脚本第 11–16 行），根本不含 `agents/`。
已实跑确认 🟢，不是靠豁免文件压下去的。

超限的处置（登记技术债、模块层才需要精确豁免、偿还条件绑在"谁第一个动它谁负责拆"）
见裁决单 `agents/cfo/arbiter/docs/decisions/2026-07-28-index-html-行数豁免-技术债.md`。

## 依赖漂移说明（检查项 90）

无。本次未新增/删除任何包依赖，未改动任何契约文件（无 `contract.md` / openapi / proto 变更），
未新增跨模块引用。入仓的两份 HTML 是**静态资产快照**，不被任何代码 import。

## 待裁决（挡住下一步，需人类拍板）

### D1 · 模块划分：两份前端进一个模块还是两个

HANDOFF §4 把四个前端列为**四条并列的静态路由**（`/ring/ /table/ /gantt/ /garden/`），
读起来像四个交付单元；但 §9.2 的 arbiter 编制只列了六个**后端**模块
（events / timer / planner / views / projector / ai-gateway），前端一个都没列。
两处对"前端是不是模块"没有一致口径，我不自行发明。

### D2 · 框架自身的两处硬缺陷（建议先修再建模块）

均已实证，非推测：

1. **`scripts/new_module.sh` 建出来的模块没有门禁。**
   脚本第 73 行调 `scripts/install-ci.sh`，但仓内实际叫 `scripts/install-gates.sh`。
   调用点写成 `if [ -x … ]`，文件不存在时**静默跳过**，随后仍打印
   "门禁 已安装（scripts/gates + hooks + pre-commit）"。
   后果：新模块处于"有规范、无门禁"的裸奔状态，而屏幕上告诉你它装好了——
   正是铁律 22 之外另一类"沉默失败"。这条直接挡住本任务的下一步（建模块）。
2. **导航指向不存在的路径。** `agents/reference/manual/文档地图.md` 与 CFO arbiter
   角色卡 `agents/cfo/arbiter/AGENTS.md` 都引用 `scripts/merge-to-main.sh`
   与 `scripts/install-ci.sh`；实际文件为 `scripts/merge-to-integration.sh`
   与 `scripts/install-gates.sh`。报告协议的落点也有两个写法：
   铁律 12 与文档地图写 `agents/roles/report-schema.md`，实际在
   `agents/protocol/report-schema.md`。

D2 属于框架根规范与脚本，在 CFO 写边界内，但**改脚本行为不是"顺手一行小修"**，
等人类确认后单开 `fix/` 任务，不混进本次入仓 commit。
**（已闭环：人类裁决全修，consulter 落在 `794c641`，详见下文 D3 节末。）**

### D3 · 门禁对中文文件名失效（P0，本次实证撞上）

`git diff --cached --name-only` 对含非 ASCII 字节的路径会做 C 转义并**加上双引号**：

```
"agents/cfo/arbiter/docs/worklog/2026-07-28-\344\273\273\345\212\241\345\215\225-upstream-vault.md"
agents/cfo/arbiter/docs/report.json          ← 纯 ASCII 才是裸路径
```

本框架的留痕命名规约（`docs/worklog/YYYY-MM-DD-<角色>-<任务>.md`、任务单、裁决单）
天然产出中文文件名，于是九个 check 里有四个行为不正确：

| check | 匹配方式 | 中文名下的后果 | 性质 |
|---|---|---|---|
| `_common/05-write-lease.sh` | 前缀 `case "$p" in "$l"*` | 首字符是 `"`，前缀永不匹配 → **合法文件被判越界** | 吵闹的假阳性（本次撞上） |
| `_common/60-doc-paths-exist.sh` | `[ -f "$f" ] \|\| continue` | 带引号路径不存在 → **静默跳过整份文档** | **静默假阴性** |
| `arbiter/80-taskcard-complete.sh` | `grep -E '\.md$'` | 路径以 `"` 结尾 → **中文任务单不被检查** | **静默假阴性** |
| `programmer/81-scope-respected.sh` | `grep -E '^(review/\|module_docs/)'` | 行首是 `"` → **中文名越界文件放行** | **静默假阴性** |

10 / 50 / 90 用的是子串 grep，**碰巧**不受影响——是运气，不是设计。

三个静默假阴性比那个假阳性严重得多：`60` 在本次提交里报了 ✅，
而它实际只看了我五份 .md 里的两份。**一个只对英文文件名生效的门禁，
在一个以中文留痕为规约的框架里，等于没有门禁**——而它每次都显示绿。
按铁律 16，这是必须绑 fix owner 的 P0，不是记录归档就算完。

修法确定且不削弱任何判据：把 `--name-only` 换成 `-z --name-only`，
读侧用 `while IFS= read -r -d ''`，路径不再经过引号层。

**本次未自行修改门禁脚本。** 理由：改门禁行为属于规范面变更，
应经人类确认后单开 `fix/` 任务并过独立审核，不该由撞上它的人在同一个 commit 里顺手改掉——
那正是"自己给自己开门"的形状。

### D2 / D3 的处置结果（2026-07-28 当日闭环）

人类裁决「三条挡路的全修」，由 CFO consulter 执行，落在 `794c641`：

- 新增 `scripts/lib/paths.sh`，九个 check 统一走 `-z` + 数组取路径，判据一条未削弱；
- `new_module.sh` 的 `install-ci.sh` → `install-gates.sh`，并冒烟确认新模块真装上门禁；
- check 60 按人类裁决**恢复严格**（文件必须存在，不再是"目录存在即可"）。
  恢复后全仓扫出 **28 条死链**——本 CFO 当初只肉眼找到 2 条。
  这个数字值得记下来：**判据弱化的代价是实的，且弱化期间门禁一直显示绿。**

本轮交付已在修复后的门禁上重跑：`mission_complete.sh` **7 项全绿，未使用任何逃生口**。

> 历史记录：本任务在修复前有过一版本地 commit 使用了 `AIMERGENT_MISSION_OVERRIDE`
> 绕过 05 的假阳性（当时已逐一人工核验九个路径均在 `agents/` 写区内，并记入
> `logs/diary.jsonl`）。该 commit 已随 rebase 到修复后的 v5 而重做，
> **逃生口未进入最终历史**。diary 里那两条 override 记录保留不删——
> 它记录的是当时真实发生过的事，抹掉它才是篡改留痕。

### 仍存的同形隐患（已修实例，形状未除）

`new_module.sh` 的调用点仍是 `if [ -x "$PROJECT_ROOT/scripts/install-gates.sh" ]; then …; fi`：
**脚本一旦再次改名或丢失，仍然静默跳过，仍然照常打印「门禁 已安装」。**
这次修好的是"名字对不上"，没修的是"沉默失败"这个形状本身。
建议改为文件缺失即 `die`，或安装后回验 `.git/hooks/pre-commit` 真实存在再打印成功。
未自行改动，登记在此交由人类裁量。

## 自检结果

| 判据 | 结果 |
|---|---|
| A1 两份原件被 Git 跟踪 | ✅ `git ls-files agents/reference/upstream/` |
| A2 `sha256sum -c SHA256SUMS` | ✅ 2×OK |
| A3 与上游源逐字节相同 | ✅ 两侧 sha256 相等 |
| A4 行数技术债已登记 | ✅ 见 decisions/ |
| A5 导航同步（铁律 11） | ✅ 文档地图已加行 |
| A6 `scripts/gates/run-gates.sh` | ✅ 🟢 |
