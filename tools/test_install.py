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
    # assistant consumes agent.chat.v1 → 拉上 agent，agent consumes mcp.tools.v1 → 拉上 mcp（CI 同）
    plan = install.resolve(["hive", "ring", "assistant"])
    generate.emit(install.ROOT, plan, tmp_path)
    compose = yaml.safe_load((tmp_path / "docker-compose.yml").read_text(encoding="utf-8"))
    nginx = _render((tmp_path / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8"))
    return plan, compose, nginx


def _render(template: str, base: str = "/") -> str:
    """模拟 nginx 镜像的 envsubst：只换站点前缀（缺省 /，即不挂子路径）。"""
    return template.replace("${HONEYCOMB_BASE_PATH}", base)


def _location(nginx: str, head: str) -> str:
    """取出 `location <head> {` 那一段（到第一个单独一行的 `    }`）。"""
    start = nginx.index(f"    location {head} {{")
    return nginx[start:nginx.index("\n    }", start)]


def test_plan_resolves_single_file_specs_and_pulls_in_nexus_core(out):
    plan, _, _ = out
    assert plan["modules"] == ["hive", "ring", "assistant", "nexus-core", "agent", "mcp"]
    assert "contracts.design-tokens.v1" in plan["specs"]
    assert "contracts.timer-ring-visual.v1" in plan["specs"]


def test_static_modules_are_mounted_not_run(out):
    _, compose, _ = out
    services = compose["services"]
    assert not {"hive", "ring", "assistant"} & set(services), "纯前端不该起容器"
    assert set(services) == {"nexus-core", "agent", "mcp", "auth", "mongo", "web"}
    volumes = services["web"]["volumes"]
    for mount in (
        "../../modules/hive/code/frontend:/usr/share/nginx/html/hive:ro",
        "../../modules/ring/code/frontend:/usr/share/nginx/html/ring:ro",
        "../../modules/assistant/code/frontend:/usr/share/nginx/html/assistant:ro",
        "${HONEYCOMB_LOGIN_DIR:-../../contracts/auth.gate.v1/stub/web}:/usr/share/nginx/html/login:ro",
        "../../modules/nginx-docker/static:/usr/share/nginx/html/__cockpit:ro",
    ):
        assert mount in volumes


def test_frontends_are_gated_and_login_is_not(out):
    _, _, nginx = out
    for prefix in ("/hive/", "/ring/", "/assistant/"):
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
    assert "proxy_pass http://${AUTH_UPSTREAM}/api/auth/;" in _location(nginx, "/api/auth/")
    assert "proxy_pass http://${AUTH_UPSTREAM}/api/auth/verify;" in _location(nginx, "= /__auth_verify")
    assert "${HONEYCOMB_EXTRA_ROUTES_DIR:-../nginx/extra}:/etc/nginx/templates/extra:ro" in web["volumes"]
    assert "include /etc/nginx/conf.d/extra/*.conf;" in nginx
    assert "location @to_login" in nginx, "gateway.v1 冻结的名字"


def test_navbar_tabs_follow_installed_frontends(tmp_path):
    """只装 ring：顶栏只有计时一个页签，不出点了 404 的死页签。"""
    plan = install.resolve(["ring"])
    generate.emit(install.ROOT, plan, tmp_path)
    nginx = _render((tmp_path / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8"))
    assert """set $honeycomb_nav '{"home":"/","timer":"/ring/","tabs":[{"href":"/ring/","label":"计时"}]}';""" in nginx


def test_navbar_three_tabs_same_in_hand_written_and_generated(out):
    """缺省组装的顶栏：任务、计时、AI助理，手写与生成的两份逐字相同。"""
    _, _, nginx = out
    hand = _render((install.ROOT / "deploy" / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8"))
    nav = """set $honeycomb_nav '{"home":"/hive/","timer":"/ring/","tabs":[{"href":"/hive/","label":"任务"},""" \
          """{"href":"/ring/","label":"计时"},{"href":"/assistant/","label":"AI助理"}]}';"""
    assert nav in nginx and nav in hand


def test_ring_alone_no_longer_pulls_in_the_chat_backend():
    """聊天搬去 AI助理页之后，只装 ring 不再带上 agent / mcp；装 assistant 才带。"""
    assert "agent" not in install.resolve(["ring"])["modules"]
    assert {"agent", "mcp"} <= set(install.resolve(["assistant"])["modules"])


def test_hand_written_gateway_has_the_same_frozen_surface():
    hand = (install.ROOT / "deploy" / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8")
    for needle in (
        "location = /__auth_verify {", "location @to_login {",
        "proxy_pass http://${AUTH_UPSTREAM}/api/auth/;", 'set $honeycomb_base "${HONEYCOMB_BASE_PATH}";',
        "include /etc/nginx/conf.d/extra/*.conf;", 'proxy_set_header X-Nexus-Tenant "";',
    ):
        assert needle in hand, needle


def test_auth_accounts_file_lives_in_a_declared_named_volume(out):
    _, compose, _ = out
    auth = compose["services"]["auth"]
    assert "honeycomb_auth_data:/data" in auth["volumes"]
    assert auth["environment"]["AUTH_USERS_FILE"] == "/data/users.json"
    assert set(compose["volumes"]) == {"honeycomb_mongo_data", "honeycomb_auth_data", "honeycomb_agent_data"}
    hand = yaml.safe_load((install.ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert set(hand["volumes"]) == set(compose["volumes"])
    assert compose["services"]["nexus-core"]["environment"]["NEXUS_TENANT_STRICT"] == \
        hand["services"]["nexus-core"]["environment"]["NEXUS_TENANT_STRICT"]


def test_every_public_location_hangs_under_the_base_path(tmp_path):
    """挂子路径（/Cockpit/）时，对外的每一条 location 都在前缀下，转给后端时去掉前缀；
    只有容器健康检查与内部子请求例外。手写与生成的两份都查。"""
    plan = install.resolve(["hive", "ring", "assistant", "mcp"])
    generate.emit(install.ROOT, plan, tmp_path)
    hand = (install.ROOT / "deploy" / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8")
    gen = (tmp_path / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8")
    for name, tpl in (("hand", hand), ("generated", gen)):
        nginx = _render(tpl, "/Cockpit/")
        for line in nginx.splitlines():
            t = line.strip()
            if t.startswith("location ") and not t.startswith("location @"):
                path = t.split()[2] if t.split()[1] == "=" else t.split()[1]
                assert path.startswith("/Cockpit/") or path in ("/healthz", "/__auth_verify"), (name, t)
            if t.startswith("return 302"):
                assert t.split()[2].startswith("/Cockpit/"), (name, t)
        assert "proxy_pass http://nexus-core:8000/api/core/;" in nginx, name
        assert '"home":"/Cockpit/hive/"' in nginx, name


def _bridge_checks(block: str, name: str) -> None:
    for needle in ("include /etc/nginx/honeycomb/gate.inc;", "resolver 127.0.0.11", 'proxy_set_header Cookie "";',
                   'proxy_set_header Authorization "";', "proxy_buffering off;"):
        assert needle in block, (name, needle)
    assert "proxy_pass http://$" in block, (name, "上游须运行期解析（变量 proxy_pass）")


def test_ai_bridge_routes_hand_and_generated(out):
    """gateway.v1 第八节：过门、清凭据头、关缓冲、运行期解析；网关不等 mcp，mcp 与网关同在 AI 桥内网。"""
    _, compose, nginx = out
    hand = _render((install.ROOT / "deploy" / "nginx" / "templates" / "default.conf.template").read_text(encoding="utf-8"))
    for prefix in ("/api/mcp/", "/api/agent/"):
        _bridge_checks(_location(hand, prefix), "hand " + prefix)
    assert 'set $honeycomb_agent "${AGENT_UPSTREAM}";' in hand
    block = _location(nginx, "/api/mcp/")
    _bridge_checks(block, "generated")
    assert "rewrite ^/api/mcp/(.*)$ /api/mcp/$1 break;" in block
    web, mcp = compose["services"]["web"], compose["services"]["mcp"]
    assert "mcp" not in web["depends_on"], "缺了 mcp 网关也得起"
    assert set(web["networks"]) == set(mcp["networks"]) == {"honeycomb-net", "honeycomb-agent-net"}
    assert compose["networks"]["honeycomb-agent-net"] == {"driver": "bridge"}
    assert compose["services"]["nexus-core"]["networks"] == ["honeycomb-net"], "nexus-core 不上 AI 桥内网"
    # 聊天后端只在 AI 桥内网（够不着 nexus-core / auth / mongo），网关不等它；聊天记录在自己的卷里
    agent = compose["services"]["agent"]
    assert agent["networks"] == ["honeycomb-agent-net"] and "agent" not in web["depends_on"]
    assert agent["volumes"] == ["honeycomb_agent_data:/data"] and "honeycomb_agent_data" in compose["volumes"]
    assert 'set $bridge_' in _location(nginx, "/api/agent/") and "proxy_read_timeout 300s;" in _location(nginx, "/api/agent/")
    assert hand_c_agent_only_on_bridge()
    hand_c = yaml.safe_load((install.ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    assert "honeycomb-agent-net" in hand_c["networks"]
    for svc in ("nexus-core", "auth", "mongo"):
        assert "honeycomb-agent-net" not in hand_c["services"][svc]["networks"], svc
    assert "mcp" not in hand_c["services"]["web"].get("depends_on", {})
    assert hand_c["services"]["mcp"]["environment"]["NEXUS_TENANT_STRICT"] == \
        hand_c["services"]["nexus-core"]["environment"]["NEXUS_TENANT_STRICT"]


def hand_c_agent_only_on_bridge() -> bool:
    hand_c = yaml.safe_load((install.ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    rel = yaml.safe_load((install.ROOT / "release" / "docker-compose.yml").read_text(encoding="utf-8"))
    return all(c["services"]["agent"]["networks"] == ["honeycomb-agent-net"] for c in (hand_c, rel))


def test_bridge_route_upstream_env(tmp_path):
    """聊天后端那种可由 .env 换的上游：upstreamEnv → 模板里写变量、web 带缺省值、envsubst 放行。"""
    mod = tmp_path / "modules" / "agent"
    mod.mkdir(parents=True)
    (mod / "module.yaml").write_text(
        "kind: backend\nservice: {image: x, port: 8030, networks: [honeycomb-agent-net]}\n"
        "routes:\n  - {prefix: /api/agent/, upstream: /api/agent/, gated: true, bridge: true,"
        " upstreamEnv: AGENT_UPSTREAM, readTimeout: 300}\n", encoding="utf-8")
    stub = install.ROOT / "contracts" / "auth.gate.v1"
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "auth.gate.v1").symlink_to(stub)
    generate.emit(tmp_path, {"modules": ["agent"], "stubs": []}, tmp_path / "out")
    compose = yaml.safe_load((tmp_path / "out" / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["web"]["environment"]
    assert env["AGENT_UPSTREAM"] == "${AGENT_UPSTREAM:-agent:8030}"
    assert env["NGINX_ENVSUBST_FILTER"] == "^(AUTH_UPSTREAM|AGENT_UPSTREAM|HONEYCOMB_)"
    block = _location(_render((tmp_path / "out" / "nginx" / "templates" / "default.conf.template").read_text(
        encoding="utf-8")), "/api/agent/")
    assert 'set $bridge_0 "${AGENT_UPSTREAM}";' in block and "proxy_read_timeout 300s;" in block


def test_bridge_route_must_be_gated(tmp_path):
    mod = tmp_path / "modules" / "x"
    mod.mkdir(parents=True)
    (mod / "module.yaml").write_text(
        "kind: backend\nservice: {image: x, port: 1}\nroutes:\n  - {prefix: /api/x/, bridge: true}\n"
        "static:\n  - {prefix: /y/, root: ., gated: true}\n", encoding="utf-8")
    (tmp_path / "contracts").mkdir()
    (tmp_path / "contracts" / "auth.gate.v1").symlink_to(install.ROOT / "contracts" / "auth.gate.v1")
    with pytest.raises(generate.BadManifest, match="gated"):
        generate.emit(tmp_path, {"modules": ["x"], "stubs": []}, tmp_path / "out")
