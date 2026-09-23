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
    nginx = (tmp_path / "nginx" / "honeycomb.conf").read_text(encoding="utf-8")
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
        "../../contracts/auth.gate.v1/stub/web:/usr/share/nginx/html/login:ro",
    ):
        assert mount in volumes


def test_frontends_are_gated_and_login_is_not(out):
    _, _, nginx = out
    for prefix in ("/hive/", "/ring/"):
        block = _location(nginx, prefix)
        assert "auth_request /__auth_verify;" in block
        assert f"alias /usr/share/nginx/html{prefix}" in block
    assert "auth_request" not in _location(nginx, "/login/"), "登录页设门 = 谁都进不来"
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
