# 科普定位：新手的第一个多 agent 架构（数据弹药 + 可视化教学功能清单）

> 用途：科普视频素材 + 教学产品定位。数据截至 2026-08，全部带源。
> 受众：刚开始学 vibecoding 的人——无编程经验，或有编程经验但无 vibecoding 经验。
> 定位一句话：**「年轻人的第一个多 agent 架构」——不是教你写代码，是教你不被 AI 骗。**

## 0. 第一性原理：新手缺的是什么

新手面对 LLM 的三个结构性缺口（所有功能设计对准这三条）：

| 缺口 | 表现 | 本框架的对应资产 |
|---|---|---|
| **信任校准缺失** | 不知道 AI 会一本正经地谎报完成 | 三源对账、撒谎式成功防御、Git 核验收货 |
| **过程不可见** | 看不见 agent 在读什么写什么、为什么卡住 | 控制台、diary.jsonl、写区路签 |
| **错误无判例** | 没见过失败长什么样，绿了就以为对了 | 判例库（记忆.md）、门禁红绿、打回流程 |

对照数据支撑：多 agent 系统失败归因 **41.8% 规范缺陷 + 36.9% agent 间失调 + 21.3% 验证缺失**（MAST，NeurIPS 2025，1,600+ 条真实执行轨迹）——**约 79% 的失败发生在「规范与协调」层，恰是新手最没有概念、也是本框架机械化掉的那一层。**

## 1. 数据弹药库（视频可直接引用，每条带口播版）

### 1.1 为什么新手最需要约束（翻车实测）

- 扫描 1,400+ 个 vibe-coded 生产应用：**65% 有安全问题、58% 含至少一个严重漏洞**，400+ 泄露密钥（Escape.tech，2025-10）。
  口播：「一千四百个 vibe coding 上线的应用，一半以上带着严重漏洞在裸奔。」
- Lovable 公开应用 **10.3% 可被直接利用**（CVE-2025-48757），几乎全是非开发者无从知晓的默认配置问题（2025-05）。
- LLM 生成代码 **45% 引入 OWASP Top 10 漏洞**（Veracode，2025）；重灾区是业务逻辑与 API 授权，不是经典注入（Tenzai，2025-12）。
- AI 生成代码错误率是人写的 **1.7 倍**，代码审查时间 **+93%**（Checksum 引用，2026-02）。
- 组织级遥测：人均合并 PR +98%，但审查时长 +91%，2026 年数据进一步恶化——**瓶颈从写码迁到审查与集成**（Faros，22,000+ 开发者遥测）。
  口播：「AI 让你写得更快，但堵车的地方换成了审查。谁来审？这就是多 agent 架构要回答的问题。」

### 1.2 AI 对新手的提效与技能税（必须一起讲，别只讲一半）

- 提效（对照实验）：初级开发者提升 **21–40%**，资深仅 7–16%——**初级受益 2–3 倍**（Cui et al.，N=4,867，Management Science 2025）；Copilot 提速 55.8%，经验少者受益更大（Peng et al. 2023）。
- 技能税（RCT）：初级工程师用 AI 学新库，完成时间相同，但事后理解测验**低 17 个百分点**，debug 能力掉最狠；**用 AI 问概念的人得分 ≥65%，纯甩活生成的 <40%**（Anthropic，2026-01）。
  口播：「AI 帮你干活不收费，收的是你的理解力。除非你逼自己搞懂每一个红灯为什么红。」
  ——这条直接决定教学产品必须有「理解检查点」（见 §2 功能 9）。
- 反直觉：生产环境里**资深者上线 AI 代码是初级的 2.5 倍**（Fastly，~800 人）——实验室里新手提速最多，生产里老手才兜得住 AI 的错。**兜错能力可以被架构补：这是本产品的核心命题。**
- 引用注意：METR「资深慢 19%」已被其 2026-02 更新自我动摇（复测翻正+选择偏差，实验重做中），引用必须带此上下文。

### 1.3 高低模型搭配（省钱数据，complexity 分档的依据)

- RouteLLM（ICLR 2025）：保 GPT-4 95% 质量，省 85%+ 成本；只需 **14–26% 请求走强模型**。
- Aider 实测（最硬的生产级数据点）：R1 规划 + Sonnet 编辑 = polyglot **64.0% / $13.29**，vs 单 o1 61.7% / $186.5——**分数更高、成本 1/14**。
- 便宜模型形状（2025-26 稳定在「旗舰 64–95% 分、1/3–1/25 价」）：Haiku 4.5 = Sonnet 4.5 的 95% 分、1/3 价；Gemini 3 Flash agentic coding 反超 Pro、1/4 价；DeepSeek V3.2 ≈ GPT-5 的 84% 分、~1/100 价。
- 等能力推理价格**年降中位 50×**，2024 后中位 200×/年（Epoch AI）。
  口播：「今天要最贵模型才能干的活，两年后白菜价。分档表不用改，档位边界自己往下走。」
- 质量不变的保证不来自模型，来自机器验收层——「省不省取决于验证是不是机器扛的」的定量版。

### 1.4 赛道安全性（LLM vs 世界模型）

- 主流世界模型（Genie 3 / Marble / Cosmos 3 / Waymo World Model）应用清单里**没有软件工程**；编程域只有弱化变体（Meta CWM 拿执行轨迹当训练信号、Checksum 建运行时仿真器）。
- 时间线：仿真/合成数据用途已进生产（Waymo 11 城、Marble 商用）；通用 agent 用途仍在实验室（Genie 3 公测限 60 秒、记忆 ~1 分钟）；LeCun 给自主物理 agent 的研究周期是**十年**。
- 结论：**软件工程治理在可见期内是纯 LLM 地盘，本架构投入不会被世界模型浪潮作废。**

### 1.5 诚实缺口（也是机会）

「结构化治理让新手更快上手」**尚无直接对照实验**（2026-08 时点明确的研究空白）。间接链条成立（MAST 79% + DORA 2025「有平台/流程的团队 AI 不掉稳定性」），但没人做过实验。
**从 0 构建 X_structure 的全程记录本身可以成为第一个有数据的公开案例——这是内容护城河，不是短板。**

## 2. 可视化教学功能清单（每条：功能 / 教什么 / 依托的现有资产）

用户已提两个种子：
- **S1 抓谎现场**：LLM 犯错的过程被抓出来、展现在控制台上。
- **S2 写区领地地图**：arbiter / programmer 的写区展现在文件地图上（understand-anything 生成）。

以下为扩展提案，按教学冲击力排序：

1. **双屏对照实验（杀手级 demo）**：同一任务，左屏裸奔 vibecoding agent，右屏治理架构。左屏先绿、bug 计数持续上涨；右屏慢、但零逃逸。教「为什么要约束」——第一性原理的可视化：让学生亲眼看到反事实。依托：轻量模式 vs 完整治理模式本来就是同一框架的两档。
2. **抓谎直播间（S1 落地形态）**：控制台三栏——agent 自述（「已完成✓」）、机器判定（门禁红）、矛盾高亮。撒谎式成功的指纹（自述绿 × diary 红）直接闪给学生看。教「自报不可信，三源对账」。依托：report.json × diary.jsonl × git 三源已存在。
3. **写区领地地图（S2 落地形态 + 违规事件）**：understand-anything 生成文件地图，各 agent 写区着色；agent 越界写入瞬间领地闪红 + commit 被拒的实时弹幕。教「权限边界」。依托：写区路签 + 门禁已有，缺的只是渲染层。
4. **撒谎计数器 / 信任仪表盘**：累计「agent 说完成 N 次，机器只认 M 次」，按 agent 分栏。教「信任是统计出来的，不是感觉出来的」。依托：report.json status × 门禁 verdict 对账。
5. **一键搞破坏（mutation testing 民主化）**：学生按钮式注入错误（删一个测试 / 让 agent 谎报 / 改坏契约），看哪道门在几秒内抓住。教「验证体系的可信度要靠攻击来证明」——把治理断言 mutation testing 变成游戏。依托：铁律 23 断言 + selftest。
6. **升级梯子动画**：打回 1 次 → 打回 2 次 → 升级 arbiter → 门铃响（人类出场）。「五类必叫事」渲染成门铃事件流。教「人什么时候必须介入」——人在回路阈值的可视化。依托：打回计数 + 升级协议已有。
7. **成本滴答表**：任务按 complexity 分档路由（simple→便宜档），屏幕角落实时美元计数。教「模型分档经济学」。依托：complexity 字段（蓝图 §9.1 第 9 项）+ §1.3 数据。
8. **协作泳道时间轴**：comm.jsonl 实时渲染成泳道图——arbiter 开单 → programmer 实现 → reviewer 审 → 合并门。教「多 agent 协作协议」，不用读一行文档。依托：comm.jsonl / arbiter.jsonl 本来就是结构化事件流。
9. **理解检查点（Anthropic -17pp 数据直接驱动）**：门禁红灯后，控制台先问学生一道「这个门为什么红？」选择题，答对才放行继续。教「问概念的人 ≥65%，甩活的 <40%」那条曲线的正确一侧。依托：判例库出题。
10. **判例回放剧场**：记忆.md 里每条判例（撒谎式成功 / 散文无半衰期 / 归档窗口回写）做成可回放的脚本化事故：翻车现场 → 病根 → 加断言 → 红变绿。教「每条规则都是撞出来的」。依托：判例库 = 现成剧本库；事故内容传播力 > 成果内容（前判）。
11. **进度 = 绿灯数，不是代码行数**：仪表盘主指标改为「已验证绿灯数 / 零逃逸天数」，刻意不显示 LOC。教新手反 vibecoding 的度量素养。
12. **学生的第一条断言（从消费者到贡献者）**：毕业任务 = 亲手写一条 check 脚本抓住一个预埋错误。教「你也能给笼子加一根栏杆」——同时是 good-first-issue 管道的入口。
13. **术语隐喻包（视频统一话术）**：contract=合同、arbiter=包工头、gate=质检门、写区=工地围挡、停线旗=全场熄灯、打回=返工单。全系列视频用同一套隐喻，不混用。
14. **零安装门槛**：受众装不了环境。最低阶=录制好的交互式回放（浏览器点进度条看事件流）；进阶=Codespaces 一键开箱。git pull 即用是给有 git 的人的，视频受众要更低的台阶。

## 3. 课程叙事线：每条规则从一次翻车诞生

单调加严级联本身就是课程大纲——治理的渐进采纳路径 = 教学的渐进解锁路径：

- 第 0 课：裸奔。一个 agent、零门禁，做个小应用。翻车（§1.1 数据保底：大概率自己就翻）。
- 第 1 课：第一道门。加 git + 一条 check，同样的错被当场抓红。
- 第 N 课：每课由一次真实翻车引出一道新门（判例回放 → 学生复现 → 加断言 → 红变绿）。
- 毕业课：学生写自己的断言（功能 12），然后按一键搞破坏（功能 5）验证别人的断言抓不抓得住自己。

这条线的卖点：**规则不是背的，是撞出来的。** 判例库有多少条，课程就有多少集。

## 4. 落地优先级建议（待用户排铲）

1. 抓谎直播间（S1）——控制台已有原型，三源数据已有，纯渲染层工作，冲击力最大。
2. 写区领地地图（S2）——understand-anything 已能生成底图。
3. 双屏对照实验——录一次就是首发视频的核心素材。
4. 判例回放剧场——把记忆.md 判例逐条脚本化，课程内容与产品功能一鱼两吃。
5. 其余按视频排期拉动，不预先全建。

## Sources

- MAST 多 agent 失败分类：<https://arxiv.org/abs/2503.13657>
- Escape.tech vibe-coded 应用扫描：<https://escape.tech/state-of-security-of-vibe-coded-apps>
- Cui et al. 三场现场实验：<https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4945566>
- Peng et al. Copilot RCT：<https://arxiv.org/abs/2302.06590>
- Anthropic 技能形成研究：<https://www.anthropic.com/research/AI-assistance-coding-skills>
- Fastly 资深/初级 AI 代码上线调查：<https://www.fastly.com/blog/senior-developers-ship-more-ai-code>
- METR 2025 研究及 2026-02 更新：<https://metr.org/blog/2026-02-24-uplift-update/>
- DORA 2025 State of AI-assisted Software Development：<https://dora.dev/dora-report-2025/>
- Faros 遥测解读：<https://www.faros.ai/blog/key-takeaways-from-the-dora-report-2025>
- RouteLLM：<https://arxiv.org/pdf/2406.18665>
- Aider R1+Sonnet：<https://aider.chat/2025/01/24/r1-sonnet.html>
- Haiku 4.5 发布：<https://www.anthropic.com/news/claude-haiku-4-5>
- Epoch AI 推理价格趋势：<https://epoch.ai/data-insights/llm-inference-price-trends>
- Genie (world model) 时间线：<https://en.wikipedia.org/wiki/Genie_(world_model)>
- Waymo World Model：<https://en.wikipedia.org/wiki/Waymo_World_Model>
- Meta Code World Model：<https://arxiv.org/abs/2510.02387>
- Checksum 软件世界模型：<https://checksum.ai/blog/from-atoms-to-bits-building-a-world-model-for-software>
- Veracode / Tenzai / Lovable 漏洞数据：<https://www.ox.security/blog/vibe-coding-security/>、<https://www.csoonline.com/article/4116923/output-from-vibe-coding-tools-prone-to-critical-security-flaws-study-finds.html>
