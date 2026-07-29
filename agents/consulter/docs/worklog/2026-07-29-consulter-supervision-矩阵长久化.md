# 2026-07-29 · consulter · 监督分工长久化（裁决 J1）+ 计数化石清扫

## 背景与裁决

用户裁决（J1）：cfo_reviewer 职位先搁置，空缺期由 consulter 代任 CFO 产出的例行审查；
例行审查归 reviewer 层、consulter 长期只做模式监督。此前新分工只活在 consulter 的
session prompt 里——会话蒸发即消失。本任务把它固化进框架仓，git pull 即复现。

## 做了什么

1. **新建 `agents/protocol/supervision.md`**：监督矩阵唯一事实源
   （谁写的谁不审 / 例行 vs 模式两层 / cfo_reviewer 搁置状态与退出条件）。
2. **同步四处旧表述**（铁律 11/23，一处不漏）：
   - `agents/cfo/AGENTS.md`：「你的活由 consulter 审」→ 例行归 cfo_reviewer（暂代任）+
     consulter 长期只做模式监督，指回矩阵。
   - `agents/AGENTS.md`：「它审 CFO 的活」→「它监督 CFO 的产出」，指回矩阵。
   - `agents/consulter/审查提示词.md`：首段与「谁写的谁不审」清单按矩阵改写。
   - `agents/consulter/AGENTS.md`：标题「CFO agent · consulter」（隶属暗示，与平级矛盾）
     → 「consulter（独立架构顾问 · 与 CFO 平级）」；职责补「看模式，不看单点」。
3. **裁决入台账**：`agents/CONSTITUTION.md` §4 新增 J1（顺手修掉了我自己插坏的表格分隔行）。
4. **selftest #35**：断言 supervision.md 存在且三份角色文档指回它。
   反向验证：抹掉一处引用 → FAIL(2/3)、退 1；恢复 → PASS、退 0。
5. **计数化石清扫（同轮撞见的第二个类）**：仓内同时存在「12 条闸门」「22 条铁律」
   「23 条断言」三代化石（实际 23 条铁律 / 35 条断言）。修类 = 全部去数字化
   （改成新数字只是把化石换新），共 9 处：CONSTITUTION、HANDOFF×3、文档地图×2、
   scripts/README、根 README、审查提示词。
6. **selftest #36**：断言禁止「N 条铁律/闸门/断言」形状复活（worklog/findings 留痕除外）。
   反向验证：植入「99 条铁律」→ FAIL 点名、退 1；移除 → 退 0。

## 验证

- selftest 36/36 PASS（正向），#35/#36 各做红/绿反向验证，退出码直取。
- check-references 全绿；文档地图新增 supervision.md 条目、协议数去计数化。

## 补充（同日）：跨 checkout 交叉审流程入矩阵

推送时撞上结构性鸡生蛋：推送门要 approved 审核，CFO 却只能从远端看到 commit。
把「先推后审 + 记账逃生口 = 本流程常规路径」写进 supervision.md，v5 非 main，
与「先审后推」判例不冲突。用户已裁决由其亲自跑逃生口推送。

另：本轮第三次被 `cmd | tail; echo $?` 骗——这次门禁拒了 commit 而管道报 0，
靠 `git log` 才识破。教训升级为「判 commit 成败只看 git log/rev-parse，不看回显」。

## 待 CFO 审

本 commit 全部是 consulter 改的框架 → 按矩阵由 CFO 审，我不自批。
