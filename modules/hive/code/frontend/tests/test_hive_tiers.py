"""三级分类打开后的一栏决策栏（hex-tiers.js）：分段顺序与计数、窗口块左滑 assign / 右滑 ignore / 撤销 reset / 409 刷新、
拆分（拖把手）、标通用窗口、规则卡批准 / 更正 / 忽略、自动化历史撤销与拖到格子改挂、手机宽度；以及 tiers 关着时旧面板不变。

端点全在浏览器侧桩掉（settings 在页面加载前就要桩好：启动时读一次，所以走 open_hive 的 setup）。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from playwright.sync_api import Route

from test_hive_decide import _drag_to, _onto, _swipe as _swipe_raw, _until  # noqa: F401
from test_hive_focus import base, browser, open_hive  # noqa: F401  (fixtures)

DATE = "2026-10-24"
T0 = int(datetime(2026, 10, 24, 1, 0, tzinfo=timezone.utc).timestamp() * 1000)   # 本地 09:00
REC = [("2026-10-24T01:00:00Z", "2026-10-24T01:10:00Z"), ("2026-10-24T01:30:00Z", "2026-10-24T01:50:00Z"), ("2026-10-24T02:20:00Z", "2026-10-24T03:00:00Z")]
DOC, PTY, ZOOM = "示例文档", "ptyxis · Coder", "zoom · 周会"


def _blocks() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    base_ = {"keySource": "auto", "via": None, "ruleId": None, "records": 1, "firstAt": now, "lastAt": now, "apps": [], "expired": False, "parts": None}
    part = lambda t, p: [{"fromTs": None, "toTs": None, "taskId": t, "projectId": p, "seconds": 60}]  # noqa: E731
    return [
        {**base_, "blockKey": DOC, "status": "pending", "generic": False, "seq": 3, "seconds": 2520, "records": 3},
        {**base_, "blockKey": PTY, "status": "generic", "generic": True, "seq": 1, "seconds": 7500},
        {**base_, "blockKey": ZOOM, "status": "pending", "generic": False, "seq": 0, "seconds": 2880},
        {**base_, "blockKey": "Nautilus", "status": "assigned", "via": "rule", "generic": False, "seq": 5, "seconds": 600, "parts": part("t_p_piano", "p_piano")},
        {**base_, "blockKey": "规则块", "status": "assigned", "via": "ai-direct", "generic": False, "seq": 2, "seconds": 300, "parts": part("t_p_math", "p_math")},
        {**base_, "blockKey": "人分的", "status": "assigned", "via": "human", "generic": False, "seq": 2, "seconds": 300, "parts": part("t_p_math", "p_math")},
    ]


PROPS = {
    "projects": [], "changes": [], "ignores": [],
    "tasks": [{"id": "nt_2", "kind": "task", "name": "调音", "reason": "钢琴相关", "projectId": "p_piano", "projectName": "钢琴", "projectProposalId": None}],
    "items": [
        {"id": "w1", "kind": "windowRule", "status": "pending", "op": "add", "rule": {"app": "Chrome", "title": "docs.example.com", "block": DOC}, "reason": "近 5 天出现 14 次"},
        {"id": "b1", "kind": "blockRule", "status": "pending", "op": "add", "blockKey": DOC, "taskId": "t_p_math", "projectId": "p_math", "reason": "每天都是"},
        {"id": "a1", "kind": "blockAction", "status": "pending", "action": "assign", "date": DATE, "blockKey": DOC, "taskId": "t_p_math", "projectId": "p_math",
         "reason": "页面标题都含「MCP」"},
    ],
}


Q1 = {"id": "aq_1", "status": "pending", "title": "14:00 到 17:00 没有任何记录", "text": "这 3 小时电脑没人用", "date": DATE,
      "from": "2026-10-24T06:00:00Z", "to": "2026-10-24T09:00:00Z", "allowText": False, "answer": None,
      "options": [{"label": "在练琴", "target": {"taskId": "t_p_piano"}}, {"label": "这段不记", "ignore": True}]}
Q2 = {"id": "aq_2", "status": "pending", "title": "上午那段是什么", "text": "", "date": DATE, "from": None, "to": None, "allowText": True,
      "answer": None, "options": [{"label": "没做事"}]}


def tiers_setup(hive: Any, tiers: Any = True, props: dict | None = None) -> None:
    """页面加载前把 activity/* 全桩好。hive.reqs = [(方法, 路径, 请求体)]；hive.blocks = 服务端那份块（写端点会改它）。"""
    hive.reqs = []
    hive.blocks = _blocks()
    hive.conflict = {}        # 写端点 -> "pending" | "taken"：下一次回 409 并把服务端 seq 抬高；taken = 同时已被别处分好
    hive.hold = None          # 列表 = 把 assign 的响应扣下，由测试自己放
    hive.blocks_fail = False
    hive.questions = [dict(Q1)]   # None = 端点不存在（404）
    hive.report = None
    hive.answer_fail = None       # (状态码, detail)

    def question(qid: str) -> dict:
        return next(q for q in hive.questions if q["id"] == qid)

    def find(key: str) -> dict:
        return next(b for b in hive.blocks if b["blockKey"] == key)

    def route(r: Route) -> None:
        req = r.request
        path = req.url.split("/api/core/", 1)[1]
        body = json.loads(req.post_data) if req.post_data else None
        hive.reqs.append((req.method, path, body))
        ok = lambda b, status=200: r.fulfill(status=status, content_type="application/json", body=json.dumps(b))  # noqa: E731
        if req.method == "GET":
            if path == "activity/settings":
                ok({"version": 1, "tiers": tiers}) if tiers != "500" else ok({}, 500)
            elif path.startswith("activity/questions"):
                ok({"items": [q for q in hive.questions if q["status"] == "pending"]}) if hive.questions is not None else ok({"detail": "Not Found"}, 404)
            elif path.startswith("activity/secretary-reports"):
                ok({"items": [hive.report] if hive.report else []})
            elif path.startswith("activity/blocks/records"):
                ok({"records": [{"startAt": a, "endAt": b, "seconds": 60, "app": "chrome", "title": "x", "deviceId": "d"} for a, b in REC]})
            elif path.startswith("activity/blocks?") and hive.blocks_fail:
                ok({}, 500)
            elif path.startswith("activity/blocks?"):
                ok({"date": DATE, "nextCursor": None, "items": hive.blocks})
            elif path.startswith("activity/proposals?"):
                ok(props if props is not None else PROPS)
            elif path.startswith("activity/reports?"):
                ok({"items": []})
            elif path.startswith("activity/auto"):
                ok({"date": DATE, "items": []})
            else:
                hive.route(r)
            return
        if path.startswith("activity/questions/"):
            q, act = question(path.split("/")[2]), path.rsplit("/", 1)[1]
            if act == "answer" and hive.answer_fail:
                ok({"detail": hive.answer_fail[1]}, hive.answer_fail[0])
                return
            q["status"] = "answered" if act == "answer" else "dismissed"
            opt = q["options"][body["option"]] if body and body.get("option") is not None else {}
            rec = bool(opt.get("target") and q.get("from"))
            ok({**q, "answer": {"option": body.get("option"), "text": body.get("text"), "recorded": rec, "eventId": "ev_1" if rec else None}} if act == "answer" else q)
            return
        if path.startswith("activity/blocks/"):
            kind = path.rsplit("/", 1)[1]
            if kind == "generic":
                find(body["blockKey"]).update(status="generic" if body["generic"] else "pending", generic=body["generic"])
                ok({"blockKey": body["blockKey"], "generic": body["generic"], "since": DATE})
                return
            b = find(body["blockKey"])
            if kind in hive.conflict:
                if hive.conflict.pop(kind) == "taken":
                    b.update(status="assigned", via="human")
                b["seq"] += 4
                ok({"detail": "ifSeq 与现状不符", "current": b}, 409)
                return
            if kind == "assign" and hive.hold is not None:
                hive.hold.append((r, b))
                return
            b["seq"] += 1
            if kind == "assign":
                b.update(status="assigned", via="human")
            elif kind == "ignore":
                b.update(status="ignored", via="human")
            elif kind == "reset":
                b.update(status="generic" if b["generic"] else "pending", via=None, parts=None)
            elif kind == "split":
                b.update(status="assigned", via="human", parts=body["parts"])
            ok(b)
        else:
            hive.writes.append(path)
            ok({})
    hive.page.route("**/api/core/activity/**", route)
    na = {"today": "2026-10-09", "zones": [{"actionable": [
        {"id": "t_p_math", "name": "数学的任务", "projectId": "p_math", "projectName": "数学"},
        {"id": "t_p_piano", "name": "钢琴的任务", "projectId": "p_piano", "projectName": "钢琴"}], "waiting": []}]}
    hive.page.route("**/api/core/views/next-actions*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(na)))


def open_tiers(browser: Any, base: str, tiers: Any = True, props: dict | None = None, **kw: Any) -> Any:
    return open_hive(browser, base, setup=lambda h: tiers_setup(h, tiers, props), **kw)


def _expand_all(hive: Any) -> None:
    """大卡 / 分组默认收起（分组里只有低置信度默认展开）：测试要看里面的卡，全点开，再等「跳一跳」跳完。"""
    for sel in (".hb-head[aria-expanded=false]", ".hb-ghead[aria-expanded=false]"):
        while hive.page.locator(sel).count():
            hive.page.locator(sel).first.click()
    hive.page.wait_for_timeout(450)


def _show(hive: Any) -> None:
    hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
    hive.page.wait_for_selector(".hd-head")
    hive.page.locator(".hd-head").click()
    hive.page.wait_for_selector(".hb-head")
    _expand_all(hive)
    hive.page.wait_for_selector(".ht-block")


def _posts(hive: Any, prefix: str) -> list[tuple[str, Any]]:
    return [(p, b) for m, p, b in hive.reqs if m == "POST" and p.startswith(prefix)]


def _block(hive: Any, key: str) -> Any:
    return hive.page.locator(f'.ht-block[data-block="{key}"]')


def _swipe(page: Any, key: str, dx: float) -> None:
    """卡片刚重画完（上一次写入后的刷新）就量位置会落空：等它在、再等一小会儿落定。"""
    page.wait_for_selector(f'.hd-card[data-key="{key}"] .hd-face')
    page.locator(f'.hd-card[data-key="{key}"] .hd-face').scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    _swipe_raw(page, key, dx)


def _bkey(key: str) -> str:
    return "blk:" + key


def test_tiers_off_keeps_old_panel(browser, base) -> None:  # noqa: F811
    for tiers in (False, "500"):
        with open_tiers(browser, base, tiers=tiers) as hive:
            hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
            hive.page.wait_for_selector(".hd-head")
            hive.page.locator(".hd-head").click()
            assert hive.page.locator(".hd-cols .hd-col-pend").count() == 1 and hive.page.locator(".ht-body").count() == 0
            assert hive.page.locator(".hd-panel").get_attribute("data-tiers") is None
            assert not any(p.startswith("activity/blocks") for _, p, _ in hive.reqs), "关着时一个块端点都不读"
            assert [p for _, p, _ in hive.reqs].count("activity/settings") == 1


def test_sections_order_and_counts(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        titles = hive.page.locator(".hb-title").all_inner_texts()
        assert titles == ["AI 问题", "待批准日程改动", "窗口分类规则", "待分类窗口块", "自动化历史"]
        assert hive.page.locator(".hb-head .hb-cnt").all_inner_texts() == ["1", "1", "2", "3"], "自动化历史没有红圈"
        assert hive.page.locator(".hd-badge").inner_text() == "7"
        assert hive.page.locator(".hd-cols").count() == 0, "一栏，没有旧的两栏"
        assert hive.page.locator("section[data-sec=q] .hd-win").inner_text() == "14:00 到 17:00 没有任何记录"
        doc = _block(hive, DOC).inner_text()
        assert DOC in doc and "↓ 数学 - 数学的任务" in doc and "AI：页面标题都含「MCP」" in doc and "42 分钟" in doc and "3 段" in doc
        assert "通用窗口" in _block(hive, PTY).inner_text() and "↓ ？" in _block(hive, ZOOM).inner_text()
        assert "is-ignore" in _block(hive, ZOOM).get_attribute("class"), "没有目标 = 灰色"
        assert _block(hive, DOC).locator("button").all_inner_texts() == ["拆分", "标为通用窗口", "改去向", "批准"]
        assert "批准" not in _block(hive, ZOOM).locator("button").all_inner_texts()
        assert hive.page.locator("section[data-sec=r] .hd-minihive").count() == 2
        assert hive.page.locator("section[data-sec=h] .hd-hcard").count() == 2
        assert hive.page.locator("section[data-sec=h]").inner_text().count("撤销") == 2
        # 项目深浅色沿用
        assert hive.page.evaluate("document.querySelector('.ht-block[data-block=\"示例文档\"]').style.getPropertyValue('--zone-raw')") != ""


def test_empty_sections_are_hidden_and_questions_endpoint_404_is_tolerated(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props={"projects": [], "tasks": []}) as hive:
        hive.questions = None
        _show(hive)
        assert hive.page.locator(".hb-title").all_inner_texts() == ["待分类窗口块", "自动化历史"]


def test_swipe_left_assigns_with_remember_and_undo_resets(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _swipe(hive.page, _bkey(DOC), -400)
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)
        assert _posts(hive, "activity/blocks/assign")[0][1] == {"date": DATE, "blockKey": DOC, "ifSeq": 3, "taskId": "t_p_math", "remember": True}
        _until(hive.page, lambda: hive.page.locator(".hd-toast", has_text="已批准：" + DOC).count() == 1)
        _until(hive.page, lambda: _block(hive, DOC).count() == 0)
        assert hive.page.locator(".hb-head .hb-cnt").nth(3).inner_text() == "2" and hive.page.locator(".hd-badge").inner_text() == "6"
        hive.page.locator(".hd-toast-undo").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/reset")) == 1)
        assert _posts(hive, "activity/blocks/reset")[0][1] == {"date": DATE, "blockKey": DOC, "ifSeq": 4}, "撤销 = reset，带新 seq"
        _until(hive.page, lambda: _block(hive, DOC).count() == 1)


def test_swipe_right_ignores_and_left_without_target_is_refused(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _swipe(hive.page, _bkey(ZOOM), -400)
        assert hive.page.locator(".hd-toast").inner_text() == "先选去向"
        assert _posts(hive, "activity/blocks/") == [] and _block(hive, ZOOM).count() == 1, "弹回，没有写"
        hive.page.wait_for_timeout(400)   # 弹回的过渡走完再量位置
        _swipe(hive.page, _bkey(ZOOM), 400)
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/ignore")) == 1)
        assert _posts(hive, "activity/blocks/ignore")[0][1] == {"date": DATE, "blockKey": ZOOM, "ifSeq": 0, "forever": False}
        _until(hive.page, lambda: any(t.startswith("已忽略：") for t in hive.page.locator(".hd-toast").all_inner_texts()))


def test_pick_destination_then_approve_button(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _block(hive, ZOOM).get_by_role("button", name="改去向").click()
        _block(hive, ZOOM).locator("select").select_option("t:t_p_piano")
        assert "↓ 钢琴 - 钢琴的任务" in _block(hive, ZOOM).inner_text()
        _block(hive, ZOOM).get_by_role("button", name="批准").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)
        assert _posts(hive, "activity/blocks/assign")[0][1]["taskId"] == "t_p_piano"


def test_conflict_retries_once_when_still_pending(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        hive.conflict["assign"] = "pending"
        _swipe(hive.page, _bkey(DOC), -400)
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 2)
        assert [b["ifSeq"] for _, b in _posts(hive, "activity/blocks/assign")] == [3, 7], "409 后重读，仍待处理就用新 seq 自动重试一次"
        _until(hive.page, lambda: _block(hive, DOC).count() == 0)
        assert hive.page.locator(".hd-toast", has_text="已刷新").count() == 0


def test_conflict_when_taken_elsewhere_refreshes_and_says_so(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        hive.conflict["assign"] = "taken"
        gets = len([1 for m, p, _ in hive.reqs if m == "GET" and p.startswith("activity/blocks?")])
        _swipe(hive.page, _bkey(DOC), -400)
        _until(hive.page, lambda: hive.page.locator(".hd-toast", has_text="已刷新").count() == 1)
        _until(hive.page, lambda: len([1 for m, p, _ in hive.reqs if m == "GET" and p.startswith("activity/blocks?")]) > gets)
        assert len(_posts(hive, "activity/blocks/assign")) == 1, "别处已经分好：不重试"
        _until(hive.page, lambda: _block(hive, DOC).count() == 0)


def test_second_write_on_the_same_block_waits_and_result_lands_on_the_current_item(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        hive.hold = []
        _swipe(hive.page, _bkey(DOC), -400)
        _until(hive.page, lambda: len(hive.hold) == 1)
        hive.page.evaluate("() => document.querySelector('.ht-block[data-block=\"示例文档\"] [data-ht=approve]').click()")
        hive.page.wait_for_timeout(200)
        assert len(_posts(hive, "activity/blocks/assign")) == 1, "上一步还没回来：不发第二个"
        r, b = hive.hold[0]
        b["seq"] += 1
        b.update(status="assigned", via="human")
        r.fulfill(status=200, content_type="application/json", body=json.dumps(b))
        _until(hive.page, lambda: _block(hive, DOC).count() == 0)
        hive.page.wait_for_timeout(200)
        assert hive.page.locator(".hd-toast", has_text="已批准：").count() == 1 and len(_posts(hive, "activity/blocks/assign")) == 1


def test_undo_failure_is_reported_as_such(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _swipe(hive.page, _bkey(DOC), -400)
        _until(hive.page, lambda: hive.page.locator(".hd-toast-undo").count() == 1)
        hive.conflict["reset"] = "pending"
        hive.page.locator(".hd-toast-undo").click()
        _until(hive.page, lambda: hive.page.locator(".hd-toast.is-error", has_text="撤销失败：" + DOC).count() == 1)
        assert hive.page.locator(".hd-toast", has_text="已刷新").count() == 0


def test_blocks_read_failure_hides_the_block_sections(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.blocks_fail = True
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        hive.page.locator(".hd-head").click()
        hive.page.wait_for_selector(".ht-err")
        assert hive.page.locator(".ht-err").inner_text() == "读取失败，稍后重试"
        assert hive.page.locator(".ht-block, .ht-hist").count() == 0
        assert hive.page.locator(".hb-title").all_inner_texts() == ["AI 问题", "待批准日程改动", "窗口分类规则"]


def test_rerender_keeps_focus_and_an_open_select(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _block(hive, ZOOM).get_by_role("button", name="改去向").click()
        assert hive.page.evaluate("document.activeElement.dataset.ht") == "dest", "重画后焦点还在那个按钮上"
        hive.page.evaluate("() => { const s = document.querySelector('.ht-select'); window.__sel = s; s.focus(); }")
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_timeout(300)
        assert hive.page.evaluate("document.querySelector('.ht-select') === window.__sel && document.activeElement === window.__sel"), "下拉开着不重画"


def test_start_failure_falls_back_to_the_old_panel(browser, base) -> None:  # noqa: F811
    def setup(h: Any) -> None:
        tiers_setup(h)
        h.page.route("**/hex-tiers.js", lambda r: r.fulfill(
            status=200, content_type="application/javascript", body="window.NexusTableHexTiers={start:function(){throw new Error('boom')}}"))
    with open_hive(browser, base, setup=setup) as hive:
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        hive.page.locator(".hd-head").click()
        hive.page.wait_for_selector(".hd-col-pend .hd-card")
        assert hive.page.locator(".ht-body").count() == 0 and hive.page.locator(".hd-panel").get_attribute("data-tiers") is None


def test_handle_hot_area_is_wide_enough(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, width=390, height=800) as hive:
        _show(hive)
        _block(hive, DOC).get_by_role("button", name="拆分").click()
        hive.page.wait_for_selector(".ht-handle")
        w = hive.page.evaluate("""() => { const c = getComputedStyle(document.querySelector('.ht-handle'), '::after');
            return document.querySelector('.ht-handle').getBoundingClientRect().width + parseFloat(c.left) * -1 + parseFloat(c.right) * -1 }""")
        assert w >= 32


def test_split_drag_handle_and_confirm(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _block(hive, DOC).get_by_role("button", name="拆分").click()
        hive.page.wait_for_selector(".ht-handle")
        assert hive.page.locator(".ht-rec").count() == 3 and hive.page.locator(".ht-part").count() == 2
        bar = hive.page.locator(".ht-bar").bounding_box()
        h = hive.page.locator(".ht-handle").bounding_box()
        hive.page.mouse.move(h["x"] + h["width"] / 2, h["y"] + 20)
        hive.page.mouse.down()
        hive.page.mouse.move(bar["x"] + bar["width"] * 0.5, h["y"] + 20, steps=4)
        hive.page.mouse.move(bar["x"] + bar["width"] * 0.75, h["y"] + 20, steps=4)
        hive.page.mouse.up()
        parts = _block(hive, DOC).locator(".ht-part select")
        parts.nth(0).select_option("t:t_p_math")
        parts.nth(1).select_option("p:p_piano")
        _block(hive, DOC).get_by_role("button", name="确认拆分").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/split")) == 1)
        b = _posts(hive, "activity/blocks/split")[0][1]
        t0, t1 = T0, int(datetime(2026, 10, 24, 3, 0, tzinfo=timezone.utc).timestamp() * 1000)
        cut = b["parts"][0]["toTs"]
        assert isinstance(cut, int) and abs(cut - (t0 + (t1 - t0) * 0.75)) < (t1 - t0) * 0.05, "拆分点是 UTC 毫秒，落在拖到的位置"
        assert b["parts"] == [{"fromTs": T0, "toTs": cut, "taskId": "t_p_math"}, {"fromTs": cut, "toTs": t1, "projectId": "p_piano"}], "首尾填块里实际的首尾，不用 null"
        assert b["date"] == DATE and b["blockKey"] == DOC and b["ifSeq"] == 3
        _until(hive.page, lambda: hive.page.locator(".hd-toast", has_text="已拆分：").count() == 1)
        _until(hive.page, lambda: _block(hive, DOC).count() == 0)


def test_split_point_dropped_in_a_gap_snaps_to_a_record_edge(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _block(hive, DOC).get_by_role("button", name="拆分").click()
        hive.page.wait_for_selector(".ht-handle")
        bar, h = hive.page.locator(".ht-bar").bounding_box(), hive.page.locator(".ht-handle").bounding_box()
        hive.page.mouse.move(h["x"] + h["width"] / 2, h["y"] + 20)
        hive.page.mouse.down()
        hive.page.mouse.move(bar["x"] + bar["width"] * 0.54, h["y"] + 20, steps=4)   # 01:00 起 2 小时：0.54 = 02:05，在 01:50–02:20 的空隙里
        hive.page.mouse.up()
        parts = _block(hive, DOC).locator(".ht-part select")
        parts.nth(0).select_option("t:t_p_math")
        parts.nth(1).select_option("p:p_piano")
        _block(hive, DOC).get_by_role("button", name="确认拆分").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/split")) == 1)
        cut = _posts(hive, "activity/blocks/split")[0][1]["parts"][0]["toTs"]
        edges = {int(datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp() * 1000) for a, b in REC for x in (a, b)}
        assert cut in edges, "吸附到记录边界"


def test_split_add_point_needs_every_target_and_cancel_collapses(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _block(hive, DOC).get_by_role("button", name="拆分").click()
        hive.page.wait_for_selector(".ht-handle")
        _block(hive, DOC).get_by_role("button", name="再加一个拆分点").click()
        assert hive.page.locator(".ht-handle").count() == 2 and hive.page.locator(".ht-part").count() == 3
        _block(hive, DOC).get_by_role("button", name="确认拆分").click()
        assert hive.page.locator(".hd-toast").last.inner_text() == "每一块都要选去向"
        assert _posts(hive, "activity/blocks/split") == []
        hive.page.locator(".ht-handle").first.focus()
        hive.page.keyboard.press("ArrowRight")
        _block(hive, DOC).get_by_role("button", name="取消").click()
        assert hive.page.locator(".ht-split").count() == 0 and _block(hive, DOC).count() == 1


def test_mark_generic_window(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        assert hive.page.locator(".ht-block .ht-badge").count() == 1
        _block(hive, ZOOM).get_by_role("button", name="标为通用窗口").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/generic")) == 1)
        assert _posts(hive, "activity/blocks/generic")[0][1] == {"blockKey": ZOOM, "generic": True}
        _until(hive.page, lambda: hive.page.locator(".ht-block .ht-badge").count() == 2)
        _until(hive.page, lambda: hive.page.locator(".hd-toast", has_text="已标为通用窗口：").count() == 1)


def test_rule_cards_approve_correct_ignore(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        w1, b1 = hive.page.locator('.hd-card[data-key="w1"]'), hive.page.locator('.hd-card[data-key="b1"]')
        assert "Chrome 标题含「docs.example.com」" in w1.inner_text() and f"收进「{DOC}」" in w1.inner_text()
        assert f"窗口块「{DOC}」" in b1.inner_text() and "记到 数学的任务 任务（数学）" in b1.inner_text()
        # 更正第二级：换去向
        b1.get_by_role("button", name="更正").click()
        b1.locator("select").select_option("t:t_p_piano")
        b1.get_by_role("button", name="确定").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/proposals/b1/approve")) == 1)
        assert _posts(hive, "activity/proposals/b1/approve")[0][1] == {"edit": {"taskId": "t_p_piano"}}
        # 更正第一级：改块名
        w1.get_by_role("button", name="更正").click()
        w1.locator("input").fill("新块名")
        w1.get_by_role("button", name="确定").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/proposals/w1/approve")) == 1)
        assert _posts(hive, "activity/proposals/w1/approve")[0][1] == {"edit": {"rule": {"block": "新块名"}}}


def test_rule_cards_button_and_swipe_use_the_five_second_undo(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        hive.page.locator('.hd-card[data-key="w1"]').get_by_role("button", name="批准").click()
        assert hive.page.locator(".hd-toast").inner_text().startswith("已批准：")
        assert hive.page.locator(".hb-head .hb-cnt").nth(2).inner_text() == "1" and hive.page.locator(".hd-badge").inner_text() == "6"
        hive.page.clock.run_for(4900)
        assert hive.writes == [], "5 秒前不发"
        hive.page.clock.run_for(200)
        _until(hive.page, lambda: "activity/proposals/w1/approve" in hive.writes)
        hive.page.wait_for_timeout(300)   # 批准完成后的重画落定
        _swipe(hive.page, "b1", 400)    # 右滑 = 拒绝
        assert hive.page.locator(".hd-toast").last.inner_text().startswith("已拒绝：")
        hive.page.locator(".hd-toast-undo").last.click()
        assert hive.page.locator('.hd-card[data-key="b1"]').count() == 1
        hive.page.locator('.hd-card[data-key="b1"]').get_by_role("button", name="忽略").click()
        hive.page.clock.run_for(5100)
        _until(hive.page, lambda: "activity/proposals/b1/reject" in hive.writes)
        # 转后台：待发的立刻冲刷，不丢
        hive.page.wait_for_timeout(300)
        _swipe(hive.page, "nt_2", -400)
        hive.page.evaluate("() => { Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true}); document.dispatchEvent(new Event('visibilitychange')); }")
        _until(hive.page, lambda: "activity/proposals/nt_2/approve" in hive.writes)


def test_history_undo_resets(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        card = hive.page.locator('.ht-hist[data-block="Nautilus"]')
        assert "↓ 钢琴 - 钢琴的任务" in card.inner_text() and "第二级规则" in card.inner_text()
        assert "AI 直接施加" in hive.page.locator('.ht-hist[data-block="规则块"]').inner_text()
        card.get_by_role("button", name="撤销").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/reset")) == 1)
        assert _posts(hive, "activity/blocks/reset")[0][1] == {"date": DATE, "blockKey": "Nautilus", "ifSeq": 5}
        _until(hive.page, lambda: hive.page.locator(".ht-hist").count() == 1)
        _until(hive.page, lambda: _block(hive, "Nautilus").count() == 1)


def test_history_card_dragged_onto_a_task_reassigns(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, width=1900) as hive:
        _show(hive)
        hive.page.locator(".hd-hcard").first.scroll_into_view_if_needed()   # 历史段在栏的最下面
        cell = hive.page.locator("[data-project-id=p_math].hex-cell")
        _drag_to(hive.page, 0, cell)
        hive.page.clock.run_for(600); hive.page.wait_for_timeout(400)
        li = hive.page.locator(".hex-cell.is-expanded .hex-detail li[data-task-id=t_p_math]")
        _onto(hive.page, li)
        hive.page.mouse.up()
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)
        body = _posts(hive, "activity/blocks/assign")[0][1]
        assert body["blockKey"] == "Nautilus" and body["taskId"] == "t_p_math" and body["remember"] is False and body["ifSeq"] == 5
        _until(hive.page, lambda: any("已改归属" in t for t in hive.page.locator(".hd-toast").all_inner_texts()))


def test_phone_width_fits_and_swipes(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, width=390, height=800) as hive:
        _show(hive)
        box = hive.page.locator(".hd-panel").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
        assert hive.page.evaluate("document.documentElement.scrollWidth") <= 390, "没有横向滚动"
        _block(hive, DOC).get_by_role("button", name="拆分").click()
        hive.page.wait_for_selector(".ht-handle")
        bar, pbox = hive.page.locator(".ht-bar").bounding_box(), hive.page.locator(".hd-panel").bounding_box()
        assert bar["x"] + bar["width"] <= pbox["x"] + pbox["width"]
        assert hive.page.locator(".ht-part").first.bounding_box()["width"] > 200, "各段单列排"
        _block(hive, DOC).get_by_role("button", name="取消").click()
        _swipe(hive.page, _bkey(DOC), -300)
        _until(hive.page, lambda: len(_posts(hive, "activity/blocks/assign")) == 1)


def _q(hive: Any, qid: str = "aq_1") -> Any:
    return hive.page.locator(f'.ht-question[data-key="{qid}"]')


def _qrange(hive: Any, q: dict) -> str:
    return hive.page.evaluate("(q) => window.NexusTableHexTiers.qRange(q)", q)


def _hhmm(hive: Any, iso: str) -> str:
    return hive.page.evaluate("(s) => window.NexusTableHexTiers.hhmm(Date.parse(s))", iso)


def test_question_card_shows_ai_tag_and_exact_target_and_range(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        rg = _qrange(hive, Q1)
        q = _q(hive)
        assert q.locator(".ht-badge").inner_text() == "AI 提问" and q.locator(".hd-win").inner_text() == Q1["title"]
        assert "这 3 小时电脑没人用" in q.inner_text() and rg in q.inner_text()
        opt = q.locator(".ht-opt").nth(0)
        assert opt.inner_text().replace("\n", " ") == f"在练琴 ↓ 钢琴 - 钢琴的任务 · {rg}"
        assert q.locator(".ht-opt").nth(1).inner_text() == "这段不记"
        assert q.get_by_role("button", name="撤下").count() == 1 and q.locator("input").count() == 0


def test_question_text_is_never_html(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.questions[0].update(title="<img src=x onerror=window.__x=1>", text="<b>粗</b>")
        _show(hive)
        assert hive.page.locator(".ht-question img, .ht-question b").count() == 0
        assert hive.page.evaluate("window.__x") is None


def test_answer_option_records_and_removes_card(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _q(hive).locator(".ht-opt").nth(0).click()
        _until(hive.page, lambda: len(_posts(hive, "activity/questions/aq_1/answer")) == 1)
        assert _posts(hive, "activity/questions/aq_1/answer")[0][1] == {"option": 0}
        _until(hive.page, lambda: hive.page.locator(".hd-toast", has_text="已记到 钢琴 - 钢琴的任务").count() == 1)
        _until(hive.page, lambda: _q(hive).count() == 0)
        assert hive.page.locator("section[data-sec=q]").count() == 0 and hive.page.locator(".hd-badge").inner_text() == "6"


def test_answer_without_target_says_answered(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _q(hive).locator(".ht-opt").nth(1).click()
        _until(hive.page, lambda: hive.page.locator(".hd-toast", has_text="已回答：").count() == 1)
        assert _posts(hive, "activity/questions/aq_1/answer")[0][1] == {"option": 1}


def test_answer_error_shows_detail_and_keeps_card(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.answer_fail = (404, "目标任务已删除")
        _show(hive)
        _q(hive).locator(".ht-opt").nth(0).click()
        _until(hive.page, lambda: hive.page.locator(".hd-toast", has_text="目标任务已删除").count() == 1)
        assert _q(hive).count() == 1 and hive.page.locator(".hd-badge").inner_text() == "7"
        hive.answer_fail = None    # 还能再点
        _q(hive).locator(".ht-opt").nth(0).click()
        _until(hive.page, lambda: _q(hive).count() == 0)


def test_free_text_answer(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.questions.append(dict(Q2))
        _show(hive)
        q = _q(hive, "aq_2")
        assert q.locator(".ht-badge").inner_text() == "AI 提问"
        q.get_by_role("button", name="提交").click()
        hive.page.wait_for_timeout(100)
        assert _posts(hive, "activity/questions/aq_2/answer") == [], "空的不发"
        q.locator("input").fill("在开会")
        q.locator("input").press("Enter")
        _until(hive.page, lambda: len(_posts(hive, "activity/questions/aq_2/answer")) == 1)
        assert _posts(hive, "activity/questions/aq_2/answer")[0][1] == {"text": "在开会"}
        _until(hive.page, lambda: _q(hive, "aq_2").count() == 0)
        assert _q(hive).count() == 1


def test_dismiss_question_waits_five_seconds_and_can_be_undone(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        _q(hive).get_by_role("button", name="撤下").click()
        assert hive.page.locator(".hd-toast").last.inner_text().startswith("已撤下：")
        hive.page.clock.run_for(4900)
        assert _posts(hive, "activity/questions/aq_1/dismiss") == []
        hive.page.locator(".hd-toast-undo").last.click()
        assert _q(hive).count() == 1
        _q(hive).get_by_role("button", name="撤下").click()
        hive.page.clock.run_for(5100)
        _until(hive.page, lambda: len(_posts(hive, "activity/questions/aq_1/dismiss")) == 1)
        _until(hive.page, lambda: _q(hive).count() == 0)


def test_secretary_report_card_is_pinned_and_toggles(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.report = {"id": "sr_1", "date": DATE, "author": "秘书", "revision": 1, "text": "\n".join(f"第 {i} 行" for i in range(1, 9))}
        _show(hive)
        assert hive.page.evaluate("document.querySelector('.ht-body').firstElementChild.className") .startswith("hb-report"), "日报在最上面"
        txt, card = hive.page.locator(".ht-rtext"), hive.page.locator(".hb-report")
        h0 = txt.evaluate("n => n.getBoundingClientRect().height")
        assert txt.inner_text().startswith("第 1 行") and card.get_attribute("aria-expanded") == "false"
        card.click()
        assert card.get_attribute("aria-expanded") == "true" and txt.evaluate("n => n.getBoundingClientRect().height") > h0 * 2
        card.click()
        assert txt.evaluate("n => n.getBoundingClientRect().height") == h0


def test_report_alone_shows_without_questions(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.questions = []
        hive.report = {"id": "sr_1", "date": DATE, "text": "今天一切正常"}
        _show(hive)
        assert hive.page.locator(".hb-report").count() == 1 and hive.page.locator(".hb-big[data-sec=q]").count() == 0


def test_big_cards_collapsed_by_default_and_groups_by_confidence(browser, base) -> None:  # noqa: F811
    props = {**PROPS, "items": [{**PROPS["items"][0], "confidencePercent": 90}, {**PROPS["items"][1], "confidencePercent": 79}, PROPS["items"][2]]}
    with open_tiers(browser, base, props=props) as hive:
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        hive.page.locator(".hd-head").click()
        hive.page.wait_for_selector(".hb-head")
        r = hive.page.locator(".hb-big[data-sec=r]")
        assert r.locator(".hb-body").is_hidden() and hive.page.locator(".hb-big[data-sec=q] .hb-body").is_visible()
        r.locator(".hb-head").click()
        assert r.locator(".hb-gbody").count() == 2 and r.locator(".hb-ghead").all_inner_texts()[0].startswith("高置信度")
        assert r.locator(".hb-group[data-grp='r:high'] .hb-gbody").is_hidden() and r.locator(".hb-group[data-grp='r:low'] .hb-gbody").is_visible()
        assert r.locator(".hb-group[data-grp='r:high'] [data-key=w1]").count() == 1 and r.locator(".hb-group[data-grp='r:low'] [data-key=b1]").count() == 1
        r.locator(".hb-group[data-grp='r:high'] .hb-ghead").click()
        r.locator(".hb-group[data-grp='r:high'] [data-ht=batch-grp]").click()
        assert hive.page.locator(".hd-toast").inner_text().startswith("已批准 1 项")
        hive.page.clock.run_for(5200)
        _until(hive.page, lambda: hive.writes == ["activity/proposals/w1/approve"])
        r.locator("[data-ht=batch-no]").click()
        assert hive.page.locator(".hd-toast").last.inner_text().startswith("已拒绝 ")


def test_big_card_order_is_draggable_and_remembered(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        hive.page.locator(".hd-head").click()
        hive.page.wait_for_selector(".hb-head")
        hive.page.locator(".hb-big[data-sec=h] .hb-head").drag_to(hive.page.locator(".hb-big[data-sec=s] .hb-head"))
        assert hive.page.locator(".hb-title").all_inner_texts()[1] == "自动化历史"
        assert json.loads(hive.page.evaluate("localStorage.getItem('hive.decide.order')"))[:3] == ["q", "h", "s"]


def test_big_card_order_buttons(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_selector(".hd-head")
        hive.page.locator(".hd-head").click()
        hive.page.wait_for_selector(".hb-head")
        assert hive.page.locator(".hb-big[data-sec=q] [data-ht=big-up]").is_disabled() and hive.page.locator(".hb-big[data-sec=h] [data-ht=big-down]").is_disabled()
        hive.page.locator(".hb-big[data-sec=b] [data-ht=big-up]").click()
        assert hive.page.locator(".hb-title").all_inner_texts()[2:4] == ["待分类窗口块", "窗口分类规则"]
        assert json.loads(hive.page.evaluate("localStorage.getItem('hive.decide.order')"))[:4] == ["q", "s", "b", "r"]
        assert hive.page.evaluate("document.activeElement.dataset.ftok") == "mv:up:b", "重画后焦点还在按钮上"


SESSION = {"id": "ns_1", "kind": "sessionChange", "status": "pending", "eventId": "ev_9", "op": "reassign", "taskId": "t_p_piano", "projectId": "p_piano",
           "fromTaskId": "t_p_math", "fromProjectId": "p_math", "startAt": "2026-10-24T01:00:00Z", "durationSeconds": 2520, "reason": "其实在练琴", "corrected": False}


def test_session_change_card_approve_and_correct(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props={**PROPS, "sessionChanges": [SESSION]}) as hive:
        _show(hive)
        assert hive.page.locator(".hb-head .hb-cnt").all_inner_texts() == ["1", "2", "2", "3"]
        c = hive.page.locator('.hd-card[data-key="ns_1"]')
        t = c.inner_text()
        assert "更改" in t and "改挂到 钢琴的任务 任务" in t and "42 分钟" in t and _hhmm(hive, SESSION["startAt"]) in t and "其实在练琴" in t
        c.get_by_role("button", name="更正").click()
        c.locator("select").select_option("t:t_p_math")
        c.get_by_role("button", name="确定").click()
        _until(hive.page, lambda: len(_posts(hive, "activity/proposals/ns_1/approve")) == 1)
        assert _posts(hive, "activity/proposals/ns_1/approve")[0][1] == {"edit": {"taskId": "t_p_math"}}


def test_session_change_card_plain_approve_uses_five_second_undo(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props={**PROPS, "sessionChanges": [SESSION]}) as hive:
        _show(hive)
        hive.page.locator('.hd-card[data-key="ns_1"]').get_by_role("button", name="批准").click()
        assert hive.page.locator(".hd-toast").last.inner_text().startswith("已批准：")
        hive.page.clock.run_for(5100)
        _until(hive.page, lambda: "activity/proposals/ns_1/approve" in hive.writes)


def test_ime_composing_enter_does_not_submit(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.questions.append(dict(Q2))
        _show(hive)
        inp = _q(hive, "aq_2").locator("input")
        inp.fill("选词中")
        inp.evaluate("n => n.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', isComposing: true, bubbles: true}))")
        inp.evaluate("n => n.dispatchEvent(new KeyboardEvent('keydown', {key: 'Process', keyCode: 229, bubbles: true}))")
        hive.page.wait_for_timeout(200)
        assert _posts(hive, "activity/questions/aq_2/answer") == []
        inp.press("Enter")
        _until(hive.page, lambda: len(_posts(hive, "activity/questions/aq_2/answer")) == 1)


def test_stale_option_index_is_not_sent_when_options_changed(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        held: list = []
        hive.page.route("**/api/core/activity/questions?*", lambda r: held.append(r) if not held else r.fallback())   # 第一次读取在途中
        hive.page.evaluate("() => { window.NexusTableHexApp.refresh(); }")
        _until(hive.page, lambda: len(held) == 1)
        _block(hive, DOC).get_by_role("button", name="改去向").click()
        hive.page.locator(".ht-block select").focus()   # 下拉有焦点 = 重画被推迟
        new = {**Q1, "options": [{"label": "新的选项"}, {"label": "另一个"}]}
        hive.questions[0]["options"] = new["options"]
        held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"items": [new]}))
        hive.page.wait_for_timeout(300)
        assert _q(hive).locator(".ht-opt").nth(0).inner_text().startswith("在练琴"), "画面还是旧的"
        hive.page.evaluate("() => document.querySelector('.ht-question .ht-opt').click()")
        _until(hive.page, lambda: hive.page.locator(".hd-toast", has_text="问题已更新，请重选").count() == 1)
        assert _posts(hive, "activity/questions/aq_1/answer") == []
        hive.page.locator(".ht-block select").blur()
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        _until(hive.page, lambda: _q(hive).locator(".ht-opt").first.inner_text() == "新的选项")


def test_answer_failure_shows_alert_in_card_and_reloads(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.answer_fail = (409, "已经回答过了")
        _show(hive)
        hive.questions[0]["status"] = "answered"   # 服务端其实已经答过
        hive.questions.append(dict(Q2))
        _q(hive).locator(".ht-opt").nth(1).click()
        _until(hive.page, lambda: _q(hive).count() == 0 and _q(hive, "aq_2").count() == 1)
        assert hive.page.locator(".hd-toast", has_text="已经回答过了").count() == 1


def test_answer_error_card_has_alert_and_enabled_buttons(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.answer_fail = (404, "目标任务已删除")
        _show(hive)
        _q(hive).locator(".ht-opt").nth(0).click()
        _until(hive.page, lambda: _q(hive).locator('[role=alert]').count() == 1)
        assert "目标任务已删除" in _q(hive).locator('[role=alert]').inner_text()
        assert _q(hive).locator("button:disabled").count() == 0


def test_success_keeps_focus_elsewhere_and_moves_card_focus(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.questions.append(dict(Q2))
        _show(hive)
        inp = _q(hive, "aq_2").locator("input")
        inp.fill("好")
        inp.press("Enter")
        _until(hive.page, lambda: _q(hive, "aq_2").count() == 0)
        assert hive.page.evaluate("document.activeElement && document.activeElement.tagName") != "BODY", "焦点没丢到页面上"
        # 别处的焦点不被抢走：答 Q1 时焦点在另一块的按钮上
        btn = _block(hive, DOC).get_by_role("button", name="拆分")
        btn.focus()
        _q(hive).locator(".ht-opt").nth(1).evaluate("n => n.click()")
        _until(hive.page, lambda: _q(hive).count() == 0)
        assert hive.page.evaluate("document.activeElement.dataset.ht") == "split"


def test_session_correct_and_rule_correct_ignore_double_click(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base, props={**PROPS, "sessionChanges": [SESSION]}) as hive:
        _show(hive)
        c = hive.page.locator('.hd-card[data-key="ns_1"]')
        c.get_by_role("button", name="更正").click()
        c.locator("select").select_option("t:t_p_math")
        c.locator("select").blur()
        c.get_by_role("button", name="确定").evaluate("n => { n.click(); n.click(); }")
        _until(hive.page, lambda: len(_posts(hive, "activity/proposals/ns_1/approve")) >= 1)
        hive.page.wait_for_timeout(300)
        assert len(_posts(hive, "activity/proposals/ns_1/approve")) == 1
        b1 = hive.page.locator('.hd-card[data-key="b1"]')
        b1.get_by_role("button", name="更正").click()
        b1.locator("select").select_option("t:t_p_piano")
        b1.locator("select").blur()
        b1.get_by_role("button", name="确定").evaluate("n => { n.click(); n.click(); }")
        _until(hive.page, lambda: len(_posts(hive, "activity/proposals/b1/approve")) >= 1)
        hive.page.wait_for_timeout(300)
        assert len(_posts(hive, "activity/proposals/b1/approve")) == 1


def test_option_without_range_shows_label_only_and_unresolvable_target_is_disabled(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        hive.questions.append({**Q2, "id": "aq_3", "title": "没有时间段", "allowText": False, "options": [{"label": "练琴", "target": {"taskId": "t_p_piano"}}]})
        hive.questions[0]["options"] = [{"label": "已删的任务", "target": {"taskId": "t_gone"}}, {"label": "在练琴", "target": {"taskId": "t_p_piano"}}]
        _show(hive)
        assert _q(hive).locator(".ht-opt").nth(0).is_disabled() and not _q(hive).locator(".ht-opt").nth(1).is_disabled()
        o = _q(hive, "aq_3").locator(".ht-opt")
        assert o.inner_text() == "练琴" and not o.is_disabled()


def test_answering_disables_dismiss(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        hive.page.route("**/api/core/activity/questions/aq_1/answer", lambda r: None)   # 不回应：一直在进行中
        _q(hive).locator(".ht-opt").nth(1).click()
        _until(hive.page, lambda: _q(hive).get_by_role("button", name="撤下").is_disabled())


def test_non_404_read_failure_keeps_previous_questions(browser, base) -> None:  # noqa: F811
    with open_tiers(browser, base) as hive:
        _show(hive)
        hive.page.route("**/api/core/activity/questions?*", lambda r: r.fulfill(status=500, content_type="application/json", body="{}"))
        hive.page.evaluate("() => window.NexusTableHexApp.refresh()")
        hive.page.wait_for_timeout(400)
        assert _q(hive).count() == 1
