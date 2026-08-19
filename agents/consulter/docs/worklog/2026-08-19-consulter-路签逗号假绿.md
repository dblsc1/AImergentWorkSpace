# 2026-08-19 consulter：mission_start.sh 路签逗号假绿

## 目标

修 `scripts/mission_start.sh` 的一个「发签成功 ≠ 签有效」缺陷：写区参数本是
空格分隔的变参，若调用方误传成一个逗号分隔的字符串（如
`"code/nginx-docker/module_docs/,agents/cfo/docs/worklog/"`），起飞检查全绿、
正常发签，但 `checks/_common/05-write-lease.sh` 逐行按前缀匹配时，这条带逗号
的字符串永远匹配不上任何真实路径 —— 用户拿到 🟢 以为拿到写区，实际着陆时
全部文件会被判越界。铁律 23 要求：不只修这一处实例，要扫同类形状并留断言。

## 验收标准

1. `mission_start.sh` 在发签前对每个写区参数做校验：拒绝含逗号 / 绝对路径
   （`/` 开头）/ 含 `..` 的参数，非零退出，报错信息给出可直接粘贴的正确命令。
2. 不新增「路径必须已存在」之类的校验（合法场景允许申领未建目录）。
3. 全仓扫描同类「变参可能被传成分隔符字符串」的入口（至少
   `scripts/new_agent.sh`、`scripts/new_instance.sh`、`scripts/dispatch.sh`、
   `scripts/lib/lease.sh` 写 `.lease` 的那条路径），报告里写清查了哪些、
   有没有同类、依据。
4. 加一条确定性断言（`scripts/checks/` 或 `scripts/gates/` 或 selftest），
   拿逗号写区调用 `mission_start.sh`，断言非零退出且不写 lease 文件；
   必须做红绿反向验证（临时回退修复 → 断言变红 → 恢复 → 断言变绿），
   报告贴两次真实输出。
5. 不改坏原有 9 项起飞检查行为；改完跑一次正常发签/还签验证无回归。

## 可触碰目录（写区）

`scripts/`、`agents/consulter/docs/`。禁动任何 `code/<模块>/`。

## 自检门

- `bash -n scripts/mission_start.sh` 语法检查
- 新断言脚本本身可独立执行，退出码符合预期
- 正常发签/还签流程手工跑一遍，确认 9 项检查未回归
- 本轮无独立审核（consulter 改框架，铁律「谁写的谁不审」，交人类审）
