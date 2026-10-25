import pytest

import fake_nexus
import mcp_server
import tools
from fake_nexus import Fake


@pytest.fixture(scope="session")
def servers():
    nexus, mcp = fake_nexus.serve(fake_nexus.FakeNexus), fake_nexus.serve(mcp_server.Handler)
    tools.NEXUS_CORE_URL = f"http://127.0.0.1:{nexus.server_port}"
    yield f"http://127.0.0.1:{mcp.server_port}/api/mcp/"
    nexus.shutdown()
    mcp.shutdown()


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    Fake.requests.clear()
    Fake.scopes.clear()
    Fake.bodies.clear()
    Fake.override = None
    monkeypatch.setenv("NEXUS_TZ", "UTC")
    monkeypatch.setattr(tools, "_LEARNED", (float("inf"), None))   # 默认不去 nexus-core 学时区（免得多一条请求）；要测的用例自己放开
    tools._SETTINGS.clear()   # permissions 的设置缓存按租户，测试之间不留
    monkeypatch.setattr(mcp_server, "STRICT", False)
    monkeypatch.setattr(mcp_server, "ALLOWED_ORIGINS", set())
