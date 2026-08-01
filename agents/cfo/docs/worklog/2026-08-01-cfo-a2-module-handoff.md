# 2026-08-01 · CFO · A-2 模块级 handoff 无人把关（新增 checks/17）

## 做了什么

排查表 A-2：规范一直写「handoff 每改必核」，实测 `checks/14` **只扫
`code/<子文件夹>/handoff.md`（programmer 侧）、从不看 `module_docs/handoff.md`
（arbiter 侧、规范里标为 canonical 的那份）**。结果六个模块的 arbiter 级交接
文档全是 15 行空模板，与 `code/_template/` 逐字相同，建仓至今一次都没红过。

新增 `scripts/checks/_common/17-module-handoff.sh`：

- **不并进 14**：14 管 programmer 侧（代码怎么跑），17 管 arbiter 侧
  （模块是什么、对外给什么）。两份文件、两个写属主、两种失效方式，
  合成一条报错说不清该谁去改。
- **判据认内容不认行数**：匹配模板里的**字面占位串**。用行数判会被
  「随手加一行注释」变绿——那只能证明有人动过，不能证明填实了。
- 契约变更时要求同批更新 handoff 或在 report.json `docs_reviewed` 表态。

## 红绿

- 加完判据：ring / table / nexus-core / nginx-docker / auth **五个模块全红**。
  这正是要的效果——闸门装上就立刻暴露欠债。
- 逐个填实后：**五个全绿**。
- 框架仓正确跳过（无 `codeagent/`+`module_docs/` 布局）。
- `selftest` 新增第 52 条：沙箱里喂空模板必红、喂填实内容必绿，
  证明判据真在测内容。全量 `PASS 52 · FAIL 0`。

## 填了什么

五份都按四小节写实，重点是「避坑/冻结点」那节——把这几天踩过的坑固化下来：

- nexus-core：只有 repo.py 能碰 mongo、DISPATCH 单独成 commit、防重按 dedupeKey
  不是 id、占比 0–100 不是 0–1、空闲态 null 不是 0、J10 三分标识、
  timer 快照三级 id；以及「曾因硬取 `project["key"]` 对切片1旧数据炸 500」
- ring：不改渲染逻辑（派生自保险柜且逐字节未改）、空闲态不能直接喂 null、
  零自动化测试是最大缺口
- table：写完重拉不做乐观更新、后端报错原样展示不重复校验、
  DOM 层零测试（两个真 bug 都出在这层）
- nginx-docker：三条路径不得经 auth_request、auth 挂掉 fail-closed 是有意的、
  envsubst 白名单不带会静默出错且 nginx -t 照样过、alias 尾斜杠 500 坑
- auth：verify 异常必须转 401 不能 500、比对必须 compare_digest、
  以及**已知安全边界**（只防点删除不防抓包、logout 不吊销）

## 顺带

同批修了 D-4（table 契约里「key/order 全用」而代码零命中）——逐字段核实后
改成如实列出，并写明「契约写假话会传染到消费方」。
