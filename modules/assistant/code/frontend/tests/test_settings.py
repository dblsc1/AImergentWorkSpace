"""「活动检测设置」面板（settings.js，contracts/detector.settings.v1）。

判据落在行为上：表单照文档填得对不对、PUT 出去的文档一字不差、422 挂到哪一项、强制清洗是灰的关不掉、
换设备 / 恢复默认发了什么请求、窄屏不横滚。
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import open_page, overflowing
from playwright.sync_api import Route

DEFAULTS = {
    "schemaVersion": 1,
    "privacy": {"paths": "full", "pathWhitelist": [], "titles": "keep", "appOnly": True, "appOnlyApps": None,
                "browser": "domain", "queryStrings": True, "emails": True, "phones": True, "addresses": True,
                "ips": True, "usernames": True, "longNumbers": True},
    "idle": {"afkThresholdMinutes": 0, "audibleAsPresent": False, "focusAppsEnabled": False, "focusApps": None,
             "focusMaxMinutes": 60, "idleSuggestions": False},
}

CUSTOM = copy.deepcopy(DEFAULTS)
CUSTOM["privacy"].update({"paths": "half", "pathWhitelist": ["garden/\\S+", "(?i)notes"], "titles": "pseudonymize",
                          "appOnlyApps": ["WeChat", "Slack"], "browser": "full", "phones": False,
                          "futureKey": True})   # 服务端将来追加的键：页面不认识，也得原样带回去
CUSTOM["idle"].update({"afkThresholdMinutes": 10, "focusAppsEnabled": True, "idleSuggestions": True})

DEVICES = [
    {"deviceId": "dev_a", "lastUploadAt": "2026-09-30T01:55:02+00:00", "lastFetchAt": "2026-09-30T01:56:00+00:00",
     "hasSettings": True, "settingsUpdatedAt": "2026-09-30T02:00:00+00:00"},
    {"deviceId": "dev_b", "lastUploadAt": None, "lastFetchAt": None, "hasSettings": False, "settingsUpdatedAt": None},
]


class DetectorStub:
    def __init__(self, devices: list[dict] | None = None, settings: dict[str, dict | None] | None = None) -> None:
        self.devices = devices if devices is not None else copy.deepcopy(DEVICES)
        self.settings = settings if settings is not None else {"dev_a": copy.deepcopy(CUSTOM), "dev_b": None}
        self.calls: list[tuple[str, str, str | None, Any]] = []   # (方法, 路径, deviceId, body)
        self.put_reply: tuple[int, dict] | None = None             # 非 None = PUT 回这个（422 / 403…）
        self.get_status = 200                                      # 非 200 = 读设置出错

    def route(self, route: Route) -> None:
        req = route.request
        u = urlparse(req.url)
        path = u.path.split("/api/core/detector/", 1)[1]
        dev = (parse_qs(u.query).get("deviceId") or [None])[0]
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, path, dev, body))
        j = lambda code, obj: route.fulfill(status=code, content_type="application/json",  # noqa: E731
                                            body=json.dumps(obj, ensure_ascii=False))
        if path == "devices":
            if self.devices is None:
                return j(404, {"detail": "Not Found"})
            return j(200, {"devices": self.devices})
        if req.method == "GET":
            if self.get_status != 200:
                return j(self.get_status, {"detail": "数据库暂时连不上"})
            s = self.settings.get(dev)
            return j(200, {"deviceId": dev, "settings": s, "updatedAt": "2026-09-30T02:00:00+00:00" if s else None})
        if req.method == "PUT":
            if self.put_reply:
                return j(*self.put_reply)
            self.settings[dev] = body
            return j(200, {"deviceId": dev, "settings": body, "updatedAt": "2026-09-30T03:00:00+00:00"})
        if req.method == "DELETE":
            self.settings[dev] = None
            return route.fulfill(status=204)
        return j(405, {"detail": "no"})


def page_with(browser, base, stub: DetectorStub, **kw):
    return open_page(browser, base, routes={r"/api/core/detector/": stub.route}, **kw)


def ready(page) -> None:
    page.wait_for_selector("#det-form:not([hidden])")


def puts(stub):
    return [c for c in stub.calls if c[0] == "PUT"]


def test_renders_devices_and_settings_from_fixture(browser, static_base_url):
    stub = DetectorStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        opts = page.eval_on_selector_all("#det-device option", "os => os.map(o => [o.value, o.textContent])")
        assert opts == [["dev_a", "dev_a · 最近活动 09-30 09:56"], ["dev_b", "dev_b · 还没见过活动"]]
        assert page.input_value("#det-device") == "dev_a"
        assert ("GET", "settings", "dev_a", None) in stub.calls
        meta = page.inner_text("#det-device-meta")
        assert "最近上传建议：09-30 09:55" in meta and "最近拉设置：09-30 09:56" in meta and "网页设置：有" in meta
        assert page.is_checked('input[name="privacy.paths"][value="half"]')
        assert page.is_checked('input[name="privacy.titles"][value="pseudonymize"]')
        assert page.is_checked('input[name="privacy.browser"][value="full"]')
        assert page.input_value("#det-whitelist") == "garden/\\S+\n(?i)notes"
        assert not page.is_checked('[data-null="privacy.appOnlyApps"]')
        assert page.input_value("#det-apponly-apps") == "WeChat\nSlack" and page.is_enabled("#det-apponly-apps")
        assert page.is_checked('[data-null="idle.focusApps"]') and page.is_disabled("#det-focus-apps")
        assert not page.is_checked('[data-key="privacy.phones"]') and page.is_checked('[data-key="privacy.emails"]')
        assert page.input_value("#det-afk") == "10" and page.input_value("#det-focus-max") == "60"
        assert page.is_checked('[data-key="idle.idleSuggestions"]')
        # 没改动：不标脏、保存禁用；有网页设置：恢复默认可点
        assert page.is_hidden("#det-dirty") and page.is_disabled("#det-save") and page.is_enabled("#det-reset")
        assert page.is_hidden("#det-note")
        assert "下一轮" in page.inner_text("#settings-panel .suggest-sub")


def test_mandatory_redaction_is_checked_greyed_and_disabled(browser, static_base_url):
    with page_with(browser, static_base_url, DetectorStub()) as page:
        ready(page)
        boxes = page.locator(".set-mandatory input[type=checkbox]")
        assert boxes.count() == 5
        for i in range(5):
            assert boxes.nth(i).is_checked() and boxes.nth(i).is_disabled()
            assert boxes.nth(i).get_attribute("data-key") is None     # 从不进文档
        text = page.inner_text(".set-mandatory")
        for word in ("密码", "密钥 / 令牌", "私钥", "银行卡号", "身份证号", "不能关；需要改源码重新编译（见 ai-detector README）"):
            assert word in text
        assert page.eval_on_selector(".set-mandatory .set-opt", "e => getComputedStyle(e).cursor") == "not-allowed"


def test_save_sends_exact_document_and_keeps_unknown_keys(browser, static_base_url):
    stub = DetectorStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.check('input[name="privacy.paths"][value="off"]')
        page.uncheck('[data-key="privacy.emails"]')
        page.fill("#det-whitelist", "garden/\\S+\n(?i)notes\n\n ^docs/[a-z]+$ ")
        page.check('[data-null="privacy.appOnlyApps"]')            # 改回用本机名单 = null
        assert page.is_disabled("#det-apponly-apps")
        page.uncheck('[data-null="idle.focusApps"]')
        page.fill("#det-focus-apps", "Zotero\n腾讯会议")
        page.fill("#det-afk", "15")
        page.check('[data-key="idle.audibleAsPresent"]')
        assert page.is_visible("#det-dirty") and page.is_enabled("#det-save")
        page.click("#det-save")
        page.wait_for_selector("#det-message:not([hidden])")
        want = copy.deepcopy(CUSTOM)
        want["privacy"].update({"paths": "off", "emails": False, "appOnlyApps": None,
                                "pathWhitelist": ["garden/\\S+", "(?i)notes", " ^docs/[a-z]+$ "]})
        want["idle"].update({"afkThresholdMinutes": 15, "audibleAsPresent": True, "focusApps": ["Zotero", "腾讯会议"]})
        assert puts(stub) == [("PUT", "settings", "dev_a", want)]
        assert "已保存" in page.inner_text("#det-message") and "5 分钟" in page.inner_text("#det-message")
        assert page.is_hidden("#det-dirty") and page.is_disabled("#det-save")


def test_dirty_clears_when_change_is_undone(browser, static_base_url):
    with page_with(browser, static_base_url, DetectorStub()) as page:
        ready(page)
        page.uncheck('[data-key="privacy.ips"]')
        assert page.is_visible("#det-dirty")
        page.check('[data-key="privacy.ips"]')
        assert page.is_hidden("#det-dirty") and page.is_disabled("#det-save")


@pytest.mark.parametrize("field,sel,value,words", [
    ("privacy.pathWhitelist", "#det-whitelist", "ok/.*\n(?<=x)y", "第 2 条"),
    ("privacy.pathWhitelist", "#det-whitelist", "a\\1", "RE2 不支持"),
    ("idle.afkThresholdMinutes", "#det-afk", "241", "0–240"),
    ("idle.focusMaxMinutes", "#det-focus-max", "1.5", "1–480"),
])
def test_client_validation_blocks_save(browser, static_base_url, field, sel, value, words):
    stub = DetectorStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.fill(sel, value)
        page.click("#det-save")
        err = page.locator(f'[data-field="{field}"] .field-error')
        err.wait_for(state="visible")
        assert words in err.inner_text()
        assert page.get_attribute(sel, "aria-invalid") == "true"
        assert page.evaluate("document.activeElement.id") == sel[1:]
        assert puts(stub) == []


def test_whitelist_accepts_re2_only_syntax(browser, static_base_url):
    r = None
    with page_with(browser, static_base_url, DetectorStub()) as page:
        ready(page)
        r = page.evaluate("""() => ['(?i)notes', '\\\\p{Han}+', '(?P<n>a)b', 'end\\\\z', '(?i:x)y', '\\\\Q(\\\\E']
            .map(p => window.assistantSettings.checkPattern(p))""")
    assert r == ["", "", "", "", "", ""]


def test_server_422_goes_under_its_field(browser, static_base_url):
    stub = DetectorStub()
    stub.put_reply = (422, {"detail": "idle.focusMaxMinutes: Input should be less than or equal to 480"})
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.uncheck('[data-key="privacy.ips"]')
        page.click("#det-save")
        err = page.locator('[data-field="idle.focusMaxMinutes"] .field-error')
        err.wait_for(state="visible")
        assert err.inner_text() == "idle.focusMaxMinutes: Input should be less than or equal to 480"
        assert "服务器没收" in page.inner_text("#det-message")
        assert page.is_visible("#det-dirty")          # 没存上，改动还在
        # 对不上某一项的 422 显示在表单底部
        stub.put_reply = (422, {"detail": "deviceId 格式非法：'x y'"})
        page.click("#det-save")
        page.wait_for_function("document.querySelector('#det-message').textContent.includes('deviceId')")
        assert page.is_hidden('[data-field="idle.focusMaxMinutes"] .field-error')


def test_server_403_is_explained(browser, static_base_url):
    stub = DetectorStub()
    stub.put_reply = (403, {"detail": "设备令牌只能读检测设置"})
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.uncheck('[data-key="privacy.ips"]')
        page.click("#det-save")
        page.wait_for_selector("#det-message.is-error")
        msg = page.inner_text("#det-message")
        assert "403" in msg and "设备令牌只能读检测设置" in msg


def test_reset_deletes_and_shows_defaults(browser, static_base_url):
    stub = DetectorStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.once("dialog", lambda d: d.accept())
        page.click("#det-reset")
        page.wait_for_selector("#det-note:not([hidden])")
        assert ("DELETE", "settings", "dev_a", None) in stub.calls
        assert page.is_checked('input[name="privacy.paths"][value="full"]')
        assert page.input_value("#det-whitelist") == "" and page.input_value("#det-afk") == "0"
        assert page.is_checked('[data-null="privacy.appOnlyApps"]')
        assert page.is_disabled("#det-reset")
        assert page.is_enabled("#det-save")             # 没有网页设置：可以把缺省值存成网页设置
        assert "本机" in page.inner_text("#det-message")


def test_reset_cancelled_sends_nothing(browser, static_base_url):
    stub = DetectorStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.once("dialog", lambda d: d.dismiss())
        page.click("#det-reset")
        page.wait_for_timeout(200)
        assert not [c for c in stub.calls if c[0] == "DELETE"]


def test_switch_device(browser, static_base_url):
    stub = DetectorStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.select_option("#det-device", "dev_b")
        page.wait_for_selector("#det-note:not([hidden])")
        assert ("GET", "settings", "dev_b", None) in stub.calls
        assert page.is_checked('input[name="privacy.titles"][value="keep"]')
        assert "用本机配置文件" in page.inner_text("#det-device-meta")
        page.click("#det-save")                          # 缺省值原样存成这台设备的网页设置
        page.wait_for_selector("#det-message:not([hidden])")
        assert puts(stub) == [("PUT", "settings", "dev_b", DEFAULTS)]


def test_switch_device_with_unsaved_changes_asks_first(browser, static_base_url):
    stub = DetectorStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.uncheck('[data-key="privacy.ips"]')
        page.once("dialog", lambda d: d.dismiss())
        page.select_option("#det-device", "dev_b")
        page.wait_for_timeout(200)
        assert page.input_value("#det-device") == "dev_a"
        assert ("GET", "settings", "dev_b", None) not in stub.calls
        assert not page.is_checked('[data-key="privacy.ips"]')     # 改动还在


def test_presence_absent_is_not_shown_or_sent(browser, static_base_url):
    stub = DetectorStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        assert page.is_hidden("#det-presence-row")
        page.uncheck('[data-key="privacy.ips"]')
        page.click("#det-save")
        page.wait_for_selector("#det-message:not([hidden])")
        body = puts(stub)[0][3]
        assert "presence" not in body and "presence" not in body["privacy"] and "presence" not in body["idle"]


@pytest.mark.parametrize("where", ["top", "idle"])
def test_presence_present_round_trips_in_place(browser, static_base_url, where):
    s = copy.deepcopy(CUSTOM)
    (s if where == "top" else s["idle"])["presence"] = False
    stub = DetectorStub(settings={"dev_a": s, "dev_b": None})
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        assert page.is_visible("#det-presence-row") and not page.is_checked("#det-presence")
        page.check("#det-presence")
        assert page.is_visible("#det-dirty")
        page.click("#det-save")
        page.wait_for_selector("#det-message:not([hidden])")
        body = puts(stub)[0][3]
        assert (body if where == "top" else body["idle"])["presence"] is True
        # 换到没有 presence 的设备：勾选项消失
        page.select_option("#det-device", "dev_b")
        page.wait_for_selector("#det-note:not([hidden])")
        assert page.is_hidden("#det-presence-row")


def test_old_backend_without_endpoint(browser, static_base_url):
    stub = DetectorStub()
    stub.devices = None   # devices 404 = 后端早于 v2.5
    with page_with(browser, static_base_url, stub) as page:
        page.wait_for_selector("#det-note:not([hidden])")
        assert "v2.5" in page.inner_text("#det-note")
        assert page.is_hidden("#det-form") and page.is_hidden("#det-device-row")


def test_no_devices_yet(browser, static_base_url):
    with page_with(browser, static_base_url, DetectorStub(devices=[])) as page:
        page.wait_for_selector("#det-note:not([hidden])")
        assert "还没有设备" in page.inner_text("#det-note")
        assert page.is_hidden("#det-form")


def test_keyboard_reaches_and_toggles_options(browser, static_base_url):
    with page_with(browser, static_base_url, DetectorStub()) as page:
        ready(page)
        page.focus('[data-key="privacy.ips"]')
        page.keyboard.press("Space")
        assert not page.is_checked('[data-key="privacy.ips"]')
        page.focus('input[name="privacy.paths"][value="half"]')
        page.keyboard.press("ArrowDown")
        assert page.is_checked('input[name="privacy.paths"][value="off"]')
        assert page.is_visible("#det-dirty")


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_narrow_no_horizontal_scroll(browser, static_base_url, width, theme):
    s = copy.deepcopy(CUSTOM)
    s["privacy"]["pathWhitelist"] = ["x" * 200]
    s["privacy"]["appOnlyApps"] = ["很长的程序名" * 10]
    with page_with(browser, static_base_url, DetectorStub(settings={"dev_a": s, "dev_b": None}),
                   width=width, theme=theme) as page:
        ready(page)
        assert overflowing(page, "#settings-panel") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        bg = page.eval_on_selector("#settings-panel", "e => getComputedStyle(e).backgroundColor")
        assert bg == ("rgb(21, 28, 46)" if theme == "dark" else "rgb(255, 255, 255)")


def test_load_error_is_visible_while_form_is_hidden(browser, static_base_url):
    stub = DetectorStub()
    stub.get_status = 500
    with page_with(browser, static_base_url, stub) as page:
        page.wait_for_selector("#det-message.is-error")
        assert page.is_visible("#det-message") and page.is_hidden("#det-form")
        assert "数据库暂时连不上" in page.inner_text("#det-message")


def test_form_locked_while_saving(browser, static_base_url):
    stub = DetectorStub()
    held = []
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.route(re.compile(r"/api/core/detector/settings"), lambda r: held.append(r) if r.request.method == "PUT" else r.fallback())
        page.uncheck('[data-key="privacy.ips"]')
        page.click("#det-save")
        page.wait_for_function("document.querySelector('[data-key=privacy\\\\.emails]').matches(':disabled')")
        held[0].fallback()
        page.wait_for_selector("#det-message:not([hidden])")
        assert page.is_enabled('[data-key="privacy.emails"]')
