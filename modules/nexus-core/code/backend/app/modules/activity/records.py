"""原始记录上传（契约 v2.38「三级分类」一、原始记录，唯一事实源）。

逐条校验，坏的进 ``rejected``（部分成功，同建议上传：检测程序只在 2xx 后推进游标，整批 4xx 会永远卡住）。
一条跨午夜的记录（``NEXUS_TZ`` 本地日）切成两条存，第二条防重键用午夜那一刻。
上传完触发一次对账（契约「四」）。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, StrictBool, StrictStr, ValidationError

from ... import config, timeutil
from ...tenant import current as current_tenant
from . import block_display, block_key, ignore, reconcile, records_repo
from .service import _parse, _reason

MAX_APP, MAX_TITLE = 128, 512
_log = logging.getLogger(__name__)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MS = timedelta(milliseconds=1)


class _Rec(BaseModel):
    startAt: Annotated[StrictStr, Field(max_length=64)]
    endAt: Annotated[StrictStr, Field(max_length=64)]
    app: Annotated[StrictStr, Field(min_length=1), AfterValidator(lambda v: v[:ignore.GATE_CHARS])]
    title: Annotated[StrictStr, AfterValidator(lambda v: v[:ignore.GATE_CHARS])] = ""
    titleIsPseudonym: StrictBool
    block: StrictStr | None  # 键必须出现（可为 null）
    folder: Annotated[StrictStr, Field(min_length=1, max_length=64)] | None = None  # onboarding.v1 二·2：可缺


def _ms(t: datetime) -> int:
    return (t.astimezone(timezone.utc) - _EPOCH) // _MS


def _pieces(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """按本地午夜切开（通常 1 段，跨午夜 2 段）。"""
    tz, out = config.settings.tz, []
    while True:
        s_local = start.astimezone(tz)
        cut = datetime.combine(s_local.date() + timedelta(days=1), time(0), tzinfo=tz)
        if end <= cut:
            return [*out, (start, end)]
        out.append((start, cut.astimezone(start.tzinfo)))
        start = cut.astimezone(start.tzinfo)


def _agent_linker(user: str, valid: list) -> Any:
    """终端记录连代理运行（onboarding.v1 二·1）：记录中点落在恰好一条运行的 attend 半开区间 ``[at, until)`` 里 → ``(runId, agent)``。
    读不到运行就不连（不拿记录陪葬）。"""
    cand = [v for v in valid if not (v[0].block or "").strip() and block_display.kind_of(v[0].app[:MAX_APP]) == "terminal"]
    if not cand:
        return lambda _rec, _start, _end: None
    try:
        from ..views import collab_runs  # noqa: PLC0415 —— views 反过来 import activity

        _now, runs = collab_runs.load(min(c[1] for c in cand), max(c[2] for c in cand))[:2]
    except Exception:  # noqa: BLE001
        _log.warning("终端记录连代理运行：读运行失败，这一批不连", exc_info=True)
        return lambda _rec, _start, _end: None

    def link(rec: _Rec, start: datetime, end: datetime) -> tuple[str, str] | None:
        if block_display.kind_of(rec.app[:MAX_APP]) != "terminal" or (rec.block or "").strip():
            return None
        mid = start + (end - start) / 2
        hit = [r for r in runs if any(a <= mid < b for a, b in r["attend"])]
        return (hit[0]["runId"], hit[0]["agent"]) if len(hit) == 1 and hit[0].get("agent") else None

    return link


def upload(device_id: str, records: list[Any]) -> dict:
    user, now, tz = current_tenant(), datetime.now(timezone.utc), config.settings.tz
    rules_ = ignore.prepared(user)  # 先读忽略规则（读不到 → 5xx，此时什么都没改）
    records_repo.purge(user, now - timedelta(days=config.settings.record_ttl_days),
                       (now.astimezone(tz) - timedelta(days=config.settings.record_ttl_days)).date().isoformat())
    rejected: list[dict] = []
    valid: list[tuple[_Rec, datetime, datetime, float]] = []
    for index, raw in enumerate(records):
        try:
            rec = _Rec.model_validate(raw)
            start, end = _parse(rec.startAt, "startAt"), _parse(rec.endAt, "endAt")
            seconds = (end - start).total_seconds()
            if not 1 <= seconds <= 86400:
                raise ValueError(f"秒数 endAt-startAt={seconds:g} 越界：须在 [1, 86400] 内")
            valid.append((rec, start, end, seconds))
        except ValidationError as exc:
            rejected.append({"index": index, "reason": _reason(exc)})
        except ValueError as exc:
            rejected.append({"index": index, "reason": str(exc)})
    valid, ignored = ignore.gate_incoming(user, rules_, valid, lambda v: (v[0].app, v[0].title, v[3]))
    link = _agent_linker(user, valid)
    accepted = duplicates = 0
    touched: set[tuple[str, str]] = set()
    for rec, start, end, _seconds in valid:
        given = (rec.block or "").strip()[:block_key.MAX_BLOCK] or None
        key, source = (given, "rule") if given else (block_key.default_key(rec.app[:MAX_APP], rec.title[:MAX_TITLE]), "auto")
        agent = link(rec, start, end)
        if agent:
            key, source = f"{rec.app[:MAX_APP]}{block_key.SEP}{agent[1]}", "agent"
        fresh = False
        for p_start, p_end in _pieces(start, end):
            dedupe = f"rec:{device_id}:{_ms(p_start)}"
            touched.add((timeutil.local_date(p_start, tz), key))
            fresh |= records_repo.insert_if_absent({
                "user": user, "id": "rec_" + hashlib.sha256(dedupe.encode()).hexdigest()[:20], "dedupeKey": dedupe,
                "deviceId": device_id, "date": timeutil.local_date(p_start, tz), "startAt": p_start.isoformat(),
                "endAt": p_end.isoformat(), "startTs": p_start, "endTs": p_end, "seconds": (p_end - p_start).total_seconds(),
                "app": rec.app[:MAX_APP], **({"folder": rec.folder} if rec.folder else {}),
                **({"agentRunId": agent[0]} if agent else {}), "title": rec.title[:MAX_TITLE], "titleIsPseudonym": rec.titleIsPseudonym,
                "block": given, "blockKey": key, "keySource": source, "rv": None,  # 都不盖：上传时没按服务端规则匹配过，读时（块还没分类才）检查一次再盖（block_key.py）
                 "receivedAt": now, "ledgerEventId": None})
        accepted, duplicates = accepted + fresh, duplicates + (not fresh)
    if accepted:
        reconcile.kick(only=touched)
    return {"accepted": accepted, "duplicates": duplicates, "ignored": ignored, "rejected": rejected}
