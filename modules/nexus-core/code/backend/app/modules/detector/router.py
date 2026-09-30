"""HTTP 层：路径、入参、响应。**不许有业务判断**（谁能写、schema 校验都在 service）。

PUT 的请求体按原始字节交给 service：要先量大小（413）、再按 schema 严格校验，
错误体保持 nexus-core 的 ``{detail: string}``（契约「错误响应形状」）。
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from . import service

router = APIRouter(prefix="/detector", tags=["detector"])


@router.get("/settings")
def get_settings(deviceId: str, request: Request) -> dict:
    return service.get_settings(deviceId, request.headers.get("authorization"))


@router.put("/settings")
async def put_settings(deviceId: str, request: Request) -> dict:
    body = await request.body()
    # service 里是同步的 pymongo：放线程池，不堵事件循环（同步 def 路由 FastAPI 自动这么做）
    return await run_in_threadpool(service.put_settings, deviceId, request.headers.get("authorization"), body)


@router.delete("/settings", status_code=204)
def delete_settings(deviceId: str, request: Request) -> Response:
    service.delete_settings(deviceId, request.headers.get("authorization"))
    return Response(status_code=204)


@router.get("/devices")
def list_devices() -> dict:
    return service.list_devices()
