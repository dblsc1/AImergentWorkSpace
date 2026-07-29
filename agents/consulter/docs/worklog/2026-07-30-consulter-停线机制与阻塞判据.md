# 2026-07-30 · consulter · 阻塞判据 + 停线机制 + reviewer 常驻化 + J7 同步

## 用户指令落点

1. **阻塞判据**（今晚定）→ `agents/protocol/supervision.md` 新节：
   一句话＝挡住主路径**且无合法绕行**＝阻塞；三档分流（阻塞→停线 / 高危不阻塞→限期 /
   不阻塞→攒批）。红线：需要变形文案、去引号、隐瞒才能过的「绕行」不算绕行，按阻塞处理
   （判例＝昨日前向引用去反引号事件）。判据标注「consulter 起草待人类追认」。
2. **reviewer 常驻 + 实时架构问题清单**：cfo_reviewer 卡改为常驻（开一次、session 存
   `agents/cfo/reviewer/session`、每轮唤醒同一个）；新增职责②——实时维护
   `agents/cfo/reviewer/docs/架构问题清单.md`（占位已建），按阻塞判据当场分流，
   阻塞项置停线旗。它记单点，consulter 看批量。
3. **停线机制**：`logs/STOPLINE` 旗（本地不入仓），三入口齐认——pre-commit 拒提交、
   mission_start 拒发新签（--checklist/--list/--release 放行）、arbiter-push 拒推送。
   无逃生口：停线就是停线。机器态路径与旗文件走 doc-path-exempt 合法登记，不搞去引号规避。
4. **J7 同步**（CFO 转述的撞号裁决：consulter 框架改动由人类审、CFO 只辅助举证）：
   supervision 矩阵行 + 跨 checkout 流程节 + CFO 卡（审→举证，verdict 留人类）+
   审查提示词③。CONSTITUTION 本轮**不动**——CFO 已在其 checkout 让号（其 J7），
   等它推上来，避免第二次撞号。
5. **80 号检查**（CFO 撞两次的 worklog 硬套四小节）：按目录判改按文件名判
   （只认 `*任务单*.md`）。

## 验证（直取退出码）

- selftest 45/45；#45 红/绿（抹一个入口→红）；#44 在改 CFO 卡时**抓了真回归**
  （实例卡路径被我重写弄丢——CFO 将不知道拿哪张卡开子代理），补回后绿。
- 停线功能实测（本仓）：置旗→pre-commit 红 1 / mission_start 红 1 / arbiter-push 红 1；
  清旗→复绿。
- 80 号沙箱：纯 worklog 不再被硬套（绿 0）；任务单缺小节仍拦（红 1 并点名）。

## 撞号处理（CFO 的让号是对的）

同日两条 J6 内容不同——分布式编号无协调的第二次实证（第一次是 selftest 断言撞 35）。
CFO 把号让给已被三处框架文件引用的那条、自己改 J7 并注明让号原因：处理正确。
类修法（编号协调或内容寻址）挂账，不阻塞。

## 停线旗的权限边界（待人类裁决）

旗文件谁能置：机制上任何有文件系统权限的人/agent 都行。consulter 对项目仓有
「一个字都别写」的禁令——**如需 consulter 能直接对 CFO 仓停线**，请授权唯一例外路径
`logs/STOPLINE`；未授权前，consulter 发现阻塞项走「报 CFO/人类置旗」。

## 附：前人成果对照入仓（同日）

预研代理复位后跑通，报告落 `agents/reference/前人成果对照-多agent框架.md`：
角色分层/结构化交接/审核代码化/判例库均有已验证先例（用户判断正确）；
lease/停线旗/门禁新鲜度/逃生口记账为真空白；「pre-commit 过重会被绕过」是
别人踩过的坑，J5 把全量测试放 push 层的方向被印证。三条落地建议见报告第 5 节。
