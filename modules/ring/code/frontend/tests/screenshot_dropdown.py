"""截一张"两级选择器展开"的图，证明三个项目都在、零任务项目显示不可选提示行
（F-RING-2/F-RING-6 的两级选择器版人眼证据；2026-08-03 版截的是旧版单选优化，
本版随 cockpit-v1 改造重截，两个 <select> 同时展开）。

    python screenshot_dropdown.py [输出路径]

**只读**：树数据优先从本机后端 `GET /api/core/views/tree` 现拉（纯读接口），
拉不到就退回 conftest 里的静态夹具。计时接口一律 abort —— 本脚本不可能写库。

### 为什么是 `size` 展开而不是真的点开下拉

原生 `<select>` 的下拉是**操作系统级弹窗**，不在页面渲染树里，Playwright
（乃至任何截页面的工具）都截不到它。所以这里把两个 `<select>` 都临时设成
`size=N` 让它们以列表框形式**原地铺开**——渲染的是同一批 option，不可选的
提示行照样是灰的，与点开下拉看到的内容逐行一致。
"""

from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.sync_api import sync_playwright  # noqa: E402

import conftest as C  # noqa: E402

BACKEND_TREE_URL = "http://127.0.0.1:8000/api/core/views/tree"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "screenshots" / "2026-08-08-两级选择器展开.png"
# 展开任务选择器时预先选中的项目：考级计划是零任务项目，这样一张图同时
# 证明「三个项目都在」（project-select 展开）与「零任务项目的提示行」
# （task-select 展开）两件事。
PREVIEW_EMPTY_PROJECT_ID = "p_book"


def live_tree() -> tuple[dict, str]:
    try:
        with urllib.request.urlopen(BACKEND_TREE_URL, timeout=3) as resp:
            if resp.status == 200:
                return json.load(resp), "本机后端数据（只读拉取）"
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return C.TREE, "静态夹具（后端不可达）"


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    tree, source = live_tree()
    names = [p["name"] for p in tree.get("projects", [])]
    empty_project_id = next(
        (p["id"] for p in tree.get("projects", []) if not p.get("tasks")),
        PREVIEW_EMPTY_PROJECT_ID,
    )
    print(f"树数据来源：{source}；项目：{names}；预览零任务项目：{empty_project_id}")

    handler = functools.partial(C._QuietHandler, directory=str(C.FRONTEND_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_context(viewport={"width": 900, "height": 760}).new_page()

            page.route("**/api/core/views/tree", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(tree, ensure_ascii=False)))
            page.route("**/api/core/views/current", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps(C.CURRENT_IDLE, ensure_ascii=False)))
            page.route("**/__cockpit/current", lambda r: r.fulfill(
                status=200, content_type="application/json", body='{"running": false}'))
            page.route("**/api/core/timer/**", lambda r: r.abort())

            page.goto(f"{base}/{C.PAGE_NAME}")
            page.wait_for_selector("#start-big-btn", state="attached")
            page.select_option("#project-select", empty_project_id)

            # 顶栏：网关活着就按 nginx 的 sub_filter 原样注入，让截图与线上一致。
            try:
                with urllib.request.urlopen(f"{C.GATEWAY_BASE_URL}/healthz", timeout=2) as resp:
                    if resp.status == 200:
                        C._inject_navbar(page)
                        page.wait_for_timeout(600)
            except (urllib.error.URLError, OSError):
                print("顶栏未注入（网关不可达）")

            counts = page.evaluate(
                """() => {
                     const proj = document.getElementById("project-select");
                     const task = document.getElementById("task-select");
                     proj.size = proj.options.length; proj.style.height = "auto";
                     task.size = Math.max(2, task.options.length); task.style.height = "auto";
                     return { projects: proj.options.length, taskRows: task.options.length };
                   }"""
            )
            print(f"project-select 行数：{counts['projects']}；task-select 行数：{counts['taskRows']}")
            page.wait_for_timeout(300)
            page.screenshot(path=str(out), full_page=True)
            print(f"已写出：{out}")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
