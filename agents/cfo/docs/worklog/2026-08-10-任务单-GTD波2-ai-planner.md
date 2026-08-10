# 任务单 · GTD 波2-A · ai-planner（新模块，Opus）

- 派活方：cfo · 日期：2026-08-10 · tier：hard · 档位：**Opus**（D17：agentic + 安全边界 + 外部集成）
- 需求源：`agents/cfo/docs/design/gtd-v1/PRD.md` §3 F-AI-*、§4 F-ACTOR（注入侧）、§7 F-API-3
- 契约（system prompt + 工具契约唯一事实）：`contracts/ai-planner-guide-v1.md`（F-GUIDE，已成文）
- 前置已就绪：codex 0.147.0 + `~/.codex/auth.json`；nexus v1.5 读/写端 live（10000 auth-gated）

## 目标

宿主 AI 规划服务：驱动 codex，包一层**受控工具层**（白名单 planner 命令，注入 `actor=ai`，
高风险只产提议不执行），对前端出流式接口。**安全边界是真交付物，不是 codex 本身**。

## 契约先行（arbiter 帽）

先写 `module_docs/contract.md`：
- 宿主服务落点/端口（不进 compose——codex 在宿主够不到容器，F-AI-1）；对前端出的端点（建议 SSE 流式，F-AI-5）
- 受控工具层白名单**逐条对齐** `ai-planner-guide-v1.md` §3（读/低风险写/高风险提议/不暴露面）
- codex 驱动形态：`exec` vs `mcp-server`（O2，冒烟后定，契约标注选型理由）
- actor 注入点、提议对象 schema、降级行为（F-AI-6）

## 实现（programmer 帽）

- 受控工具层：白名单命令映射 nexus `/api/core/planner/{type}`，**层内注入 actor=ai**，AI 无从带 human
- 高风险（move/reschedule/delete）：层只产提议对象，**无调 CRUD 的路径**（F-API-3）
- codex 驱动：适配器接口 + 一个具体实现（复用宿主登录态，免 API key）
- 流式呈现骨架（边想边报）
- 降级：codex 挂/超时 明确提示，不拖垮主栈

## 验收（机械优先，PRD §9）

- [ ] A8：AI 只能调白名单；events/timer/mongo/凭据/裸 shell **不可达**（工具面测试）
- [ ] A8b：AI 无执行高风险写的路径（直发高风险 CRUD 应无认证/被拒）
- [ ] A8c：actor 由层注入，AI 无法带 actor=human（层测试）
- [ ] codex 驱动冒烟（真调一次，读日程 → 产一条低风险写 + 一条高风险提议）
- [ ] 降级路径测试（codex 不可用 明确提示）

## 可触碰目录（写边界）

`code/ai-planner/`（模块自有仓，全权）。契约引用 `contracts/ai-planner-guide-v1.md`（只读）、nexus 契约（只读）。禁写 nexus/table/其它模块。

## 自检门

- `scripts/gates/run-tests.sh`（或模块 reviewcode）全绿；A8/A8b/A8c/降级测试真实 passed
- codex 冒烟留真实输出；report.json 入仓且与交接一致（Git 核验）

## 硬约束

- 红线：AI **只 planner，永不碰 events**（宪法级）
- 数据出本机到 LLM 可含计时明细（用户放开），但**绝不进公开仓**
- 留痕四件套 + report.json（canonical path）+ commit trailer `Agent-Attribution: programmer@ai-planner+gtd-w2-aiplanner`
- 新增代码同批新增 pytest（铁律 17）；缺陷修到类 + 留断言（铁律 23）
- 开工先读级联：根 AGENTS → agents/AGENTS → CONSTITUTION → 模块 AGENTS → 角色卡
