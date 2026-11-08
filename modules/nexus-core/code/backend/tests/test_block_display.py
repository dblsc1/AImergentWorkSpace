"""displayName / legible / kind（onboarding.v1 二）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from _onboarding_common import live_rec, rec, run_attending, set_browser_domains
from _tier_common import BEARER, BLK, TZ, day, db, one, upload

D = day(-1)


def _disp(client, key):
    it = one(client, D, key)
    return it["displayName"], it["legible"], it["kind"]


def test_rule_block_is_legible(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", block="示例文档")])
    assert _disp(client, "示例文档") == ("示例文档", True, "browser")


def test_browser_domain_rules(client):
    set_browser_domains(["figma.com", "*.notion.so"])
    upload(client, [rec(D, "09:00:00", "09:01:00", title="figma.com · 首页改版"),
                    rec(D, "09:02:00", "09:03:00", title="www.notion.so · x"),
                    rec(D, "09:04:00", "09:05:00", title="notion.so · y"),
                    rec(D, "09:06:00", "09:07:00", title="Figma.com · z"),
                    rec(D, "09:08:00", "09:09:00", title="other.com · z"),
                    rec(D, "09:10:00", "09:11:00", title="Google Chrome"),
                    rec(D, "09:12:00", "09:13:00", title="没有分隔符")])
    assert _disp(client, "chrome · figma.com · 首页改版") == ("figma.com · 首页改版", True, "browser")
    assert one(client, D, "chrome · www.notion.so · x")["legible"] is True
    assert one(client, D, "chrome · notion.so · y")["legible"] is True
    assert one(client, D, "chrome · Figma.com · z")["legible"] is True
    assert _disp(client, "chrome · other.com · z") == (None, False, "browser")
    assert one(client, D, "chrome · Google Chrome")["legible"] is False
    assert one(client, D, "chrome · 没有分隔符")["legible"] is False


def test_empty_domain_rules(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", title="nodefault.example · 首页改版")])
    assert _disp(client, "chrome · nodefault.example · 首页改版") == (None, False, "browser")


def test_unlisted_browser_goes_to_other(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", app="weirdbrowser", title="foo.com · x")])
    assert _disp(client, "weirdbrowser · foo.com · x") == ("foo.com · x", True, "other")


def test_terminal_unrecognized_and_folder(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", app="org.gnome.Ptyxis", title="Coder"),
                    rec(D, "10:00:00", "10:00:10", app="ptyxis", title="t", folder="a"),
                    rec(D, "10:01:00", "10:01:40", app="ptyxis", title="t", folder="b"),
                    rec(D, "11:00:00", "11:00:10", app="ptyxis", title="tie", folder="x"),
                    rec(D, "11:01:00", "11:01:10", app="ptyxis", title="tie", folder="m")])
    assert _disp(client, "org.gnome.Ptyxis · Coder") == (None, False, "terminal")
    assert _disp(client, "ptyxis · t") == ("b", True, "terminal")
    assert _disp(client, "ptyxis · tie") == ("m", True, "terminal")


def test_other_apps(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", app="code", title="plot.gd"),
                    rec(D, "09:02:00", "09:03:00", app="oddapp", title="w_ab12", pseudo=True),
                    rec(D, "09:04:00", "09:05:00", app="oddapp", title="")])
    assert _disp(client, "code · plot.gd") == ("plot.gd", True, "other")
    assert _disp(client, "oddapp · w_ab12") == (None, False, "other")
    assert _disp(client, "oddapp") == (None, False, "other")


def test_known_app_family_and_editor_workspace(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", app="code", title="a.py — proj — Visual Studio Code"),
                    rec(D, "09:02:00", "09:03:00", app="Cursor", title="x.go - svc - Cursor"),
                    rec(D, "09:04:00", "09:05:00", app="pycharm", title="myapp – main.py"),
                    rec(D, "09:06:00", "09:07:00", app="WeChat", title="窗口名1", pseudo=True),
                    rec(D, "09:08:00", "09:09:00", app="WINWORD.EXE", title="")])
    assert _disp(client, "code · a.py — proj — Visual Studio Code")[:2] == ("proj", True)
    assert _disp(client, "Cursor · x.go - svc - Cursor")[:2] == ("svc", True)
    assert _disp(client, "pycharm · myapp – main.py")[:2] == ("myapp", True)
    assert _disp(client, "WeChat · 窗口名1") == ("微信", True, "other")
    assert _disp(client, "WINWORD.EXE") == ("Word", True, "other")


def test_default_domains_and_site_names(client):
    upload(client, [rec(D, "09:00:00", "09:01:00", title="github.com · repo"),
                    rec(D, "09:02:00", "09:03:00", title="foo.readthedocs.io · x"),
                    rec(D, "09:04:00", "09:05:00", title="视频 - YouTube"),
                    rec(D, "09:06:00", "09:07:00", title="某问题 - 知乎 - Google Chrome"),
                    rec(D, "09:08:00", "09:09:00", title="页面 | Notion"),
                    rec(D, "09:10:00", "09:11:00", title="首页 - 哔哩哔哩"),
                    rec(D, "09:12:00", "09:13:00", title="evilgithub.com · x"),
                    rec(D, "09:14:00", "09:15:00", title="随便 - 某站")])
    for k in ("github.com · repo", "foo.readthedocs.io · x", "视频 - YouTube", "某问题 - 知乎 - Google Chrome", "页面 | Notion", "首页 - 哔哩哔哩"):
        assert one(client, D, f"chrome · {k}")["legible"] is True, k
    assert one(client, D, "chrome · evilgithub.com · x")["legible"] is False
    assert one(client, D, "chrome · 随便 - 某站")["legible"] is False


def test_user_domains_extend_defaults(client):
    set_browser_domains(["myco.internal"])
    upload(client, [rec(D, "09:00:00", "09:01:00", title="myco.internal · wiki"),
                    rec(D, "09:02:00", "09:03:00", title="github.com · r")])
    assert one(client, D, "chrome · myco.internal · wiki")["legible"] is True
    assert one(client, D, "chrome · github.com · r")["legible"] is True


def test_default_list_sizes():
    from app.modules.activity import window_defaults as wd

    assert len(wd.DEFAULT_DOMAINS) >= 150 and len(wd.SITE_NAMES) >= 80 and len(wd.APP_FAMILIES) >= 80


def test_terminal_with_agent_run_beats_folder(client):
    r = live_rec(-100, 30, folder="zzz")
    a = datetime.fromisoformat(r["startAt"])
    run_attending(client, "garden", [(a - timedelta(seconds=5), a + timedelta(seconds=60))])
    assert upload(client, [r])["accepted"] == 1
    it = one(client, a.astimezone(TZ).date().isoformat(), "ptyxis · garden")
    assert (it["displayName"], it["legible"], it["keySource"]) == ("garden", True, "agent")


def test_server_lists_match_appendix():
    from app.modules.activity import block_display as bd

    assert len(bd.BROWSERS) == 20 and len(bd.TERMINALS) == 34
    assert {"google-chrome", "Arc", "librewolf"} <= bd.BROWSERS and {"org.gnome.Ptyxis", "WindowsTerminal", "dev.warp.Warp"} <= bd.TERMINALS


def test_device_token_still_forbidden(client):
    assert client.get(BLK, params={"date": D}, headers=BEARER).status_code == 403


def test_focus_display_name(client):
    from test_auto_track import _beat  # noqa: PLC0415

    set_browser_domains(["figma.com"])
    _beat(client, app="chrome", title="figma.com · 首页改版")
    assert client.get("/api/core/views/current").json()["focus"]["displayName"] == "figma.com · 首页改版"
    _beat(client, app="chrome", title="other.com · x")
    assert client.get("/api/core/views/current").json()["focus"]["displayName"] is None
