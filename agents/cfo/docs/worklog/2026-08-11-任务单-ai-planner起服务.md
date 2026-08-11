# 任务单 · ai-planner 组装根 + 真起服务 + 真连 nexus

- 派活方：cfo · 日期：2026-08-11 · tier：hard · 档位：Sonnet
- 前置已解：模块远端已建并推送（`dblsc1/cockpit-ai-planner`，`84827597` 远端实证），P0 障签解除条件满足

## 目标

让 ai-planner **第一次真正跑起来**，并**第一次真连线上 nexus**。
此前它 52 个测试全绿、codex 冒烟过，但冒烟用的是 mock 后端，**服务进程从没起来过**。

## CFO 已确认的缺口

`code/backend/handoff.md:13` 写的启动命令**跑不起来**：
```
uvicorn ai_planner.service:create_app --host 127.0.0.1 --port 8700
```
`create_app(layer: ControlledToolLayer, driver: CodexDriver)` 要两个参数，
uvicorn 工厂模式**无参调用** → 必然 TypeError。

**缺的是组装根**：从 env 建 `Config` → 构造受控工具层 + codex 驱动 → 交出 app。
没人真起过它，所以这条错命令在 handoff 里躺着没被发现。

## 交付

1. **组装根**：一个 uvicorn 能直接吃的入口（`--factory` 无参工厂，或模块级 `app`）。
   env 缺失即 die，别弱默认值（铁律 2）。
2. **启动脚本** `code/backend/scripts/up.sh`（或等价）：读 env、前置校验、起 uvicorn。
   **关键路径缺文件必须 die，不许静默跳过后报成功**（铁律 23 点名形状）。
3. **绑定地址**：`AI_PLANNER_BIND` 要能让 **nginx 容器够得到**。
   CFO 实测：nginx 容器默认网关 = `172.24.0.1`（宿主 bridge 侧地址）。
   默认的 `127.0.0.1` 容器够不到；`0.0.0.0` 被宪法 §3 禁且代码里硬拒。
   **你自己判断并在 handoff 写清理由。**
4. **真连 nexus 冒烟**：起服务后真调一次 `POST /api/planner/plan`，
   走通「读日程 → 低风险写落库(actor=ai) → 高风险产提议」，**贴真实 SSE 输出**。
   - `AI_PLANNER_NEXUS_BASE` 指向线上入口
   - 凭据：`AI_PLANNER_NEXUS_PASSWORD` **就是现有登录口令**（不是新密钥），
     值在 `code/auth/code/backend/.env` 的 `AUTH_PASSWORD`。
     **走 env 注入，绝不写进任何入仓文件**（铁律 2）。
5. **handoff 更正**：那条错的启动命令必须改对。

## 造数据纪律（硬要求）

生产库有用户**真实项目**（英语学习/读书计划/X-structure/上班/收件箱）。
冒烟会让 AI **真的写库**：
- 只准让它建**可识别前缀**的测试对象，**绝不修改/删除任何既有对象**
- 用完**删干净**
- 冒烟前后各拉一次 `GET /api/core/export` 做对象计数比对，**写进报告**
- 高风险提议**只看不执行**（那正是设计：AI 没有执行路径）

## 验收标准

- [ ] `curl http://<bind>/health` → 200
- [ ] 服务能被 **nginx 容器**够到（`docker exec cockpit-c-nginx wget -qO- http://<bind>/health`）
- [ ] 真连 nexus 冒烟走通，贴真实 SSE 事件流
- [ ] 冒烟产生的 `actor=ai` 写在库里可见（`lastWriter`）
- [ ] 测试数据清理干净，前后计数一致
- [ ] `handoff.md` 启动命令改对且**照着能跑通**
- [ ] 现有 52 passed 基线不退

## 可触碰目录

`code/ai-planner/code/backend/`。**禁改 nginx 路由**（那是 nginx-docker 属地，本单不做②环）。

## 自检门

`python3 -m pytest tests/ -q` ≥52 passed + 起服务 + health 200 + 真连冒烟，全贴真实输出。

## 硬约束

- 红线：AI **只 planner，永不碰 events**
- 口令走 env，**绝不入仓**；日志里也不许出现
- 铁律 17 新增代码同批测试；铁律 23 修到类 + 留断言
- trailer 恰一条 `Agent-Attribution: programmer@ai-planner+service-bringup`
- 留痕 + report.json 入仓 commit
- **别切根仓分支**（工作树多方共享）
