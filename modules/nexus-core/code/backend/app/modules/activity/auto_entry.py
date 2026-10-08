"""自动记录：上传的段直接记成事实（契约 v2.14「自动记录」小节 + 2026-10-08 波次统一审核的追加）。
``auto.py`` 贴着 300 行预算，拆出来；它同时要看 AI 的问询（``auto_ai``），放在 ``auto.py`` 里会成环。

一段的目标与页面上 ``human.auto`` **同一个取法**（规则优先，其次人的临时选择）：

- 规则的猜测（``classifier: "rules"`` 且有目标）——人说过「不对」的那个 AI 目标不算（同 ``Asks.guess``：检测程序拉到新规则
  之前还会带着它）。把握 ≥ ``AUTO_CONFIDENCE`` 才记；不够就留在待确认，**不**退到临时选择（页面此刻显示的也是规则的目标）。
- 没有规则的猜测 → 这个窗口此刻未过期的、**人**的临时选择（AI 写的那份不算：AI 的把握走它那条规则），且这一段结束在
  人做选择之后（补传的积压不追认）。
例外不变：开关关着、无操作段、与手动计时重叠 → 留在待确认。

这些依据（问询、临时选择）是先读下来的；人可能在读与写事实之间否掉它。所以给这一段占位之后、写事实之前**重读一遍
再定一次**（``service.confirm`` 的 ``still_wanted``），目标变了就退出占位、留在待确认。
ponytail: 重读与写台账之间仍隔着几次读库（归属链）——台账与问询不在一个文档里，做不成一次原子写；
落在这条缝里的那一段会按旧依据记下，要彻底就得在记完后再查一遍、把它改挂走（需要一条非人的改挂路径）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ...tenant import current as current_tenant
from ..detector import service as detector_service
from . import ask_repo, auto, auto_ai, choice_repo, service

log = logging.getLogger("uvicorn.error")


def _target(doc: dict, end: datetime, rec: dict | None, choice: dict | None) -> tuple | None:
    """这一段记到哪：``(task_id, project_id, 出处)``（``task_id`` 为 None 且没有项目 = 用建议里的任务）；不记 → None。"""
    sug = doc["suggestion"]
    project = sug.get("projectId") if "projectSource" not in sug else None
    guess = {"taskId": sug["taskId"]} if sug["taskId"] else {"projectId": project} if project else None
    if guess and sug["classifier"] == "rules" and not auto_ai.stale(rec, guess):
        # task_id 不给 = 用建议里的任务（占位时要求它没变）；只到项目 = 记到它的「未分类」
        return (None, guess.get("projectId"), "rules") if sug["confidence"] >= auto.AUTO_CONFIDENCE else None
    if choice and choice["kind"] == "choice" and choice.get("by") != "ai" and end > choice["at"]:
        return choice.get("taskId"), choice.get("projectId"), "choice"
    return None


def record(device_id: str, docs: list[dict], request: Any) -> None:
    """对新写入的建议做自动记录。记不成的留在待确认，**从不抛**——检测程序只在 2xx 后推进游标，这一步不能让上传失败。"""
    user = current_tenant()
    docs = [d for d in docs if not d["idle"]]
    if not docs or not detector_service.device_flags(user, device_id)["autoTrack"]:
        return
    keys = {d["id"]: auto.window_key(d["app"], d["title"]) for d in docs}
    recs = ask_repo.by_keys(user, sorted(set(keys.values())))
    now = auto._now()  # noqa: SLF001
    choices = choice_repo.live(user, now)
    eligible = []
    for d in docs:
        end = datetime.fromisoformat(d["endAt"])
        target = _target(d, end, recs.get(keys[d["id"]]), choices.get(keys[d["id"]]))
        if target:
            eligible.append((d, end, target))
    if not eligible:
        return
    manual = auto._manual_spans(user, min(d["startTs"] for d, _, _ in eligible),  # noqa: SLF001
                                max(end for _, end, _ in eligible))
    for d, end, target in eligible:
        if any(start < end and stop > d["startTs"] for start, stop in manual):
            continue  # 人自己掐着表的那段时间，AI 不插手
        task_id, project_id, source = target
        key = keys[d["id"]]

        def unchanged(d=d, end=end, key=key, target=target) -> bool:
            return _target(d, end, ask_repo.get(user, key), choice_repo.live(user, now).get(key)) == target

        try:
            service.confirm(d["id"], task_id, "do", request=request, project_id=project_id, auto=source,
                            still_wanted=unchanged)
        except Exception as exc:  # noqa: BLE001 —— 任务刚被删、桶的 id 被占……：留在待确认
            log.warning("自动记录没记成，留在待确认：%s（%s: %s）", d["id"], type(exc).__name__, exc)


def upload(device_id: str, segments: list[Any], request: Any) -> dict:
    fresh: list[dict] = []
    out = service.upload(device_id, segments, fresh)
    record(device_id, fresh, request)
    return out
