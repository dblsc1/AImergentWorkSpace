"""窗口 ↔ 代理会话（契约 v2.13「窗口 ↔ 代理会话」节，唯一事实源）。

终端标签页的标题就是代理会话的名字；钩子在源头报了这个会话属于哪个项目。上传一段没有任务猜测的活动时，
标题（归一化后）与时间上重叠的代理运行的 ``label`` / ``match`` 完全相等 → 给建议写上那个项目。
**只是提示**：不确认、不动任务；只在新写入一段时做（``service.upload``）。**本文件只读。**

在跑的运行经 timer service 读（跨子边界只走 service）；已结束的读 ``proj_lanes`` 的指定读路径（同 views/lanes）。
"""

from __future__ import annotations

import re
from datetime import datetime

from ..planner import service as planner_service
from ..projector.handlers import lanes as lanes_projection
from ..timer import service as timer_service
from . import proposals

SOURCE = "agent-session"
_MIN_LEN = 3
# ponytail: 一批上传的时间窗里最多看最近 500 次已结束的运行；补传几天的积压时更早的对不上，真遇到再分页
_MAX_CLOSED = 500
#: 开头的状态符号 / 转圈符号 / "(3)" 计数（同 ai-detector 的 tabKey）
_LEAD = re.compile(r"^(?:\(\d+\)|[\W_])+")
#: 「 - 」「 — 」「 | 」分隔：最后一个后面那截是程序名时去掉
_SEP = re.compile(r"\s[—–|-]\s")


def norm(text: str, app: str = "") -> str:
    """标题与 label / match 共用的归一化（契约「归一化」）。``app`` 给了才去结尾的「 - 程序名」。"""
    text = " ".join(text.split())
    seps = list(_SEP.finditer(text))
    if app and seps:
        tail = text[seps[-1].end():].strip().casefold()
        if len(tail) >= _MIN_LEN and tail in app.casefold():
            text = text[:seps[-1].start()]
    return _LEAD.sub("", text).strip().casefold()


def _keys(run: dict) -> set[str]:
    return {k for k in (norm(run.get(f) or "") for f in ("label", "match")) if len(k) >= _MIN_LEN}


def watched(rows: list[dict], app: str, title: str) -> str | None:
    """这个窗口**就是**哪一条在跑的代理运行（v2.17 注意力；``rows`` = ``timer_service.list_lane_runs`` 的运行）。
    同一条相等规则：归一化标题 == 归一化 label / match。不要求挂着项目（看的是会话，不是项目）；对上不止一条 → None。
    匿名开的运行（v2.19 ``unverified``）不算：否则不带凭据的调用方起一个同名的运行，就能把人对真会话的注意力
    搅成「对上不止一条」，或者认到自己头上。"""
    key = norm(title, app)
    if len(key) < _MIN_LEN:
        return None
    hits = [run["runId"] for run in rows
            if run["endTs"] is None and not run.get("unverified") and key in _keys(run)]
    return hits[0] if len(hits) == 1 else None


def _candidates(rows: list[dict], now: datetime) -> list[dict]:
    out = []
    for run in rows:
        keys = _keys(run)
        project = run.get("projectId")
        if keys and project and project != planner_service.INBOX_PROJECT_ID:
            out.append({"keys": keys, "label": run.get("label") or run.get("match"), "projectId": project,
                        "startTs": run["startTs"], "endTs": run["endTs"] or now})
    return out


def runs(user: str, start: datetime, end: datetime) -> list[dict]:
    """与 [start, end) 有重叠、挂在真项目上的代理运行：``{keys, label, projectId, startTs, endTs}``。"""
    now, live_rows = timer_service.list_lane_runs(user)
    seen = {r["runId"] for r in live_rows}
    closed = [{**r, "startTs": r["startAt"], "endTs": r["endAt"]}
              for r in lanes_projection.read_lanes(user, "run", start, end, _MAX_CLOSED) if r["runId"] not in seen]
    return _candidates(live_rows + closed, now)


def live(user: str) -> list[dict]:
    """此刻还在跑、挂在真项目上的代理运行（v2.16「此刻的焦点」用；形状同 ``runs``）。"""
    now, rows = timer_service.list_lane_runs(user)
    return _candidates([r for r in rows if r["endTs"] is None], now)


def link(candidates: list[dict], app: str, title: str, start: datetime, end: datetime) -> dict:
    """这段该追加进 ``suggestion`` 的键；对不上 / 指向不止一个项目 → ``{}``。"""
    key = norm(title, app)
    if len(key) < _MIN_LEN:
        return {}
    hits = [r for r in candidates if key in r["keys"] and r["startTs"] < end and r["endTs"] > start]
    if len({r["projectId"] for r in hits}) != 1 or planner_service.get_project(hits[0]["projectId"]) is None:
        return {}
    out = {"projectId": hits[0]["projectId"], "projectSource": SOURCE}
    try:
        out["collection"] = proposals.collection(hits[0]["label"])
    except ValueError:  # label 不成名字（去空白后为空之类）：只给项目
        pass
    return out
