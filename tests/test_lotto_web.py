from unittest.mock import MagicMock

import pytest

from lotto_web import login

PASSWORD_NOTICE_URL = "https://www.dhlottery.co.kr/mbrsrvc/ExpryPswdNoti"
MAIN_URL = "https://www.dhlottery.co.kr/main"


def test_login_defers_password_change_notice_and_continues_to_main():
    page = MagicMock()
    page.url = PASSWORD_NOTICE_URL
    locators = {"#passwdNoti": MagicMock()}
    page.locator.side_effect = lambda selector: locators.setdefault(
        selector, MagicMock()
    )
    locators["#passwdNoti"].is_visible.return_value = True

    def wait_for_url(url, **kwargs):
        if url == "**/main*":
            page.url = MAIN_URL

    page.wait_for_url.side_effect = wait_for_url

    login(page, "user", "password")

    first_url_matcher = page.wait_for_url.call_args_list[0].args[0]
    assert first_url_matcher.search(PASSWORD_NOTICE_URL)
    locators["#btnCancel"].click.assert_called_once_with()
    assert page.wait_for_url.call_args_list[-1].args[0] == "**/main*"


def test_login_does_not_defer_security_password_notice():
    page = MagicMock()
    page.url = PASSWORD_NOTICE_URL
    locators = {"#passwdNoti": MagicMock(), "#btnCancel": MagicMock()}
    page.locator.side_effect = lambda selector: locators.setdefault(
        selector, MagicMock()
    )
    locators["#passwdNoti"].is_visible.return_value = False

    with pytest.raises(RuntimeError, match="보안"):
        login(page, "user", "password")

    locators["#btnCancel"].click.assert_not_called()
