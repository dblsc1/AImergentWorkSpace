"""Mongo 连接工厂 —— 共享的驱动入口。

rules.md §7.3 红线 1：**只有 ``repo.py`` 允许 import mongo 客户端**。
各子边界的 ``repo.py`` 从这里拿 ``Database``，驱动对象**不**泄漏到
``service.py`` / ``router.py`` / ``queries.py`` / ``handlers/``。

一个进程一个 ``MongoClient``（pymongo 自带连接池；到处 new 客户端只会耗光连接）。
连接串与库名来自 ``config.py``——那里已保证缺失即启动失败。
"""

from __future__ import annotations

from pymongo import MongoClient
from pymongo.database import Database

from .config import settings

_client: MongoClient | None = None


def get_db() -> Database:
    """进程级懒加载单例。首次调用才建客户端，pymongo 再懒连接。"""
    global _client
    if _client is None:
        _client = MongoClient(settings.mongo_uri, tz_aware=True)
    return _client[settings.db_name]
