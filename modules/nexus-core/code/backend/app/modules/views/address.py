"""位置编号 ``address``（契约 v2.39「MCP v0.5 的后端」一）。**不存储**，读时现算。

名字 → 基础编号（词首字符 / 汉字拼音首字母，小写，分区 2 位、项目 4 位、任务 4 位，取不出 = ``x``）；同一层级按创建先后
依次分配 ``基础`` / ``基础2`` / ``基础3``……里第一个还没被占用的（不分大小写）；三级用 ``-`` 连。
纯函数在前（``base`` / ``assign`` / ``build`` / ``resolve``，不读库，单测直接喂），``load`` 是唯一读库的一处。

创建先后：planner 文档没存 ``createdAt``，用 Mongo ``_id``（ObjectId 随插入单调）代；同刻（排序键相等）按 ``id``。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from pypinyin import Style, pinyin

ZONE_LEN, PROJECT_LEN, TASK_LEN = 2, 4, 4
_HAN = re.compile("[㐀-䶿一-鿿豈-﫿\U00020000-\U0002fa1f]")


@lru_cache(maxsize=8192)
def _initial(ch: str) -> str:
    """一个汉字的拼音首字母（pypinyin 缺省读音，不看上下文）；读不出 = ``""``。"""
    got = pinyin(ch, style=Style.FIRST_LETTER, errors="ignore", heteronym=False)
    first = got[0][0][:1].lower() if got and got[0] else ""
    return first if first.isascii() and first.isalpha() else ""


def _word_char(ch: str) -> bool:
    """词 = 连续的字母或数字：数字，或拉丁字母（含带重音的，如 é）。其余文字、标点、空白、表情只当分隔。"""
    if ch.isdecimal():
        return True
    return ch.isalpha() and unicodedata.name(ch, "").startswith("LATIN")


def _digit(ch: str) -> str:
    return str(unicodedata.decimal(ch)) if ch.isdecimal() else ch


def base(name: str, limit: int) -> str:
    out: list[str] = []
    in_word = False
    for ch in name or "":
        if _HAN.match(ch):  # 每个汉字单独成一个词（也截断前面的拉丁词）
            in_word = False
            if init := _initial(ch):
                out.append(init)
        elif _word_char(ch):
            if not in_word:
                out.append(_digit(ch).lower())
                in_word = True
        else:
            in_word = False
        if len(out) >= limit:
            break
    return "".join(out[:limit]) or "x"


def assign(entries: Iterable[tuple[str, str, Any]], limit: int) -> dict[str, str]:
    """同一层级的 ``(id, name, 创建先后键)`` → ``{id: 编号}``。按 ``(键, id)`` 升序依次取第一个没被前面占用的。"""
    taken: set[str] = set()
    out: dict[str, str] = {}
    for eid, name, _created in sorted(entries, key=lambda e: (e[2], e[0])):
        stem = base(name, limit)
        cand, n = stem, 1
        while cand.lower() in taken:
            n += 1
            cand = f"{stem}{n}"
        taken.add(cand.lower())
        out[eid] = cand
    return out


@dataclass
class Addresses:
    zones: dict[str, str] = field(default_factory=dict)
    projects: dict[str, str] = field(default_factory=dict)
    tasks: dict[str, str] = field(default_factory=dict)
    #: 小写完整编号 → (kind, id)
    lookup: dict[str, tuple[str, str]] = field(default_factory=dict)

    def target(self, task_id: str | None, project_id: str | None) -> str | None:
        """一笔时间的去向的编号：有任务用任务的，否则项目的；都查不到 = None。"""
        return self.tasks.get(task_id) or self.projects.get(project_id) if (task_id or project_id) else None


def build(zones: list[dict], projects: list[dict], tasks: list[dict], created: dict[str, dict]) -> Addresses:
    """``created`` = ``planner_service.creation_order()`` 的形状（缺的 id 排在最前，同刻按 id）。"""
    key = lambda coll, eid: str(created.get(coll, {}).get(eid, ""))  # noqa: E731
    z = assign(((d["id"], d.get("name", ""), key("zones", d["id"])) for d in zones), ZONE_LEN)
    by_zone: dict[str, list] = {}
    for p in projects:
        by_zone.setdefault(p.get("zoneId"), []).append((p["id"], p.get("name", ""), key("projects", p["id"])))
    p_part: dict[str, str] = {}
    for rows in by_zone.values():
        p_part.update(assign(rows, PROJECT_LEN))
    p_addr = {p["id"]: f"{z.get(p.get('zoneId'), 'x')}-{p_part[p['id']]}" for p in projects}
    by_task = {t["id"]: t.get("projectId") for t in tasks}
    by_proj: dict[str, list] = {}
    for t in tasks:
        by_proj.setdefault(t.get("projectId"), []).append((t["id"], t.get("name", ""), key("tasks", t["id"])))
    t_part: dict[str, str] = {}
    for rows in by_proj.values():
        t_part.update(assign(rows, TASK_LEN))
    t_addr = {t["id"]: f"{p_addr.get(t.get('projectId'), 'x-x')}-{t_part[t['id']]}" for t in tasks}
    # 缺 zoneId（或分区不存在）的项目、挂在它下面的任务，不进解析表（地址里的 x 只是占位，不能当真去匹配）
    ok_p = {p["id"] for p in projects if p.get("zoneId") in z}
    lookup = {a.lower(): (kind, i) for kind, m in (("zone", z), ("project", {i: p_addr[i] for i in ok_p}),
                                                   ("task", {i: a for i, a in t_addr.items() if by_task[i] in ok_p})) for i, a in m.items()}
    return Addresses(z, p_addr, t_addr, lookup)


def resolve(addrs: Addresses, text: str | None) -> tuple[str, str] | None:
    """address → ``("zone" | "project" | "task", id)``：去首尾空白、不分大小写、整串精确匹配（1 段分区 / 2 段项目 / 3 段任务），不做前缀匹配。"""
    return addrs.lookup.get((text or "").strip().lower())


def load() -> Addresses:
    """当前租户的全部编号（含已完成、临时任务、收件箱、各项目的「未分类」桶）。"""
    from ..planner import service as planner_service  # noqa: PLC0415

    zones, projects = planner_service.list_zones(), planner_service.list_projects()
    return build(zones, projects, planner_service.list_tasks(), planner_service.creation_order())
