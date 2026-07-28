# A1/A2 框架级修复（CFO 上报的两条 🔴）

## A1 · 模板占位符检查自毁

CFO 判得完全正确：模板 run_all.sh 含连续 token 字面量，new_module 的 replace_token
把检查脚本自己也替换了——「查占位符」变成「查模块名」，**正确的模块恰因替换成功而永远红**，
三个模块全推不上远端。

修在类（模板）：token 拆分书写（'{{'"MODULE_NAME"'}}'），文件字节里不再含连续 token，
replace_token 不碰它。铁律 23 断言：selftest 17 守住「模板 *.sh 不得含连续 token」。
实测：新生成模块占位符检查 ✅ 绿。

## A2 · 根仓走不了审核流程（撒谎式成功）

CFO 的一般化我全盘采纳，原话入库：**凡是写「将来要被 commit 的产物」的脚本，
写完必须验落点没被 gitignore 吞掉，不然就是撒谎式成功。**

- lib/emit.sh 新增 assert_trackable()（类修复的载体，check-ignore 即响亮失败）
- review_complete.sh：写前验落点；角色白名单收 consulter，
  canonical 落点 agents/cfo/consulter/docs/findings/report.json（根仓 L0 独立审核归位）
- review_start.sh：收 consulter；区间起点补 origin/$ib 与 origin/main 回退（根仓无本地 dev/main 也能算）
- selftest 18 守类：review_complete 必须含落点核验

## 判定记录

- CFO「修必须在模板不在实例」——对。实例（ring/table/nexus-core）的 run_all.sh
  仍是坏的，需 CFO 拉框架更新后用模板版覆盖模块内该文件（模块特有检查段保留）。
- CFO「我审不了自己的活」——对，根仓两分支由 consulter 子代理审（提示词已放
  agents/cfo/consulter/，见 CFO 工作树）。
