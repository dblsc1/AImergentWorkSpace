"""拖「分类历史」卡片穿过大蜂巢的性能护栏（hex-decide.js 拖拽）。

大树：12 区 / 60 项目 / 400 任务，历史 60 张。用 CDP 把 CPU 降速 4 倍模拟慢机，拖一张卡穿过 ~8 个格子再放到任务上。
断言的是**次数**（App.expand 调用、elementFromPoint、格子重画），不是墙钟 —— CI 上墙钟会抖。
直接跑本文件（python tests/test_hive_drag_perf.py <base-url>）会打印 getMetrics / 长帧，用来前后对比。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any, Iterator

from test_hive_decide import _install, _until  # noqa: F401
from test_hive_focus import TREE, base, browser, open_hive  # noqa: F401  (fixtures)


def big_tree() -> dict:
    zones = [{"id": f"z{i}", "key": f"Z{i:02d}", "name": f"区{i}", "color": f"hsl({i * 30} 70% 55%)", "order": i} for i in range(12)]
    projects = []
    for n in range(60):
        pid = f"p{n}"
        projects.append({"id": pid, "key": pid, "zoneId": f"z{n % 12}", "name": f"项目{n}", "status": "active", "progress": 0,
                         "progressSource": "computed", "deadline": None, "unclassifiedTaskId": None,
                         "tasks": [{"id": f"t{n}_{k}", "key": "k", "name": f"项目{n}的任务{k}", "done": k % 5 == 0, "kind": "normal",
                                    "flags": [], "plan": None, "dependsOn": []} for k in range(7)]})
    return {"zones": zones, "projects": projects}   # 60 * 7 = 420 个任务


def big_hist() -> dict:
    items = [{"id": f"a{i}", "eventId": f"e{i}", "startAt": "2026-10-23T01:00:00Z", "endAt": "2026-10-23T01:10:00Z", "durationSeconds": 600,
              "app": "code", "title": f"窗口标题{i}", "taskId": f"t{i % 60}_1", "projectId": f"p{i % 60}", "reassigned": False,
              "source": "rules", "ai": False} for i in range(60)]
    return {"date": "2026-10-23", "items": items}


HOOK = """() => {
  const A = window.NexusTableHexApp, st = { expand: 0, efp: 0, frames: [], fills: 0 };
  window.__perf = st;
  const ex = A.expand; A.expand = function () { st.expand++; return ex.apply(this, arguments); };
  const efp = document.elementFromPoint.bind(document); document.elementFromPoint = function () { st.efp++; return efp.apply(document, arguments); };
  new MutationObserver(ms => { for (const m of ms) if (m.target.classList && m.target.classList.contains("hex-body")) st.fills++; })
    .observe(document.getElementById("hive"), { childList: true, subtree: true });
  let last = performance.now();
  (function loop(t) { st.frames.push(t - last); last = t; requestAnimationFrame(loop); })(last);
}"""


@contextlib.contextmanager
def big_hive(browser: Any, base: str, fake_clock: bool) -> Iterator[Any]:
    """大树 + 60 张历史卡，面板已展开。fake_clock=False 走真时间（量性能），True 走假时钟（断言次数）。"""
    with open_hive(browser, base, width=1900, height=1000, fake_clock=fake_clock) as hive:
        tree, orig = big_tree(), hive.route

        def route(r: Any) -> None:
            if "/api/core/views/tree" in r.request.url:
                r.fulfill(status=200, content_type="application/json", body=json.dumps(tree, ensure_ascii=False))
            else:
                orig(r)
        page = hive.page
        page.unroute("**/api/core/**")
        page.route("**/api/core/**", route)
        _install(hive)
        page.route("**/api/core/activity/auto*", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(big_hist())))
        page.evaluate("() => window.NexusTableHexApp.refresh()")
        page.wait_for_selector(".hd-head")
        page.wait_for_selector(".hex-cell[data-project-id=p59]")
        page.locator(".hd-head").click()
        page.wait_for_selector(".hd-hcard")
        assert page.locator(".hd-hcard").count() == 60
        yield hive


def run_drag(browser: Any, base: str, throttle: int = 4) -> dict:
    """真时间 + CPU 降速：拖一张历史卡扫过蜂巢（60 步，~8 个格子）再放到任务上。返回次数 / 长帧 / getMetrics 差值。"""
    with big_hive(browser, base, False) as hive:
        page = hive.page
        page.wait_for_timeout(800)
        cdp = page.context.new_cdp_session(page)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
        cdp.send("Performance.enable")
        page.evaluate(HOOK)
        before = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
        box = page.locator(".hd-hcard .hd-face").first.bounding_box()
        page.mouse.move(box["x"] + 30, box["y"] + 20)
        page.mouse.down()
        for i in range(60):   # 60 步 x 20px
            page.mouse.move(600 + 20 * i, 400 + 3 * i)
        page.wait_for_timeout(300)
        li = page.locator(".hex-cell.is-expanded .hex-detail li[data-task-id]").first
        if li.count():
            t = li.bounding_box()
            page.mouse.move(t["x"] + 20, t["y"] + t["height"] / 2, steps=4)
            page.wait_for_timeout(100)
        page.mouse.up()
        after = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
        st = page.evaluate("() => window.__perf")
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 1})
        fr = st["frames"]
        return {"expand": st["expand"], "efp": st["efp"], "fills": st["fills"], "long_frames": sum(1 for f in fr if f > 50),
                "max_frame": round(max(fr)), "frames": len(fr),
                **{k: round(after[k] - before[k], 3) for k in ("ScriptDuration", "LayoutDuration", "RecalcStyleDuration", "LayoutCount", "RecalcStyleCount")}}


def test_sweep_over_cells_expands_none_until_dwell(browser, base) -> None:  # noqa: F811
    """假时钟：指针一口气扫过 ~8 个格子（时间没走）= 一个格子都不展开、每帧至多一次命中测试；停下 180ms 才展开停住的那格。"""
    with big_hive(browser, base, True) as hive:
        page = hive.page
        page.evaluate(HOOK)
        box = page.locator(".hd-hcard .hd-face").first.bounding_box()
        page.mouse.move(box["x"] + 30, box["y"] + 20)
        page.mouse.down()
        page.mouse.move(box["x"] - 40, box["y"] + 20, steps=3)
        for i in range(60):
            page.mouse.move(300 + 20 * i, 300 + 3 * i)
        t = page.evaluate("""() => { for (const c of document.querySelectorAll(".hex-cell[data-project-id]")) {
            const r = c.getBoundingClientRect(); if (r.left > 200 && r.right < 1100 && r.top > 150 && r.bottom < 900) return { x: r.x, y: r.y, width: r.width, height: r.height }; } }""")
        assert t, "视口里没有项目格"
        page.mouse.move(t["x"] + t["width"] / 2, t["y"] + t["height"] / 2)   # 停在一个格子上
        page.clock.run_for(16)   # 一帧：60 个 pointermove 合并成一次处理
        st = page.evaluate("() => window.__perf")
        assert st["expand"] == 0 and st["efp"] <= 2, st
        page.clock.run_for(300)
        st = page.evaluate("() => window.__perf")
        assert st["expand"] == 1, "停下后只展开停住的那一格"
        assert st["efp"] <= 6, st
        assert page.evaluate("() => document.querySelector('.hive-cam').style.transition") == "", "拖拽中的展开不做推镜过渡"
        assert page.evaluate("() => getComputedStyle(document.querySelector('.hex-borders')).display") == "none", "拖拽中不画界线层"
        page.mouse.move(5, 500)
        page.mouse.up()


def test_drag_over_big_hive_budget(browser, base) -> None:  # noqa: F811
    r = run_drag(browser, base)
    print("\nDRAG-PERF", json.dumps(r))
    assert r["expand"] <= 12, "路过的格子不该一格一展开（悬停意图延迟）"
    assert r["efp"] <= 80, "命中测试每帧最多一次"
    assert r["fills"] <= 30, "格子内容重画次数"
    assert r["LayoutCount"] <= 40, "强制同步布局（拖拽里读 offsetWidth / clientWidth）没回来"
