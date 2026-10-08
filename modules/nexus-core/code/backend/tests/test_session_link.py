"""v2.13：只挂项目的代理运行 + 窗口 ↔ 代理会话（契约「只挂项目的运行」「窗口 ↔ 代理会话」两节）。

要害：连线**只是提示**——对上了才写、对不上 / 拿不准就什么都不写，永不覆盖别人的选择、永不确认。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

API = "/api/core"
AGENTS = f"{API}/agents"
SUG = f"{API}/activity/suggestions"
A = {"X-Nexus-Tenant": "ch_aaaa"}
BEARER = {"Authorization": "Bearer " + "x" * 8}  # 形状像设备令牌即可
DEV = "dev_3f9a1c2b7d4e5a60"


def _db():
    from app.repo import get_db  # noqa: PLC0415

    return get_db()


def _start(client, headers=None, expect=201, **body):
    resp = client.post(f"{AGENTS}/start", json={"agent": "a", "tool": "claude-code", **body}, headers=headers or {})
    assert resp.status_code == expect, resp.text
    return resp.json()


def _seg(title: str, *, app: str = "ptyxis", ago: int = 60, task_id=None) -> dict:
    """跨过「现在」的一段（结束在 1 分钟后，时钟误差容忍之内）——与刚开的运行有重叠。"""
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=ago)
    return {"startAt": start.isoformat(), "endAt": (start + timedelta(seconds=120)).isoformat(),
            "durationSeconds": 60, "app": app, "title": title,
            "suggestion": {"taskId": task_id, "confidence": 0.9 if task_id else 0.0, "reason": "",
                           "classifier": "rules"}}


def _upload_one(client, seg, headers=None) -> dict:
    resp = client.post(SUG, json={"deviceId": DEV, "segments": [seg]}, headers=headers or {})
    assert resp.status_code == 200 and resp.json()["accepted"] == 1, resp.text
    items = client.get(SUG, headers={k: v for k, v in (headers or {}).items() if k != "Authorization"}).json()["items"]
    return next(i for i in items if i["startAt"] == seg["startAt"])["suggestion"]


def _projects(seeded) -> list[dict]:
    return list(seeded["projects"].values())


# ─────────────────────────────────────────── agents/start 的 projectId


def test_project_only_run_is_stored_projected_and_accounted_per_project(client, seeded):
    project = _projects(seeded)[0]
    run = _start(client, projectId=project["id"], label="CFO_agent")
    doc = _db()["agent_runs"].find_one({"runId": run["runId"]})
    assert (doc["projectId"], doc["zoneId"], doc["taskId"]) == (project["id"], project["zoneId"], None)

    lane = client.get(f"{API}/views/lanes").json()["agents"][0]
    assert (lane["projectId"], lane["taskId"]) == (project["id"], None) and "match" not in lane

    assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"}).status_code == 200
    event = _db()["events"].find_one({"type": "agent.run.completed"})
    subject = event["subject"]  # 无 task，同收件箱运行
    assert (subject["zone"], subject["project"], subject.get("task")) == (project["zoneId"], project["id"], None)
    lane = client.get(f"{API}/views/lanes").json()["agents"][0]
    assert (lane["projectId"], lane["taskId"]) == (project["id"], None)
    tasks = client.get(f"{API}/views/agent-time").json()["tasks"]
    assert [(t["projectId"], t["taskId"], t["runs"]) for t in tasks] == [(project["id"], None, 1)]
    assert _db()["events"].count_documents({"type": "session.completed"}) == 0  # 不发明人的时间


def test_project_id_validation(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    other = next(p for p in _projects(seeded) if p["id"] != task["projectId"])
    assert "不存在" in _start(client, expect=404, projectId="p_nope")["detail"]
    _start(client, expect=422, projectId="")
    _start(client, expect=422, projectId={"$ne": None})  # 只收有界的字符串，进不了查询
    assert "不是任务" in _start(client, expect=400, taskId=task["id"], projectId=other["id"])["detail"]
    assert _db()["agent_runs"].count_documents({}) == 0

    run = _start(client, taskId=task["id"], projectId=task["projectId"])  # 多余但不矛盾
    lane = client.get(f"{API}/views/lanes").json()["agents"][0]
    assert (lane["runId"], lane["taskId"], lane["projectId"]) == (run["runId"], task["id"], task["projectId"])


def test_project_id_is_tenant_scoped(client, seeded):
    _start(client, expect=404, headers=A, projectId=_projects(seeded)[0]["id"])  # 别的租户的项目 = 不存在


# ─────────────────────────────────────────── 归一化


@pytest.mark.parametrize(("title", "app"), [
    ("CFO_agent", "ptyxis"),
    ("✳ CFO_agent", "ptyxis"),
    ("⠋ CFO_agent - Ptyxis", "ptyxis"),
    ("(3) ✳  cfo_AGENT — Ptyxis", "org.gnome.Ptyxis"),
    ("* CFO_agent | ptyxis", "ptyxis"),
])
def test_norm_strips_glyphs_counters_and_app_suffix(title, app):
    from app.modules.activity.session_link import norm  # noqa: PLC0415

    assert norm(title, app) == norm("CFO_agent") == "cfo_agent"


def test_norm_keeps_what_is_not_noise():
    from app.modules.activity.session_link import norm  # noqa: PLC0415

    assert norm("CFO_agent - notes", "ptyxis") == "cfo_agent - notes"  # 结尾不是程序名：不去
    assert norm("CFO_agent - Ptyxis") == "cfo_agent - ptyxis"  # label / match 不去后缀
    assert norm("✳ ") == ""


# ─────────────────────────────────────────── 连线


def test_title_matching_a_live_session_gets_its_project(client, seeded):
    project = _projects(seeded)[0]
    _start(client, projectId=project["id"], label="CFO_agent", match="CFO_agent")
    sug = _upload_one(client, _seg("⠋ CFO_agent - Ptyxis"), headers=BEARER)  # 设备令牌上传照常
    assert sug == {"taskId": None, "confidence": 0.0, "reason": "", "classifier": "rules",
                   "projectId": project["id"], "projectSource": "agent-session",
                   "collection": {"key": "cfo_agent", "name": "CFO_agent"}}
    doc = _db()["activity_suggestions"].find_one({})
    assert doc["status"] == "pending" and _db()["events"].count_documents({}) == 0  # 不确认任何东西


def test_task_bound_and_closed_sessions_link_to_the_task_project(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    run = _start(client, taskId=task["id"], label="garden")
    assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"}).status_code == 200
    assert _db()["agent_runs"].count_documents({}) == 0  # 已结束：只剩 proj_lanes 里那条
    sug = _upload_one(client, _seg("✳ garden"))
    assert (sug["projectId"], sug["projectSource"]) == (task["projectId"], "agent-session")
    assert sug["taskId"] is None  # 只给项目，不替人挑任务


def test_live_match_clue_also_links(client, seeded):
    project = _projects(seeded)[0]
    _start(client, projectId=project["id"], label="显示名", match="CFO_agent")
    sug = _upload_one(client, _seg("CFO_agent"))
    assert sug["projectId"] == project["id"] and sug["collection"]["name"] == "显示名"


@pytest.mark.parametrize("title", ["窗口名3", "", "CFO_agent notes", "my CFO_agent", "✳ CF"])
def test_titles_that_do_not_equal_the_session_name_get_nothing(client, seeded, title):
    _start(client, projectId=_projects(seeded)[0]["id"], label="CFO_agent", match="CFO_agent")
    _start(client, projectId=_projects(seeded)[0]["id"], label="CF")  # 归一后不足 3 个码点：不参与
    assert _upload_one(client, _seg(title)) == {"taskId": None, "confidence": 0.0, "reason": "",
                                                "classifier": "rules"}


def test_no_time_overlap_no_link(client, seeded):
    _start(client, projectId=_projects(seeded)[0]["id"], label="CFO_agent")
    sug = _upload_one(client, _seg("CFO_agent", ago=3600))  # 一小时前的两分钟：运行那时还没开
    assert "projectId" not in sug and "collection" not in sug


def test_ambiguous_projects_and_inbox_runs_do_not_link(client, seeded):
    one, two = _projects(seeded)[:2]
    _start(client, label="CFO_agent")  # 收件箱运行：没有项目信息
    assert "projectId" not in _upload_one(client, _seg("CFO_agent", ago=62))
    _start(client, projectId=one["id"], label="CFO_agent")
    _start(client, projectId=one["id"], label="cfo_agent")  # 同一个项目的两个会话：不算歧义
    assert _upload_one(client, _seg("CFO_agent", ago=61))["projectId"] == one["id"]
    _start(client, projectId=two["id"], label="CFO_agent")
    assert "projectId" not in _upload_one(client, _seg("CFO_agent", ago=60))  # 两个项目：不替人挑


def test_never_overwrites_rule_guess_reupload_or_assistant(client, seeded):
    task = seeded["tasks"]["示例任务三"]
    other = next(p for p in _projects(seeded) if p["id"] != task["projectId"])
    _start(client, projectId=other["id"], label="CFO_agent")

    ruled = _upload_one(client, _seg("CFO_agent", ago=90, task_id=task["id"]))
    assert ruled == {"taskId": task["id"], "confidence": 0.9, "reason": "", "classifier": "rules"}

    seg = _seg("CFO_agent")
    assert _upload_one(client, seg)["projectSource"] == "agent-session"
    sug_id = next(i["id"] for i in client.get(SUG).json()["items"] if i["startAt"] == seg["startAt"])
    # 助理给了项目：换成助理的，来历标记去掉
    body = {"matches": [{"id": sug_id, "projectId": task["projectId"]}]}
    assert client.post(f"{SUG}/matches", json=body).json()["matched"] == 1
    # 重传同一段（会话还在）：防重命中，什么都不改
    resp = client.post(SUG, json={"deviceId": DEV, "segments": [seg]}).json()
    assert (resp["accepted"], resp["duplicates"]) == (0, 1)
    sug = next(i for i in client.get(SUG).json()["items"] if i["id"] == sug_id)["suggestion"]
    assert sug["projectId"] == task["projectId"] and "projectSource" not in sug
    assert sug["collection"] == {"key": "cfo_agent", "name": "CFO_agent"}  # 集合标签留着


def test_sessions_of_another_tenant_are_invisible(client, seeded):
    zone = client.post(f"{API}/planner/zones", json={"name": "z"}, headers=A).json()
    proj = client.post(f"{API}/planner/projects", json={"zoneId": zone["id"], "name": "p"}, headers=A).json()
    _start(client, headers=A, projectId=proj["id"], label="CFO_agent")
    assert "projectId" not in _upload_one(client, _seg("CFO_agent"))  # 缺省租户看不见 A 的会话
    assert _upload_one(client, _seg("CFO_agent"), headers=A)["projectId"] == proj["id"]


# ─────────────────────────────────────────── 会话改名（同 clientKey 再 start）


def test_restart_with_same_client_key_renames_the_run_and_nothing_else(client, seeded):
    project = _projects(seeded)[0]
    run = _start(client, projectId=project["id"], label="garden", match="garden", clientKey="k1", phase="idle")
    before = _db()["agent_runs"].find_one({"runId": run["runId"]}, {"_id": 0})

    again = _start(client, expect=200, label="Cockpit-Pub-Coder1", match="Cockpit-Pub-Coder1", clientKey="k1",
                   agent="other", projectId="p_nope", phase="working")  # 其余字段对原运行不起作用，也不校验
    assert again == run
    after = _db()["agent_runs"].find_one({"runId": run["runId"]}, {"_id": 0})
    assert after == {**before, "label": "Cockpit-Pub-Coder1", "match": "Cockpit-Pub-Coder1"}

    _start(client, expect=200, clientKey="k1")  # 没给名字：不动
    _start(client, expect=200, clientKey="k1", label="只换显示名")
    doc = _db()["agent_runs"].find_one({"runId": run["runId"]})
    assert (doc["label"], doc["match"]) == ("只换显示名", "Cockpit-Pub-Coder1")
    assert _db()["agent_runs"].count_documents({}) == 1

    _start(client, headers=A, clientKey="k1", label="别的租户")  # 别的租户的同 key 是另一条运行
    assert _db()["agent_runs"].find_one({"runId": run["runId"]})["label"] == "只换显示名"


def test_renamed_session_links_by_its_new_name_live_and_after_it_ends(client, seeded):
    project = _projects(seeded)[0]
    run = _start(client, projectId=project["id"], label="garden", match="garden", clientKey="k1")
    _start(client, expect=200, label="Cockpit-Pub-Coder1", match="Cockpit-Pub-Coder1", clientKey="k1")
    assert _upload_one(client, _seg("✳ Cockpit-Pub-Coder1 - Ptyxis", ago=62))["projectId"] == project["id"]
    assert "projectId" not in _upload_one(client, _seg("garden", ago=61))  # 旧名字不再认

    assert client.post(f"{AGENTS}/{run['runId']}/stop", json={"outcome": "done"}).status_code == 200
    assert _db()["events"].find_one({"type": "agent.run.completed"})["data"]["label"] == "Cockpit-Pub-Coder1"
    sug = _upload_one(client, _seg("Cockpit-Pub-Coder1", ago=60))
    assert sug["collection"] == {"key": "cockpit-pub-coder1", "name": "Cockpit-Pub-Coder1"}
