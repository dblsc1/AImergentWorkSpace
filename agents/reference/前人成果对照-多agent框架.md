# 多 agent 编码框架先例调查（对照 workspace v5）

> 2026-07-30 consulter 派研究代理产出，来源链接见各条。结论供架构决策，不是文献综述。

## 1. 角色分工与审核回路：高度重合，且有已验证结论

- **分层角色**有成熟先例：MetaGPT（arxiv 2308.00352）用 SOP 把 PM/架构师/工程师/QA 串成流水线，
  核心结论是「角色间只交接结构化中间产物」——与我们的 canonical report.json 同构。
  ChatDev 的 chat chain 每阶段配 reviewer/tester。但两者都是**一次性生成玩具项目**，
  不管长期维护仓库，不碰 git 治理。
- **reviewer 独立写测试**已被验证有效：AgentCoder（programmer / test designer / test executor
  三分）证明测试设计者与实现者分离显著提效——J2「reviewer 写整合测试」可放心保留。
- **编排层**：CrewAI hierarchical manager、AutoGen UserProxyAgent（人在回路）≈ CFO+人类裁决。
  已知教训：**CrewAI 层级委派会摘要化转述、丢失信息**（arxiv 2512.14860）——
  反面支持我们「全保真 report.json 交接、不许口头转述」（铁律 12）。
- **Devin 最接近我们的运营形态**：Knowledge（跨 session 记忆）、Playbooks（≈检查单+验收标准+
  guardrails）、session 结局复盘；「Devin 管理 Devins」即 arbiter→编号实例；
  Cognition 每日跑脚本审计 PR 违规＝「审核代码化优先」（铁律 17）。**Playbook 写法可直接抄**。
- **留痕**：OpenHands 事件流架构（AgentController 强制约束 + 全事件日志）≈ 我们的 jsonl 流水，成熟件。

## 2. 门禁 / 写区隔离 / 断言自证

- **hook 硬闸门有充分先例**：Claude Code hooks 官方推荐为确定性强制层且递归作用于子代理；
  Microsoft Agent Governance Toolkit 的 commit→PR→CI 三层闸与我们同构；
  agentic-os（"无证据不算完成"五步流）几乎是铁律 18 的独立重发明。
  **反面教训：pre-commit 太重会教人绕过——本地层求快，权威层放 CI**。
- **写区隔离**：业界主流答案是**每 agent 一个 git worktree**，冲突推迟到合并点。
  **共享工作树内的前缀 lease 未找到公开先例**——真空白，但也意味着没人验证过它优于
  worktree；需评估 worktree 能否替代 lease 的一部分。
- **断言自证**：红/绿反向验证＝mutation testing 思路的轻量版，有现成件：
  MuTAP（存活变异体反馈回路，缺陷检出 +50%）、Meta 已规模化部署 LLM 变异测试（73% 采纳率）。
  可考虑给 selftest 引入变异测试工具而非全手工。

## 3. 长会话与判例库

- **判例库有直接先例**：ChatDev Experiential Co-Learning（历史轨迹提经验入池）、Devin Knowledge。
- **已验证的失败路径**：指望大 context window 取代外部记忆——长 context 检索精度衰减，
  记忆故障是生产 agent 最高频可靠性问题。「判例落仓、结构化」方向正确；
  可参考 multi-scope memory 打标模式。
- session id 复用续用是 Claude Code / Devin 原生能力，非创新点。

## 4. 结论对照表

| 我们的设计 | 判定 |
|---|---|
| 角色分层、结构化交接、reviewer 独立测试 | **重合**，已验证有效（MetaGPT/AgentCoder） |
| hook 闸门、三层门禁、证据强制 | **重合**，可对照 agentic-os / Governance Toolkit 校准 |
| 判例库、Playbook 式检查单 | **重合**，直接借鉴 Devin Knowledge/Playbooks |
| 红绿断言自证 | **半重合**，建议引入现成 mutation testing 件 |
| 全保真 report 交接（拒摘要转述） | 被反面证据支持（CrewAI 摘要丢信息） |
| 重 pre-commit | **别人踩过的坑**：太慢会被绕过，权威层放 CI |
| lease 写区路签、停线旗、门禁新鲜度比对、逃生口记账 | **真空白**，无公开先例；lease 需与 worktree 方案对比论证 |

## 5. consulter 的落地建议（待人类/CFO 取舍）

1. **可直接抄**：Devin Playbook 的任务单写法（对照现有四小节，看差什么）；
   OpenHands 事件流的字段设计（对照 comm.jsonl）。
2. **该量的**：pre-commit 耗时——「太重会被绕过」是别人用血换的；现在着陆单是纯 shell
   秒级，但 J5 全量测试进 push 层是对的方向（权威层后置），别让它滑进 commit 层。
3. **该对比论证的**：lease vs per-instance worktree——worktree 是主流验证过的，
   lease 是我们的空白发明；模块实例模型（programmer/1、/2）天然适合一实例一 worktree，
   值得开一单评估（不阻塞，攒批）。
4. **不用自证的**：结构化交接、审核代码化、判例库——前人已验证，停止怀疑，继续用。
