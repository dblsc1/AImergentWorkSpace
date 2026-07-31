# 2026-07-31 · CFO · report.json base 陈旧导致净零文件漏判，同批重写为当前状态

推 v5 前门禁报 `report schema` 红：`git.changed_files` 里
`blockers/2026-07-30-model-tier-not-enforced.md` 不在 `base..resolved_head` 的
diff 里。根因：`agents/cfo/docs/report.json` 的 `git.base` 停在 `211bdec`——
一个远早于该文件创建时间的祖先提交，该文件在这个区间内是"创建又删除"，
两端 tree 都不含它，`git diff --name-only` 自然不会列出它。这是本会话已经在
nexus-core 报告里踩过、并修过一次的同一类缺口（"净零文件"），这次是根仓自己的
report.json 又犯了同一个错——`base` 没有跟着后续提交推进。

同时这份 report.json 内容也整体停在"模型分档修复"这一个任务，没有反映后续
切片2落地、ring/table落地、新障签这几件事——作为"当下交接"的 canonical report，
放着不更新本身就是漂移。索性一次性重写：`base` 改成上次真实推送到 v5 的
tip（`e767df3`），`changed_files` 用 `git diff --name-only e767df3..HEAD` 实际
核对过，`summary`/`cross_module_impact`/`escalation`/`docs_reviewed` 都改成
反映当前真实状态。
