"""well-known 收件箱：固定 id 的「未分类」zone + `p_inbox` 项目（契约 v1.5
「收件箱」节，PRD F-INBOX-1）。

拆成独立文件的理由同 `deps.py`/`errors.py`——`service.py` 已经贴着 300 行
预算，收件箱是一小块自成一体的逻辑（幂等建 + "是不是它"判断），拆出去更好读。
**不是新的子边界**，仍在 `planner/` 内部，只服务于 `service.py`。

**固定 id，不走三类对象平时的 uuid 生成路径**——它们是系统单例，不是用户
建的对象，固定 id 才能让"这是不是收件箱"这个判断在任何库上都成立（不用
猜名字、不用查配置、幂等种子重跑也认得出它）。
"""

from __future__ import annotations

from . import repo

#: 「未分类」zone 的固定 id。
ZONE_ID = "z_inbox"
ZONE_NAME = "未分类"

#: 收件箱项目的固定 id——契约正文直接点名 `p_inbox`，禁删判据认的就是这个常量。
PROJECT_ID = "p_inbox"
PROJECT_NAME = "收件箱"


def is_protected_project(project_id: str) -> bool:
    """删除保护判据（F-API-4）：这是不是系统收件箱。"""
    return project_id == PROJECT_ID


def ensure() -> dict:
    """幂等确保收件箱存在：库里已有就直接返回现有的，没有才建。

    **对生产库幂等**——重复调用不重建、不重复；不在 app 启动路径里自动调用
    （同其余种子数据的纪律，见 `code/backend/seed_planner.py`），显式跑一次
    种子脚本才生效。
    """
    zone = repo.get_zone(ZONE_ID)
    if zone is None:
        zone = {
            "id": ZONE_ID,
            "key": str(repo.name_num(ZONE_NAME)),  # 分区 key 格式与 create_zone 一致
            "name": ZONE_NAME,
            "color": "#999999",
            "order": -1,  # 置顶：捕捉落点永远排在其余分区之前
            "lastWriter": "human",
        }
        repo.insert_one("zones", zone)

    project = repo.get_project(PROJECT_ID)
    if project is None:
        project = {
            "id": PROJECT_ID,
            "key": f"{repo.name_num(ZONE_NAME)}-{repo.name_num(PROJECT_NAME)}",
            "zoneId": ZONE_ID,
            "name": PROJECT_NAME,
            "status": "active",
            "plannedWeight": 0.0,  # 系统容器，不参与权重排序类的展示
            "plan": None,
            "progress": 0,
            "progressSource": "computed",
            "lastWriter": "human",
        }
        repo.insert_one("projects", project)
    return project
