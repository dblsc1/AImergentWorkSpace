"""actor 来源区分与高风险二次设防（contract.md v1.6，PRD F-ACTOR-1 波2 / F-API-3）。

**这一套测的是安全边界，不是校验规则。** 前提假设：调用方绕过了 ai-planner 的
受控工具层，直接打 HTTP。受控层的白名单是第一道防线，本模块是第二道——
所以每条用例都以"坏调用方能不能得逞"提问，而不是"好调用方走不走得通"。
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

API = "/api/core"
PLANNER = f"{API}/planner"

# 假凭据的取值刻意带 "test-only" 前缀：**一眼看得出不是真凭据**。
# 这不是为了讨好扫描器——2026-08-02 的 P0 正是「开发口令被原样贴进仓」，
# 而那份文档当时读起来和真凭据毫无区别。让假的看起来就是假的，
# 人和机器同时受益（checks/_common/18 的占位符白名单认这个形态）。
AI_TOKEN = "test-only-ai-0987654321abcdef"
HUMAN_TOKEN = "test-only-human-0987654321abcdef"
HEADER = "X-Nexus-Client-Token"

AI_HEAD = {HEADER: AI_TOKEN}
HUMAN_HEAD = {HEADER: HUMAN_TOKEN}


@pytest.fixture()
def tokens(monkeypatch):
    """给本进程配上两把来源凭据（生产由 env 注入，见契约「配置与密钥」）。"""
    from app import config  # noqa: PLC0415

    patched = dataclasses.replace(
        config.settings, ai_client_token=AI_TOKEN, human_client_token=HUMAN_TOKEN,
    )
    monkeypatch.setattr(config, "settings", patched)
    return patched


@pytest.fixture()
def strict(monkeypatch, tokens):
    from app import config  # noqa: PLC0415

    monkeypatch.setattr(config, "settings", dataclasses.replace(tokens, actor_strict=True))


def _mk_tree(client, **extra) -> tuple[dict, dict, dict]:
    zone = client.post(f"{PLANNER}/zones", json={"name": "示例分区一", **extra}).json()
    project = client.post(
        f"{PLANNER}/projects", json={"zoneId": zone["id"], "name": "示例项目二", **extra},
    ).json()
    task = client.post(
        f"{PLANNER}/tasks", json={"projectId": project["id"], "name": "背单词", **extra},
    ).json()
    return zone, project, task


# --------------------------------------------------------------- 一、来源判定


def test_ai_credential_forces_last_writer_ai_without_self_report(client, tokens):
    """带 AI 凭据、请求体一个 actor 都没写 → 服务端自己判成 ai。

    这是整节的要害：受控层**不需要**自报，服务端也**不信**自报。
    """
    zone, _, _ = _mk_tree(client)
    resp = client.post(
        f"{PLANNER}/projects",
        json={"zoneId": zone["id"], "name": "AI 建的项目"},
        headers=AI_HEAD,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["lastWriter"] == "ai"


def test_ai_credential_claiming_human_is_403(client, tokens):
    """带 AI 凭据却自报 human = 伪装，403，且**什么都没落库**。"""
    zone, _, _ = _mk_tree(client)
    before = len(client.get(f"{PLANNER}/projects").json())

    resp = client.post(
        f"{PLANNER}/projects",
        json={"zoneId": zone["id"], "name": "冒充人的项目", "actor": "human"},
        headers=AI_HEAD,
    )
    assert resp.status_code == 403, resp.text
    assert "伪装" in resp.json()["detail"]
    assert len(client.get(f"{PLANNER}/projects").json()) == before


def test_unknown_token_is_rejected_not_downgraded(client, tokens):
    """凭据不认识 → 403（fail-closed），**不许静默降级成"没带凭据"**。

    静默降级 = 用一个更宽松的身份接住了一次失败的鉴权，
    正是「静默跳过后报成功」那种反模式。
    """
    zone, _, _ = _mk_tree(client)
    resp = client.post(
        f"{PLANNER}/projects",
        json={"zoneId": zone["id"], "name": "拿错钥匙"},
        # 头值必须是 ASCII（HTTP 规范），所以这里不写中文——不是随手选的
        headers={HEADER: "not-any-of-our-keys-0987654321"},
    )
    assert resp.status_code == 403, resp.text
    assert HEADER in resp.json()["detail"]


def test_empty_header_counts_as_absent(client, tokens):
    """头存在但是空串 = 没带（网关变量未设时发的正是空串，那是运维事故不是攻击）。"""
    zone, _, _ = _mk_tree(client)
    resp = client.post(
        f"{PLANNER}/projects",
        json={"zoneId": zone["id"], "name": "空头"},
        headers={HEADER: ""},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["lastWriter"] == "human"


def test_human_credential_may_self_downgrade_to_ai(client, tokens):
    """"我是 ai" 是降权声明，谁说都信——前端代 AI 提议落库时用得着。"""
    zone, _, _ = _mk_tree(client)
    resp = client.post(
        f"{PLANNER}/projects",
        json={"zoneId": zone["id"], "name": "人替 AI 落的库", "actor": "ai"},
        headers=HUMAN_HEAD,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["lastWriter"] == "ai"


def test_unverified_keeps_v15_semantics(client, tokens):
    """未携带凭据的调用方（＝今天的前端）行为与 v1.5 **逐字不变**。

    这条不是"顺便测测"：v1.6 的向后兼容承诺全押在它身上——
    前端一行代码都没改，写路径必须原样能用。
    """
    _, _, task = _mk_tree(client)
    assert task["lastWriter"] == "human"

    patched = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"name": "改名"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["lastWriter"] == "human"

    declared = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"actor": "ai"})
    assert declared.json()["lastWriter"] == "ai"


def test_credentialed_patch_overrides_self_reported_actor(client, tokens):
    """携凭据的 PATCH：`lastWriter` 由来源强制覆盖，不看请求体不传就不改那套。"""
    _, _, task = _mk_tree(client)
    resp = client.patch(f"{PLANNER}/tasks/{task['id']}", json={"name": "AI 改名"}, headers=AI_HEAD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["lastWriter"] == "ai"


# ----------------------------------------------------- 二、高风险二次设防（F-API-3）


@pytest.mark.parametrize("headers", [AI_HEAD, None], ids=["ai凭据", "自报ai"])
def test_delete_with_actor_ai_is_403(client, tokens, headers):
    """删除是高风险：带 AI 凭据的删、以及诚实自报 `?actor=ai` 的删，一律拒。"""
    _, _, task = _mk_tree(client)
    url = f"{PLANNER}/tasks/{task['id']}"
    resp = (
        client.delete(url, headers=headers) if headers
        else client.delete(url, params={"actor": "ai"})
    )
    assert resp.status_code == 403, resp.text
    assert "高风险" in resp.json()["detail"]
    # 对象必须还在——设防拒的是动作，不是"拒了但已经删了"
    assert any(t["id"] == task["id"] for t in client.get(f"{PLANNER}/tasks").json())


def test_delete_by_human_path_still_works(client, tokens):
    """人的路径照常删得掉——设防不能把正常业务一起锁死。"""
    _, _, task = _mk_tree(client)
    assert client.delete(f"{PLANNER}/tasks/{task['id']}", headers=HUMAN_HEAD).status_code == 204


def test_human_credential_high_risk_patch_still_works(client, tokens):
    """人路径的**搬移与改期**照常放行，且真落库。

    上面那条只覆盖了 DELETE。搬移（改 `projectId`/`zoneId`）与改期（改 `plan`）
    同样在高风险表里，而它们走的是另一条判据分支（`HIGH_RISK_UPDATE_FIELDS`）——
    「删得掉」不蕴含「搬得动」。少了这条，设防把人的归类动作一起锁死时
    整套测试仍然全绿（未携凭据的旧用例走的是 `unverified` 分支，覆盖不到
    `source=human`）。
    """
    zone, project, task = _mk_tree(client)
    other_project = client.post(
        f"{PLANNER}/projects", json={"zoneId": zone["id"], "name": "另一个项目"},
        headers=HUMAN_HEAD,
    ).json()
    other_zone = client.post(
        f"{PLANNER}/zones", json={"name": "示例分区三"}, headers=HUMAN_HEAD,
    ).json()

    moved = client.patch(
        f"{PLANNER}/tasks/{task['id']}",
        json={"projectId": other_project["id"]}, headers=HUMAN_HEAD,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["projectId"] == other_project["id"]
    assert moved.json()["lastWriter"] == "human"

    rescheduled = client.patch(
        f"{PLANNER}/tasks/{task['id']}",
        json={"plan": {"start": "2026-08-01", "end": "2026-08-31"}}, headers=HUMAN_HEAD,
    )
    assert rescheduled.status_code == 200, rescheduled.text
    assert rescheduled.json()["plan"]["start"] == "2026-08-01"

    project_moved = client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"zoneId": other_zone["id"]}, headers=HUMAN_HEAD,
    )
    assert project_moved.status_code == 200, project_moved.text
    assert project_moved.json()["zoneId"] == other_zone["id"]

    # 读回一次：断言的是库里的状态，不是响应体自说自话
    stored = next(t for t in client.get(f"{PLANNER}/tasks").json() if t["id"] == task["id"])
    assert stored["projectId"] == other_project["id"]
    assert stored["plan"]["end"] == "2026-08-31"


@pytest.mark.parametrize(
    ("type_", "payload"),
    [
        ("tasks", {"projectId": "p_inbox"}),          # 搬移任务
        ("tasks", {"plan": {"start": "2026-08-01", "end": "2026-08-31"}}),  # 改期
        ("tasks", {"plan": None}),                     # 清空计划期也是改期
    ],
    ids=["搬移", "改期", "清计划期"],
)
def test_high_risk_task_patch_with_ai_is_403(client, tokens, type_, payload):
    _, _, task = _mk_tree(client)
    resp = client.patch(f"{PLANNER}/{type_}/{task['id']}", json=payload, headers=AI_HEAD)
    assert resp.status_code == 403, resp.text


def test_high_risk_project_patch_with_ai_is_403(client, tokens):
    """项目搬移（改 zoneId）与项目改期同样是高风险。"""
    zone, project, _ = _mk_tree(client)
    other = client.post(f"{PLANNER}/zones", json={"name": "示例分区三"}).json()
    moved = client.patch(
        f"{PLANNER}/projects/{project['id']}", json={"zoneId": other["id"]}, headers=AI_HEAD,
    )
    assert moved.status_code == 403, moved.text
    rescheduled = client.patch(
        f"{PLANNER}/projects/{project['id']}",
        json={"plan": {"start": "2026-08-01", "end": "2026-08-31"}},
        headers=AI_HEAD,
    )
    assert rescheduled.status_code == 403, rescheduled.text
    assert client.get(f"{PLANNER}/projects").json()[0]["zoneId"] == zone["id"]


@pytest.mark.parametrize(
    "payload",
    [{"name": "AI 改的标题"}, {"plannedWeight": 42}, {"done": True}, {"flags": ["x"]}],
    ids=["改标题", "调权重", "打勾", "贴纸"],
)
def test_low_risk_patch_by_ai_still_allowed(client, tokens, payload):
    """低风险写不受本节限制——AI 的归类能力不能被设防误伤（PRD F-AI-4）。"""
    _, _, task = _mk_tree(client)
    resp = client.patch(f"{PLANNER}/tasks/{task['id']}", json=payload, headers=AI_HEAD)
    assert resp.status_code == 200, resp.text
    assert resp.json()["lastWriter"] == "ai"


def test_create_by_ai_is_not_high_risk(client, tokens):
    """建对象是低风险（可撤销）：AI 照常建，只是留下 ai 角标。"""
    zone, project, _ = _mk_tree(client)
    resp = client.post(
        f"{PLANNER}/tasks", json={"projectId": project["id"], "name": "AI 建的任务"},
        headers=AI_HEAD,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["lastWriter"] == "ai"


def test_denial_precedes_existence_check(client, tokens):
    """删一个**不存在**的 id：403，不是 404。

    顺序是有意的——先拒来源、再谈对象在不在。反过来的话，404 与 409 的差异
    就成了一个 id 探测器：无权删除的调用方能靠状态码枚举出哪些 id 存在。
    """
    resp = client.delete(f"{PLANNER}/tasks/t_根本不存在", headers=AI_HEAD)
    assert resp.status_code == 403, resp.text


def test_patch_plan_denied_leaves_object_untouched(client, tokens):
    """被拒的高风险 PATCH **一个字节都没写**——不是"写了再回滚"。"""
    _, _, task = _mk_tree(client)
    client.patch(
        f"{PLANNER}/tasks/{task['id']}",
        json={"plan": {"start": "2026-01-01", "end": "2026-01-02"}}, headers=AI_HEAD,
    )
    after = next(t for t in client.get(f"{PLANNER}/tasks").json() if t["id"] == task["id"])
    assert after["plan"] is None
    assert after["lastWriter"] == "human"


# ------------------------------------------------------------------ 三、严格模式


def test_strict_mode_rejects_unverified_high_risk(client, strict):
    """严格模式：没有人路径凭据的高风险写一律 403（`unverified` 不再当人看）。"""
    _, _, task = _mk_tree(client)
    resp = client.delete(f"{PLANNER}/tasks/{task['id']}")
    assert resp.status_code == 403, resp.text
    assert "严格模式" in resp.json()["detail"]


def test_strict_mode_allows_human_credential(client, strict):
    _, _, task = _mk_tree(client)
    assert client.delete(f"{PLANNER}/tasks/{task['id']}", headers=HUMAN_HEAD).status_code == 204


def test_strict_mode_does_not_block_low_risk(client, strict):
    """严格模式只管高风险——把低风险也锁死等于让前端在网关改造前全瘫。"""
    _, _, task = _mk_tree(client)
    assert client.patch(f"{PLANNER}/tasks/{task['id']}", json={"name": "改名"}).status_code == 200


def test_health_exposes_actor_guard(client, monkeypatch):
    """姿态必须跨进程可判（契约 v1.6，同 v1.0 加 `db` 的理由）。"""
    from app import config, main  # noqa: PLC0415

    assert client.get(f"{API}/health").json()["actorGuard"] == "lenient"
    monkeypatch.setattr(
        main, "settings",
        dataclasses.replace(config.settings, human_client_token=HUMAN_TOKEN, actor_strict=True),
    )
    assert client.get(f"{API}/health").json()["actorGuard"] == "strict"


# ------------------------------------------------------------------ 四、配置


def test_config_rejects_short_token():
    from app.config import ConfigError, load_settings  # noqa: PLC0415

    base = {
        "NEXUS_MONGO_URI": "mongodb://127.0.0.1:27017",
        "NEXUS_DB_NAME": "nexus_core_test",
        "NEXUS_TZ": "UTC",
    }
    with pytest.raises(ConfigError, match="太短"):
        load_settings({**base, "NEXUS_AI_CLIENT_TOKEN": "test-only"})  # 9 字符 < 16
    with pytest.raises(ConfigError, match="相同"):
        load_settings({
            **base, "NEXUS_AI_CLIENT_TOKEN": AI_TOKEN, "NEXUS_HUMAN_CLIENT_TOKEN": AI_TOKEN,
        })
    with pytest.raises(ConfigError, match="NEXUS_HUMAN_CLIENT_TOKEN 未设"):
        load_settings({**base, "NEXUS_ACTOR_STRICT": "1"})
    with pytest.raises(ConfigError, match="NEXUS_ACTOR_STRICT 非法"):
        load_settings({**base, "NEXUS_ACTOR_STRICT": "yes"})

    ok = load_settings({
        **base, "NEXUS_AI_CLIENT_TOKEN": AI_TOKEN,
        "NEXUS_HUMAN_CLIENT_TOKEN": HUMAN_TOKEN, "NEXUS_ACTOR_STRICT": "true",
    })
    assert ok.actor_strict is True
    assert ok.actor_guard == "strict"
    assert load_settings(base).ai_client_token is None


def test_resolve_source_unit():
    """来源判定的纯函数级断言（不经 HTTP，省得被路由细节挡住）。"""
    from app.modules.planner import guard  # noqa: PLC0415
    from app.modules.planner.errors import ActorForgeryError  # noqa: PLC0415

    assert guard.resolve_source({}) == guard.SOURCE_UNVERIFIED
    assert guard.resolve_actor(guard.SOURCE_UNVERIFIED, None) == "human"
    assert guard.resolve_actor(guard.SOURCE_AI, None) == "ai"
    assert guard.resolve_actor(guard.SOURCE_AI, "ai") == "ai"
    with pytest.raises(ActorForgeryError):
        guard.resolve_actor(guard.SOURCE_AI, "human")

    assert guard.high_risk_reason("delete", {}) is not None
    assert guard.high_risk_reason("update", {"name": "x"}) is None
    for field in guard.HIGH_RISK_UPDATE_FIELDS:
        assert guard.high_risk_reason("update", {field: None}) is not None
    assert guard.high_risk_reason("create", {"plan": {}}) is None, "建对象不是高风险"


# ------------------------------------------- 五、断言：写路由不许绕过 guard


_WRITE_METHODS = ("post", "patch", "delete")


def _route_paths(func: ast.FunctionDef) -> list[str]:
    paths = []
    for deco in func.decorator_list:
        if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
            continue
        if deco.func.attr not in _WRITE_METHODS or not deco.args:
            continue
        first = deco.args[0]
        if isinstance(first, ast.Constant):
            paths.append(first.value)
    return paths


def _calls_run_write(func: ast.FunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run_write"
        for node in ast.walk(func)
    )


def test_every_write_route_goes_through_guard():
    """**新增写端点忘了接 guard 就直接红。**

    修完/定完规矩必须留下能重现拦住违规的断言。这里防的不是
    "少了条审计"，是**那条路径没有二次设防**——而它长得和别的路由一模一样，
    肉眼 review 极容易放过去。
    """
    from app.modules.planner import unified_router  # noqa: PLC0415

    tree = ast.parse(Path(unified_router.__file__).read_text(encoding="utf-8"))
    guarded, unguarded = [], []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for path in _route_paths(node):
            if path.startswith("/{type}"):
                continue  # 未知 type 兜底：它只抛 404，不写任何东西
            (guarded if _calls_run_write(node) else unguarded).append(f"{path}:{node.name}")

    assert not unguarded, f"这些写路由没经过 guard.run_write（＝没有二次设防）：{unguarded}"
    # 断言"找到了东西"：解析不到路由时上面那条 assert 会空转变绿——
    # 静默跳过后报成功，正是这里要防的反模式。
    assert len(guarded) == 9, f"期望 3 类对象 × 3 种写方法 = 9 条写路由，实际 {guarded}"
