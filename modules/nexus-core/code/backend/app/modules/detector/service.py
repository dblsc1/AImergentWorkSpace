"""检测程序设置（契约 v2.5；设置文档的字段、缺省、校验以 contracts/detector.settings.v1 为准）。

关键取舍：

- **严格写、宽松读**：写入按 schema 严格校验（未知键、类型不对都 422）；存的是补齐缺省后的完整文档，
  检测程序读到比自己新的键会忽略。**强制脱敏不在 schema 里**——想关它的键就是未知键。
- **设备令牌只能读**：PUT / DELETE 带 ``Authorization: Bearer`` 一律 403。依据 auth.gate v1.2
  「带了 Bearer 就只看令牌」：检测程序只能带着令牌过门，浏览器里的页面走 cookie 不带 Bearer。
  前提是网关对 /api/core/ 原样转发 Authorization（deploy/test/tokens.sh 从门外验）。
- 设置不是事实：不进台账、投影、导出、快照恢复。
"""

from __future__ import annotations

import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from ...tenant import current as current_tenant
from ..activity import service as activity_service
from . import repo

MAX_BODY = 64 * 1024
RESTART_TTL = timedelta(minutes=5)  # v2.27：重启请求过了这么久没人理就作废（离线时点的，几小时后别又把 ActivityWatch 拉起来）
GAP = timedelta(seconds=90)  # v2.28：health 之间隔了这么久 = 检测程序这段时间没在报告（离线）
MAX_LOG_BYTES, MAX_LOG_LINES, MAX_LOG_LINE = 4096, 40, 300
MAX_BODY_WITH_LOG = 32 * 1024  # 带 startLog 的健康报告（JSON 转义后）最多这么大；不带的仍是 1024
EVENT_TTL = timedelta(days=30)
_UNSAFE = {"Cc", "Cf", "Co", "Cs", "Zl", "Zp"}
DEVICE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# RE2（Go regexp）不支持、Python re 支持的构造：前后查找、反向引用、条件组、原子组、占有量词。
# 服务端先拒，免得检测程序那边只能跳过。这是尽力校验：检测程序自己再用 Go 编译一次，
# 编译不过的那条跳过并写日志（契约「校验」）。
_NOT_RE2 = re.compile(r"\(\?<?[=!]|\\[1-9]|\(\?P=|\(\?\(|\(\?>|(?<!\\)[*+?}]\+")
# Go 有、Python 没有的写法，编译检查前换成 Python 认得的等价物（只为检查语法，不存）：
# Unicode 类 \p{Han} / \pL、命名组 (?<name>…)、文本末尾 \z。
_RE2_ONLY = (
    (re.compile(r"\\[pP](\{[^}]*\}|[A-Za-z])"), "x"),
    (re.compile(r"\(\?<(?=[A-Za-z_])"), "(?P<"),
    (re.compile(r"\\z"), r"\\Z"),
    # Go 的非贪婪翻转标志 U：Python 没有。只为检查语法，去掉它（(?U) 整个去掉，(?iU:…) → (?i:…)）
    (re.compile(r"\(\?([imsU]*)U([imsU]*)(:|\))"), lambda m: "" if m[3] == ")" and not (m[1] + m[2]) else
     "(?" + (m[1] + m[2]).replace("U", "") + m[3]),
)


def re2_error(p: str) -> str | None:
    """正则能不能给检测程序（Go RE2）用：能 → None，不能 → 一句人话（以「正则」开头）。
    detector.rules.v1 的 app / title 也用它。"""
    probe = p
    for pat, repl in _RE2_ONLY:
        probe = pat.sub(repl, probe)
    if _NOT_RE2.search(probe):  # 查换过的：\p{Han}+ 的「}+」不是占有量词
        return f"正则用了 RE2 不支持的写法（前后查找 / 反向引用 / 条件组 / 原子组 / 占有量词）：{p!r}"
    try:
        re.compile(probe)
    except re.error as exc:
        return f"正则写错了：{exc}"
    return None


class ForbiddenError(RuntimeError):
    """设备令牌想改设置。main.py 映射成 403。"""


class InvalidSettingsError(ValueError):
    """设置文档不合 schema。main.py 映射成 422。"""


class TooLargeError(ValueError):
    """请求体超过 64 KiB。main.py 映射成 413。"""


class NotFoundError(LookupError):
    """设备不存在（restart）。main.py 映射成 404。"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


_Name = Annotated[StrictStr, Field(min_length=1, max_length=64)]
_Pattern = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class Privacy(_Strict):
    paths: Literal["full", "half", "off"] = "full"
    pathWhitelist: list[_Pattern] = Field(default_factory=list, max_length=20)
    titles: Literal["keep", "pseudonymize", "drop"] = "keep"
    appOnly: bool = True
    appOnlyApps: Annotated[list[_Name], Field(max_length=200)] | None = None
    browser: Literal["domain", "full", "off"] = "domain"
    queryStrings: bool = True
    emails: bool = True
    phones: bool = True
    addresses: bool = True
    ips: bool = True
    usernames: bool = True
    longNumbers: bool = True

    @field_validator("pathWhitelist")
    @classmethod
    def _regexes(cls, v: list[str]) -> list[str]:
        for i, p in enumerate(v):
            if err := re2_error(p):
                raise ValueError(f"第 {i + 1} 条{err}")
        return v


class Idle(_Strict):
    afkThresholdMinutes: int = Field(0, ge=0, le=240)
    audibleAsPresent: bool = False
    focusAppsEnabled: bool = False
    focusApps: Annotated[list[_Name], Field(max_length=200)] | None = None
    focusMaxMinutes: int = Field(60, ge=1, le=480)
    idleSuggestions: bool = False


class DetectorSettings(_Strict):
    schemaVersion: StrictInt  # 严格整数：true、1.0 都不收（Literal[1] 会按相等放过它们）
    privacy: Privacy = Field(default_factory=Privacy)
    idle: Idle = Field(default_factory=Idle)
    # v1.1 追加（只增）：在场心跳开关。null = 用检测程序本机配置的 presence。
    presence: bool | None = None
    # v1.2 追加（只增）：终端按标签页分段。名单 null = 用检测程序本机配置的名单（缺省内置终端名单）。
    segmentByTitle: bool = True
    segmentByTitleApps: Annotated[list[_Name], Field(max_length=200)] | None = None
    # v1.3 追加（只增）：「允许 AI 管理进行中的任务」，行为见 nexus-core 契约 v2.14。只在网页上有。
    autoTrack: bool = False

    @field_validator("schemaVersion")
    @classmethod
    def _v1(cls, v: int) -> int:
        if v != 1:
            raise ValueError("本端点只收 schemaVersion 1")
        return v


# v2.27：检测程序报的健康状态。没有任何自由文本字段——本机的标题、路径、错误串到不了服务端。
_Reason = Literal["not_found", "spawn_error", "exited_early", "not_up", "no_display", "state_unwritable"]


_AutoReason = Literal["not_found", "spawn_error", "exited_early", "not_up", "no_display", "state_unwritable", "daily_cap"]


class _Restart(_Strict):
    nonce: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    result: Literal["started", "already_running", "failed", "unsupported"]
    reason: _Reason | None = None  # 失败原因（枚举）；详细日志只在那台电脑上


class Health(_Strict):
    awState: Literal["ok", "unreachable", "no_window", "paused", "disabled"]
    restart: _Restart | None = None
    autoStart: Literal["off", "idle", "started", "gave_up", "unsupported"] | None = None  # 最近一次自动拉起的结果
    autoReason: _AutoReason | None = None  # 比 restart.reason 多一个 daily_cap：24 小时自动拉起上限
    # v2.28：本机开关 awLogUpload 的状态（不报 = 老检测程序 = 按关处理）与脱敏后的启动日志尾巴（不可信文本）
    logUpload: StrictBool | None = None
    startLog: StrictStr | None = None

    @field_validator("startLog")
    @classmethod
    def _log(cls, v: str | None) -> str | None:
        # 报错信息里不带日志内容（也不进任何日志）；超限一律拒绝，不替检测程序截断
        if v is None:
            return v
        lines = v.split("\n")
        if not v.strip():
            raise ValueError("startLog 不能为空")
        if len(v.encode("utf-8")) > MAX_LOG_BYTES:
            raise ValueError(f"startLog 超过 {MAX_LOG_BYTES} 字节")
        if len(lines) > MAX_LOG_LINES:
            raise ValueError(f"startLog 超过 {MAX_LOG_LINES} 行")
        if any(len(ln) > MAX_LOG_LINE for ln in lines):
            raise ValueError(f"startLog 有行超过 {MAX_LOG_LINE} 字符")
        if any(ch != "\n" and unicodedata.category(ch) in _UNSAFE for ch in v):
            raise ValueError("startLog 含控制字符")
        return v

    @model_validator(mode="after")
    def _log_needs_switch(self) -> "Health":
        if self.startLog is not None and self.logUpload is not True:
            raise ValueError("startLog 只能在 logUpload 为 true 时带")
        return self


def _iso(t: datetime | None) -> str | None:
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).isoformat()


def _check_device(device_id: str) -> None:
    if not DEVICE_ID.fullmatch(device_id or ""):
        raise InvalidSettingsError(f"deviceId 格式非法：{(device_id or '')[:80]!r}（须匹配 {DEVICE_ID.pattern}）")


def _is_device_token(authorization: str | None) -> bool:
    return (authorization or "").strip().lower().startswith("bearer ")


def _out(device_id: str, doc: dict | None) -> dict:
    settings = (doc or {}).get("settings")
    return {"deviceId": device_id, "settings": settings,
            "updatedAt": _iso((doc or {}).get("updatedAt")) if settings is not None else None}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_settings(device_id: str, authorization: str | None) -> dict:
    _check_device(device_id)
    user = current_tenant()
    if _is_device_token(authorization):
        repo.touch_fetch(user, device_id, _now())
    return _out(device_id, repo.get(user, device_id))


def put_settings(device_id: str, authorization: str | None, body: bytes) -> dict:
    if _is_device_token(authorization):
        raise ForbiddenError("设备令牌只能读检测设置；请在 Cockpit「AI助理」页登录后修改")
    _check_device(device_id)
    if len(body) > MAX_BODY:
        raise TooLargeError(f"请求体超过 {MAX_BODY} 字节")
    try:
        raw = json.loads(body)
    except ValueError as exc:
        raise InvalidSettingsError(f"请求体不是合法 JSON：{exc}") from exc
    try:
        settings = DetectorSettings.model_validate(raw).model_dump()
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        raise InvalidSettingsError(f"{loc}: {err['msg']}") from exc
    user = current_tenant()
    repo.put(user, device_id, settings, _now())
    return _out(device_id, repo.get(user, device_id))


def _fresh(restart: dict, now: datetime) -> bool:
    t = restart.get("requestedAt")
    if t is not None and t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t is not None and now - t < RESTART_TTL


def put_health(device_id: str, body: bytes) -> dict:
    """v2.27：设备令牌与 cookie 都能调（同 GET settings）。"""
    _check_device(device_id)
    if len(body) > MAX_BODY_WITH_LOG:
        raise TooLargeError(f"请求体超过 {MAX_BODY_WITH_LOG // 1024} KiB")
    try:
        raw = json.loads(body)
    except ValueError as exc:
        if len(body) > 1024:
            raise TooLargeError("请求体超过 1024 字节") from exc
        raise InvalidSettingsError(f"请求体不是合法 JSON：{exc}") from exc
    # 超过 1 KiB 只放行真的带了非 null startLog 的报告（看解析后的内容，不看原始字节里有没有这个词）
    if len(body) > 1024 and not (isinstance(raw, dict) and raw.get("startLog") is not None):
        raise TooLargeError("请求体超过 1024 字节")
    try:
        h = Health.model_validate(raw)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise InvalidSettingsError(f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}") from exc
    user, now = current_tenant(), _now()
    prev = (repo.get(user, device_id) or {}).get("health")
    events = _health_events(prev, h, now)
    if h.restart and repo.set_restart_result(user, device_id, h.restart.nonce, h.restart.result, h.restart.reason,
                                              now - RESTART_TTL, now):
        events.append(_ev("restart_result", now, reason=h.restart.reason, result=h.restart.result))
    # 关了开关（或老检测程序不报）= 删掉存着的日志
    repo.set_health(user, device_id, {"awState": h.awState, "at": now, "autoStart": h.autoStart,
                                       "autoReason": h.autoReason, "logUpload": h.logUpload is True},
                    start_log=h.startLog, clear_log=h.logUpload is not True)
    r = (repo.get(user, device_id) or {}).get("restart") or {}
    if r.get("result") is None and r and not _fresh(r, now) and repo.mark_restart_expired(user, device_id, r["nonce"]):
        events.append(_ev("restart_expired", _aware(r["requestedAt"]) + RESTART_TTL))
    repo.add_events(user, device_id, events)
    pending = r.get("result") is None and r and _fresh(r, now)
    return {"restartNonce": r["nonce"] if pending else None}


def _ev(kind: str, at: datetime, end: datetime | None = None, reason: str | None = None,
        result: str | None = None) -> dict:
    return {"kind": kind, "at": at, **({"endAt": end} if end else {}), **({"reason": reason} if reason else {}),
            **({"result": result} if result else {})}


_AW_EVENT = {"unreachable": "aw_unreachable", "no_window": "aw_no_window", "paused": "aw_paused", "disabled": "aw_disabled"}


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _health_events(prev: dict | None, h: Health, now: datetime) -> list[dict]:
    """这份报告相对上一份的**变化**（不是每份都记）：静默过久、ActivityWatch 状态变了、自动拉起的结果变了。
    第一份报告：状态不是 ok 才记一条（没有「变了」可说）。"""
    out: list[dict] = []
    if prev and now - _aware(prev["at"]) > GAP:
        out.append(_ev("detector_offline", _aware(prev["at"]), now))
    before = prev["awState"] if prev else "ok"
    if h.awState != before:
        out.append(_ev(_AW_EVENT.get(h.awState, "aw_recovered"), now))
    if (h.autoStart, h.autoReason) != ((prev or {}).get("autoStart"), (prev or {}).get("autoReason")):
        if h.autoStart == "gave_up":
            out.append(_ev("auto_gave_up", now, reason=h.autoReason))
        elif h.autoStart == "started" and (prev or {}).get("autoStart") != "started":
            out.append(_ev("auto_started", now))
        elif h.autoReason and h.autoStart != "started":
            out.append(_ev("auto_failed", now, reason=h.autoReason))
    return out


def request_restart(device_id: str, authorization: str | None) -> dict:
    if _is_device_token(authorization):
        raise ForbiddenError("设备令牌不能触发重启；请在 Cockpit「AI助理」页登录后操作")
    _check_device(device_id)
    nonce = secrets.token_hex(8)
    if not repo.set_restart(current_tenant(), device_id, {"nonce": nonce, "requestedAt": _now()}):
        raise NotFoundError(f"没有这台设备：{device_id}")
    repo.add_events(current_tenant(), device_id, [_ev("restart_requested", _now())])
    return {"nonce": nonce}


def device_flags(user: str, device_id: str) -> dict:
    """activity 子边界的指定读路径（v2.14）：这台设备开没开 autoTrack、标题是不是换成代号上传。
    没在网页上存过设置 = 都是 False。"""
    s = (repo.get(user, device_id) or {}).get("settings") or {}
    return {"autoTrack": s.get("autoTrack") is True,
            "pseudonymize": (s.get("privacy") or {}).get("titles") == "pseudonymize"}


def delete_settings(device_id: str, authorization: str | None) -> None:
    if _is_device_token(authorization):
        raise ForbiddenError("设备令牌只能读检测设置；请在 Cockpit「AI助理」页登录后修改")
    _check_device(device_id)
    repo.clear(current_tenant(), device_id)


def list_devices() -> dict:
    user, now = current_tenant(), _now()
    rows: dict[str, dict] = {}

    def row(dev: str) -> dict:
        return rows.setdefault(dev, {"deviceId": dev, "lastUploadAt": None, "lastFetchAt": None,
                                     "hasSettings": False, "settingsUpdatedAt": None,
                                     "health": None, "restart": None})

    for dev, at in activity_service.last_uploads(user).items():
        row(dev)["lastUploadAt"] = _iso(at)
    for doc in repo.all_devices(user):
        r = row(doc["deviceId"])
        r["lastFetchAt"] = _iso(doc.get("lastFetchAt"))
        if doc.get("settings") is not None:
            r["hasSettings"], r["settingsUpdatedAt"] = True, _iso(doc.get("updatedAt"))
        if h := doc.get("health"):
            r["health"] = {"awState": h["awState"], "at": _iso(h["at"]), "autoStart": h.get("autoStart"),
                           "autoReason": h.get("autoReason"), "logUpload": h.get("logUpload") is True}
        if q := doc.get("restart"):
            r["restart"] = {"nonce": q["nonce"], "requestedAt": _iso(q["requestedAt"]),
                            "result": q.get("result"), "reason": q.get("reason"),
                            "expired": q.get("result") is None and not _fresh(q, now),
                            "resultAt": _iso(q.get("resultAt"))}

    def recent(r: dict) -> str:
        return max(r["lastUploadAt"] or "", r["lastFetchAt"] or "", r["settingsUpdatedAt"] or "")

    # now：服务端时钟。面板拿它算 health.at 的新旧，不依赖浏览器时钟准不准
    return {"devices": sorted(rows.values(), key=recent, reverse=True), "now": _iso(now)}


def incidents(device_id: str, limit: int) -> dict:
    """v2.28：这台设备最近的事故（新的在前，≤ 30 天）+ 此刻是否离线（读时算，不存）+ 启动日志开关与（开着时的）文本。
    没有这台设备 404。事件只有枚举与时间，没有自由文本；startLog 是检测程序脱敏后上传的不可信文本，原样转交。"""
    _check_device(device_id)
    user, now = current_tenant(), _now()
    doc = repo.get(user, device_id)
    if doc is None:
        raise NotFoundError(f"没有这台设备：{device_id}")
    h = doc.get("health")
    seen = _aware(h["at"]) if h else None
    # 同一时刻的事件后记的在前（反过来排再稳定排序）
    evs = sorted((e for e in reversed(repo.get_events(user, device_id)) if now - _aware(e["at"]) < EVENT_TTL),
                 key=lambda e: _aware(e["at"]), reverse=True)[:limit]
    log = doc.get("startLog") if h and h.get("logUpload") is True else None
    return {
        "deviceId": device_id, "now": _iso(now), "lastReportAt": _iso(seen),
        "offline": None if seen is None else now - seen > GAP,
        "health": None if h is None else {"awState": h["awState"], "autoStart": h.get("autoStart"),
                                          "autoReason": h.get("autoReason")},
        "logUpload": bool(h and h.get("logUpload") is True),
        "startLog": None if not log else {"text": log["text"], "at": _iso(log["at"])},
        "events": [{"kind": e["kind"], "at": _iso(e["at"]), "endAt": _iso(e.get("endAt")),
                    "durationSeconds": int((_aware(e["endAt"]) - _aware(e["at"])).total_seconds()) if e.get("endAt") else None,
                    "reason": e.get("reason"), "result": e.get("result")} for e in evs],
    }
