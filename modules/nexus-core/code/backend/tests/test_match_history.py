"""匹配历史（契约 v2.12「活动建议」节「匹配历史」）。

要害一句：**历史说的是人最后的决定**——同一个窗口去重成一行、去向取台账里的当前归属
（确认到桶 = 只到项目；改挂过 = 改挂后的去向），已经不存在的去向不出。只读，别的租户看不见。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from test_activity_suggestions import A, B, BEARER, MATCHES, SUG, _pending, _recent, _seg, _upload
from test_session_reassign import PLANNER, _reassign, _world

HISTORY = f"{SUG}/history"


def _history(client, headers=None, **params) -> dict:
    resp = client.get(HISTORY, params=params, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _confirmed(client, title: str, minutes_ago: int, headers=None, app: str = "code", **body) -> dict:
    """上传一段并确认（``body`` = confirm 的请求体）。返回 confirm 的响应。"""
    _upload(client, [_seg(_recent(minutes_ago), minutes=1, title=title, app=app)], headers)
    sug_id = next(i["id"] for i in _pending(client, headers)["items"] if i["title"] == title)
    resp = client.post(f"{SUG}/{sug_id}/confirm", json=body, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.parametrize(("raw", "want"), [
    ("✳ Claude Code · cockpit", "Claude Code · cockpit"),
    ("⠂  Claude   Code · cockpit ", "Claude Code · cockpit"),
    ("(3) 收件箱 - Gmail", "收件箱 - Gmail"),
    ("[12] * ● 频道", "频道"),
    ("plot.gd (2) — garden", "plot.gd (2) — garden"),  # 只去开头的
    ("✳ ●", "✳ ●"),                                    # 去完是空的：用原样的
    ("", ""),
])
def test_norm_title_matches_the_assistant_page(raw, want):
    from app.modules.activity.history import norm_title  # noqa: PLC0415

    assert norm_title(raw) == want


def test_dedupes_by_window_and_keeps_the_latest_decision(client):
    w = _world(client)
    _confirmed(client, "✳ Claude Code · cockpit", 50, taskId=w["a"])
    _confirmed(client, "⠂ Claude Code · cockpit", 40, taskId=w["a"])
    _confirmed(client, "(2) Claude Code · cockpit", 30, taskId=w["b"])  # 人后来改了主意：以最近的为准
    _confirmed(client, "plot.gd — garden", 20, taskId=w["c"])
    _confirmed(client, "Claude Code · cockpit", 10, taskId=w["b"], app="kitty")  # 程序不同 = 另一个窗口

    body = _history(client)
    assert set(body) == {"items", "collections", "rejected"} and body["rejected"] == []
    rows = {(r["app"], r["title"]): r for r in body["items"]}
    assert [(r["app"], r["title"]) for r in body["items"]] == [
        ("kitty", "Claude Code · cockpit"), ("code", "plot.gd — garden"), ("code", "Claude Code · cockpit")]
    row = rows[("code", "Claude Code · cockpit")]
    assert row["count"] == 1, "只数与最近那次去向相同的，旧去向的两段不算"
    assert datetime.fromisoformat(row.pop("lastConfirmedAt")).tzinfo is not None
    assert row == {"app": "code", "title": "Claude Code · cockpit", "projectId": w["p"],
                   "projectPath": "改挂分区 / 改挂项目 P", "taskId": w["b"], "taskName": "任务 b", "taskDone": False,
                   "count": 1, "via": "confirm"}
    assert rows[("code", "plot.gd — garden")]["projectId"] == w["q"]

    _confirmed(client, "● Claude Code · cockpit", 5, taskId=w["b"])
    top = _history(client)["items"][0]
    assert (top["title"], top["count"]) == ("Claude Code · cockpit", 2), "新定的排到最前"


def test_bucket_confirmation_is_project_only(client):
    w = _world(client)
    _confirmed(client, "ShareGPU 开发", 10, projectId=w["p"])
    (row,) = _history(client)["items"]
    assert row["via"] == "project" and row["projectId"] == w["p"] and row["projectPath"] == "改挂分区 / 改挂项目 P"
    assert not {"taskId", "taskName", "taskDone"} & set(row), "桶不是任务：只给项目"


def test_reassigned_session_reports_the_current_assignment(client):
    w = _world(client)
    out = _confirmed(client, "ShareGPU 开发", 10, projectId=w["p"])
    _reassign(client, out["event"]["id"], taskId=w["a"])
    _reassign(client, out["event"]["id"], taskId=w["c"])  # 再改：跨项目，最后一次为准
    (row,) = _history(client)["items"]
    assert (row["via"], row["taskId"], row["projectId"]) == ("reassign", w["c"], w["q"])
    _reassign(client, out["event"]["id"], projectId=w["p"])  # 放回桶：又只到项目
    (row,) = _history(client)["items"]
    assert row["via"] == "reassign" and row["projectId"] == w["p"] and "taskId" not in row


def test_deleted_targets_are_skipped_and_done_tasks_are_flagged(client):
    w = _world(client)
    _confirmed(client, "窗口 a", 30, taskId=w["a"])
    _confirmed(client, "窗口 b", 20, taskId=w["b"])
    _confirmed(client, "窗口 c", 10, taskId=w["c"])
    assert client.patch(f"{PLANNER}/tasks/{w['b']}", json={"done": True}).status_code == 200
    assert client.delete(f"{PLANNER}/tasks/{w['a']}").status_code == 204
    from app.repo import get_db  # noqa: PLC0415 —— 项目有时间记录时端点不给删；这里验的是读端不信库里的悬空引用

    get_db()["projects"].delete_many({"id": w["q"]})
    rows = _history(client)["items"]
    assert [(r["title"], r["taskDone"]) for r in rows] == [("窗口 b", True)]


def test_collections_rollup_and_row_label(client):
    w = _world(client)
    for i, title in enumerate(("终端 1", "终端 2", "终端 3")):
        _upload(client, [_seg(_recent(40 - i * 10), minutes=1, title=title)])
    ids = {i["title"]: i["id"] for i in _pending(client)["items"]}
    labels = [{"id": sug_id, "collection": {"name": "ShareGPU 开发"}} for sug_id in ids.values()]
    assert client.post(MATCHES, json={"matches": labels}).json()["matched"] == 3
    assert client.post(f"{SUG}/{ids['终端 1']}/confirm", json={"projectId": w["q"]}).status_code == 200  # 旧去向
    for title in ("终端 2", "终端 3"):
        assert client.post(f"{SUG}/{ids[title]}/confirm", json={"taskId": w["a"]}).status_code == 200
    body = _history(client)
    assert body["collections"] == [{"name": "ShareGPU 开发", "projectId": w["p"],
                                    "projectPath": "改挂分区 / 改挂项目 P", "count": 2}]
    assert all(r["collection"] == "ShareGPU 开发" for r in body["items"])


def test_rejected_pairs_are_listed_once(client):
    w = _world(client)
    _upload(client, [_seg(_recent(30), minutes=1, title="✳ 评审"), _seg(_recent(20), minutes=1, title="⠂ 评审")])
    ids = [i["id"] for i in _pending(client)["items"]]
    for task in (w["a"], w["b"]):
        matches = [{"id": i, "taskId": task, "confidence": 0.8} for i in ids]
        assert client.post(MATCHES, json={"matches": matches}).json()["matched"] == 2
        for i in ids:
            assert client.post(f"{SUG}/{i}/unmatch").status_code == 200
    assert client.delete(f"{PLANNER}/tasks/{w['b']}").status_code == 204  # 已删的任务不出
    body = _history(client)
    assert body["rejected"] == [{"app": "code", "title": "评审", "taskId": w["a"], "taskName": "任务 a"}]
    assert body["items"] == [], "否掉不是确认"
    # 人后来还是把这个窗口确认到了那个任务：以最近的决定为准，不再算否掉
    assert client.post(f"{SUG}/{ids[0]}/confirm", json={"taskId": w["a"]}).status_code == 200
    body = _history(client)
    assert body["rejected"] == [] and [r["taskId"] for r in body["items"]] == [w["a"]]


def test_limit_default_cap_and_validation(client):
    w = _world(client)
    from app.repo import get_db  # noqa: PLC0415 —— 直接造 210 条已确认的建议与它们的事实，不走 210 次 HTTP

    now = datetime.now(timezone.utc)
    sugs, events = [], []
    for i in range(210):
        sugs.append({"user": "u_local", "id": f"sug_{i:03d}", "dedupeKey": f"aw:d:{i}", "app": "code",
                     "title": f"窗口 {i:03d}", "status": "confirmed", "decidedAt": now - timedelta(seconds=i),
                     "startTs": now - timedelta(seconds=i), "suggestion": {"taskId": None}})
        events.append({"user": "u_local", "id": f"evt_{i:03d}", "type": "session.completed",
                       "source": "activity-confirmed", "dedupeKey": f"activity:sug_{i:03d}",
                       "subject": {"zone": w["zone"], "project": w["p"], "task": w["a"]}})
    get_db()["activity_suggestions"].insert_many(sugs)
    get_db()["events"].insert_many(events)

    assert [r["title"] for r in _history(client)["items"]] == [f"窗口 {i:03d}" for i in range(60)]  # 缺省 60，新的在前
    assert len(_history(client, limit=5)["items"]) == 5
    assert len(_history(client, limit=1000)["items"]) == 200  # 上限 200
    assert len(_history(client, limit=0)["items"]) == 60      # 非正数 = 缺省（同列表）
    for bad in ("abc", "1.5", '{"$gt":0}'):
        assert client.get(HISTORY, params={"limit": bad}).status_code == 422


def test_tenant_isolation_and_bearer_can_read(client):
    wa = _world(client, A)
    _confirmed(client, "alice 的窗口", 10, A, taskId=wa["a"])
    assert _history(client, B) == {"items": [], "collections": [], "rejected": []}
    assert _history(client) == {"items": [], "collections": [], "rejected": []}  # 不带头的 u_local 也看不见
    # 设备令牌能读（同建议列表：同一类数据）
    assert [r["title"] for r in _history(client, {**A, **BEARER})["items"]] == ["alice 的窗口"]


def test_read_only(client):
    w = _world(client)
    _confirmed(client, "只读", 10, taskId=w["a"])
    from app.repo import get_db  # noqa: PLC0415

    def snapshot():
        return {name: list(get_db()[name].find({}, {"_id": 0}).sort("id", 1))
                for name in ("events", "activity_suggestions", "tasks", "proj_daily_stats")}

    before = snapshot()
    _history(client)
    assert snapshot() == before
