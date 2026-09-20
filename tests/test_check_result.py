from unittest.mock import MagicMock

import check_result
from config import Config


def test_collect_purchases_uses_api_without_opening_ledger_page(monkeypatch):
    page = MagicMock()
    expected = [(1242, [["01", "02", "03", "04", "05", "06"]])]
    monkeypatch.setattr(check_result, "_parse_purchases_from_api", lambda _: expected)

    purchases = check_result.collect_purchases(page, timeout_ms=180_000)

    assert purchases == expected
    page.goto.assert_not_called()


def test_collect_purchases_falls_back_to_bounded_ledger_navigation(monkeypatch):
    page = MagicMock()
    expected = [(1242, [["07", "08", "09", "10", "11", "12"]])]
    monkeypatch.setattr(check_result, "_parse_purchases_from_api", lambda _: [])
    click_search = MagicMock()
    monkeypatch.setattr(check_result, "_click_search_with_monthly_range", click_search)
    monkeypatch.setattr(check_result, "_parse_purchases_from_list", lambda _: expected)

    purchases = check_result.collect_purchases(page, timeout_ms=180_000)

    assert purchases == expected
    page.goto.assert_called_once_with(
        "https://www.dhlottery.co.kr/mypage/mylotteryledger",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    click_search.assert_called_once_with(page)


def test_run_uses_api_first_collection_flow(monkeypatch):
    browser = MagicMock()
    context = MagicMock()
    page = MagicMock()
    page.locator.return_value.inner_text.return_value = ""
    config = Config(
        user_id="user",
        user_pw="password",
        timeout_ms=180_000,
        debug_artifacts=False,
        slack_channel="channel",
        slack_token="token",
    )

    monkeypatch.setattr(
        check_result,
        "create_browser_context",
        lambda playwright, cfg: (browser, context),
    )
    monkeypatch.setattr(check_result, "create_page", lambda ctx, cfg: page)
    monkeypatch.setattr(check_result, "login", MagicMock())
    collect = MagicMock(return_value=[])
    monkeypatch.setattr(check_result, "collect_purchases", collect)
    notify = MagicMock()
    monkeypatch.setattr(check_result, "notify", notify)

    check_result.run(MagicMock(), config)

    collect.assert_called_once_with(page, timeout_ms=180_000)
    page.goto.assert_not_called()
    notify.assert_called_once()
    context.close.assert_called_once_with()
    browser.close.assert_called_once_with()
