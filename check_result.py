import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

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


def extract_draw_number_from_lotto_item(text: str) -> Optional[int]:
    """Extract draw number specifically from lotto purchase item text.

    In the new site, draw number appears as standalone 4-digit number
    in a row containing '로또6/45'. Valid lotto draw numbers are 1000-1500 range.
    """
    match = re.search(r"\b(1[0-5]\d{2})\b", text)
    if match:
        return int(match.group(1))
    return None


def _click_search_with_monthly_range(page) -> None:
    try:
        page.wait_for_selector("#btnSrch", timeout=10000)

        month_button = page.locator("button.btChgDt:has-text('최근 1개월')")
        if month_button.count() > 0:
            month_button.click()
            LOG.info("최근 1개월 버튼 클릭")
            page.wait_for_timeout(500)

            search_button = page.locator("#btnSrch")
            search_button.click()
            LOG.info("검색 버튼 클릭")
            page.wait_for_timeout(2000)
    except Exception as e:
        LOG.warning(f"조회 기간 설정 실패, 기본값으로 진행: {e}")


def _get_recent_date_range(days: int = 31) -> Tuple[str, str]:
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    return start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")


def _fetch_ledger_list(page, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    url = (
        "https://www.dhlottery.co.kr/mypage/selectMyLotteryledger.do"
        f"?srchStrDt={start_date}&srchEndDt={end_date}"
        "&sort=&ltGdsCd=&winResult=&pageNum=1&recordCountPerPage=10"
    )
    try:
        response = page.evaluate(
            """
            async (url) => {
              const res = await fetch(url, { credentials: 'include' });
              return res.json();
            }
            """,
            url,
        )
        if not response or not response.get("data"):
            return []
        return response["data"].get("list", []) or []
    except Exception as e:
        LOG.warning(f"구매 내역 API 요청 실패: {e}")
        return []


def _fetch_ticket_detail(page, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    url = f"https://www.dhlottery.co.kr/mypage/lotto645TicketDetail.do?{query}"
    try:
        response = page.evaluate(
            """
            async (url) => {
              const res = await fetch(url, { credentials: 'include' });
              return res.json();
            }
            """,
            url,
        )
        return response.get("data") if response else None
    except Exception as e:
        LOG.warning(f"상세 내역 API 요청 실패: {e}")
        return None


def _parse_purchases_from_api(page) -> List[Tuple[int, List[List[str]]]]:
    start_date, end_date = _get_recent_date_range()
    items = _fetch_ledger_list(page, start_date, end_date)
    purchases: List[Tuple[int, List[List[str]]]] = []

    for item in items:
        if item.get("ltGdsCd") != "LO40":
            continue
        draw_no = item.get("ltEpsd") or item.get("ltEpsdView")
        gm_info = item.get("gmInfo")
        if not draw_no or not gm_info:
            continue

        params = {
            "ntslOrdrNo": item.get("ntslOrdrNo"),
            "srchStrDt": start_date,
            "srchEndDt": end_date,
            "barcd": gm_info,
        }
        detail = _fetch_ticket_detail(page, params)
        if detail and detail.get("ticket"):
            games = detail["ticket"].get("game_dtl") or []
            if games:
                numbers = [
                    [str(n).zfill(2) for n in game.get("num", [])]
                    for game in games
                    if game.get("num")
                ]
                numbers = [nums for nums in numbers if len(nums) == 6]
                if numbers:
                    purchases.append((int(draw_no), numbers))
                    continue


    LOG.info(f"API에서 추출한 구매 건수: {len(purchases)}")
    return purchases


def _parse_purchases_from_list(page) -> List[Tuple[int, List[List[str]]]]:
    purchases: List[Tuple[int, List[List[str]]]] = []

    body_list = page.locator("ul.whl-body")
    if body_list.count() == 0:
        LOG.info("구매 내역 리스트(ul.whl-body) 없음")
        return purchases

    lotto_items = body_list.locator("> li").all()
    LOG.info(f"구매 내역 리스트 아이템 수: {len(lotto_items)}")

    for idx, item in enumerate(lotto_items):
        item_text = item.inner_text()
        LOG.debug(f"구매 아이템 {idx}: {item_text[:200]}...")

        draw_no = extract_draw_number_from_lotto_item(item_text)
        if not draw_no:
            LOG.debug(f"  -> 회차 추출 실패")
            continue

        nums = extract_numbers_from_text(item_text)

        LOG.debug(f"  -> 회차: {draw_no}, 번호 그룹 수: {len(nums)}")
        if nums:
            purchases.append((draw_no, nums))

    return purchases


def _parse_purchases_from_table(page) -> List[Tuple[int, List[List[str]]]]:
    purchases: List[Tuple[int, List[List[str]]]] = []
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

    return purchases


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

        LOG.info("조회 기간을 최근 1개월로 설정")
        _click_search_with_monthly_range(page)

        purchases: List[Tuple[int, List[List[str]]]] = []

        try:
            purchases = _parse_purchases_from_api(page)
        except Exception as e:
            LOG.error(f"API 파싱 실패: {e}")

        if not purchases:
            LOG.info("API에서 구매 내역을 찾지 못함. 리스트에서 재시도")
            try:
                purchases = _parse_purchases_from_list(page)
            except Exception as e:
                LOG.error(f"리스트 파싱 실패: {e}")

        if not purchases:
            LOG.info("리스트에서 구매 내역을 찾지 못함. 테이블에서 재시도")
            try:
                purchases = _parse_purchases_from_table(page)
            except Exception as e:
                LOG.error(f"테이블 파싱 실패: {e}")

        LOG.info(f"추출한 구매 건수: {len(purchases)}")

        if not purchases:
            LOG.info("구매 내역을 찾지 못함. body 텍스트에서 재시도")
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
