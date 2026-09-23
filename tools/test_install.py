"""安装器的单测：解依赖 + 生成物的形状。不起容器——起容器的那一半在 CI 的
compose-smoke 里（手写与生成两份组装都真起一遍）。

跑法：pip install pyyaml pytest && python -m pytest -q tools
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import generate  # noqa: E402
import install  # noqa: E402


@pytest.fixture()
def out(tmp_path):
    plan = install.resolve(["hive", "ring"])
    generate.emit(install.ROOT, plan, tmp_path)
    compose = yaml.safe_load((tmp_path / "docker-compose.yml").read_text(encoding="utf-8"))
    nginx = (tmp_path / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8")
    return plan, compose, nginx


def _location(nginx: str, head: str) -> str:
    """取出 `location <head> {` 那一段（到第一个单独一行的 `    }`）。"""
    start = nginx.index(f"    location {head} {{")
    return nginx[start:nginx.index("\n    }", start)]


def test_plan_resolves_single_file_specs_and_pulls_in_nexus_core(out):
    plan, _, _ = out
    assert plan["modules"] == ["hive", "ring", "nexus-core"]
    assert "contracts.design-tokens.v1" in plan["specs"]
    assert "contracts.timer-ring-visual.v1" in plan["specs"]


def test_static_modules_are_mounted_not_run(out):
    _, compose, _ = out
    services = compose["services"]
    assert "hive" not in services and "ring" not in services, "纯前端不该起容器"
    assert set(services) == {"nexus-core", "auth", "mongo", "web"}
    volumes = services["web"]["volumes"]
    for mount in (
        "../../modules/hive/code/frontend:/usr/share/nginx/html/hive:ro",
        "../../modules/ring/code/frontend:/usr/share/nginx/html/ring:ro",
        "${HONEYCOMB_LOGIN_DIR:-../../contracts/auth.gate.v1/stub/web}:/usr/share/nginx/html/login:ro",
        "../../modules/nginx-docker/static:/usr/share/nginx/html/__cockpit:ro",
    ):
        assert mount in volumes


def test_frontends_are_gated_and_login_is_not(out):
    _, _, nginx = out
    for prefix in ("/hive/", "/ring/"):
        block = _location(nginx, prefix)
        assert "include /etc/nginx/honeycomb/gate.inc;" in block
        assert "include /etc/nginx/honeycomb/inject.inc;" in block, "前端要被注入共享顶栏"
        assert f"alias /usr/share/nginx/html{prefix}" in block
    login = _location(nginx, "/login/")
    assert "auth_request" not in login and "gate.inc" not in login, "登录页设门 = 谁都进不来"
    assert "inject.inc" not in login, "还没进门就给导航是错的"
    assert "location = / { return 302 /hive/; }" in nginx


def test_navbar_chip_degrades_instead_of_redirecting(out):
    _, _, nginx = out
    block = _location(nginx, "= /__cockpit/current")
    assert "proxy_pass http://nexus-core:8000/api/core/views/current;" in block
    assert "error_page 401 = @degraded_" in block
    assert "@to_login" not in block


def test_generated_matches_hand_written_service_set(out):
    _, compose, _ = out
    hand = yaml.safe_load((install.ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(hand["services"]) == set(compose["services"])


def test_gated_route_without_any_gate_fails_closed(tmp_path):
    """声明了要门、却找不到门的提供方：必须硬失败，不许生成一个裸奔的配置。"""
    mod = tmp_path / "modules" / "web-only"
    mod.mkdir(parents=True)
    (mod / "module.yaml").write_text(
        "kind: static\nstatic:\n  - {prefix: /x/, root: ., gated: true}\n", encoding="utf-8",
    )
    (tmp_path / "contracts").mkdir()
    with pytest.raises(generate.BadManifest, match="auth.gate"):
        generate.emit(tmp_path, {"modules": ["web-only"], "stubs": []}, tmp_path / "out")


def test_two_homes_is_an_error(tmp_path):
    mod = tmp_path / "modules"
    for name in ("a", "b"):
        (mod / name).mkdir(parents=True)
        (mod / name / "module.yaml").write_text(
            f"kind: static\nstatic:\n  - {{prefix: /{name}/, root: ., home: true}}\n", encoding="utf-8",
        )
    (tmp_path / "contracts").mkdir()
    with pytest.raises(generate.BadManifest, match="home"):
        generate.emit(tmp_path, {"modules": ["a", "b"], "stubs": []}, tmp_path / "out")


# ------------------------------------------------ gateway.v1 的冻结接口


def test_backend_tenant_header_is_always_set_by_the_gateway(out):
    """客户端自带的 X-Nexus-Tenant 不能到后端：每条转发都由网关覆盖。"""
    _, _, nginx = out
    assert "include /etc/nginx/honeycomb/gate.inc;" in _location(nginx, "/api/core/")
    chip = _location(nginx, "= /__cockpit/current")
    assert "proxy_set_header X-Nexus-Tenant $honeycomb_tenant;" in chip
    for head in ("= /__auth_verify", "/api/auth/"):
        assert 'proxy_set_header X-Nexus-Tenant "";' in _location(nginx, head)
    gate = (install.ROOT / "modules" / "nginx-docker" / "nginx" / "gate.inc").read_text(encoding="utf-8")
    assert "proxy_set_header X-Nexus-Tenant $honeycomb_tenant;" in gate


def test_auth_upstream_and_extra_routes_are_replaceable(out):
    _, compose, nginx = out
    web = compose["services"]["web"]
    assert web["environment"]["AUTH_UPSTREAM"] == "${AUTH_UPSTREAM:-auth:8010}"
    assert "proxy_pass http://${AUTH_UPSTREAM};" in _location(nginx, "/api/auth/")
    assert "proxy_pass http://${AUTH_UPSTREAM}/api/auth/verify;" in _location(nginx, "= /__auth_verify")
    assert "${HONEYCOMB_EXTRA_ROUTES_DIR:-../nginx/extra}:/etc/nginx/templates/extra:ro" in web["volumes"]
    assert "include /etc/nginx/conf.d/extra/*.conf;" in nginx
    assert "location @to_login" in nginx, "gateway.v1 冻结的名字"


def test_navbar_tabs_follow_installed_frontends(tmp_path):
    """只装 ring：顶栏只有计时一个页签，不出点了 404 的死页签。"""
    plan = install.resolve(["ring"])
    generate.emit(install.ROOT, plan, tmp_path)
    nginx = (tmp_path / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8")
    assert """set $honeycomb_nav '{"home":"/","timer":"/ring/","tabs":[{"href":"/ring/","label":"计时"}]}';""" in nginx


def test_hand_written_gateway_has_the_same_frozen_surface():
    hand = (install.ROOT / "deploy" / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8")
    for needle in (
        "location = /__auth_verify {", "location @to_login {",
        "proxy_pass http://${AUTH_UPSTREAM};", 'set $honeycomb_base "${HONEYCOMB_BASE_PATH}";',
        "include /etc/nginx/conf.d/extra/*.conf;", 'proxy_set_header X-Nexus-Tenant "";',
    ):
        assert needle in hand, needle


def test_auth_accounts_file_lives_in_a_declared_named_volume(out):
    _, compose, _ = out
    auth = compose["services"]["auth"]
    assert "honeycomb_auth_data:/data" in auth["volumes"]
    assert auth["environment"]["AUTH_USERS_FILE"] == "/data/users.json"
    assert set(compose["volumes"]) == {"honeycomb_mongo_data", "honeycomb_auth_data"}
    hand = yaml.safe_load((install.ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(hand["volumes"]) == set(compose["volumes"])
    assert compose["services"]["nexus-core"]["environment"]["NEXUS_TENANT_STRICT"] == \
        hand["services"]["nexus-core"]["environment"]["NEXUS_TENANT_STRICT"]
