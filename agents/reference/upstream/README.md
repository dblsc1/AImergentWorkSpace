# 上游原件保险柜（upstream vault）

> **本目录是「不可再生上游输入」的固化落点（铁律 22）。**
> 这里的文件是**原件快照，只读**：任何改造、适配、拆分都发生在模块的 `code/` 下，
> 不回写本目录。原件被改动 = 保险失效。

## 为什么存在

铁律 2 / 7 管的是「不该进仓的东西别进」（密钥、运行数据）。
铁律 22 管的是反向的事：**必须进仓的东西别丢**。

判定标准：*本机只有一份 + 不可再生*。满足即在**第一次被引用时**入仓并留 SHA256，
不允许"先登记成技术债，回头再入仓"。

## 台账

| 文件 | 行数 | 字节 | 来源路径（本机） | 来源 mtime | 入仓日期 | 可再生 |
|---|---|---|---|---|---|---|
| `agents/reference/upstream/index.html` | 1280 | 45116 | `/home/xia/Downloads/index.html` | 2026-07-27 16:33:04 +0800 | 2026-07-28 | ❌ 否 |
| `agents/reference/upstream/project-task-contribution-ring.html` | 251 | 11491 | `/home/xia/Downloads/project-task-contribution-ring.html` | 2026-07-27 16:33:15 +0800 | 2026-07-28 | ❌ 否 |

指纹见同目录 `SHA256SUMS`，校验方式：

```
cd agents/reference/upstream && sha256sum -c SHA256SUMS
```

入仓时已核验：仓内副本与 `/home/xia/Downloads/` 源文件 SHA256 **逐字节相同**。

## 这两份是什么

对应 `agents/reference/HANDOFF.md` §11.1「现有资产」：

- **`agents/reference/upstream/project-task-contribution-ring.html`**（251 行）—— 贡献圆环视图前端。
  暴露 `window.renderContributionRing(json)`，中文键。HANDOFF 记录的改造点：删 demoData、
  接 `/views/current` 轮询适配器、补"无任务运行"空闲态。
- **`agents/reference/upstream/index.html`**（1280 行）—— NEXUS 控制台（项目表视图）前端。
  目前用 localStorage，HANDOFF 记录的改造点：数据层迁 API、localStorage 降级为断网缓存、
  `progress` 由手动值改为计算值并保留手动覆盖。

## 已登记的规范冲突（存量导入）

`agents/reference/upstream/index.html` 1280 行，超铁律 9 的 500 行上限，
也超 HANDOFF §8 第八条更严的 300 行。

**处置：按存量导入登记技术债，不在保险柜里拆分。** 理由与偿还路径见
`agents/cfo/arbiter/docs/decisions/`。保险柜的职责是"保住原件"，
拆分是模块 `code/` 下的改造工作，两件事不能混在一个动作里——
在保险柜里拆分会让 SHA256 与上游原件对不上，指纹立刻失去意义。

## 规矩

1. **只读**。本目录文件不接受任何修改。需要改造 → 在模块 `code/` 下新建派生文件。
2. **新增原件必须同时更新** `SHA256SUMS` 与上表，并在同一 commit 内完成（铁律 11 / 22）。
3. **删除原件必须先走归档脚本**（铁律 21）：产出可验证存档 → 校验 → 冒烟还原 → 指纹，
   再改名留墓碑。
