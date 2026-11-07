"""蜂巢新布局的网关路由（hive.stage.v1「一」、desktop.pet.v1「三」；用例 HV-N01–N03）。

不起 nginx：把 deploy/nginx/templates/default.conf.template 按 envsubst 的口径渲染，再按 nginx 的
location 选择规则（精确 > 最长前缀）找出每个请求落在哪一段，核对那一段写了什么；compose 里核对宠物包的卷。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "deploy" / "nginx" / "templates" / "default.conf.template"
COMPOSE = ROOT / "deploy" / "docker-compose.yml"


def _blocks(base: str) -> list[tuple[str, str, str]]:
    """渲染后的 server 里每个 `location [=] path { … }` → (修饰, 路径, 块内文本)。"""
    text = TEMPLATE.read_text(encoding="utf-8").replace("${HONEYCOMB_BASE_PATH}", base)
    out = []
    for m in re.finditer(r"^\s*location\s+(=\s+)?(\S+)\s*\{", text, re.M):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out.append(("=" if m.group(1) else "", m.group(2), text[m.end():i - 1]))
    return out


def _pick(base: str, uri: str) -> str:
    blocks = _blocks(base)
    for mod, path, body in blocks:
        if mod == "=" and path == uri:
            return body
    best = max((b for b in blocks if not b[0] and not b[1].startswith("@") and uri.startswith(b[1])),
               key=lambda b: len(b[1]))
    return best[2]


@pytest.mark.parametrize("base", ["/", "/Cockpit/"])
@pytest.mark.parametrize("uri", ["timer", "timer/"])
def test_timer_page_redirects_to_hive(base: str, uri: str) -> None:  # HV-N01
    body = _pick(base, base + uri)
    assert f"return 302 {base}hive/;" in body


@pytest.mark.parametrize("base", ["/", "/Cockpit/"])
def test_hive_pets_are_served_from_their_own_volume(base: str) -> None:  # HV-N03
    body = _pick(base, base + "hive/pets/dsh/pack.json")
    assert "alias /usr/share/nginx/html/hive-pets/;" in body
    assert "gate.inc" not in body            # 素材不含用户数据，同 __cockpit/
    assert "try_files $uri =404;" in body    # 不存在的种类 404，不回落到页面
    assert "hive-pets" not in _pick(base, base + "hive/app.js")


@pytest.mark.parametrize("base", ["/", "/Cockpit/"])
def test_hive_mini_is_not_blocked(base: str) -> None:  # HV-N02
    body = _pick(base, base + "hive/mini")
    assert "mini.html" in body and "gate.inc" in body   # 自己的精确路由（#179），过登录门


def test_compose_mounts_pet_volume_read_only_and_declares_it() -> None:
    text = COMPOSE.read_text(encoding="utf-8")
    assert re.search(r"^  honeycomb_pets_data:", text, re.M)
    assert "- honeycomb_pets_data:/usr/share/nginx/html/hive-pets:ro" in text
