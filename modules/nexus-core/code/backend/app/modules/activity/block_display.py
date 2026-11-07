"""窗口块的显示名 ``displayName`` / 看得懂 ``legible`` / 类 ``kind``（契约 onboarding.v1「二」，一处算）。

``GET activity/blocks``、``views/current.focus`` 都走 ``describe``；页面与 AI 不许各自再算。
"""

from __future__ import annotations

from . import blocks_repo, history

_SEP = " · "

# 契约附录「程序名单」：整串相等、不分大小写；只增。
BROWSERS = {"chrome", "Google Chrome", "google-chrome", "chromium", "chromium-browser", "firefox", "firefox-esr", "msedge",
            "Microsoft Edge", "microsoft-edge", "Safari", "brave", "Brave Browser", "brave-browser", "opera", "vivaldi",
            "vivaldi-stable", "Arc", "zen", "librewolf"}
TERMINALS = {"gnome-terminal", "gnome-terminal-server", "org.gnome.Terminal", "org.gnome.Ptyxis", "ptyxis", "org.gnome.Console",
             "kgx", "kitty", "Alacritty", "wezterm", "wezterm-gui", "org.wezfurlong.wezterm", "konsole", "org.kde.konsole",
             "xterm", "tilix", "com.gexperts.Tilix", "foot", "footclient", "terminator", "xfce4-terminal", "ghostty",
             "com.mitchellh.ghostty", "Terminal", "iTerm2", "iTerm", "WindowsTerminal", "cmd", "powershell", "pwsh", "Warp",
             "dev.warp.Warp", "Hyper", "Tabby"}
_BROWSERS, _TERMINALS = {a.casefold() for a in BROWSERS}, {a.casefold() for a in TERMINALS}


def kind_of(app: str | None) -> str:
    a = (app or "").casefold()
    return "browser" if a in _BROWSERS else "terminal" if a in _TERMINALS else "other"


def domains(user: str) -> list[str]:
    """账号设置的网页分类规则；读不到 / 没有 → 空。"""
    try:
        got = (blocks_repo.get_settings(user) or {}).get("browserDomains")
        return [d for d in got if isinstance(d, str)] if isinstance(got, list) else []
    except Exception:  # noqa: BLE001 —— 设置读不到只让浏览器块「看不懂」，不让整个读失败
        return []


def _allowed(domain: str, rules: list[str]) -> bool:
    d = domain.strip().casefold()
    for r in rules:
        r = r.casefold()
        if r.startswith("*."):
            if d == r[2:] or d.endswith(r[1:]):
                return True
        elif d == r:
            return True
    return False


def describe(key: str, source: str, recs: list[dict], rules: list[str]) -> dict:
    """``recs``：块里的记录 ``{app, title, titleIsPseudonym, seconds, folder?}``。返回 ``{displayName, legible, kind}``。"""
    secs: dict[str, float] = {}
    for r in recs:
        secs[r["app"]] = secs.get(r["app"], 0) + r.get("seconds", 0)
    app = min(secs, key=lambda a: (-secs[a], a)) if secs else ""
    kind = kind_of(app)
    rest = key[len(app) + len(_SEP):] if key.startswith(app + _SEP) else ""

    def out(name: str | None) -> dict:
        ok = bool(name)
        return {"displayName": name if ok else None, "legible": ok, "kind": kind}

    if source == "rule":
        return out(key)
    mine = [r for r in recs if r["app"] == app]
    if kind == "browser":
        title = history.norm_title(max(mine, key=lambda r: r.get("seconds", 0))["title"]) if mine else rest
        head, sep, _ = title.partition(_SEP)
        return out(title if sep and _allowed(head, rules) else None)
    if kind == "terminal":
        if source == "agent":
            return out(key.partition(_SEP)[2])
        by: dict[str, float] = {}
        for r in mine:
            if r.get("folder"):
                by[r["folder"]] = by.get(r["folder"], 0) + r.get("seconds", 0)
        return out(min(by, key=lambda f: (-by[f], f)) if by else None)
    return out(rest if rest and not all(r.get("titleIsPseudonym") for r in mine) else None)
