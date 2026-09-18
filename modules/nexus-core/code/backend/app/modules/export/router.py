"""HTTP 层：只读导出端点。**不许有业务判断**（同其余模块红线）。

``export/`` 是新的子边界，不挂在 ``views/`` 下：``views/`` 的红线是「不读
``events`` 集合」，而导出恰恰要把 events 原样吐出去，挂过去就是破坏那条红线；
也不属于 events/planner/projector 任何单独一个（它跨三者聚合），所以另立
一个只做聚合、不持有任何自己的数据的薄子边界。
"""

from __future__ import annotations

from fastapi import APIRouter

from . import service

router = APIRouter(tags=["export"])


@router.get("/export")
def read_export() -> dict:
    return service.export_all()
