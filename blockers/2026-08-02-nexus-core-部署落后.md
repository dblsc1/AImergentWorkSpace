# 障签 · nexus-core 部署落后代码

波及：code/nexus-core/

## 现象

跑着的 nexus-core（pid 起于 2026-08-02 01:24）比仓里的代码旧：

- `GET /api/core/health` 只返 `{"status":"ok"}`，**没有契约 v1.0 的 `db` 字段**
- 进程环境里**没有 `NEXUS_TZ`** —— 说明它起于归日改按时区之前

## 为什么要挡

`blockers/2026-08-02-e2e-defaults-to-prod.md` 那道护栏的判据是
「E2E 启动时查一次 health，库名不以 `_test` 结尾就 die」。
**部署不带 `db` 字段 = 那道护栏拿不到输入 = 等于空的。**
在它转绿之前，任何"E2E 已受生产库保护"的说法都不成立。

解除标志：`curl -s http://127.0.0.1:8000/api/core/health` 的输出里出现 `"db"`

## 怎么解

重启需要 kill 用户的活服务，CFO 被权限拦下，**留给人类**。备份已做且已推远端
（`2026/8/2/nexus_core-2026-08-02T081935Z.archive`，42 events / 17 daily_stats / 3 projects）。

```bash
kill <pid>   # 当前监听 127.0.0.1:8000 的那个
cd code/nexus-core/code/backend
NEXUS_MONGO_URI="mongodb://127.0.0.1:27117/" NEXUS_DB_NAME=nexus_core \
NEXUS_BIND=127.0.0.1:8000 NEXUS_TZ=Asia/Shanghai \
  nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 &
```

⚠️ **`NEXUS_TZ` 不能漏**：它无默认值、缺失即 die（铁律 2）。
当前进程没有它，照原样重启会起不来。

⚠️ 重启后归日口径从 UTC 变成 `Asia/Shanghai`，**既有 `proj_daily_stats` 可能需要重建**
（`app/modules/projector/rebuild.py`）。先看甘特图的事实条日期对不对得上。
