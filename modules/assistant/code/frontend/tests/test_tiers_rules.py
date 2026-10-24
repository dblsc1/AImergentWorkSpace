"""三级分类的两级规则栏（tiers.js）：tiers 关着旧 UI 不变；打开后第一级 / 第二级栏、上色、AI 待批准、存时带回旧式规则。"""

from __future__ import annotations

from tiers_stubs import CYAN, OLD, OLD_AI, ORANGE, AcctStub, TierStub, tier_page, wait_tiers


def color(page, sel: str) -> str:
    return page.eval_on_selector(sel, "e => e.style.getPropertyValue('--tier-c')")


def test_tiers_off_keeps_old_rules_ui(browser, static_base_url):
    acct, tier = AcctStub(tiers=False), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        page.wait_for_selector("#rules-editor:not([hidden])")
        assert page.is_visible("#det-rules") and page.is_hidden("#tier-rules")
        assert page.is_hidden("#det-input-group")
        assert page.locator("#rules-list .rule-item").count() == 5
        assert "把握" in page.inner_text("#rules-list")
        assert not [c for c in tier.calls if "block-rules" in c[1]]


def test_settings_unreachable_keeps_old_rules_ui(browser, static_base_url):
    acct, tier = AcctStub(status=404), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        page.wait_for_selector("#rules-editor:not([hidden])")
        assert page.is_visible("#det-rules") and page.is_hidden("#tier-rules") and page.is_hidden("#tier-settings")


def test_tiers_on_two_columns_filter_and_color(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        assert page.is_hidden("#det-rules")
        assert "第一级 · 收纳规则" in page.inner_text("#tier-rules") and "第二级 · 去向规则" in page.inner_text("#tier-rules")
        # 左栏只有 kind:"block"：旧式规则（含 migrated）与交给 AI 的规则不显示
        rows = page.locator("#tier1-list li")
        assert rows.count() == 3
        assert page.eval_on_selector_all("#tier1-list [data-f=block]", "n => n.map(x => x.value)") == ["花园代码", "终端", "未分配"]
        # 按目的地项目上色：花园代码 → 音阶练习（练琴区）；终端 → 考级计划（制作区）；没有第二级规则 → 灰
        assert color(page, '#tier1-list li[data-index="0"]') == CYAN
        assert color(page, '#tier1-list li[data-index="1"]') == ORANGE
        assert page.eval_on_selector('#tier1-list li[data-index="2"]', "e => e.classList.contains('is-grey')")
        assert color(page, '#tier2-list li[data-id="br_1"]') == CYAN
        # 命中计数：后端给了才显示
        assert "312 条" in page.inner_text('#tier1-list li[data-index="0"]')
        assert "条" not in page.inner_text('#tier1-list li[data-index="1"]')
        # 右栏：broken 标出；没有「把握」
        assert page.locator("#tier2-list .tier-broken").count() == 1
        assert "把握" not in page.inner_text("#tier-rules")


def test_save_level1_sends_whole_set_with_old_rules_verbatim(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        assert page.is_disabled("#tier1-save")
        page.fill('#tier1-list li[data-index="0"] [data-f=block]', "花园")
        page.fill('#tier1-list li[data-index="1"] [data-f=title]', "")
        assert page.is_enabled("#tier1-save") and page.is_visible("#tier1-dirty")
        page.click("#tier1-save")
        page.wait_for_selector("#tier-rules-message:not([hidden])")
        puts = [c for c in tier.calls if c[0] == "PUT"]
        assert len(puts) == 1 and puts[0][2] == '"7"'
        sent = puts[0][3]["rules"]
        assert sent[0] == OLD and sent[2] == OLD_AI                       # 旧式规则（含 migrated）逐字原样、位置不变
        assert sent[1]["block"] == "花园" and sent[1]["kind"] == "block" and sent[1]["id"] == "r_b1"
        assert sent[3]["title"] is None and sent[4]["id"] == "r_b3"
        assert "已保存" in page.inner_text("#tier-rules-message")
        assert page.is_disabled("#tier1-save")


def test_add_delete_reorder_level1(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        page.click('#tier1-list li[data-index="2"] [aria-label="上移第 3 条"]')    # 未分配 上移到终端前
        page.click('#tier1-list li[data-index="0"] [aria-label="删除第 1 条"]')     # 删掉 花园代码
        page.click("#tier1-add")
        page.fill('#tier1-list li:last-child [data-f=app]', "firefox")
        page.fill('#tier1-list li:last-child [data-f=block]', "网页")
        page.click("#tier1-save")
        page.wait_for_selector("#tier-rules-message:not([hidden])")
        sent = [c for c in tier.calls if c[0] == "PUT"][0][3]["rules"]
        # 第一级规则占原来的三个位置（1、3、4）依次填回：未分配、终端、新的；旧式规则不动
        assert [r.get("block") for r in sent] == [None, "未分配", None, "终端", "网页"]
        assert sent[4]["app"] == "firefox" and sent[4]["title"] is None and "id" not in sent[4]
        assert sent[0] == OLD and sent[2] == OLD_AI


def test_412_does_not_overwrite_then_second_save_wins(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        tier.version = 9                                                           # 别处先改了
        page.fill('#tier1-list li[data-index="0"] [data-f=block]', "我的")
        page.click("#tier1-save")
        page.wait_for_selector("#tier-rules-message.is-error")
        assert "第 9 版" in page.inner_text("#tier-rules-message")
        assert page.input_value('#tier1-list li[data-index="0"] [data-f=block]') == "我的"
        assert tier.rules[1]["block"] == "花园代码"                                  # 没覆盖
        page.click("#tier1-save")
        page.wait_for_function("document.querySelector('#tier-rules-message').textContent.includes('已保存')")
        puts = [c for c in tier.calls if c[0] == "PUT"]
        assert [p[2] for p in puts] == ['"7"', '"9"'] and tier.rules[1]["block"] == "我的"


def test_422_marks_the_row(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    tier.put_reply = (422, {"detail": "1 条规则不合规", "errors": [{"index": 3, "field": "app", "message": "正则不合法"}]})
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        page.fill('#tier1-list li[data-index="1"] [data-f=app]', "(")
        page.click("#tier1-save")
        page.wait_for_selector('#tier1-list li[data-index="1"] .field-error')
        assert "正则不合法" in page.inner_text('#tier1-list li[data-index="1"] .field-error')
        assert page.locator('#tier1-list li[data-index="0"] .field-error').count() == 0


def test_level2_add_change_delete_and_broken(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        assert "失效" in page.inner_text('#tier2-list li[data-id="br_3"]')
        page.click("#tier2-add")
        page.fill("#tier2-new input", "未分配")
        page.select_option("#tier2-new select", "t_exam")
        page.click("#tier2-new .btn-fact")
        page.wait_for_selector('#tier2-list li[data-id="br_new"]')
        assert ("POST", "block-rules", None, {"blockKey": "未分配", "taskId": "t_exam"}) in tier.calls
        assert color(page, '#tier2-list li[data-id="br_new"]') == ORANGE
        assert color(page, '#tier1-list li[data-index="2"]') == ORANGE            # 左栏同名规则跟着上色
        page.select_option('#tier2-list li[data-id="br_1"] select', "p:p_book")     # 改目标 = 同块名再 POST
        page.wait_for_function("document.querySelector('#tier2-list li[data-id=\"br_1\"]').style.getPropertyValue('--tier-c') == '%s'" % ORANGE)
        assert ("POST", "block-rules", None, {"blockKey": "花园代码", "projectId": "p_book"}) in tier.calls
        page.click('#tier2-list li[data-id="br_2"] .danger')
        page.wait_for_selector('#tier2-list li[data-id="br_2"]', state="detached")
        assert ("DELETE", "block-rules/br_2", None, None) in tier.calls


def test_pending_ai_rules_approve_correct_reject(browser, static_base_url):
    props = {"projects": [], "tasks": [], "changes": [], "ignores": [],
             "windowRules": [
                 {"id": "nw_1", "kind": "windowRule", "status": "pending", "op": "add",
                  "rule": {"app": "chrome", "title": "docs", "block": "示例文档"}},
                 {"id": "nw_2", "kind": "windowRule", "status": "pending", "op": "add",
                  "rule": {"app": "gimp", "block": "<b>图</b>"}}],
             "blockRules": [
                 {"id": "nb_1", "kind": "blockRule", "status": "pending", "op": "add", "blockKey": "示例文档",
                  "taskId": "t_read"}]}
    acct, tier = AcctStub(tiers=True), TierStub(proposals=props)
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        assert page.locator("#tier1-pending .is-ai").count() == 2 and page.locator("#tier2-pending .is-ai").count() == 1
        assert page.eval_on_selector("#tier1-pending .is-ai", "e => getComputedStyle(e).borderStyle") == "dashed"
        assert "chrome · docs → 块「示例文档」" in page.inner_text('#tier1-pending [data-id="nw_1"]')
        assert "<b>图</b>" in page.inner_text('#tier1-pending [data-id="nw_2"]')            # 当文本
        assert page.locator("#tier1-pending b").count() == 0
        page.click('#tier1-pending [data-id="nw_1"] >> text=批准')
        page.wait_for_selector('#tier1-pending [data-id="nw_1"]', state="detached")
        page.click('#tier1-pending [data-id="nw_2"] >> text=更正')
        page.fill('#tier1-pending [data-id="nw_2"] input', "图片")
        page.click("text=确认更正并批准")
        page.wait_for_selector('#tier1-pending [data-id="nw_2"]', state="detached")
        page.click('#tier2-pending [data-id="nb_1"] >> text=忽略')
        page.wait_for_selector('#tier2-pending [data-id="nb_1"]', state="detached")
        w = [c for c in tier.calls if c[1].startswith("proposals")]
        assert w == [("POST", "proposals/nw_1/approve", None, {}),
                     ("POST", "proposals/nw_2/approve", None, {"edit": {"rule": {"block": "图片"}}}),
                     ("POST", "proposals/nb_1/reject", None, {})]


def test_pending_block_rule_correct_target_and_no_proposals_hides(browser, static_base_url):
    props = {"blockRules": [{"id": "nb_9", "kind": "blockRule", "status": "pending", "op": "add",
                             "blockKey": "新块", "taskId": "t_read"}]}
    acct, tier = AcctStub(tiers=True), TierStub(proposals=props)
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        page.click('#tier2-pending [data-id="nb_9"] >> text=更正')
        page.select_option('#tier2-pending [data-id="nb_9"] select', "p:p_book")
        page.click("text=确认更正并批准")
        page.wait_for_selector('#tier2-pending [data-id="nb_9"]', state="detached")
        assert tier.writes()[-1] == ("POST", "proposals/nb_9/approve", None, {"edit": {"projectId": "p_book"}})
    with tier_page(browser, static_base_url, AcctStub(tiers=True), TierStub()) as page:
        wait_tiers(page)
        assert page.locator("#tier-rules .is-ai").count() == 0


def test_412_merges_into_latest_rules_not_stale_snapshot(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        newer = {"id": "r_new_other", "app": "vlc", "title": None, "taskId": "t_word", "confidence": 0.9, "note": None,
                 "enabled": True}
        tier.rules = tier.rules + [newer]                                          # 别处加了一条旧式规则
        tier.version = 9
        page.fill('#tier1-list li[data-index="0"] [data-f=block]', "我的")
        page.click("#tier1-save")
        page.wait_for_selector("#tier-rules-message.is-error")
        page.click("#tier1-save")
        page.wait_for_function("document.querySelector('#tier-rules-message').textContent.includes('已保存')")
        sent = [c for c in tier.calls if c[0] == "PUT"][-1][3]["rules"]
        assert sent[-1] == newer and sent[1]["block"] == "我的"                      # 别人的改动没被覆盖，我的编辑还在


def test_target_missing_from_tree_gets_option_and_selected(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        sel = '#tier2-list li[data-id="br_3"] select'
        assert page.eval_on_selector(sel, "s => s.selectedOptions[0].textContent") == "去向已不在树里（t_gone）"


def test_background_refresh_keeps_new_row_input(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        page.click("#tier2-add")
        page.fill("#tier2-new input", "写到一半")
        page.select_option("#tier2-new select", "t_exam")
        n = len([c for c in tier.calls if c[1] == "rules"])
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function("true")
        page.wait_for_timeout(300)
        assert len([c for c in tier.calls if c[1] == "rules"]) > n                  # 确实刷新过
        assert page.input_value("#tier2-new input") == "写到一半"
        assert page.input_value("#tier2-new select") == "t_exam"


def test_actions_do_not_run_concurrently(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    held = []
    orig = tier.block_route
    def slow(route):
        if route.request.method == "DELETE" and not held:
            held.append(route)
            return
        orig(route)
    tier.block_route = slow
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        page.click('#tier2-list li[data-id="br_2"] .danger')
        page.wait_for_timeout(200)
        page.evaluate("document.querySelector('#tier-rules').inert = false")          # 绕过 inert，模拟并发点击
        page.evaluate("document.querySelector('#tier2-list li[data-id=\"br_1\"] .danger').click()")
        page.wait_for_timeout(200)
        assert len(held) == 1 and len([c for c in tier.calls if c[0] == "DELETE" or c[1].startswith("block-rules/")]) <= 1
        tier.block_rules = [b for b in tier.block_rules if b["id"] != "br_2"]
        held[0].fulfill(status=204)
        page.wait_for_selector('#tier2-list li[data-id="br_2"]', state="detached")
        assert page.locator('#tier2-list li[data-id="br_1"]').count() == 1


def test_level2_new_row_blank_destination_does_not_reuse_previous_pick(browser, static_base_url):
    acct, tier = AcctStub(tiers=True), TierStub()
    with tier_page(browser, static_base_url, acct, tier) as page:
        wait_tiers(page)
        page.click("#tier2-add")
        page.fill("#tier2-new input", "未分配")
        page.select_option("#tier2-new select", "t_exam")
        page.select_option("#tier2-new select", "")
        page.click("#tier2-new .btn-fact")
        assert not [c for c in tier.calls if c[:2] == ("POST", "block-rules") and c[3].get("blockKey") == "未分配"]
