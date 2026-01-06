import logging
import time
from typing import Optional, Tuple

from playwright.sync_api import Playwright, sync_playwright

from browser import create_browser_context, create_page
from config import (
    BASE_URL,
    GAME_URL,
    Config,
    create_buy_parser,
    load_config,
    setup_logging,
)
from lotto_utils import (
    build_session_from_context,
    fetch_today_purchase_numbers,
    fetch_user_balance,
    get_now,
    get_sale_status,
)
from lotto_web import capture_screenshot, login, save_page_html, wait_for_overlay_hidden
from slack_notify import notify, notify_low_balance

LOG = logging.getLogger(__name__)


class BalanceError(Exception):
    def __init__(self, message: str = "예치금이 부족합니다."):
        super().__init__(message)


class PurchaseUnavailableError(Exception):
    def __init__(self, message: str = "현재 구매 불가 시간대입니다."):
        super().__init__(message)


class WeeklyLimitExceededError(Exception):
    def __init__(self, message: str = "주간 구매 한도가 소진되었습니다."):
        super().__init__(message)


def safe_click(target, selector: str, timeout_ms: int = 2000) -> bool:
    try:
        target.locator(selector).click(timeout=timeout_ms)
        return True
    except Exception:
        return False


def click_any(target, selectors, timeout_ms: int = 2000) -> bool:
    for selector in selectors:
        if safe_click(target, selector, timeout_ms=timeout_ms):
            return True
    return False


def read_layer_message(layer) -> str:
    try:
        return layer.locator(".layer-message").inner_text().strip()
    except Exception:
        return ""


def wait_for_dialog(target, timeout_ms: int) -> Optional[Tuple[str, str]]:
    confirm_layer = target.locator("#popupLayerConfirm")
    alert_layer = target.locator("#popupLayerAlert")
    end_time = time.time() + timeout_ms / 1000

    while time.time() < end_time:
        if confirm_layer.is_visible():
            message = read_layer_message(confirm_layer)
            safe_click(confirm_layer, "input[value='확인']")
            return "confirm", message
        if alert_layer.is_visible():
            message = read_layer_message(alert_layer)
            safe_click(alert_layer, "input[value='확인']")
            return "alert", message
        time.sleep(0.5)
    return None


UNAVAILABLE_MESSAGES = [
    "회차정보가 존재하지 않습니다",
    "회차 정보가 존재하지 않습니다",
    "판매시간이 아닙니다",
    "판매 시간이 아닙니다",
    "구매가능시간이 아닙니다",
    "구매 가능 시간이 아닙니다",
]

SESSION_EXPIRED_MESSAGES = [
    "세션이 해제되었습니다",
    "세션이 만료되었습니다",
    "로그인해 주시기 바랍니다",
    "로그인이 필요합니다",
    "시간 초과",
]


def check_unavailable_message(text: str) -> bool:
    return any(msg in text for msg in UNAVAILABLE_MESSAGES)


def check_session_expired(text: str) -> bool:
    return any(msg in text for msg in SESSION_EXPIRED_MESSAGES)


def extract_site_message(target) -> str:
    messages = []
    
    for selector in ["#popupLayerAlert", "#popupLayerConfirm", ".layer-message", ".popup-message"]:
        try:
            el = target.locator(selector)
            if el.count() > 0 and el.is_visible():
                text = el.inner_text(timeout=2000).strip()
                if text:
                    messages.append(text)
        except Exception:
            pass
    
    if not messages:
        try:
            body_text = target.locator("body").inner_text(timeout=3000)
            for line in body_text.split("\n"):
                line = line.strip()
                if any(keyword in line for keyword in ["세션", "로그인", "시간", "판매", "구매", "오류", "실패"]):
                    messages.append(line)
                    if len(messages) >= 3:
                        break
        except Exception:
            pass
    
    return " | ".join(messages[:3]) if messages else ""


def run(playwright: Playwright, config: Config) -> None:
    browser = None
    context = None
    page = None

    try:
        browser, context = create_browser_context(playwright, config)
        page = create_page(context, config)

        login(page, config.user_id, config.user_pw, timeout_ms=config.timeout_ms)
        LOG.info("로그인 후 현재 URL: %s", page.url)

        is_sale_available, sale_status, sale_message = get_sale_status()
        if not is_sale_available:
            notify(config, sale_message or "현재 구매 불가 시간대입니다.")
            return

        session = build_session_from_context(context)
        balance, user_mndp = fetch_user_balance(session)
        user_name = (
            user_mndp.get("userNm")
            or user_mndp.get("userId")
            or user_mndp.get("loginId")
            or config.user_id
        )
        notify(config, f"로그인 사용자: {user_name}, 예치금: {balance}")

        if 1000 * config.count > balance:
            raise BalanceError()

        for attempt in range(3):
            try:
                LOG.info(f"게임 페이지 이동 시도 {attempt + 1}/3")
                page.set_extra_http_headers({"Referer": f"{BASE_URL}/main"})
                page.goto(GAME_URL, wait_until="domcontentloaded")
                page.wait_for_load_state("load", timeout=config.timeout_ms)

                if "m.dhlottery.co.kr" in page.url:
                    LOG.warning(f"모바일 페이지로 리다이렉트됨: {page.url}")
                    context.add_cookies([
                        {"name": "PC_VER", "value": "Y", "domain": ".dhlottery.co.kr", "path": "/"}
                    ])
                    time.sleep(1)
                    continue

                if "/login" in page.url.lower() or "login" in page.url.lower():
                    body_text = page.locator("body").inner_text()[:500].replace("\n", " ")
                    LOG.error("페이지 내용 덤프: %s", body_text)
                    raise RuntimeError(f"게임 페이지 접근 실패 - 로그인 페이지로 리다이렉트됨: {page.url}")

                page.wait_for_selector("iframe#ifrm_tab", state="attached", timeout=config.timeout_ms)
                break
            except Exception as exc:
                if attempt == 2:
                    if config.debug_artifacts:
                        capture_screenshot(page, config.debug_dir, "game_page_error")
                        save_page_html(page, config.debug_dir, "game_page_error")

                    try:
                        body_text = page.locator("body").inner_text()[:1000].replace("\n", " ")
                    except Exception:
                        body_text = "텍스트 추출 실패"

                    LOG.error("게임 페이지 로딩 실패. URL: %s, 제목: %s", page.url, page.title())
                    LOG.error("실패 시점 페이지 내용: %s", body_text)
                    raise RuntimeError(f"게임 프레임을 찾을 수 없습니다. 현재 URL: {page.url}") from exc
                time.sleep(2)

        game_frame = page.frame(name="ifrm_tab")
        if not game_frame:
            raise RuntimeError("게임 프레임을 찾지 못했습니다.")
        target = game_frame
        wait_for_overlay_hidden(target, "#popupLayer", timeout_ms=config.queue_timeout_ms)

        alert_layer = target.locator("#popupLayerAlert")
        alert_message = ""
        try:
            if alert_layer.is_visible():
                alert_message = read_layer_message(alert_layer)
                LOG.debug("알림 팝업 메시지: %s", alert_message)
                safe_click(alert_layer, "input[value='확인']")
        except Exception:
            pass

        if check_unavailable_message(alert_message):
            raise PurchaseUnavailableError(f"현재 구매 불가 시간대입니다. 사이트 메시지: {alert_message}")
        
        if check_session_expired(alert_message):
            raise RuntimeError(f"세션이 만료되었습니다. 다시 로그인해주세요. 사이트 메시지: {alert_message}")

        try:
            frame_content = target.locator("body").inner_text(timeout=3000)
            if check_session_expired(frame_content):
                site_msg = extract_site_message(target)
                raise RuntimeError(f"세션이 만료되었습니다. 다시 로그인해주세요. 사이트 메시지: {site_msg}")
            if check_unavailable_message(frame_content):
                site_msg = extract_site_message(target)
                raise PurchaseUnavailableError(f"현재 구매 불가 시간대입니다. 사이트 메시지: {site_msg}")
        except (PurchaseUnavailableError, RuntimeError):
            raise
        except Exception as exc:
            LOG.debug("본문 텍스트 읽기 실패: %s", exc)

        try:
            buy_area = target.locator("#num2, #amoundApply, #btnSelectNum")
            if buy_area.count() == 0:
                site_msg = extract_site_message(target)
                if check_session_expired(site_msg):
                    raise RuntimeError(f"세션이 만료되었습니다. 다시 로그인해주세요. 사이트 메시지: {site_msg}")
                raise PurchaseUnavailableError(f"구매 영역을 찾을 수 없습니다. 사이트 메시지: {site_msg or '없음'}")
        except (PurchaseUnavailableError, RuntimeError):
            raise
        except Exception:
            pass

        recommend_popup = target.locator("#recommend720Plus")
        if recommend_popup.count() > 0 and recommend_popup.is_visible():
            raise WeeklyLimitExceededError()

        auto_tab = target.locator("#num2")
        try:
            auto_tab.wait_for(state="visible", timeout=min(config.timeout_ms, 10000))
        except Exception:
            site_msg = extract_site_message(target)
            if check_session_expired(site_msg):
                raise RuntimeError(f"세션이 만료되었습니다. 다시 로그인해주세요. 사이트 메시지: {site_msg}")
            if check_unavailable_message(site_msg):
                raise PurchaseUnavailableError(f"현재 구매 불가 시간대입니다. 사이트 메시지: {site_msg}")
            raise PurchaseUnavailableError(f"구매 페이지를 불러올 수 없습니다. 사이트 메시지: {site_msg or '없음'}")
        auto_tab.click()
        time.sleep(0.5)

        select_locator = target.locator("#amoundApply")
        select_locator.wait_for(state="visible", timeout=config.timeout_ms)
        select_locator.select_option(str(config.count))
        time.sleep(0.3)

        confirm_btn = target.locator("#btnSelectNum")
        confirm_btn.wait_for(state="visible", timeout=config.timeout_ms)
        confirm_btn.click()
        time.sleep(0.5)

        select_gbn_a = target.locator("#selectGbnA")
        try:
            select_gbn_a.wait_for(state="visible", timeout=5000)
            if select_gbn_a.inner_text() == "미지정":
                raise RuntimeError("번호 선택이 적용되지 않았습니다.")
        except Exception:
            pass

        buy_btn = target.locator("button#btnBuy")
        buy_btn.wait_for(state="visible", timeout=config.timeout_ms)
        buy_btn.click()

        dialog = wait_for_dialog(target, timeout_ms=10000)
        if dialog and dialog[0] == "confirm":
            followup = wait_for_dialog(target, timeout_ms=5000)
            if followup and followup[0] == "alert":
                raise RuntimeError(followup[1] or "구매 과정에서 오류가 발생했습니다.")
        elif dialog and dialog[0] == "alert":
            raise RuntimeError(dialog[1] or "구매 과정에서 오류가 발생했습니다.")

        safe_click(target, "input[name='closeLayer']")

        notify(
            config,
            f"{config.count}개 복권 구매 성공!\n자세하게 확인하기: {BASE_URL}/mypage/mylotteryledger",
        )

        now_date = get_now().date().strftime("%Y%m%d")
        numbers, info = fetch_today_purchase_numbers(
            session,
            now_date,
            debug_dir=config.debug_dir if config.debug_artifacts else None,
        )
        if numbers:
            lines = [", ".join(group) for group in numbers]
            notify(config, "이번주 나의 행운의 번호는?!\n" + "\n".join(lines))
        elif info.get("status") == "drawing":
            notify(config, info.get("message", "추첨 중입니다. 마이페이지에서 확인해주세요."))
        else:
            LOG.info("번호 조회 실패: %s", info)
            notify(config, "구매 번호를 확인하지 못했습니다. 마이페이지에서 확인해주세요.")

    except BalanceError:
        notify_low_balance(config)
    except PurchaseUnavailableError as exc:
        notify(config, str(exc))
    except WeeklyLimitExceededError as exc:
        notify(config, str(exc))
    except Exception as exc:
        if config.debug_artifacts and page:
            capture_screenshot(page, config.debug_dir, "buy_lotto_error")
            save_page_html(page, config.debug_dir, "buy_lotto_error")
        notify(config, f"에러 발생: {exc}")
        raise
    finally:
        if context is not None:
            context.close()
        if browser is not None:
            browser.close()


if __name__ == "__main__":
    cfg = load_config(create_buy_parser(), require_count=True)
    setup_logging(cfg.debug)
    with sync_playwright() as pw:
        run(pw, cfg)
