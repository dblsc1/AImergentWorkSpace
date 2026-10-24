"""原始记录上传（契约 v2.38「三级分类」一、原始记录，唯一事实源）。

逐条校验，坏的进 ``rejected``（部分成功，同建议上传：检测程序只在 2xx 后推进游标，整批 4xx 会永远卡住）。
一条跨午夜的记录（``NEXUS_TZ`` 本地日）切成两条存，第二条防重键用午夜那一刻。
上传完触发一次对账（契约「四」）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, time, timedelta, timezone
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, StrictBool, StrictStr, ValidationError

from ... import config, timeutil
from ...tenant import current as current_tenant
from . import block_key, ignore, reconcile, records_repo
from .service import _parse, _reason

MAX_APP, MAX_TITLE = 128, 512
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MS = timedelta(milliseconds=1)


class _Rec(BaseModel):
    startAt: Annotated[StrictStr, Field(max_length=64)]
    endAt: Annotated[StrictStr, Field(max_length=64)]
    app: Annotated[StrictStr, Field(min_length=1), AfterValidator(lambda v: v[:ignore.GATE_CHARS])]
    title: Annotated[StrictStr, AfterValidator(lambda v: v[:ignore.GATE_CHARS])] = ""
    titleIsPseudonym: StrictBool
    block: StrictStr | None  # 键必须出现（可为 null）


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
    accepted = duplicates = 0
    touched: set[tuple[str, str]] = set()
    for rec, start, end, _seconds in valid:
        given = (rec.block or "").strip()[:block_key.MAX_BLOCK] or None
        key, source = (given, "rule") if given else (block_key.default_key(rec.app[:MAX_APP], rec.title[:MAX_TITLE]), "auto")
        fresh = False
        for p_start, p_end in _pieces(start, end):
            dedupe = f"rec:{device_id}:{_ms(p_start)}"
            touched.add((timeutil.local_date(p_start, tz), key))
            fresh |= records_repo.insert_if_absent({
                "user": user, "id": "rec_" + hashlib.sha256(dedupe.encode()).hexdigest()[:20], "dedupeKey": dedupe,
                "deviceId": device_id, "date": timeutil.local_date(p_start, tz), "startAt": p_start.isoformat(),
                "endAt": p_end.isoformat(), "startTs": p_start, "endTs": p_end, "seconds": (p_end - p_start).total_seconds(),
                "app": rec.app[:MAX_APP], "title": rec.title[:MAX_TITLE], "titleIsPseudonym": rec.titleIsPseudonym,
                "block": given, "blockKey": key, "keySource": source, "rv": None,  # 都不盖：上传时没按服务端规则匹配过，读时（块还没分类才）检查一次再盖（block_key.py）
                 "receivedAt": now, "ledgerEventId": None})
        accepted, duplicates = accepted + fresh, duplicates + (not fresh)
    if accepted:
        reconcile.kick(only=touched)
    return {"accepted": accepted, "duplicates": duplicates, "ignored": ignored, "rejected": rejected}
