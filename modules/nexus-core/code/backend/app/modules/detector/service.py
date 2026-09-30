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
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from ...tenant import current as current_tenant
from ..activity import service as activity_service
from . import repo

MAX_BODY = 64 * 1024
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
)


class ForbiddenError(RuntimeError):
    """设备令牌想改设置。main.py 映射成 403。"""


class InvalidSettingsError(ValueError):
    """设置文档不合 schema。main.py 映射成 422。"""


class TooLargeError(ValueError):
    """请求体超过 64 KiB。main.py 映射成 413。"""


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
            probe = p
            for pat, repl in _RE2_ONLY:
                probe = pat.sub(repl, probe)
            if _NOT_RE2.search(probe):  # 查换过的：\p{Han}+ 的「}+」不是占有量词
                raise ValueError(f"第 {i + 1} 条正则用了 RE2 不支持的写法（前后查找 / 反向引用 / 条件组 / 原子组 / 占有量词）：{p!r}")
            try:
                re.compile(probe)
            except re.error as exc:
                raise ValueError(f"第 {i + 1} 条正则写错了：{exc}") from exc
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

    @field_validator("schemaVersion")
    @classmethod
    def _v1(cls, v: int) -> int:
        if v != 1:
            raise ValueError("本端点只收 schemaVersion 1")
        return v


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


def delete_settings(device_id: str, authorization: str | None) -> None:
    if _is_device_token(authorization):
        raise ForbiddenError("设备令牌只能读检测设置；请在 Cockpit「AI助理」页登录后修改")
    _check_device(device_id)
    repo.clear(current_tenant(), device_id)


def list_devices() -> dict:
    user = current_tenant()
    rows: dict[str, dict] = {}

    def row(dev: str) -> dict:
        return rows.setdefault(dev, {"deviceId": dev, "lastUploadAt": None, "lastFetchAt": None,
                                     "hasSettings": False, "settingsUpdatedAt": None})

    for dev, at in activity_service.last_uploads(user).items():
        row(dev)["lastUploadAt"] = _iso(at)
    for doc in repo.all_devices(user):
        r = row(doc["deviceId"])
        r["lastFetchAt"] = _iso(doc.get("lastFetchAt"))
        if doc.get("settings") is not None:
            r["hasSettings"], r["settingsUpdatedAt"] = True, _iso(doc.get("updatedAt"))

    def recent(r: dict) -> str:
        return max(r["lastUploadAt"] or "", r["lastFetchAt"] or "", r["settingsUpdatedAt"] or "")

    return {"devices": sorted(rows.values(), key=recent, reverse=True)}
