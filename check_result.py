import argparse
import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from playwright.sync_api import Playwright, sync_playwright

from lotto_utils import (
    build_session_from_context,
    fetch_latest_lotto_result,
    fetch_today_purchase_numbers,
    get_now,
    get_result_check_status,
    load_env_file,
    post_to_slack,
)
from lotto_web import capture_screenshot, login, save_page_html

LOG = logging.getLogger(__name__)


@dataclass
class Config:
    user_id: str
    user_pw: str
    slack_token: str
    slack_channel: str
    headless: bool
    debug: bool
    debug_artifacts: bool
    debug_dir: str
    timeout_ms: int
    slow_mo: int


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="동행복권 로또 결과 확인")
    parser.add_argument("user_id", nargs="?")
    parser.add_argument("user_pw", nargs="?")
    parser.add_argument("slack_token", nargs="?")
    parser.add_argument("slack_channel", nargs="?")
    parser.add_argument("--user-id", dest="user_id_opt")
    parser.add_argument("--user-pw", dest="user_pw_opt")
    parser.add_argument("--slack-token", dest="slack_token_opt")
    parser.add_argument("--slack-channel", dest="slack_channel_opt")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--debug-artifacts", action="store_true")
    parser.add_argument("--debug-dir", default=None)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--slow-mo", type=int, default=0)
    return parser.parse_args()


def load_config() -> Config:
    load_env_file()
    args = parse_args()

    user_id = args.user_id_opt or args.user_id or os.getenv("DHL_USER_ID")
    user_pw = args.user_pw_opt or args.user_pw or os.getenv("DHL_USER_PW")
    slack_token = (
        args.slack_token_opt or args.slack_token or os.getenv("SLACK_BOT_TOKEN")
    )
    slack_channel = (
        args.slack_channel_opt or args.slack_channel or os.getenv("SLACK_CHANNEL")
    )

    headless_env = parse_bool(os.getenv("HEADLESS"), default=True)
    if args.headed:
        headless = False
    elif args.headless:
        headless = True
    else:
        headless = headless_env

    debug = args.debug or parse_bool(os.getenv("DEBUG"))
    debug_artifacts = args.debug_artifacts or parse_bool(os.getenv("DEBUG_ARTIFACTS"))
    debug_dir = args.debug_dir or os.getenv("DEBUG_DIR") or "artifacts"

    missing = []
    if not user_id:
        missing.append("DHL_USER_ID")
    if not user_pw:
        missing.append("DHL_USER_PW")
    if not slack_token:
        missing.append("SLACK_BOT_TOKEN")
    if not slack_channel:
        missing.append("SLACK_CHANNEL")
    if missing:
        raise SystemExit(f"Missing required settings: {', '.join(missing)}")

    return Config(
        user_id=user_id,
        user_pw=user_pw,
        slack_token=slack_token,
        slack_channel=slack_channel,
        headless=headless,
        debug=debug,
        debug_artifacts=debug_artifacts,
        debug_dir=debug_dir,
        timeout_ms=args.timeout_ms,
        slow_mo=args.slow_mo,
    )


def setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")


def hook_slack(config: Config, message: str) -> None:
    korea_time_str = get_now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "text": f"> {korea_time_str} *로또 자동 구매 봇 알림* \n{message}",
        "channel": config.slack_channel,
    }
    response = post_to_slack(payload, config.slack_token, logger=LOG)
    if response is None:
        return
    try:
        data = response.json()
        if not data.get("ok", True):
            LOG.warning("Slack error: %s", data)
    except ValueError:
        return


def get_check_lucky_number(lucky_numbers: List[str], my_numbers: List[str]) -> str:
    return_msg = ""
    for my_num in my_numbers:
        if my_num in lucky_numbers:
            return_msg += f" [ {my_num} ] "
            continue
        return_msg += f" {my_num} "
    return return_msg


def run(playwright: Playwright, config: Config) -> None:
    browser = None
    context = None
    page = None
    try:
        browser = playwright.chromium.launch(
            headless=config.headless,
            slow_mo=config.slow_mo if config.slow_mo > 0 else None,
        )
        # 모바일 리다이렉트 방지: UA 고정 + 뷰포트 설정 + 모바일/터치 비활성화
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            is_mobile=False,
            has_touch=False,
        )
        page = context.new_page()
        page.set_default_timeout(config.timeout_ms)
        page.set_default_navigation_timeout(config.timeout_ms)

        login(page, config.user_id, config.user_pw, timeout_ms=config.timeout_ms)

        latest = fetch_latest_lotto_result()
        if not latest:
            hook_slack(config, "최신 로또 결과를 가져오지 못했습니다.")
            return
        draw_no, draw_date, numbers, bonus = latest

        is_result_available, result_status, result_message = get_result_check_status()
        if not is_result_available:
            hook_slack(config, result_message or "현재 결과를 확인할 수 없습니다.")
            return
        date_fmt = (
            f"{draw_date[:4]}-{draw_date[4:6]}-{draw_date[6:]}"
            if draw_date and len(draw_date) == 8
            else draw_date
        )
        number_text = ", ".join(numbers)
        bonus_text = bonus or "-"
        hook_slack(
            config,
            f"로또 결과: {draw_no}회 ({date_fmt}) 당첨번호 {number_text} + {bonus_text}",
        )
        lucky_numbers = numbers + ([bonus] if bonus else [])

        session = build_session_from_context(context)
        now_date = get_now().date().strftime("%Y%m%d")
        my_numbers, info = fetch_today_purchase_numbers(
            session,
            now_date,
            debug_dir=config.debug_dir if config.debug_artifacts else None,
        )
        if not my_numbers:
            if info.get("status") == "drawing":
                hook_slack(config, info.get("message", "추첨 중입니다. 추첨 완료 후 확인해주세요."))
                return
            LOG.info("번호 조회 실패: %s", info)
            hook_slack(config, "구매 번호를 확인하지 못했습니다. 마이페이지에서 확인해주세요.")
            return

        result_msg = ""
        for idx, group in enumerate(my_numbers, start=1):
            result_msg += f"{idx}. " + get_check_lucky_number(lucky_numbers, group) + "\n"
        hook_slack(config, f"> 이번주 나의 행운의 번호 결과는?!?!?!\n{result_msg}")
    except Exception as exc:
        if config.debug_artifacts and page:
            capture_screenshot(page, config.debug_dir, "check_result_error")
            save_page_html(page, config.debug_dir, "check_result_error")
        hook_slack(config, f"에러 발생: {exc}")
        raise
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()


if __name__ == "__main__":
    config = load_config()
    setup_logging(config.debug)
    with sync_playwright() as playwright:
        run(playwright, config)
