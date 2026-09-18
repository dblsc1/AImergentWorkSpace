"""唯一读 env 的地方（module_docs/rules.md §3 / §7.5）。

**禁弱默认值**：任何配置缺失或非法**立即失败并说清哪个变量错在哪**，
不打条日志然后带着坏配置继续跑。

切片 1 真实现落地：开始读 ``NEXUS_MONGO_URI`` / ``NEXUS_DB_NAME``，
**无默认值，缺失/空串 → ConfigError，进程起不来**。
``NEXUS_MOCK_STATE`` 已随 fixtures.py 一起删除（rules.md §7.5）。
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: ``NEXUS_BIND`` 的默认监听地址。**绝不得默认成通配地址（对外监听）**
#: ——contract.md「配置与密钥」。这里刻意不写出那个地址的字面量：
#: M6 的检测脚本会 grep 它，注释里出现就是假阳性。
DEFAULT_BIND = "127.0.0.1:8000"

#: 认证后置期的兼容锚点：一切数据照常带 ``user`` 字段，写死这个值。
#: **今天省认证，不省字段**——将来接 JWT 只替换注入来源，零数据迁移。
LOCAL_USER = "u_local"

#: 来源凭据头（契约 v1.6「actor 来源区分与高风险二次设防」）。
#: 它**不是**认证凭据（认证仍归网关的 ``auth_request``），只回答
#: "这个请求是哪一类客户端发来的"。
CLIENT_TOKEN_HEADER = "X-Nexus-Client-Token"

#: 来源凭据的最短长度。短 token 是弱默认值的另一种形态——
#: "abc" 能通过配置校验，然后在第一次爆破里失守。
MIN_CLIENT_TOKEN_LEN = 16

_TRUE_WORDS = ("1", "true")
_FALSE_WORDS = ("0", "false")


class ConfigError(RuntimeError):
    """配置缺失或非法。抛出即启动失败——这就是「立即失败」。"""


@dataclass(frozen=True)
class Settings:
    """进程级配置快照。字段全部来自 env，别处不许再读 env。"""

    bind_host: str
    bind_port: int
    mongo_uri: str
    db_name: str
    tz: ZoneInfo
    #: AI 受控工具层的来源凭据（v1.6）。None＝没有调用方会被判成 ``ai`` 来源。
    ai_client_token: str | None = None
    #: 人路径（网关注入）的来源凭据（v1.6）。**绝不得落进受控层可及的文件系统**。
    human_client_token: str | None = None
    #: 严格模式：高风险写要求持有人路径凭据（v1.6）。默认关，见契约「剩余缺口」。
    actor_strict: bool = False

    @property
    def bind(self) -> str:
        return f"{self.bind_host}:{self.bind_port}"

    @property
    def actor_guard(self) -> str:
        """``/api/core/health`` 的 ``actorGuard`` 字段（契约 v1.6）。"""
        return "strict" if self.actor_strict else "lenient"


def _parse_bind(raw: str) -> tuple[str, int]:
    """把 ``host:port`` 拆成 (host, port)；任何一处不合规都立即失败。"""
    if raw.count(":") != 1:
        raise ConfigError(
            f"NEXUS_BIND 非法：取值 {raw!r} 不是 'host:port' 形式"
            f"（应形如 {DEFAULT_BIND!r}）"
        )
    host, _, port_text = raw.partition(":")
    host = host.strip()
    if not host:
        raise ConfigError(f"NEXUS_BIND 非法：取值 {raw!r} 缺少 host 部分")
    if not port_text.isdigit():
        raise ConfigError(
            f"NEXUS_BIND 非法：端口 {port_text!r} 不是整数（来自 NEXUS_BIND={raw!r}）"
        )
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise ConfigError(
            f"NEXUS_BIND 非法：端口 {port} 不在 1–65535 内（来自 NEXUS_BIND={raw!r}）"
        )
    return host, port


def _require(source: Mapping[str, str], key: str, purpose: str) -> str:
    """必填变量：缺失或空串都立即失败。**没有回退值**——
    弱默认值会让配置错误在生产上表现为「连到了一个空库」而不是「启动失败」。
    """
    value = (source.get(key) or "").strip()
    if not value:
        raise ConfigError(
            f"{key} 缺失或为空：{purpose}。本变量无默认值，必须显式设置"
        )
    return value


def _parse_tz(raw: str) -> ZoneInfo:
    """``NEXUS_TZ`` 必须是可识别的 IANA 时区名（如 ``Asia/Shanghai``）。

    契约「日界与时区」v0.9：不给默认值是有意的——猜错时区会**静默**把工作记到
    错误的日子，而数据一旦按错误日界落库，之后每次统计都继承这个错。非法值
    与缺失同等对待：立即失败，且点名取值（不是笼统的「格式错误」）。
    """
    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(
            f"NEXUS_TZ 非法：取值 {raw!r} 不是可识别的 IANA 时区名"
            f"（应形如 'Asia/Shanghai'；缺失或猜错会把工作静默记到错误的日子）"
        ) from exc


def _optional_token(source: Mapping[str, str], key: str, purpose: str) -> str | None:
    """可选的来源凭据：没设＝该类来源不存在；设了就必须够长。

    **"没设"与"设了个弱值"是两回事**：前者是明确的姿态（这台部署暂时不区分
    该来源），后者是把门装上却用纸糊——所以只对后者立即失败。
    """
    value = (source.get(key) or "").strip()
    if not value:
        return None
    if len(value) < MIN_CLIENT_TOKEN_LEN:
        raise ConfigError(
            f"{key} 太短：{len(value)} 字符，至少 {MIN_CLIENT_TOKEN_LEN}（{purpose}）。"
            f"短 token 与弱默认值是同一种病"
        )
    return value


def _parse_bool(source: Mapping[str, str], key: str, default: str, purpose: str) -> bool:
    """布尔开关：取值只认 0/1/true/false，其余立即失败。

    **不许"看不懂就当 false"**——把 ``NEXUS_ACTOR_STRICT=yes`` 静默读成"关"，
    等于运维以为设防开着、实际没开，这正是"静默跳过后报成功"的反模式。
    """
    raw = (source.get(key) or default).strip().lower()
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    raise ConfigError(
        f"{key} 非法：取值 {raw!r} 不是 "
        f"{'/'.join(_TRUE_WORDS + _FALSE_WORDS)} 之一（{purpose}）"
    )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """从 ``env``（默认真实进程环境）读出配置。

    测试传入显式 dict 即可，**不需要也不许**在 config.py 之外碰 ``os.environ``。
    """
    source: Mapping[str, str] = os.environ if env is None else env
    host, port = _parse_bind(source.get("NEXUS_BIND", DEFAULT_BIND))
    # 顺序保持与既有检查一致（先 mongo_uri 再 db_name 再 tz）：多个变量同时缺失时，
    # 报出的是「先声明的那个」，测试对着这个顺序断言，别悄悄打乱。
    mongo_uri = _require(source, "NEXUS_MONGO_URI", "Mongo 连接串")
    db_name = _require(source, "NEXUS_DB_NAME", "库名")
    tz_name = _require(
        source, "NEXUS_TZ",
        "日界与「今天」使用的 IANA 时区名（如 'Asia/Shanghai'）——契约「日界与时区」v0.9",
    )
    ai_token = _optional_token(
        source, "NEXUS_AI_CLIENT_TOKEN", "AI 受控工具层的来源凭据，契约 v1.6",
    )
    human_token = _optional_token(
        source, "NEXUS_HUMAN_CLIENT_TOKEN", "人路径（网关注入）的来源凭据，契约 v1.6",
    )
    if ai_token and human_token and hmac.compare_digest(ai_token, human_token):
        raise ConfigError(
            "NEXUS_AI_CLIENT_TOKEN 与 NEXUS_HUMAN_CLIENT_TOKEN 相同——"
            "两把钥匙一模一样就分不出两个人，来源区分整节失效（契约 v1.6）"
        )
    actor_strict = _parse_bool(
        source, "NEXUS_ACTOR_STRICT", "0", "严格模式：高风险写要求人路径凭据，契约 v1.6",
    )
    if actor_strict and not human_token:
        # 开了严格模式却没有人路径凭据 = 前端的搬移/改期/删除全被 403 打死。
        # 这种配置必须炸在启动那一刻，不是炸在用户点删除那一刻。
        raise ConfigError(
            "NEXUS_ACTOR_STRICT=1 但 NEXUS_HUMAN_CLIENT_TOKEN 未设——"
            "严格模式下高风险写要求人路径凭据，缺了它前端的搬移/改期/删除会全部 403"
        )
    return Settings(
        bind_host=host,
        bind_port=port,
        mongo_uri=mongo_uri,
        db_name=db_name,
        tz=_parse_tz(tz_name),
        ai_client_token=ai_token,
        human_client_token=human_token,
        actor_strict=actor_strict,
    )


#: 进程启动时就解析——配置错了要在拉起服务那一刻炸，不是在第一个请求打进来时才炸。
settings = load_settings()
