# 运行监督机制 · 谁审谁（监督矩阵）

> 本文件是监督分工的**唯一事实源**。角色卡、提示词里的相关表述与本文件冲突时，
> 以本文件为准，并在同一逻辑变更里修正冲突处（铁律 11）。
> 本机制随框架仓 `v5` 分支分发：项目仓 `git pull` 即拿到最新分工，不依赖任何会话记忆。

## 核心原则

1. **谁写的谁不审。** 自审抓不出撒谎式成功（已实证）。
2. **例行审查（逐单）与模式监督（系统性）是两层，不混。**
   例行审查问「这一单对不对」；模式监督问「这批产出里有没有反复出现的同形状偏差」。
   逐单审得再勤也看不见模式；只看模式则放过单点缺陷——两层都得有属主。

## 监督矩阵

| 产出方 | 例行审查（逐单） | 模式监督 |
|---|---|---|
| programmer | programmer_reviewer（模块内） | consulter |
| 模块 arbiter | module_reviewer（交付面/规范面） | consulter |
| CFO | **cfo_reviewer**（专职职位，暂缺——见下） | consulter |
| consulter（框架改动） | **CFO**（交叉审核：那不是它的活，它审得动） | 人类 |
| 双方共同参与的改动 | ——不许互相签字 | 人类裁决 |

## cfo_reviewer 职位状态（裁决 J1，2026-07-29）

用户裁决：**cfo_reviewer 职位先搁置**，空缺期由 consulter **代任**CFO 产出的例行审查。

- 代任是**过渡状态**，不是新分工：职位设立后 consulter 即退出例行审查，只保留模式监督。
- 代任期间「谁写的谁不审」不放松：consulter 代任审 CFO 的活，仍与被审产出无利害；
  consulter 自己改的框架仍由 CFO 审，双方共同参与的仍交人类。
- 登记见 `agents/CONSTITUTION.md` §4。

## 跨 checkout 的交叉审怎么走（consulter 框架改动 → CFO 审）

框架仓与项目仓是**同一个 GitHub 仓的不同 checkout**，CFO 只能通过远端看到 consulter 的
commit——所以这条审必然**先推后审**，与「先审后推」判例不冲突：v5 是工作分支，不是 main。

1. consulter 落 commit，`report.json` 的 `reviewer_opinion` 标 `pending`；
2. 推 v5 用 `AIMERGENT_PUSH_UNREVIEWED=1 scripts/arbiter-push.sh origin v5`
   （记账逃生口，diary 可查；这是本流程的**常规路径**，不是违规）；
3. CFO 仓 `git pull` 后对固定区间 `<旧tip>..<新tip>` 审，verdict 经
   `review_complete.sh` 落痕；rejected 的改动由 consulter 在框架仓返修再推。

## consulter 的长期定位（本矩阵的推论）

- **看模式，不看单点**：从 canonical report、留痕与 diff 的**批量证据**里找系统性偏差
  （任务单质量退化、恒定答案检查、橡皮图章式 docs_reviewed……）。
- **不做逐单例行审查**——那是 reviewer 层的活；consulter 逐单审是在用最贵的眼睛干最便宜的活，
  且会挤掉没人替它干的模式监督。
- 架构级疑点不裁决：写清两边代价，交人类。
