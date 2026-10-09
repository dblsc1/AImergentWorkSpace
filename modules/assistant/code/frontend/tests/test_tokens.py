"""「Agent 令牌」面板（tokens.js，contracts/auth.gate.v1 v1.4）：发令牌、只显示一次、列出、单独吊销。

认证服务的三个端点（GET/POST api/auth/tokens、POST api/auth/tokens/revoke）与 health 全在浏览器侧桩掉。
"""

from __future__ import annotations

import json

import pytest
from conftest import open_page, overflowing

# 一眼就是假的（不是任何真令牌的形状能通过校验的值）
FAKE = "hct2.0.0.0.{scope}.0000000000000000..fake-token-for-tests"
EXISTING = [
    {"id": "aaaaaaaaaaaaaaaa", "name": "笔记本上的 Claude Code", "scope": "report",
     "createdAt": "2026-10-01T02:00:00Z", "expiresAt": "2027-10-01T02:00:00Z", "revoked": False},
    {"id": "bbbbbbbbbbbbbbbb", "name": "<img src=x onerror=window.__xss=1>", "scope": "write",
     "createdAt": "2026-10-02T02:00:00Z", "expiresAt": "2027-10-02T02:00:00Z", "revoked": False},
    {"id": "cccccccccccccccc", "name": "", "scope": "read",
     "createdAt": "2026-10-03T02:00:00Z", "expiresAt": "2027-10-03T02:00:00Z", "revoked": True},
]
# 复制：记下页面想写进剪贴板的东西（无头浏览器里不去碰真剪贴板）
CLIP = "window.__copied=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:t=>{window.__copied.push(t);return Promise.resolve()}}});"


class AuthStub:
    def __init__(self, tokens=None, list_status=200, mint_reply=None, anonymous=False):
        self.tokens = [dict(t) for t in (EXISTING if tokens is None else tokens)]
        self.list_status = list_status
        self.mint_reply = mint_reply
        self.anonymous = anonymous
        self.calls: list[tuple] = []

    def route(self, route):
        req = route.request
        path = req.url.split("/api/auth/", 1)[1]
        body = json.loads(req.post_data) if req.post_data else None
        self.calls.append((req.method, path, body, req.headers.get("content-type")))
        j = lambda status, obj: route.fulfill(status=status, content_type="application/json",  # noqa: E731
                                              body=json.dumps(obj, ensure_ascii=False))
        if path == "health":
            return j(200, {"status": "ok", "accounts": False, "sharedPassword": True, "anonymousReport": self.anonymous})
        if path == "tokens" and req.method == "GET":
            if self.list_status != 200:
                return j(self.list_status, {"ok": False, "error": "tokens_disabled"})
            return j(200, {"tokens": self.tokens})
        if path == "tokens" and req.method == "POST":
            if self.mint_reply:
                return j(*self.mint_reply)
            meta = {"id": "dddddddddddddddd", "name": body.get("name", ""), "scope": body.get("scope", "write"),
                    "createdAt": "2026-10-09T02:00:00Z", "expiresAt": "2027-10-09T02:00:00Z", "revoked": False}
            self.tokens.append(meta)
            return j(201, {"token": FAKE.format(scope=meta["scope"]), "tenant": "u_local", **meta})
        if path == "tokens/revoke" and req.method == "POST":
            for t in self.tokens:
                if t["id"] == body.get("tokenId"):
                    t["revoked"] = True
                    return route.fulfill(status=204)
            return j(404, {"ok": False, "error": "no_such_token"})
        return j(404, {})


def page_with(browser, base, stub: AuthStub, **kw):
    return open_page(browser, base, routes={r"/api/auth/": stub.route}, init=CLIP, **kw)


def ready(page) -> None:
    page.wait_for_selector("#tokens-panel:not([hidden])")


def mints(stub):
    return [c for c in stub.calls if c[:2] == ("POST", "tokens")]


def revokes(stub):
    return [c for c in stub.calls if c[:2] == ("POST", "tokens/revoke")]


@pytest.mark.parametrize("status", [401, 404, 500])
def test_panel_stays_hidden_when_the_auth_service_cannot_list_tokens(browser, static_base_url, status):
    stub = AuthStub(list_status=status)
    with page_with(browser, static_base_url, stub) as page:
        page.wait_for_function("() => true")
        page.wait_for_timeout(300)
        assert page.is_hidden("#tokens-panel")
    assert mints(stub) == []  # 页面自己从不发令牌


def test_503_explains_auth_secret_and_offers_no_form(browser, static_base_url):
    with page_with(browser, static_base_url, AuthStub(list_status=503)) as page:
        ready(page)
        assert "AUTH_SECRET" in page.inner_text("#tok-note")
        assert page.is_hidden("#tok-form") and page.is_hidden("#tok-new")


def test_lists_tokens_newest_first_as_text_and_never_mints_on_its_own(browser, static_base_url):
    stub = AuthStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        rows = page.eval_on_selector_all("#tok-list .tok-item", "ls => ls.map(l => [l.dataset.id, l.classList.contains('is-revoked')])")
        assert rows == [["cccccccccccccccc", True], ["bbbbbbbbbbbbbbbb", False], ["aaaaaaaaaaaaaaaa", False]]
        first = page.inner_text('.tok-item[data-id="aaaaaaaaaaaaaaaa"]')
        assert "只上报" in first and "笔记本上的 Claude Code" in first and "2026-10-01 发" in first and "2027-10-01 到期" in first
        assert "读写" in page.inner_text('.tok-item[data-id="bbbbbbbbbbbbbbbb"]')
        # 备注是用户写的字：当文本显示，不当 HTML
        assert "<img src=x onerror=window.__xss=1>" in page.inner_text('.tok-item[data-id="bbbbbbbbbbbbbbbb"] .tok-item-name')
        assert page.locator("#tok-list img").count() == 0 and page.evaluate("window.__xss") is None
        # 已吊销的没有吊销按钮
        assert page.locator('.tok-item[data-id="cccccccccccccccc"] .tok-revoke').count() == 0
        assert "已吊销" in page.inner_text('.tok-item[data-id="cccccccccccccccc"]')
        assert page.is_hidden("#tok-new") and page.is_hidden("#tok-empty") and page.is_hidden("#tok-anon")
        # 三种权限各有一句中文说明，缺省选最小的「只上报」
        assert page.is_checked('input[name="tok-scope"][value="report"]')
        text = page.inner_text("#tok-form")
        for needle in ("只上报", "读不到你的任何数据", "只读", "什么都改不了", "读写", "ai-detector"):
            assert needle in text, needle
    assert mints(stub) == [] and revokes(stub) == []


def test_empty_list_and_anonymous_hint(browser, static_base_url):
    with page_with(browser, static_base_url, AuthStub(tokens=[], anonymous=True)) as page:
        ready(page)
        page.wait_for_selector("#tok-anon:not([hidden])")
        assert page.is_visible("#tok-empty") and "AUTH_ANONYMOUS_REPORT=false" in page.inner_text("#tok-anon")


def test_mint_shows_the_secret_once_with_copy_and_snippets(browser, static_base_url):
    stub = AuthStub(tokens=[])
    logs: list[str] = []
    with page_with(browser, static_base_url, stub) as page:
        page.on("console", lambda m: logs.append(m.text))
        ready(page)
        page.fill("#tok-name", "  沙箱里的编码代理  ")
        page.click("#tok-mint")
        page.wait_for_selector("#tok-new:not([hidden])")
        secret = FAKE.format(scope="report")
        assert mints(stub) == [("POST", "tokens", {"scope": "report", "name": "沙箱里的编码代理"}, "application/json")]
        assert page.inner_text("#tok-secret") == secret
        assert "只显示这一次" in page.inner_text("#tok-new") and "只上报" in page.inner_text("#tok-new-scope")
        # 可以直接粘贴的配置：代理钩子的配置文件与环境变量
        snippets = page.eval_on_selector_all("#tok-snippets .tok-pre", "ps => ps.map(p => p.textContent)")
        assert json.loads(snippets[0]) == {"url": f"{static_base_url}/", "token": secret}
        assert snippets[1] == f"export COCKPIT_URL={static_base_url}/\nexport COCKPIT_TOKEN={secret}"
        page.click("#tok-secret-copy")
        page.click("#tok-snippets .tok-copy >> nth=0")
        assert page.evaluate("window.__copied") == [secret, snippets[0]]
        assert page.inner_text("#tok-secret-copy") == "已复制"
        # 列表刷新了，里面没有令牌本身
        page.wait_for_selector('.tok-item[data-id="dddddddddddddddd"]')
        assert secret not in page.inner_text("#tok-list") and "fake-token" not in page.inner_html("#tok-list")
        assert page.input_value("#tok-name") == ""
        # 令牌哪儿都没落：存储、cookie、地址栏、console
        stored = page.evaluate("JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage), document.cookie, location.href])")
        assert "fake-token" not in stored and "hct2" not in stored
        # 「我已保存好」之后页面上再也找不到它
        page.click("#tok-done")
        assert page.is_hidden("#tok-new")
        assert "fake-token" not in page.content()
    assert not any("fake-token" in line or "hct2" in line for line in logs)


@pytest.mark.parametrize("scope,titles", [
    ("report", ["代理钩子", "环境变量"]),
    ("read", ["MCP 客户端", "代理钩子"]),
    ("write", ["桌面检测程序", "MCP 客户端"]),
])
def test_each_scope_is_sent_as_chosen_and_gets_its_own_snippets(browser, static_base_url, scope, titles):
    stub = AuthStub(tokens=[])
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        page.check(f'input[name="tok-scope"][value="{scope}"]')
        page.click("#tok-mint")
        page.wait_for_selector("#tok-new:not([hidden])")
        assert mints(stub)[0][2] == {"scope": scope, "name": ""}
        heads = page.eval_on_selector_all("#tok-snippets .tok-snippet-head .set-label", "ls => ls.map(l => l.textContent)")
        assert len(heads) == len(titles) and all(t in h for t, h in zip(titles, heads))
        secret = FAKE.format(scope=scope)
        texts = page.eval_on_selector_all("#tok-snippets .tok-pre", "ps => ps.map(p => p.textContent)")
        assert all(secret in t for t in texts)
        if scope != "report":
            mcp = json.loads(texts[titles.index("MCP 客户端")])["mcpServers"]["honeycomb"]
            assert mcp == {"type": "http", "url": f"{static_base_url}/api/mcp/", "headers": {"Authorization": f"Bearer {secret}"}}
        # 再发一个：上一个令牌先从页面上清掉
        page.check('input[name="tok-scope"][value="report"]')
        page.click("#tok-mint")
        page.wait_for_function("document.getElementById('tok-secret').textContent.includes('.report.')")
        if scope != "report":
            assert secret not in page.content()


def test_revoke_asks_first_and_only_revokes_that_token(browser, static_base_url):
    stub = AuthStub()
    with page_with(browser, static_base_url, stub) as page:
        ready(page)
        asked: list[str] = []
        answer = {"accept": False}

        def on_dialog(d):
            asked.append(d.message)
            d.accept() if answer["accept"] else d.dismiss()

        page.on("dialog", on_dialog)
        button = '.tok-item[data-id="aaaaaaaaaaaaaaaa"] .tok-revoke'
        page.click(button)
        assert len(asked) == 1 and "笔记本上的 Claude Code" in asked[0] and "不能撤回" in asked[0]
        assert revokes(stub) == []  # 点了取消：什么都没发
        answer["accept"] = True
        page.click(button)
        page.wait_for_selector('.tok-item[data-id="aaaaaaaaaaaaaaaa"].is-revoked')
        assert revokes(stub) == [("POST", "tokens/revoke", {"tokenId": "aaaaaaaaaaaaaaaa"}, "application/json")]
        assert page.inner_text("#tok-message") == "已吊销。"
        assert page.locator('.tok-item[data-id="bbbbbbbbbbbbbbbb"] .tok-revoke').count() == 1  # 别的没动


@pytest.mark.parametrize("reply,needle", [
    ((409, {"ok": False, "error": "too_many_tokens"}), "太多"),
    ((503, {"ok": False, "error": "tokens_unavailable"}), "发不了令牌"),
    ((401, {"ok": False, "error": "not_logged_in"}), "重新登录"),
    ((201, {"tenant": "u_local"}), "没成功"),   # 形状不对：不当成功，也不显示任何「令牌」
])
def test_mint_failures_are_said_plainly(browser, static_base_url, reply, needle):
    with page_with(browser, static_base_url, AuthStub(mint_reply=reply)) as page:
        ready(page)
        page.click("#tok-mint")
        page.wait_for_selector("#tok-message:not([hidden])")
        assert needle in page.inner_text("#tok-message") and "is-error" in page.get_attribute("#tok-message", "class")
        assert page.is_hidden("#tok-new") and page.is_enabled("#tok-mint")


@pytest.mark.parametrize("width", [320, 390])
def test_no_horizontal_scroll_with_a_fresh_token_showing(browser, static_base_url, width):
    with page_with(browser, static_base_url, AuthStub(), width=width) as page:
        ready(page)
        page.check('input[name="tok-scope"][value="write"]')
        page.click("#tok-mint")
        page.wait_for_selector("#tok-new:not([hidden])")
        assert overflowing(page, "#tokens-panel") == []
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
