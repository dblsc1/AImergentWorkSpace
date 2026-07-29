# 2026-07-29 · consulter · J6：CFO 例行审查落地为子代理机制 + 模块本地豁免名单

## 裁决 J6（用户，取代 J1 的代任安排）

CFO 的例行审查不再由 consulter 代任：**CFO 每轮交付前自己开一个子代理**，
以固定实例卡 `agents/cfo/reviewer/agent.md` 冷启动，审它这一轮的**规范面**
（留痕 / 范围 / 台账纪律 / docs_reviewed 理由成色 / 回收纪律），verdict 写回其
report.json 的 reviewer_opinion。无利害来源＝冷启动不继承上下文 + 卡里明写
「打回它的活是本卡存在的意义」。consulter 代任解除，只留模式监督。

**命名说明**：用户口述写的是「cfo/consults/agent.md」；实际落点用
`agents/cfo/reviewer/agent.md`、职位名沿用台账已有的 cfo_reviewer——
「consulter」已是项目级平级角色的名字（AGENTS.md 明文解释过它为何不在 cfo 下），
同名会造成两个角色混淆。如用户坚持原名可改，一处 mv + 四处引用。

## 改动

- 新卡 `agents/cfo/reviewer/agent.md`（审五件、只举证不修复、与 consulter 的
  单/批分界写明）。
- 六处「代任」表述全部同步：supervision.md（矩阵行 + J6 节）、CONSTITUTION
  （J1 标被取代、新增 J6 生效已落地）、cfo/AGENTS.md、consulter/AGENTS.md、
  审查提示词.md、重新生成 .claude prompt（#41 盯字节一致）。
- **模块本地豁免名单**（CFO 实报缺口）：`doc-path-exempt.local.txt`——
  checks/60、12 都读；13 号新鲜度排除 `*.local.*`（模块自有件内容本该不同）；
  框架仓放带说明的占位份。
- selftest #44（J6 机制三件套 + 本地名单），总 44 条。

## 验证（直取退出码）

- #44：正向先红——断言 grep 找字面 `doc-path-exempt.local` 而 60 里是参数拼接，
  判据修准后 红1/绿0（藏卡→红）。红/绿再次抓住断言自身的弱判据。
- 本地豁免功能沙箱：未登记前向引用→红1；登进 .local→绿0。
- 13 号连带修复的红/绿：模块 .local 内容不同→绿0（修复前会恒红——CFO 撞过的
  「改副本=漂移」形状）；真门禁副本过期→红1 并点名。
- check-references 中途红过一次（12 引用的 .local 在框架仓不存在）——
  用占位文件让路径诚实存在，不绕扫描器。
- 现行文档「代任」残留扫描：仅 J6 节标题的历史指称，无活表述。

## 模式层备忘

CFO 报的「先纳 scripts/ 进路签再补装门禁」交互属正常规矩相互作用，不修；
「先 rebase 再装」硬序化仍挂账待评估。
