import logging
import re
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
        LOG.info("브라우저 컨텍스트 생성 시작")
        browser, context = create_browser_context(playwright, config)
        page = create_page(context, config)
        LOG.info("브라우저 컨텍스트 생성 완료")

        LOG.info("로그인 시작")
        login(page, config.user_id, config.user_pw, timeout_ms=config.timeout_ms)
        LOG.info("로그인 완료")

        LOG.info("구매/당첨 내역 페이지로 이동")
        ledger_url = "https://www.dhlottery.co.kr/mypage/mylotteryledger"
        LOG.debug(f"Ledger URL: {ledger_url}")
        page.goto(ledger_url, wait_until="domcontentloaded")
        LOG.info(f"구매/당첨 내역 페이지 로드 완료. 현재 URL: {page.url}")

        try:
            page.wait_for_selector(".tbl_data, table", timeout=config.timeout_ms)
            LOG.info("테이블 셀렉터 발견")
        except Exception as e:
            LOG.warning(f"테이블 셀렉터 대기 실패: {e}")

        purchases: List[Tuple[int, List[List[str]]]] = []

        try:
            rows = page.locator("table tbody tr").all()
            LOG.info(f"테이블 행 수: {len(rows)}")
            for idx, row in enumerate(rows):
                row_text = row.inner_text()
                LOG.debug(f"행 {idx}: {row_text[:100]}...")
                draw_no = extract_draw_number_from_text(row_text)
                if draw_no:
                    nums = extract_numbers_from_text(row_text)
                    LOG.debug(f"  -> 회차: {draw_no}, 번호 그룹 수: {len(nums)}")
                    if nums:
                        purchases.append((draw_no, nums))
        except Exception as e:
            LOG.error(f"테이블 파싱 실패: {e}")

        LOG.info(f"테이블에서 추출한 구매 건수: {len(purchases)}")

        if not purchases:
            LOG.info("테이블에서 구매 내역을 찾지 못함. body 텍스트에서 재시도")
            body_text = page.locator("body").inner_text()
            LOG.debug(f"Body 텍스트 (처음 500자): {body_text[:500]}")
            draw_no = extract_draw_number_from_text(body_text)
            nums = extract_numbers_from_text(body_text)
            LOG.info(f"Body에서 추출: 회차={draw_no}, 번호 그룹 수={len(nums) if nums else 0}")
            if draw_no and nums:
                purchases.append((draw_no, nums))

        if not purchases:
            LOG.warning("구매 내역을 찾지 못함")
            notify(config, "구매 내역을 찾을 수 없습니다. 마이페이지에서 확인해주세요.")
            return

        latest_draw_no = max(p[0] for p in purchases)
        latest_purchases = [p for p in purchases if p[0] == latest_draw_no]
        LOG.info(f"최근 구매 회차: {latest_draw_no}회, 해당 회차 구매 건수: {len(latest_purchases)}")

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

        LOG.info(f"중복 제거 후 구매 번호 수: {len(my_numbers)}")
        for idx, nums in enumerate(my_numbers, 1):
            LOG.debug(f"  {idx}. {', '.join(nums)}")

        LOG.info(f"{latest_draw_no}회 당첨 결과 조회 시작")
        result = fetch_lotto_result_by_round(latest_draw_no)
        if not result:
            msg = f"{latest_draw_no}회 당첨 결과를 가져오지 못했습니다. 아직 추첨 전이거나 결과가 집계 중일 수 있습니다."
            LOG.warning(msg)
            notify(config, msg)
            return

        draw_no, draw_date, numbers, bonus = result
        LOG.info(f"당첨 결과: {draw_no}회, 날짜={draw_date}, 번호={numbers}, 보너스={bonus}")

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
        LOG.info("결과 알림 전송 완료")

    except Exception as exc:
        LOG.error(f"에러 발생: {exc}", exc_info=True)
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
        LOG.info("브라우저 종료")


if __name__ == "__main__":
    cfg = load_config(create_check_parser(), require_count=False)
    setup_logging(cfg.debug)
    with sync_playwright() as pw:
        run(pw, cfg)
