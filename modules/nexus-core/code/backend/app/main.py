"""组装 app：挂 ``/api/core`` 前缀、health、include 各子边界路由。

nginx 公开前缀 ``/api/core/`` 已在契约里定死，前端写死地址——**别改**。
``UnknownTaskError → 404`` 的映射放这里：router 里不许有业务判断，
「域错误对应什么状态码」是组装层的接线。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import settings
from .modules.activity.presence_router import router as presence_router
from .modules.activity.router import router as activity_router
from .modules.activity.service import ConflictError as SuggestionConflictError
from .modules.detector import rules as detector_rules
from .modules.detector import service as detector_service
from .modules.detector.router import router as detector_router
from .modules.events.router import router as events_router
from .modules.events.service import InvalidQueryError
from .modules.export.router import router as export_router
from .modules.planner.errors import ForbiddenError, StalePlanError, UnprocessableError
from .modules.planner.import_router import router as planner_import_router
from .modules.planner.repo import ensure_tenant_indexes
from .modules.planner.service import HasChildrenError, InvalidInputError, NotFoundError
from .modules.planner.unified_router import router as planner_unified_router
from .modules.projector.rebuild import backfill_lanes_if_empty
from .modules.restore.router import router as restore_router
from .modules.restore.service import NotEmptyError
from .modules.timer.router import agents_router
from .modules.timer.router import router as timer_router
from .modules.timer.router import sessions_router
from .modules.timer.service import NoRunningTimerError, UnknownTaskError
from .modules.views.router import router as views_router
from .tenant import TenantMiddleware

API_PREFIX = "/api/core"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时把旧的全局唯一索引换成按租户的（v2.0）。只动索引、不动数据，幂等。
    v2.4：``proj_lanes`` 空而台账里有事实时自动补建一次（升级上来的用户不会手跑 rebuild）。"""
    ensure_tenant_indexes()
    log = logging.getLogger("uvicorn.error")
    try:
        replayed = backfill_lanes_if_empty()
    except Exception:  # noqa: BLE001 —— 补建失败不许挡住服务启动：时间线空着，其余一切照常
        log.exception("proj_lanes 自动补建失败，服务照常启动；可手动 rebuild --only proj_lanes")
    else:
        if replayed:
            log.info("proj_lanes 为空，已从 %d 条事实自动补建（v2.4 升级）", replayed)
    yield


app = FastAPI(
    title="nexus-core",
    version="0.2.0",
    summary="切片 1「金链路」：events 入口 + timer + proj_current 投影 + 两条读端。",
    lifespan=lifespan,
)
# 租户在任何路由之前定下（契约 v2.0「按租户分数据」，app/tenant.py）。
app.add_middleware(TenantMiddleware)


@app.get(f"{API_PREFIX}/health")
def health() -> dict[str, str]:
    """契约「健康检查暴露库名」v1.0：跨进程（E2E）也能机械核验「打的是不是
    生产库」——单测的护栏（库名不以 ``_test`` 结尾就 die）只在进程内可判，
    E2E 跨进程打 HTTP 看不见对端库名，加这个字段让两处用同一个判据形状。

    v1.6 的 ``actorGuard`` 同款理由：设防姿态是进程级配置，跨进程同样只能靠
    字段暴露；不暴露就只能靠"我记得我配过"，而"我记得"在本项目已经翻过两次车。"""
    return {
        "status": "ok",
        "db": settings.db_name,
        "actorGuard": settings.actor_guard,
        # v2.0：租户设防姿态同样跨进程可判——多用户部署忘开严格模式，从这里一眼看出。
        "tenantGuard": settings.tenant_guard,
    }


app.include_router(events_router, prefix=API_PREFIX)
app.include_router(timer_router, prefix=API_PREFIX)
app.include_router(agents_router, prefix=API_PREFIX)  # v2.1 AI 代理运行
app.include_router(sessions_router, prefix=API_PREFIX)  # v2.11 改挂未分类时间
app.include_router(planner_unified_router, prefix=API_PREFIX)
app.include_router(views_router, prefix=API_PREFIX)
app.include_router(export_router, prefix=API_PREFIX)
app.include_router(planner_import_router, prefix=API_PREFIX)
app.include_router(restore_router, prefix=API_PREFIX)
app.include_router(activity_router, prefix=API_PREFIX)  # v2.2 活动建议
app.include_router(presence_router, prefix=API_PREFIX)  # v2.4 在场心跳
app.include_router(detector_router, prefix=API_PREFIX)  # v2.5 检测程序设置；v2.6 分类规则


# 域错误 → 状态码的映射只在这里（contract.md v0.4「校验」表 + v0.6「档案读端」）：
#   InvalidInputError → 400（引用/取值非法，消息指名道姓）
#   NotFoundError / UnknownTaskError → 404（目标 id 不存在）
#   HasChildrenError → 409（删除拒绝级联，消息说明还剩几个）
#   InvalidQueryError → 400（GET /events 的 from/to 不是合法 ISO8601，消息指名道姓）
#   NoRunningTimerError → 409（cancel 时没在计时：请求合法但与当前状态冲突）
#   ForbiddenError → 403（v1.6 actor 设防：凭据不认识 / 伪装 human / 高风险带 ai）
#   StalePlanError → 409（v1.7 JSON 导入：apply 的 checksum 与当前库重算不一致；
#                    v1.9 快照恢复：apply 的快照不是 dry-run 过的那一份）
#   NotEmptyError → 409（v1.9 快照恢复：目标实例不是空库）
#   SuggestionConflictError → 409（v2.2 活动建议：已忽略的再确认 / 已确认的再忽略）
#   UnprocessableError → 422（v2.4：相位 at 超前 300 秒；views/lanes 的参数互斥 / 跨度超 7 天）
#   detector ForbiddenError → 403（v2.5：设备令牌想改检测设置）
#   v2.11 改挂复用上面几条：ForbiddenError 403（设备令牌 / actor=ai）、NotFoundError / UnknownTaskError 404、
#     InvalidInputError 400（taskId / projectId 没二选一、目标是桶）、HasChildrenError 409（不是未分类的段 / 并发没抢到）
#   detector InvalidSettingsError → 422、TooLargeError → 413（v2.5：设置文档不合 schema / 太大）
#   detector RulesError → 自带状态码（v2.6 分类规则：403/404/412/413/422/428，体 {detail, **附加字段}）


@app.exception_handler(detector_service.ForbiddenError)
def detector_forbidden(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(detector_service.InvalidSettingsError)
def detector_invalid(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(detector_rules.RulesError)
def detector_rules_error(_request: Request, exc: detector_rules.RulesError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail, **exc.extra})


@app.exception_handler(detector_service.TooLargeError)
def detector_too_large(_request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": str(exc)})


@app.exception_handler(UnknownTaskError)
def unknown_task(_request: Request, exc: UnknownTaskError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(NoRunningTimerError)
def no_running_timer(_request: Request, exc: NoRunningTimerError) -> JSONResponse:
    """409 而不是 404：路由与请求都合法，冲突的是**当前状态**（同 HasChildrenError
    那条「拒绝级联删除」的形状）。404 会让调用方以为是端点拼错了。

    **绝不能是 200**：静默成功会让界面显示「已取消」而其实什么都没发生。"""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(NotFoundError)
def planner_not_found(_request: Request, exc: NotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(InvalidInputError)
def planner_bad_input(_request: Request, exc: InvalidInputError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(HasChildrenError)
def planner_has_children(_request: Request, exc: HasChildrenError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(ForbiddenError)
def planner_forbidden(_request: Request, exc: ForbiddenError) -> JSONResponse:
    """契约 v1.6「actor 来源区分与高风险二次设防」：三种拒绝共用一条映射。

    **只挂在基类上**（Starlette 按 `type(exc).__mro__` 查 handler）：三个子类
    对外必须长得一样，否则调用方能靠状态码差异反推"我是哪一种不合法"，
    那本身就是信息泄漏。403 而不是 401——请求已过网关认证，被拒的是**权限**，
    不是身份未知；回 401 会让前端去弹登录框，而重新登录并不能解决这件事。"""
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(InvalidQueryError)
def events_bad_query(_request: Request, exc: InvalidQueryError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(StalePlanError)
def planner_stale_plan(_request: Request, exc: StalePlanError) -> JSONResponse:
    """契约 v1.7：apply 的 checksum 对不上当前库重算的结果——409，不是 400，
    因为请求体本身合法，冲突的是**当前状态**（同 `HasChildrenError` 的形状）。"""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(NotEmptyError)
def restore_not_empty(_request: Request, exc: NotEmptyError) -> JSONResponse:
    """契约 v1.9：恢复只对空实例开放。409 同 `StalePlanError`——请求合法，
    冲突的是**当前状态**；detail 里那句「恢复通道不是合并通道」本身就是护栏。"""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(SuggestionConflictError)
def suggestion_conflict(_request: Request, exc: SuggestionConflictError) -> JSONResponse:
    """契约 v2.2：请求合法，冲突的是建议的**当前状态**（同 `NoRunningTimerError`）。"""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(UnprocessableError)
def unprocessable(_request: Request, exc: UnprocessableError) -> JSONResponse:
    """契约 v2.4：取值不可处理，与 pydantic 请求体校验同为 422。"""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


def main() -> None:
    """按 ``NEXUS_BIND`` 起服务。默认 ``127.0.0.1:8000``，**绝不默认对外监听**。"""
    import uvicorn

    uvicorn.run(app, host=settings.bind_host, port=settings.bind_port)


if __name__ == "__main__":
    main()
