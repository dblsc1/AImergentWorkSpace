"""planner 审计流水（contract.md v1.6「planner 审计流水」节，PRD F-ACTOR-2）。

三件事必须被机械守住，缺一条这张表就白建：
1. **它不是事件**——`events` 台账一个字节都不许被它碰（本轮红线）。
2. **append-only**——不是文档承诺，是"审计集合上只允许 insert/find"的 AST 断言。
3. **三种 outcome 都记**——只记成功等于把攻击痕迹和"批量写崩在第几步"一起丢掉。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

API = "/api/core"
PLANNER = f"{API}/planner"
AUDIT = f"{PLANNER}/audit"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _mk_tree(client) -> tuple[dict, dict, dict]:
    zone = client.post(f"{PLANNER}/zones", json={"name": "示例分区一"}).json()
    project = client.post(
        f"{PLANNER}/projects", json={"zoneId": zone["id"], "name": "示例项目二"},
    ).json()
    task = client.post(
        f"{PLANNER}/tasks", json={"projectId": project["id"], "name": "背单词"},
    ).json()
    return zone, project, task


# ------------------------------------------------------------- 红线：不是事件


def test_audit_never_touches_events(client):
    """**本轮最重要的一条**：planner 写产生审计，但 `events` 集合纹丝不动。"""
    _mk_tree(client)
    client.patch(f"{PLANNER}/tasks/t_不存在", json={"name": "x"})  # 失败的写也记审计

    assert _db()["events"].count_documents({}) == 0, "planner 写绝不许流进事实台账"
    assert _db()["planner_audit"].count_documents({}) > 0
    assert client.get(AUDIT).json()["total"] > 0


def test_audit_not_exposed_through_events_or_export(client):
    """审计不进档案读端、不进全量导出——它不是"世界上发生了什么"。"""
    _mk_tree(client)
    assert client.get(f"{API}/events").json()["total"] == 0

    export = client.get(f"{API}/export").json()
    assert "planner_audit" not in export
    assert "audit" not in export
    assert export["events"] == []


def test_audit_collection_name_is_separate():
    from app.modules.planner import repo  # noqa: PLC0415

    assert repo.AUDIT_COLLECTION == "planner_audit"
    assert repo.AUDIT_COLLECTION != "events"


# ------------------------------------------------------------------ 记录内容


def test_create_writes_applied_record(client):
    _, project, task = _mk_tree(client)
    items = client.get(AUDIT, params={"objectId": task["id"]}).json()["items"]
    assert len(items) == 1
    entry = items[0]
    assert entry["op"] == "create"
    assert entry["objectType"] == "tasks"
    assert entry["objectId"] == task["id"]
    assert entry["outcome"] == "applied"
    assert entry["actor"] == "human"
    assert entry["source"] == "unverified"
    assert entry["highRisk"] is False
    assert entry["reason"] is None
    assert entry["changes"]["name"] == "背单词"
    assert entry["changes"]["projectId"] == project["id"]
    assert entry["auditId"].startswith("aud_")
    assert entry["at"].endswith("+00:00"), "时刻由服务端盖章，UTC"


def test_update_records_only_the_keys_actually_sent(client):
    _, _, task = _mk_tree(client)
    client.patch(f"{PLANNER}/tasks/{task['id']}", json={"name": "改名"})
    entry = client.get(AUDIT, params={"objectId": task["id"]}).json()["items"][0]
    assert entry["op"] == "update"
    assert entry["changes"] == {"name": "改名"}, "PATCH 只记实际传了的键，不记整份文档"


def test_denied_high_risk_is_recorded_with_reason(client):
    """**被拒的尝试是安全信号**——不记下来，攻击痕迹就没了。"""
    _, _, task = _mk_tree(client)
    resp = client.delete(f"{PLANNER}/tasks/{task['id']}", params={"actor": "ai"})
    assert resp.status_code == 403

    entry = client.get(AUDIT, params={"outcome": "denied"}).json()["items"][0]
    assert entry["op"] == "delete"
    assert entry["objectId"] == task["id"]
    assert entry["actor"] == "ai"
    assert entry["highRisk"] is True
    assert "高风险" in entry["reason"]


def test_failed_write_is_recorded(client):
    """过了设防、被业务校验拒（400/404/409）也留痕——批量写靠它定位第几步崩的。"""
    _, project, _ = _mk_tree(client)
    resp = client.post(f"{PLANNER}/tasks", json={"projectId": "p_没这个", "name": "孤儿"})
    assert resp.status_code == 400

    entry = client.get(AUDIT, params={"outcome": "failed"}).json()["items"][0]
    assert entry["op"] == "create"
    assert entry["objectId"] is None, "建对象被拒时还没有 id"
    assert "UnknownProjectError" in entry["reason"]
    assert project["id"] not in entry["reason"]


def test_batch_crash_is_reconstructable_from_seq(client):
    """AI 批量写崩在半路：审计必须回答"做到第几步"。

    这条是 F-ACTOR-2 的验收场景本身——`seq` 全序 + 三种 outcome 齐全，
    才能从流水里逐条读出"前三步成了、第四步为什么停的"。
    """
    _, project, _ = _mk_tree(client)
    for name in ("步骤一", "步骤二", "步骤三"):
        client.post(f"{PLANNER}/tasks", json={"projectId": project["id"], "name": name})
    client.post(f"{PLANNER}/tasks", json={"projectId": "p_没这个", "name": "第四步炸了"})

    items = client.get(AUDIT, params={"limit": 4}).json()["items"]
    seqs = [entry["seq"] for entry in items]
    assert seqs == sorted(seqs, reverse=True), "读端按 seq 降序，最新在前"
    assert len(set(seqs)) == 4, "seq 不许重号"
    assert items[0]["outcome"] == "failed"
    assert items[0]["changes"]["name"] == "第四步炸了"
    assert [e["outcome"] for e in items[1:]] == ["applied"] * 3


def test_ai_source_recorded_on_audit(client, monkeypatch):
    import dataclasses  # noqa: PLC0415

    from app import config  # noqa: PLC0415

    token = "test-only-ai-0987654321abcdef"  # 假凭据一眼看得出是假的（同 guard 测试）
    monkeypatch.setattr(
        config, "settings", dataclasses.replace(config.settings, ai_client_token=token),
    )
    _, project, _ = _mk_tree(client)
    client.post(
        f"{PLANNER}/tasks", json={"projectId": project["id"], "name": "AI 建的"},
        headers={"X-Nexus-Client-Token": token},
    )
    entry = client.get(AUDIT, params={"actor": "ai"}).json()["items"][0]
    assert entry["source"] == "ai"
    assert entry["actor"] == "ai"
    assert token not in str(entry), "凭据绝不许出现在审计里"


def test_ai_low_risk_write_lands_and_audits_exactly_one(client, monkeypatch):
    """**A5 端到端**：AI 的低风险写落库，且审计**恰好多一条** applied。

    此前这条判据被拆在两个文件里各测一半——`test_low_risk_patch_by_ai_still_allowed`
    只断言落库，`test_ai_source_recorded_on_audit` 只断言审计有 `actor=ai`，
    没有一条把"落了库"和"记了一条"绑在同一次写上。**"恰好一条"是要害**：
    多记（重试写了两遍）与少记（漏了留痕）都会被下面的差值断言抓住，
    而分开测的两条对这两种情形都全绿。
    """
    import dataclasses  # noqa: PLC0415

    from app import config  # noqa: PLC0415

    token = "test-only-ai-0987654321abcdef"
    monkeypatch.setattr(
        config, "settings", dataclasses.replace(config.settings, ai_client_token=token),
    )
    _, _, task = _mk_tree(client)
    before = client.get(AUDIT).json()["total"]

    resp = client.patch(
        f"{PLANNER}/tasks/{task['id']}",
        json={"name": "AI 改过的标题", "plannedWeight": 42},
        headers={"X-Nexus-Client-Token": token},
    )
    assert resp.status_code == 200, resp.text

    # ① 真落库（读回来看，不信响应体自说自话）
    stored = next(t for t in client.get(f"{PLANNER}/tasks").json() if t["id"] == task["id"])
    assert stored["name"] == "AI 改过的标题"
    assert stored["plannedWeight"] == 42
    assert stored["lastWriter"] == "ai"

    # ② 审计恰好多一条，且内容对得上这一次写
    after = client.get(AUDIT).json()
    assert after["total"] == before + 1, "一次低风险写应当且只应当留下一条审计"
    entry = after["items"][0]
    assert entry["op"] == "update"
    assert entry["objectId"] == task["id"]
    assert entry["actor"] == "ai"
    assert entry["source"] == "ai"
    assert entry["highRisk"] is False, "改标题/调权重不在高风险表内"
    assert entry["outcome"] == "applied"
    assert entry["reason"] is None
    assert entry["changes"] == {"name": "AI 改过的标题", "plannedWeight": 42}

    # ③ 红线复核：这一路写下来 events 台账仍是空的
    assert _db()["events"].count_documents({}) == 0


def test_changes_summary_truncates_and_redacts():
    """摘要不是快照：长值截断、疑似密钥的键脱敏。

    今天的写入口不收凭据字段，但"今天不收"不是不变量——加个字段就破了，
    所以脱敏压在摘要函数里，而不是靠"入参里本来就没有密钥"这条巧合。
    """
    from app.modules.planner.audit import MAX_ITEMS, MAX_STR_LEN, summarize_changes  # noqa: PLC0415

    summary = summarize_changes({
        "name": "x" * (MAX_STR_LEN + 50),
        "dependsOn": [f"t_{i}" for i in range(MAX_ITEMS + 5)],
        "apiToken": "test-only-value",
        "nested": {"password": "p", "ok": 1},
    })
    assert summary["name"].endswith("…")
    assert len(summary["name"]) == MAX_STR_LEN + 1
    assert summary["dependsOn"][-1] == "…"
    assert len(summary["dependsOn"]) == MAX_ITEMS + 1
    assert summary["apiToken"] == "<已脱敏>"
    assert summary["nested"]["password"] == "<已脱敏>"
    assert summary["nested"]["ok"] == 1


# ------------------------------------------------------------------ 读端


def test_audit_read_filters_and_validation(client):
    _, _, task = _mk_tree(client)
    client.delete(f"{PLANNER}/tasks/{task['id']}", params={"actor": "ai"})

    assert client.get(AUDIT, params={"actor": "ai"}).json()["total"] == 1
    assert client.get(AUDIT, params={"outcome": "applied"}).json()["total"] == 3
    assert client.get(AUDIT, params={"objectId": task["id"]}).json()["total"] == 2
    assert len(client.get(AUDIT, params={"limit": 1}).json()["items"]) == 1

    for params, hint in (
        ({"limit": 0}, "limit"), ({"limit": 501}, "limit"),
        ({"actor": "robot"}, "actor"), ({"outcome": "maybe"}, "outcome"),
    ):
        resp = client.get(AUDIT, params=params)
        assert resp.status_code == 400, resp.text
        assert hint in resp.json()["detail"]


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
def test_audit_is_read_only_over_http(client, method):
    """审计只有一条 GET 暴露面。写方法落进未知 type 的 404——
    **没有任何 HTTP 路径能改写审计**。"""
    url = AUDIT if method == "post" else f"{AUDIT}/aud_x"
    resp = getattr(client, method)(url, **({"json": {}} if method != "delete" else {}))
    assert resp.status_code == 404, resp.text


# ------------------------------------------- 断言：append-only


_ALLOWED_AUDIT_METHODS = {
    "insert_one", "find", "count_documents", "create_index", "sort", "limit",
}


def _is_audit_expr(node: ast.AST, aliases: set[str]) -> bool:
    """这个表达式是不是"审计集合"（含链式调用与局部别名）。"""
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Name) and key.id == "AUDIT_COLLECTION":
            return True
        return isinstance(key, ast.Constant) and key.value == "planner_audit"
    if isinstance(node, ast.Name):
        return node.id in aliases
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return _is_audit_expr(node.func.value, aliases)
    return False


def _audit_methods_in(func: ast.FunctionDef) -> set[str]:
    """本函数内、落在审计集合上的方法名。

    **别名必须按函数作用域算**：`col` 这个局部名在 `seed_many`/`insert_one`
    里也用着，全模块拉平会把别人的 `replace_one` 算到审计头上——
    第一版就是这么误报的，判据本身也得对。
    """
    aliases = {
        target.id
        for node in ast.walk(func)
        if isinstance(node, ast.Assign) and _is_audit_expr(node.value, set())
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    return {
        node.func.attr
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and _is_audit_expr(node.func.value, aliases)
    }


def test_audit_collection_is_insert_only():
    """审计集合上只准出现 insert/find 这一族调用。

    口头承诺 append-only 不算数，**能重现拦住违规写法的断言才算**。
    有人哪天为了"修一条记错的审计"加一行 `update_one`，这里立刻红。
    """
    from app.modules.planner import repo  # noqa: PLC0415

    tree = ast.parse(Path(repo.__file__).read_text(encoding="utf-8"))
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            used |= _audit_methods_in(node)
    assert used, "没扫到任何审计集合调用——断言空转就是静默跳过后报成功"
    assert used <= _ALLOWED_AUDIT_METHODS, f"审计集合上出现了非追加调用：{used - _ALLOWED_AUDIT_METHODS}"


def _code_string_constants(tree: ast.AST) -> list[str]:
    """代码里的字符串字面量（**不含 docstring**——文档里提集合名是应该的）。"""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_audit_collection_is_only_touched_by_repo():
    """审计集合名的**代码级**出现只许在 `planner/repo.py`。

    别处出现 = 有人绕开了唯一写路径。判据看字符串字面量而不是全文，
    否则 `audit.py` 文档里正当地提一句集合名就会被判成违规——
    **误报的断言会被人关掉，然后真违规也没人拦了**。
    """
    app_dir = Path(__file__).resolve().parent.parent / "app"
    offenders = [
        str(path.relative_to(app_dir))
        for path in app_dir.rglob("*.py")
        if path.name != "repo.py"
        and "planner_audit" in _code_string_constants(
            ast.parse(path.read_text(encoding="utf-8"))
        )
    ]
    assert not offenders, f"这些文件直接写了审计集合名，绕过了 repo 的唯一写路径：{offenders}"


def _collection_names_used_by_app() -> set[str]:
    """app/ 里出现的全部集合名：`get_db()["x"]` 的字面下标 + `*COLLECTION = "x"` 常量。"""
    app_dir = Path(__file__).resolve().parent.parent / "app"
    names: set[str] = set()
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # get_db()["<名字>"]
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "get_db"
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)
            ):
                names.add(node.slice.value)
            # <名字>COLLECTION = "<名字>"
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and any(
                    isinstance(t, ast.Name) and t.id.upper().endswith("COLLECTION")
                    for t in node.targets
                )
            ):
                names.add(node.value.value)
    return names


def test_every_collection_is_registered_for_cleanup():
    """**新增一个集合就必须登记进清库表**——这条断言盯的是「登记表漂了」这个类。

    实证（本轮 v1.6）：新增 `planner_audit` 时 `conftest._COLLECTIONS` 记得改了，
    但**同一形状的清库表在仓里不止一份**，
    漏掉的那几份让检测脚本在 `uniq_seq` 上撞重号直接崩——
    症状离病因十万八千里。修一个实例不算修完，得留下能拦住同类的断言。

    本条只管**本子文件夹能管的那一份**（`conftest._COLLECTIONS`）。
    判据是静态的：不看"库里现在有哪些集合"（那会被历史遗留集合污染成假红），
    只看 app 代码里写死了哪些集合名。
    """
    import conftest  # noqa: PLC0415 —— 测试引导文件，只在断言里读它的登记表

    used = _collection_names_used_by_app()
    assert used, "没扫到任何集合名——断言空转就是静默跳过后报成功"
    missing = sorted(used - set(conftest._COLLECTIONS))
    assert not missing, (
        f"这些集合被 app 代码写了却没进 conftest._COLLECTIONS 清库表：{missing}。"
        f"漏登记的后果不是「少清一个集合」，是**记录跨测试累积**，"
        f"而失败会出现在毫不相干的用例上。"
    )
