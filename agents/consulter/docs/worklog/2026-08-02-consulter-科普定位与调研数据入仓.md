# 2026-08-02 consulter：科普定位与调研数据入仓

## 任务

用户要做面向 vibecoding 新手的科普视频，把本项目定位为「年轻人的第一个多 agent 架构」。
要求把三路调研数据（world model vs LLM / 高低模型搭配评测 / 新手提效证据）写成可引用文档，
并从第一性原理扩展可视化教学功能清单（用户种子：S1 抓谎现场、S2 写区领地地图）。

## 产出

- `agents/reference/科普定位-新手第一个多agent架构.md`：
  - §0 第一性原理：新手三缺口（信任校准 / 过程不可见 / 错误无判例）对 MAST 79% 失败归因
  - §1 数据弹药库：翻车实测（Escape 65%）、提效与技能税（Cui 21-40% / Anthropic -17pp）、
    路由省钱（RouteLLM 95%@-85% / Aider 1/14 / Epoch 年降 50×）、赛道安全（世界模型不进软件工程）
  - §2 功能清单 14 条（含用户 S1/S2 落地形态；杀手级 = 双屏对照实验）
  - §3 课程叙事线：单调加严级联 = 课程大纲，判例库 = 剧本库
  - §4 落地优先级（待用户排铲）
- 调研方式：三个并行子代理 WebSearch，结果全带源与日期；诚实缺口已标注
  （Triage 论文无实测、METR 自我推翻、「治理帮新手」无对照实验）。

## 边界

- 只入仓 reference 文档 + 本留痕 + report.json 更新；未动协议、脚本、控制台代码。
- 功能清单是提案，不是任务单；排铲归用户。

## 补录：推送冲突与 rebase（2026-08-02）

- 远端 v5 领先 25 笔（另一 checkout 的框架工作，含 J10 裁决、mission_complete export 修复等）。
- 唯一冲突文件：`agents/consulter/docs/findings/report.json`——**canonical report 单槽位，
  两个 checkout 的 consulter 各写各的任务，天然打架**。本次取本任务版（较新），对方任务
  记录保留在其 commit 历史（4ab1b36 等）。此单槽位冲突是框架级缺陷，建议随蓝图 §9.1 一并
  移交 X_structure：canonical path 需按 checkout/实例分槽或改追加式。
- rebase 后旧 base 0318b1f 被重写，report git.base 改指远端 tip 4ab1b36。
