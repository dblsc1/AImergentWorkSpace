"""配置的无弱默认值单测（M19 + 契约「日界与时区」v0.9 T1）：Mongo 两变量与
``NEXUS_TZ`` 均无默认值，缺失/空串/非法值立即失败且说清是谁。"""

from __future__ import annotations

import pytest
from zoneinfo import ZoneInfo

from app.config import ConfigError, load_settings

VALID = {
    "NEXUS_MONGO_URI": "mongodb://127.0.0.1:27017",
    "NEXUS_DB_NAME": "nexus_core_test",
    "NEXUS_TZ": "UTC",
}


def test_missing_mongo_uri_fails_loud_naming_the_variable():
    with pytest.raises(ConfigError, match="NEXUS_MONGO_URI"):
        load_settings({"NEXUS_DB_NAME": "x"})


def test_missing_db_name_fails_loud_naming_the_variable():
    with pytest.raises(ConfigError, match="NEXUS_DB_NAME"):
        load_settings({"NEXUS_MONGO_URI": "mongodb://127.0.0.1:27017"})


def test_empty_string_is_missing_too():
    """空串不算「设了」——弱默认值的另一种伪装。"""
    with pytest.raises(ConfigError, match="NEXUS_MONGO_URI"):
        load_settings({**VALID, "NEXUS_MONGO_URI": "  "})


def test_no_fallback_uri_anywhere():
    """全空 env → 立即失败；绝不静默连到某个「默认库」。"""
    with pytest.raises(ConfigError):
        load_settings({})


def test_valid_env_parses_and_binds_loopback_by_default():
    settings = load_settings(dict(VALID))
    assert settings.bind == "127.0.0.1:8000"
    assert settings.mongo_uri == VALID["NEXUS_MONGO_URI"]
    assert settings.db_name == VALID["NEXUS_DB_NAME"]
    assert settings.tz == ZoneInfo("UTC")


@pytest.mark.parametrize(
    "bind",
    ["127.0.0.1:abc", ":8000", "127.0.0.1", "127.0.0.1:70000", ""],
)
def test_invalid_bind_still_fails_loud(bind: str):
    with pytest.raises(ConfigError, match="NEXUS_BIND"):
        load_settings({**VALID, "NEXUS_BIND": bind})


# --------------------------------------------------- T1（契约「日界与时区」v0.9）


def test_missing_tz_fails_loud_naming_the_variable():
    """NEXUS_TZ 必填、无默认值——缺失即 die（同 mongo_uri/db_name 的纪律）。"""
    env = {k: v for k, v in VALID.items() if k != "NEXUS_TZ"}
    with pytest.raises(ConfigError, match="NEXUS_TZ"):
        load_settings(env)


def test_empty_tz_is_missing_too():
    with pytest.raises(ConfigError, match="NEXUS_TZ"):
        load_settings({**VALID, "NEXUS_TZ": "  "})


@pytest.mark.parametrize(
    "bad_tz",
    ["Not/AZone", "UTC+8", "上海", "", "GMT+08:00"],
)
def test_invalid_tz_fails_loud_and_names_the_value(bad_tz: str):
    """非法时区名必须 die 并点名取值，不许猜、不许悄悄回退 UTC。"""
    if not bad_tz.strip():
        pytest.skip("空串已由 test_empty_tz_is_missing_too 覆盖")
    with pytest.raises(ConfigError, match="NEXUS_TZ"):
        load_settings({**VALID, "NEXUS_TZ": bad_tz})


def test_invalid_tz_error_names_the_bad_value():
    """错误消息要点名具体取值，不是笼统的「格式错误」（契约「错误响应形状」同一纪律）。"""
    with pytest.raises(ConfigError, match="Not/AZone"):
        load_settings({**VALID, "NEXUS_TZ": "Not/AZone"})


def test_valid_iana_tz_name_parses_to_zoneinfo():
    settings = load_settings({**VALID, "NEXUS_TZ": "Asia/Shanghai"})
    assert settings.tz == ZoneInfo("Asia/Shanghai")
