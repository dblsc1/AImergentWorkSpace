# 2026-07-31 · consulter · 模型档位与任务难度适配对照入仓

## 起因

用户观察：opus 老忘记把简单任务委派给 codex。三轮问答（如何保证高低搭配→
定量适配→质量评估/闭环/报告提信息），调研结论汇总入
`agents/reference/社区对照-模型档位与任务难度适配.md`（放蓝图旁，作落地队列设计输入）。

## 用户裁定（本轮）

- **简化落法**：暂不做完整级联闭环，只在派单 json 加 `complexity` 字段 + 一句提醒。
- 档位判据采纳社区三信号（行数×文件数×有无新设计决策）：simple<100行/1-2文件/无设计；
  normal<500行/单模块；hard 其余。500 行是 normal 上限，不是 simple。
- 完整版（cascade+25%盈亏线+三层裁判+尾采样+model-fit.sh）存档待排铲，见文档 §3–§5、§7。

## 成本账结论（对用户三问的答复留档）

token 用量升（多 agent 比单 agent 3–10×），美元可由分档+结构化对冲；
省的是人时与返工复利；小项目不回本（轻量模式），大项目回本。

## 验证

- 文档入仓路径在 agents/reference/（已有白名单覆盖），`git ls-files` 确认跟踪。
- 文档地图不逐篇收 reference 调研产物（同「前人成果对照」先例），report.json 已表态。

## 补录

- 推送门实测暴露 check-report-schema 两处长期分支模型缺口（main 基线假设、consulter 非审查轮硬要 review_target）；本轮按先例恢复最近审查区间入报告，缺口记落地队列。

## 补录2（用户裁定）

- 三个门禁缺陷 sample-workspace 不再自修：连同 complexity 字段一起写入蓝图 §9.1，由 X_structure 侧落地（该 checkout 有活跃写者，未触碰）。
