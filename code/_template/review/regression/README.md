# 回归测试（与 reviewcode 分家）

| | `review/reviewcode/` | `review/regression/` |
|---|---|---|
| 是什么 | **本次审核**的一次性核验脚本 | **永久回归**用例 |
| 何时跑 | 审这一轮时 | **每次全跑**（run-tests / CI） |
| 生命周期 | 审完可归档 | 只增不减 |

**reviewer 的关键产出在这里**：每个新功能配一条回归用例。
旧功能被回归套件锁住，`module_reviewer` 才敢不去肉眼看老代码 —— 跑一遍就知道有没有坏，
token 全花在新功能上。这是「审核代码化」真正省钱的地方。

浏览器端用例（playwright）放 `browser/`，由模块 `package.json` 的 `test` 脚本串起来。
