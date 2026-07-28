# control-panel —— 任务控制台

**每个脚本被激活,这里都看得到**:时间、脚本名、激活者、简短报告、退出码。

原理:所有脚本 `source` 了 `scripts/lib/emit.sh`,启动与退出各写一行进 `logs/diary.jsonl`;
本服务用 SSE 追这个文件推给页面。**不需要各脚本各写一遍上报代码。**

## 起

```bash
python3 control-panel/server.py          # http://127.0.0.1:8787
AIMERGENT_PANEL_PORT=9000 python3 control-panel/server.py
```

`scripts/install-gates.sh` 会自动调 `control-panel/install.sh` 装成 systemd --user 服务。

## 看什么

| 区 | 内容 |
|---|---|
| 指标条 | 脚本运行/失败、报告总数、打回、升级、**考试未过、提交被拒、逃生口使用** |
| 脚本活动 | 实时流水,新事件闪一下 |
| 报告流 | 各角色 report.json:状态、审核意见、升级面 |

**逃生口使用**那格要盯着——它是唯一一个"规矩被绕过"的可见入口。

## 边界

- **只读。** 不写仓、不改留痕、不引入新数据源——它不是第二个事实源。
- 零依赖,只用 python3 标准库;默认只绑 `127.0.0.1`。
- 数据来自 Git 里已有的东西,面板挂了不影响任何流程。
