# 门禁三类缺陷修复（CFO 报告驱动）

CFO 在首个任务里抓出三条挡路问题，**全部是我写的 bug**。逐条实测复现后修复。

## D3（P0）门禁对非 ASCII 路径失效

`git diff --cached --name-only` 对非 ASCII 路径会加引号并 C 转义：
`"docs/worklog/2026-07-28-\344\273\273\345\212\241\345\215\225.md"`
于是 `grep '\.md$'` 匹配不到（结尾是 `.md"`）。而本框架的留痕规约天然产出中文名。

九个 check 里四个行为不正确，**三个是静默假阴性**——该拦的没拦、门禁照样报绿。
**静默的远比吵闹的危险**：吵闹的会有人来问，静默的没人会知道。

修法：新增 `scripts/lib/paths.sh`，统一用 `-z` + `mapfile -t -d ''` 数组，
所有 check 经它取路径。**不削弱任何判据**，只修取值方式。

踩过的两个坑记下来：
- `staged=$(git diff -z ...)` 无效 —— 命令替换会吞掉 NUL，必须用数组。
- 对 NUL 流用 `grep` 无效 —— 换成数组上的 `filter_paths`。

**我自己在本轮早些时候就撞过同一个 bug**（`git ls-files | grep -c 'worklog.*\.md'` 数出 0），
当时只修了那一处计数，**没有把它一般化到 checks**。这是真正的失误：
见到一个 bug 只修实例、不问"这个形状还出现在哪"。

## D2 重组回归：install-ci.sh → install-gates.sh

`new_module.sh` 调的是改名前的 `install-ci.sh`，而调用点是 `if [ -x … ]`，
**文件不存在就静默跳过，然后照样打印「门禁 已安装」——它在撒谎**。比崩溃更糟：
崩溃会停下，撒谎会让人以为装好了。

## 60-doc-paths-exist 恢复严格（人类裁决）

上一轮我把判据从「文件必须存在」弱化成「目录必须存在」（为了不误伤未产出的 report.json），
**这个弱化直接放过了 `scripts/merge-to-main.sh` 这类重组死链**。

人类裁决恢复严格。恢复后全仓扫出 **28 条死链**（CFO 只找到 2 条）——弱化的代价是实的。

两处配套，都不是弱化：
- **有意的前向引用必须显式登记** `scripts/gates/doc-path-exempt.txt`。
  登记本身可审计（git blame 看得到谁加的），未登记的死链一律红。
- **跳过 worklog / findings / decisions**：它们是历史叙事，引用的路径当时存在、现在可能已删。
  强制它们指向现存路径 = 强制篡改历史，与「留痕不可篡改」直接冲突。

## 其余

- `dispatch.sh` 找不到 CFO 角色卡（它在 `agents/cfo/<角色>/AGENTS.md`，不在 `agents/roles/`）
- `CONSTITUTION.md` 自称 20 条，实际 22 条
- 铁律 22 补：不可再生输入**必须落在有远端的仓**。
  这条采纳 CFO 的判断：把「本机唯一一份」放进另一个只存在于本机的仓，保险等于没上。
- selftest 扩到 17 条，新增第 17 条专盯非 ASCII 路径。

## 一条给我自己的教训

CFO 拒绝自己修门禁，理由是「撞上门禁的人在同一个 commit 里顺手改掉门禁，
正是自己给自己开门的形状」。**这个判断完全正确。**
它改走记账逃生口，并把理由与九个路径的人工核验写进 worklog 和 diary ——
**这是逃生口的正确用法**，不追究。

## 防复发：从「抽样断言」升级成「穷举性质」

人类的要求是**保证下一次 git pull 不会拉出同样的问题**。逐条修完不够——
本轮所有问题是同一个形状：**改名/搬家留下悬空引用，而它们只在运行时炸**。
六次重组（ci→scripts、roles→agents/roles、module_template→code/_template、
merge-to-main→merge-to-integration、install-ci→install-gates、backend/frontend→programmer）
每次都留下引用，每次都没人查。

原有防御是**抽样**的：`selftest` 那 17 条，每条都是「我被烧过的地方」，不是「所有可能烧的地方」；
严格版 60 只查 `.md`，**`.sh` 里的引用完全没人查** —— `install-ci.sh` 就是这么活下来的。

三条改动：

1. **`scripts/gates/check-references.sh`（穷举）**：扫全仓 `.sh` 与 hooks 里的仓内路径引用、
   报告协议点名的 canonical 目录、脚本提到的角色是否有角色卡、每个 check 是否满足 `--describe` 契约。
   **首次运行就抓出 `scripts/hooks/pre-push` 仍指着 `merge-to-main.sh`** —— 一个活着的 hook 里的死引用。
2. **挂进 `run-gates.sh`**：不自动跑的检查会腐烂，**这正是 V1 假绿门禁的成因**。
3. **铁律 23**：修缺陷必须一般化到类并留下断言。**没有断言的修复不算修复，只算这次没炸。**

调试中两个假阳性也记下来，都是「像路径但不是引用」：
- `reviewcode/run_all.sh` 被正则切成 `code/run_all.sh` → 前缀必须落在词边界上
- `scripts/tests/test-*.sh` 的 glob 被截成碎片 → 把 glob 字符一并吃进来再整体判掉
- 错误提示里的例子 `code/backend/orders/` → 改成 `code/<模块>/backend/orders/`，占位形式天然被跳过

原则：**注释与占位不是引用，代码里的路径才是引用。**

## 文档同步：铁律 11 终于有了执行者

人类问「如何保证改了文件相关文档都同步改？维护一张表？」

**表要有，但不该手维护。** 手维护的映射表本身会腐烂，而且新文件忘登记就是静默漏掉——
又绕回「静默假阴性」那个老问题。

**关系从文档反查**：某份 `.md` 用反引号写了 `` `scripts/foo.sh` ``，它就依赖 foo.sh；
改 foo.sh 时自动查「谁提到了我」。零维护、新文件自动纳入。
自动推不出的语义关联才登记 `scripts/gates/doc-deps.txt`，
**那张表越短越好：越长说明文档越没把自己依赖的路径写清楚。**

与 `gates/check-references.sh` 分工，别搞混：
- check-references 管**引用的东西还在不在**（死链，机器能判）
- 11-doc-sync 管**引用还在、但描述已经不准**（只能人判 → 所以要声明）

声明落在 `report.json` 的 `docs_reviewed`，`action` 只有 `updated` / `no-change-needed`
（后者**必须写 reason**）。写「无需改」完全可以，**写不出理由才是问题**。
声明是结构化的、可审计的，并已接进控制面板（新增「文档声明无需改」一格）。

**噪音实测**（32 个脚本）：平均每个脚本被 **1.1 份**文档提到；
最多的是 `merge-to-integration.sh` 8 份、`new_agent.sh` 6 份。
也就是说**多数改动只需表态 0–1 条**，只有动核心脚本时才需要批量声明。
这个量我判断可接受；若日后嫌吵，降噪的正确方向是**让文档少重复提同一个路径**，
而不是放宽这条检查。
