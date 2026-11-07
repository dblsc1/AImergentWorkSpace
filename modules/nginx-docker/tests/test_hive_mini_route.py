"""/hive/mini（桌宠浮窗里的蜂巢缩略页）的网关路由：过登录门、送 mini.html、不注入顶栏。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = (ROOT / "deploy/nginx/templates/default.conf.template").read_text(encoding="utf-8")
BLOCK = re.search(r"location = \$\{HONEYCOMB_BASE_PATH\}hive/mini \{(.*?)\n    \}", TEMPLATE, re.S)


def test_route_exists_and_serves_mini_page() -> None:
    assert BLOCK, "缺 location = .../hive/mini"
    assert "alias /usr/share/nginx/html/hive/mini.html;" in BLOCK.group(1)
    assert (ROOT / "modules/hive/code/frontend/mini.html").is_file()


def test_route_is_gated_without_navbar() -> None:
    body = BLOCK.group(1)
    assert "gate.inc" in body, "页面本身要过登录门"
    assert "inject.inc" not in body, "小窗口里不要顶栏"
    assert "window.HONEYCOMB_BASE" in body, "子路径部署要拿到站点前缀"


def test_route_comes_before_generic_hive_location() -> None:
    assert TEMPLATE.index("hive/mini {") < TEMPLATE.index("location ${HONEYCOMB_BASE_PATH}hive/ {")
