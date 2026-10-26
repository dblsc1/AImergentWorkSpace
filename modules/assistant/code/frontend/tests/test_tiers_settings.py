"""三级分类的设置区（tiers.js、settings.js）：开关确认、409 / 412、agent 权限、键盘鼠标活动、autoTrack 隐藏。"""

from __future__ import annotations

from test_settings import DetectorStub
from tiers_stubs import AcctStub, TierStub, tier_page, wait_tiers


def det_page(browser, base, acct, **kw):
    det = kw.pop("det", None) or DetectorStub()
    return tier_page(browser, base, acct, TierStub(), extra={r"/api/core/detector/(?!rules)": det.route}, **kw), det


def test_tiers_switch_confirms_then_puts_with_if_match(browser, static_base_url):
    acct = AcctStub(tiers=False)
    with tier_page(browser, static_base_url, acct, TierStub()) as page:
        page.wait_for_selector("#tier-settings:not([hidden])")
        said = []
        page.once("dialog", lambda d: (said.append(d.message), d.dismiss()))      # 先拒绝：什么都不发
        page.click("#tier-on")
        assert not page.is_checked("#tier-on") and acct.puts() == []
        assert "每段 ≥ 1 秒的窗口记录（程序名与脱敏标题，脱敏同段）" in said[0] and "旧规则会自动拆成两级" in said[0]
        page.once("dialog", lambda d: d.accept())
        page.click("#tier-on")
        wait_tiers(page)
        assert acct.puts() == [("PUT", '"3"', {"tiers": True,
                                              "agentDirect": {"enabled": False, "windowsClassify": False,
                                                              "honeycombRecords": False}})]
        assert page.is_checked("#tier-on") and page.is_hidden("#det-rules")
        assert len([c for c in acct.calls if c[0] == "GET"]) == 2          # 翻转成功后整页重载（旧编辑器的快照作废）


def test_tiers_409_detail_shown_and_switch_reverts(browser, static_base_url):
    acct = AcctStub(tiers=False)
    acct.put_reply = (409, {"detail": "迁移后规则总数 520 超过 500 条上限（超 20 条）"})
    with tier_page(browser, static_base_url, acct, TierStub()) as page:
        page.wait_for_selector("#tier-settings:not([hidden])")
        page.once("dialog", lambda d: d.accept())
        page.click("#tier-on")
        page.wait_for_selector("#tier-message.is-error")
        assert "超 20 条" in page.inner_text("#tier-message")
        assert not page.is_checked("#tier-on") and page.is_hidden("#tier-rules") and page.is_visible("#det-rules")


def test_settings_412_reloads_and_asks_again(browser, static_base_url):
    acct = AcctStub(tiers=False)
    with tier_page(browser, static_base_url, acct, TierStub()) as page:
        page.wait_for_selector("#tier-settings:not([hidden])")
        acct.doc["version"] = 8
        page.check("#tier-direct")
        page.wait_for_selector("#tier-message.is-error")
        assert "别处改过" in page.inner_text("#tier-message")
        assert not page.is_checked("#tier-direct")
        page.check("#tier-direct")
        page.wait_for_function("document.querySelector('#tier-message').textContent.includes('已保存')")
        assert [p[1] for p in acct.puts()] == ['"3"', '"8"'] and acct.doc["agentDirect"]["enabled"] is True


def test_agent_master_gates_sub_switches(browser, static_base_url):
    acct = AcctStub(tiers=False)
    with tier_page(browser, static_base_url, acct, TierStub()) as page:
        page.wait_for_selector("#tier-settings:not([hidden])")
        assert page.is_disabled("#tier-windows") and page.is_disabled("#tier-records")
        page.check("#tier-direct")
        page.wait_for_function("!document.querySelector('#tier-windows').disabled")
        page.check("#tier-windows")
        page.wait_for_function("document.querySelector('#tier-message').textContent.includes('已保存')")
        page.check("#tier-records")
        page.wait_for_function("document.querySelector('#tier-records').checked && !document.querySelector('#tier-records').disabled")
        assert acct.puts()[-1][2]["agentDirect"] == {"enabled": True, "windowsClassify": True, "honeycombRecords": True}
        assert [p[1] for p in acct.puts()] == ['"3"', '"4"', '"5"']
        page.uncheck("#tier-direct")
        page.wait_for_function("document.querySelector('#tier-windows').disabled")
        assert acct.doc["agentDirect"]["enabled"] is False


def test_autotrack_hidden_and_input_activity_shown_only_with_tiers(browser, static_base_url):
    for tiers in (False, True):
        pg, det = det_page(browser, static_base_url, AcctStub(tiers=tiers))
        with pg as page:
            page.wait_for_selector("#det-form:not([hidden])")
            if tiers:
                wait_tiers(page)
            page.wait_for_function("document.querySelector('#det-autotrack-group').hidden === %s" % str(tiers).lower())
            assert page.is_visible("#det-input-group") is tiers
            assert page.is_visible("#det-autotrack-group") is (not tiers)


def test_tiers_off_save_document_unchanged(browser, static_base_url):
    pg, det = det_page(browser, static_base_url, AcctStub(tiers=False))
    with pg as page:
        page.wait_for_selector("#det-form:not([hidden])")
        page.uncheck('[data-key="privacy.emails"]')
        page.click("#det-save")
        page.wait_for_selector("#det-message:not([hidden])")
        put = [c for c in det.calls if c[0] == "PUT"][0][3]
        assert "inputActivity" not in put and "records" not in put


def test_input_activity_shows_notice_before_saving(browser, static_base_url):
    pg, det = det_page(browser, static_base_url, AcctStub(tiers=True))
    with pg as page:
        page.wait_for_selector("#det-form:not([hidden])")
        wait_tiers(page)
        said = []
        page.once("dialog", lambda d: (said.append(d.message), d.dismiss()))      # 看了说明不同意：勾回去、不脏
        page.click("#det-input")
        assert not page.is_checked("#det-input") and page.is_disabled("#det-save")
        for word in ("每 10 秒里有没有碰键盘或鼠标", "鼠标在动也算", "按了哪个键、打了什么字", "检测程序和你自己的蜂巢", "随时关掉"):
            assert word in said[0]
        assert not [c for c in det.calls if c[0] == "PUT"]
        page.once("dialog", lambda d: d.accept())
        page.click("#det-input")
        assert page.is_checked("#det-input") and page.is_enabled("#det-save")
        assert not [c for c in det.calls if c[0] == "PUT"]                          # 说明在存之前；存是另一步
        page.click("#det-save")
        page.wait_for_selector("#det-message:not([hidden])")
        put = [c for c in det.calls if c[0] == "PUT"][0][3]
        assert put["inputActivity"] is True and put["privacy"]["futureKey"] is True            # 未知键照旧带回
        page.uncheck("#det-input")                                                  # 关掉不需要说明
        page.click("#det-save")
        page.wait_for_function("document.querySelector('#det-input').checked === false && document.querySelector('#det-save').disabled")
        assert [c for c in det.calls if c[0] == "PUT"][1][3]["inputActivity"] is False


def test_settings_412_keeps_unsaved_level1_edits(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        page.fill('#tier1-list li[data-index="0"] [data-f=block]', "没存的")
        acct.doc["version"] = 8
        page.check("#tier-direct")
        page.wait_for_selector("#tier-message.is-error")
        assert page.input_value('#tier1-list li[data-index="0"] [data-f=block]') == "没存的"
        assert page.is_enabled("#tier1-save")


def test_high_confidence_threshold_saved_with_put_and_validated(browser, static_base_url):
    acct = AcctStub(tiers=False)
    acct.doc["highConfidenceThresholdPercent"] = 80
    with tier_page(browser, static_base_url, acct, TierStub()) as page:
        page.wait_for_selector("#tier-settings:not([hidden])")
        assert page.input_value("#tier-threshold") == "80" and "高置信度阈值" in page.inner_text("#tier-threshold-field")
        page.fill("#tier-threshold", "150")
        page.dispatch_event("#tier-threshold", "change")
        page.wait_for_selector("#tier-message.is-error")
        assert acct.puts() == [] and page.input_value("#tier-threshold") == "80"
        page.fill("#tier-threshold", "65")
        page.dispatch_event("#tier-threshold", "change")
        page.wait_for_function("document.querySelector('#tier-message').textContent.includes('已保存')")
        assert acct.puts()[0][1] == '"3"' and acct.puts()[0][2]["highConfidenceThresholdPercent"] == 65
        assert acct.doc["highConfidenceThresholdPercent"] == 65


def test_threshold_field_hidden_when_backend_lacks_it(browser, static_base_url):
    with tier_page(browser, static_base_url, AcctStub(tiers=False), TierStub()) as page:
        page.wait_for_selector("#tier-settings:not([hidden])")
        assert page.is_hidden("#tier-threshold-field")
