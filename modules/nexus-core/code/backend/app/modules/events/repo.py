"""events 集合的存取。**本文件是 events 子边界唯一碰 mongo 的地方。**

窄接口（rules.md §7.4）——驱动对象不出本文件：

- ``append_if_absent(envelope) -> bool``   True=新写入，False=命中防重
- ``find_by_dedupe(user, source, dedupe_key) -> dict | None``   仅供防重与测试
- ``query_events(type_) -> list[dict]``   档案读端（contract.md v0.6）用的只读查询

防重 = 唯一索引 ``(user, source, dedupeKey)``（契约 §3）。
**索引上没有 ``id``**——B2 要求同 ``id`` 不同 ``dedupeKey`` 两条都落，
``id`` 只是事件标识，不是防重键。

``events`` 集合只增不改不删：本文件刻意**不提供** update/delete。
"""

from __future__ import annotations

from pymongo.errors import DuplicateKeyError

from ...repo import get_db
from ...tenant import scope

_COLLECTION = "events"
_indexes_ready = False


def _col():
    global _indexes_ready
    col = get_db()[_COLLECTION]
    if not _indexes_ready:
        # 幂等；名字固定，重复调用是 no-op。唯一键就是防重语义本身：
        # 靠「先查再插」防重在并发下有竞态，唯一索引没有。
        col.create_index(
            [("user", 1), ("source", 1), ("dedupeKey", 1)],
            unique=True,
            name="uniq_user_source_dedupe",
        )
        _indexes_ready = True
    return col


def append_if_absent(envelope: dict) -> bool:
    """追加一条事件；命中防重键返回 False（**不是异常**——重复不是错误）。"""
    try:
        _col().insert_one(dict(envelope))  # copy：不让驱动把 _id 塞回调用方的 dict
    except DuplicateKeyError:
        return False
    return True


def find_by_dedupe(user: str, source: str, dedupe_key: str) -> dict | None:
    doc = _col().find_one(
        {"user": user, "source": source, "dedupeKey": dedupe_key}, {"_id": 0}
    )
    return doc


def query_events(type_: str | None = None, *, all_tenants: bool = False) -> list[dict]:
    """档案读端（contract.md v0.6）的唯一读入口。按 ``type`` 过滤（可选），

    剔除 ``_id``，返回全部命中文档——时间范围过滤、排序、分页交给 ``service.py``：
    ``time`` 是带任意时区偏移的 ISO8601 字符串，对它做字典序比较在跨时区时不可靠，
    必须先解析成 ``datetime`` 才能比，这不该下推进 mongo 查询。
    """
    filt: dict = {} if all_tenants else scope()
    if type_:
        filt["type"] = type_
    return list(_col().find(filt, {"_id": 0}))
