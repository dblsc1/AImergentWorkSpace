"""``POST /timer/cancel`` —— 取消计时**不记账**（2026-08-03 · T1–T3 + 反向验证）。

要害只有一句：**cancel 从一开始就不产生事实**。

本文件的断言全部围着这句话转，而不是围着「cancel 返回了 200」转。
「返回 200」是任何实现都能做到的，包括那个错误的实现——
先 ``stop()`` 落一条 ``session.completed``、再想办法把它删掉。
那样在事实流里会留下一条又删一条，而 ``events`` 是 append-only
（无软删除、无回收站），投影可能在两步之间读到那条中间态。

所以这里有一条**反向验证**（``test_reverse_cancel_via_stop_turns_red``）：
把 cancel 换成「内部调 stop」，T1 的那组断言必须**变红**。
如果它没变红，说明 T1 根本没在验「不产生事实」，是摆设。

**全部用例只跑测试库**——conftest.py 那条「库名不以 `_test` 结尾就 SystemExit」
的硬护栏（2026-08-01 清空真库事故之后立的）不绕、不改。
"""

from __future__ import annotations

import pytest

from app.config import LOCAL_USER

API = "/api/core"


def _db():
    from app.repo import get_db

    return get_db()


def _event_count() -> int:
    """events 集合的**全部**条数——不按 type 过滤。

    刻意不过滤：若某个实现产生的是别的 type 的事实，按 type 过滤会看不见它，
    于是「没产生事实」这个断言就被自己绕过去了。
    """
    return _db()["events"].count_documents({})


def _projection_snapshot() -> dict[str, list[dict]]:
    """两张投影的完整快照（**含 ``_id``**，逐字节对照用）。

    只取投影名对应的集合，排序后返回——Mongo 不保证返回顺序，
    不排序会让「投影没变」这条断言变成一个偶尔发红的假警报。
    """
    snapshot = {}
    for name in ("proj_current", "proj_daily_stats"):
        snapshot[name] = sorted(
            _db()[name].find({}), key=lambda doc: str(doc.get("_id"))
        )
    return snapshot


def _assert_t1_verdict(*, before: int, after: int, current_body: dict) -> None:
    """**T1 的判据本体**，抽成函数是为了让反向验证能对同一组断言下 ``pytest.raises``。

    正向调用必须过；把 cancel 换成内部调 stop 之后调用必须抛 AssertionError。
    """
    assert current_body["running"] is False, "T1：cancel 后 views/current 的 running 必须是 false"
    assert after == before, (
        f"T1：cancel **不许产生任何事实** —— events 条数 {before} → {after}。"
        f"条数变了说明这个实现在某处调了 events 入口（多半是内部调了 stop）。"
    )


def _start(client, task_id: str):
    resp = client.post(f"{API}/timer/start", json={"taskId": task_id})
    assert resp.status_code == 200, resp.text
    return resp


def _current(client) -> dict:
    resp = client.get(f"{API}/views/current")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ───────────────────────────────────────────── T1 不记账 + 停表


def test_t1_cancel_stops_timer_without_producing_any_event(client, seeded):
    """T1：cancel 后 ``views/current.running`` 变 false，且 **events 条数不变**。"""
    task_id = seeded["tasks"]["示例任务三"]["id"]
    _start(client, task_id)

    before = _event_count()
    assert _current(client)["running"] is True, "前置：cancel 之前应该真的在计时"

    resp = client.post(f"{API}/timer/cancel")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["running"] is False
    assert body["cancelled"]["taskId"] == task_id
    assert "event" not in body, "cancel 的出参里连 event 字段都不该有（它从不产生事件）"

    _assert_t1_verdict(before=before, after=_event_count(), current_body=_current(client))

    # 活状态真的被丢了（不是只把响应写成 false）
    from app.modules.timer import service

    assert service.get_running_state(LOCAL_USER) is None


def test_t1_cancel_leaves_earlier_real_facts_untouched(client, seeded):
    """cancel 丢的只是**当前这一段**：之前 stop 出来的真实事实一条不许少。

    「不产生新事实」和「不动老事实」是两件事，都得钉住——
    一个把 events 清空的实现也能让「条数不变」以外的粗糙断言看着是绿的。
    """
    first = seeded["tasks"]["示例任务三"]["id"]
    second = seeded["tasks"]["示例任务四"]["id"]

    _start(client, first)
    client.post(f"{API}/timer/stop")  # 一段真实工作，必须留下
    real = list(_db()["events"].find({}, {"_id": 0}))
    assert len(real) == 1, "前置：应有一条真实事实"

    _start(client, second)
    client.post(f"{API}/timer/cancel")

    assert list(_db()["events"].find({}, {"_id": 0})) == real, "老事实必须一个字节都没动"


# ───────────────────────────────────────────── T2 投影零变化


def test_t2_cancel_does_not_touch_projections(client, seeded):
    """T2：cancel 前后两张投影**一个字节都没变**。

    先跑一段真实 start→stop 把投影喂出内容来再验——
    对着两张空投影比「没变」是不会失败的断言，等于没验。
    """
    first = seeded["tasks"]["示例任务三"]["id"]
    second = seeded["tasks"]["示例任务四"]["id"]

    _start(client, first)
    client.post(f"{API}/timer/stop")

    before = _projection_snapshot()
    assert before["proj_current"], "前置：proj_current 应已被真实会话喂出内容"
    assert before["proj_daily_stats"], "前置：proj_daily_stats 应已被真实会话喂出内容"

    _start(client, second)
    resp = client.post(f"{API}/timer/cancel")
    assert resp.status_code == 200, resp.text

    assert _projection_snapshot() == before, (
        "T2：cancel 不产生事实，就不该有任何投影更新——"
        "投影变了说明有事实流进了 projector"
    )


# ───────────────────────────────────────────── T3 没在计时时取消


def test_t3_cancel_without_running_timer_is_a_loud_error(client):
    """T3：没在计时时 cancel → **明确的错误响应**，不是 500，也不是静默 200。"""
    resp = client.post(f"{API}/timer/cancel")

    assert resp.status_code == 409, (
        f"T3：应是 409（请求合法但与当前状态冲突），实际 {resp.status_code} {resp.text}"
    )
    assert resp.status_code != 500, "T3：不许是 500——这不是服务端故障"
    assert resp.status_code != 200, "T3：不许静默成功——界面会显示「已取消」而其实什么都没发生"

    detail = resp.json()["detail"]
    assert "没有正在计时" in detail, f"错误消息要说清没有可取消的东西，实际：{detail}"
    assert _event_count() == 0, "失败的 cancel 更不该产生事实"


def test_t3_failed_cancel_is_distinguishable_from_stop_semantics(client):
    """同一情形下 stop 是平静 200（S8 幂等），cancel 是 409 —— 语义差别必须可观察。

    这条钉的是「为什么不把 cancel 做成幂等」：两个端点在同一状态下给出
    不同答案，正是因为它们回答的是不同的问题。
    """
    stop = client.post(f"{API}/timer/stop")
    cancel = client.post(f"{API}/timer/cancel")

    assert stop.status_code == 200 and stop.json() == {"running": False, "event": None}
    assert cancel.status_code == 409


# ───────────────────────────────────────────── 反向验证


def test_reverse_cancel_via_stop_turns_red(client, seeded, monkeypatch):
    """**反向验证**：把 cancel 换成「内部调 stop」，T1 必须变红。

    这是规格点名要的那条。被换上的实现**正是不许的那种**——
    它对外看着一样（同样的出参形状、同样的 200），但内部走了事件入口，
    于是 events 集合 +1：用户档案里凭空多出一条「这段时间发生过」。

    如果这条用例没变红，说明 T1 的断言没在验「不产生事实」，是摆设。
    """
    from app.modules.timer import repo, service

    def cancel_via_stop(user: str = LOCAL_USER) -> dict:
        """错误实现：先 stop 落一条事实，再把出参包装成 cancel 的样子。"""
        state = repo.get_running(user)
        service.stop(user)  # ← 事实在这里被产生
        return {
            "running": False,
            "cancelled": {
                "taskId": state["taskId"],
                "startAt": state["startAt"],
                "discardedSeconds": 0,
            },
        }

    monkeypatch.setattr(service, "cancel", cancel_via_stop)

    task_id = seeded["tasks"]["示例任务三"]["id"]
    _start(client, task_id)
    before = _event_count()

    resp = client.post(f"{API}/timer/cancel")
    assert resp.status_code == 200, "前置：这个错误实现对外看着是成功的——这正是它危险的地方"

    after = _event_count()

    # 先把「错在哪」钉死：凭空多了一条事实
    assert after == before + 1, "错误实现应当产生一条 session.completed"
    assert _db()["events"].count_documents({"type": "session.completed"}) == 1

    # 再证明 T1 的那组断言确实会因此变红（红，不是绿）
    with pytest.raises(AssertionError):
        _assert_t1_verdict(before=before, after=after, current_body=_current(client))


def test_reverse_guard_also_catches_projection_change(client, seeded, monkeypatch):
    """反向验证的 T2 侧：错误实现同样会把投影改掉（事实流进了 projector）。"""
    from app.modules.timer import repo, service

    def cancel_via_stop(user: str = LOCAL_USER) -> dict:
        state = repo.get_running(user)
        service.stop(user)
        return {
            "running": False,
            "cancelled": {
                "taskId": state["taskId"],
                "startAt": state["startAt"],
                "discardedSeconds": 0,
            },
        }

    _start(client, seeded["tasks"]["示例任务三"]["id"])
    before = _projection_snapshot()

    monkeypatch.setattr(service, "cancel", cancel_via_stop)
    client.post(f"{API}/timer/cancel")

    assert _projection_snapshot() != before, "错误实现会更新投影——T2 因此也会变红"


# ───────────────────────────────────────────── 边界：cancel 不该悄悄影响后续


def test_cancel_then_start_again_works(client, seeded):
    """取消之后还能正常开始下一段，并且那一段 stop 时正常记账。

    「取消」不该让计时器进入某种半死状态——这是用户最可能紧接着做的事。
    """
    task_id = seeded["tasks"]["示例任务三"]["id"]
    _start(client, task_id)
    client.post(f"{API}/timer/cancel")

    _start(client, task_id)
    stop = client.post(f"{API}/timer/stop")
    assert stop.status_code == 200
    assert stop.json()["event"]["type"] == "session.completed"
    assert _event_count() == 1, "被取消的那段不记账，后面这段照常记账"
