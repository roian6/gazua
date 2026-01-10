import logging
import re
from datetime import timedelta
from typing import List, Optional, Tuple

from playwright.sync_api import Playwright, sync_playwright

from browser import create_browser_context, create_page
from config import (
    Config,
    create_check_parser,
    load_config,
    setup_logging,
)
from lotto_utils import (
    extract_numbers_from_text,
    fetch_lotto_result_by_round,
    get_now,
    get_result_check_status,
)
from lotto_web import capture_screenshot, login, save_page_html
from slack_notify import notify

LOG = logging.getLogger(__name__)


def get_check_lucky_number(lucky_numbers: List[str], my_numbers: List[str]) -> str:
    return_msg = ""
    for my_num in my_numbers:
        if my_num in lucky_numbers:
            return_msg += f" [ {my_num} ] "
            continue
        return_msg += f" {my_num} "
    return return_msg


def extract_draw_number_from_text(text: str) -> Optional[int]:
    match = re.search(r"(\d{4})회", text)
    if match:
        return int(match.group(1))
    return None


def run(playwright: Playwright, config: Config) -> None:
    browser = None
    context = None
    page = None

    try:
        browser, context = create_browser_context(playwright, config)
        page = create_page(context, config)

        login(page, config.user_id, config.user_pw, timeout_ms=config.timeout_ms)

        is_result_available, result_status, result_message = get_result_check_status()
        if not is_result_available:
            notify(config, result_message or "현재 결과를 확인할 수 없습니다.")
            return

        now = get_now()
        end_date = now.date()
        start_date = end_date - timedelta(days=60)
        start_date_str = start_date.strftime("%Y%m%d")
        end_date_str = end_date.strftime("%Y%m%d")

        LOG.info("구매 내역 페이지로 이동합니다... (조회 기간: %s ~ %s)", start_date_str, end_date_str)
        ledger_url = f"https://dhlottery.co.kr/mypage/selectMyLottoLedgerList.do?srchStrDt={start_date_str}&srchEndDt={end_date_str}"
        page.goto(ledger_url, wait_until="domcontentloaded")

        try:
            page.wait_for_selector(".tbl_data, table", timeout=config.timeout_ms)
        except Exception:
            LOG.warning("구매 내역 테이블을 찾을 수 없습니다.")

        purchases: List[Tuple[int, List[List[str]]]] = []

        try:
            rows = page.locator("table tbody tr").all()
            for row in rows:
                row_text = row.inner_text()
                draw_no = extract_draw_number_from_text(row_text)
                if draw_no:
                    nums = extract_numbers_from_text(row_text)
                    if nums:
                        purchases.append((draw_no, nums))
        except Exception as e:
            LOG.error(f"테이블 파싱 실패: {e}")

        if not purchases:
            body_text = page.locator("body").inner_text()
            draw_no = extract_draw_number_from_text(body_text)
            nums = extract_numbers_from_text(body_text)
            if draw_no and nums:
                purchases.append((draw_no, nums))

        if not purchases:
            notify(config, "구매 내역을 찾을 수 없습니다. 마이페이지에서 확인해주세요.")
            return

        latest_draw_no = max(p[0] for p in purchases)
        latest_purchases = [p for p in purchases if p[0] == latest_draw_no]

        my_numbers: List[List[str]] = []
        for _, nums in latest_purchases:
            my_numbers.extend(nums)

        unique_numbers: List[List[str]] = []
        seen = set()
        for group in my_numbers:
            t = tuple(group)
            if t not in seen:
                seen.add(t)
                unique_numbers.append(group)
        my_numbers = unique_numbers

        LOG.info(f"최근 구매 회차: {latest_draw_no}회, 구매 번호 수: {len(my_numbers)}")

        result = fetch_lotto_result_by_round(latest_draw_no)
        if not result:
            notify(
                config,
                f"{latest_draw_no}회 당첨 결과를 가져오지 못했습니다. 아직 추첨 전이거나 결과가 집계 중일 수 있습니다.",
            )
            return

        draw_no, draw_date, numbers, bonus = result
        date_fmt = (
            f"{draw_date[:4]}-{draw_date[4:6]}-{draw_date[6:]}"
            if draw_date and len(draw_date) == 8
            else draw_date
        )
        number_text = ", ".join(numbers)
        bonus_text = bonus or "-"
        notify(
            config,
            f"로또 결과: {draw_no}회 ({date_fmt}) 당첨번호 {number_text} + {bonus_text}",
        )
        lucky_numbers = numbers + ([bonus] if bonus else [])

        result_msg = ""
        for idx, group in enumerate(my_numbers, start=1):
            result_msg += f"{idx}. " + get_check_lucky_number(lucky_numbers, group) + "\n"
        notify(config, f"> {draw_no}회 나의 행운의 번호 결과는?!?!\n{result_msg}")

    except Exception as exc:
        if config.debug_artifacts and page:
            capture_screenshot(page, config.debug_dir, "check_result_error")
            save_page_html(page, config.debug_dir, "check_result_error")
        notify(config, f"에러 발생: {exc}")
        raise
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()


if __name__ == "__main__":
    cfg = load_config(create_check_parser(), require_count=False)
    setup_logging(cfg.debug)
    with sync_playwright() as pw:
        run(pw, cfg)
