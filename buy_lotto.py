import argparse
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from playwright.sync_api import Playwright, sync_playwright

from lotto_utils import (
    BASE_URL,
    build_session_from_context,
    fetch_today_purchase_numbers,
    fetch_user_balance,
    get_now,
    get_sale_status,
    load_env_file,
    post_to_slack,
)
from lotto_web import capture_screenshot, login, save_page_html, wait_for_overlay_hidden

GAME_URL = "https://el.dhlottery.co.kr/game/TotalGame.jsp?LottoId=LO40"

LOG = logging.getLogger(__name__)


class BalanceError(Exception):
    def __init__(self, message="예치금이 부족합니다."):
        super().__init__(message)


class PurchaseUnavailableError(Exception):
    def __init__(self, message="현재 구매 불가 시간대입니다."):
        super().__init__(message)


class WeeklyLimitExceededError(Exception):
    def __init__(self, message="주간 구매 한도가 소진되었습니다."):
        super().__init__(message)


@dataclass
class Config:
    user_id: str
    user_pw: str
    slack_token: str
    slack_channel: str
    count: int
    headless: bool
    debug: bool
    debug_artifacts: bool
    debug_dir: str
    timeout_ms: int
    queue_timeout_ms: int
    slow_mo: int


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="동행복권 로또 자동 구매")
    parser.add_argument("user_id", nargs="?")
    parser.add_argument("user_pw", nargs="?")
    parser.add_argument("slack_token", nargs="?")
    parser.add_argument("slack_channel", nargs="?")
    parser.add_argument("count", nargs="?")
    parser.add_argument("--user-id", dest="user_id_opt")
    parser.add_argument("--user-pw", dest="user_pw_opt")
    parser.add_argument("--slack-token", dest="slack_token_opt")
    parser.add_argument("--slack-channel", dest="slack_channel_opt")
    parser.add_argument("--count", dest="count_opt", type=int)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--debug-artifacts", action="store_true")
    parser.add_argument("--debug-dir", default=None)
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--queue-timeout-ms", type=int, default=180000)
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

    count_raw = (
        args.count_opt
        if args.count_opt is not None
        else args.count or os.getenv("LOTTO_COUNT")
    )
    count = int(count_raw) if count_raw is not None else 1

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

    if count <= 0:
        raise SystemExit("COUNT must be a positive integer.")

    return Config(
        user_id=user_id,
        user_pw=user_pw,
        slack_token=slack_token,
        slack_channel=slack_channel,
        count=count,
        headless=headless,
        debug=debug,
        debug_artifacts=debug_artifacts,
        debug_dir=debug_dir,
        timeout_ms=args.timeout_ms,
        queue_timeout_ms=args.queue_timeout_ms,
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


def hook_slack_btn(config: Config) -> None:
    korea_time_str = get_now().strftime("%Y-%m-%d %H:%M:%S")
    payload = {
        "channel": config.slack_channel,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"> {korea_time_str} *로또 자동 구매 봇 알림* "
                        "\n예치금이 부족합니다! 충전을 해주세요!"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": "충전하러 가기",
                            "emoji": True,
                        },
                        "url": "https://dhlottery.co.kr/payment.do?method=payment",
                        "action_id": "button_action",
                    }
                ],
            },
        ],
    }
    post_to_slack(payload, config.slack_token, logger=LOG)


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


def run(playwright: Playwright, config: Config) -> None:
    browser = None
    context = None
    page = None
    try:
        browser = playwright.chromium.launch(
            headless=config.headless,
            slow_mo=config.slow_mo if config.slow_mo > 0 else None,
        )
        # 모바일 리다이렉트 방지: UA 고정 + 뷰포트 설정 + 모바일/터치 비활성화 + Stealth
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            is_mobile=False,
            has_touch=False,
            device_scale_factor=1.0,
        )
        
        # WebDriver 감지 우회 스크립트 주입
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        
        page = context.new_page()
        page.set_default_timeout(config.timeout_ms)
        page.set_default_navigation_timeout(config.timeout_ms)

        login(page, config.user_id, config.user_pw, timeout_ms=config.timeout_ms)
        LOG.info("로그인 후 현재 URL: %s", page.url)

        is_sale_available, sale_status, sale_message = get_sale_status()
        if not is_sale_available:
            hook_slack(config, sale_message or "현재 구매 불가 시간대입니다.")
            return

        session = build_session_from_context(context)
        balance, user_mndp = fetch_user_balance(session)
        user_name = (
            user_mndp.get("userNm")
            or user_mndp.get("userId")
            or user_mndp.get("loginId")
            or config.user_id
        )
        hook_slack(config, f"로그인 사용자: {user_name}, 예치금: {balance}")

        if 1000 * config.count > balance:
            raise BalanceError()

        page.goto(GAME_URL, wait_until="domcontentloaded")
        page.wait_for_load_state("load", timeout=config.timeout_ms)
        
        if "/login" in page.url.lower() or "login" in page.url.lower():
            # 리다이렉트된 페이지의 내용 확인
            body_text = page.locator("body").inner_text()[:500].replace("\n", " ")
            LOG.error("페이지 내용 덤프: %s", body_text)
            raise RuntimeError(f"게임 페이지 접근 실패 - 로그인 페이지로 리다이렉트됨: {page.url}")
        
        try:
            page.wait_for_selector("iframe#ifrm_tab", state="attached", timeout=config.timeout_ms)
        except Exception as exc:
            if config.debug_artifacts:
                capture_screenshot(page, config.debug_dir, "game_page_error")
                save_page_html(page, config.debug_dir, "game_page_error")
            
            # 페이지 내용 덤프 추가
            try:
                body_text = page.locator("body").inner_text()[:1000].replace("\n", " ")
            except:
                body_text = "텍스트 추출 실패"
                
            LOG.error("게임 페이지 로딩 실패. URL: %s, 제목: %s", page.url, page.title())
            LOG.error("실패 시점 페이지 내용: %s", body_text)
            raise RuntimeError(f"게임 프레임을 찾을 수 없습니다. 현재 URL: {page.url}") from exc
        
        game_frame = page.frame(name="ifrm_tab")
        if not game_frame:
            raise RuntimeError("게임 프레임을 찾지 못했습니다.")
        target = game_frame
        wait_for_overlay_hidden(target, "#popupLayer", timeout_ms=config.queue_timeout_ms)

        unavailable_messages = [
            "회차정보가 존재하지 않습니다",
            "회차 정보가 존재하지 않습니다",
            "판매시간이 아닙니다",
            "판매 시간이 아닙니다",
            "구매가능시간이 아닙니다",
            "구매 가능 시간이 아닙니다",
        ]

        def check_unavailable_message(text: str) -> bool:
            return any(msg in text for msg in unavailable_messages)

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
            raise PurchaseUnavailableError("현재 구매 불가 시간대입니다. (팝업 확인)")

        try:
            frame_content = target.locator("body").inner_text(timeout=3000)
            if check_unavailable_message(frame_content):
                raise PurchaseUnavailableError("현재 구매 불가 시간대입니다. (본문 확인)")
        except PurchaseUnavailableError:
            raise
        except Exception as exc:
            LOG.debug("본문 텍스트 읽기 실패: %s", exc)

        try:
            buy_area = target.locator("#num2, #amoundApply, #btnSelectNum")
            if buy_area.count() == 0:
                LOG.debug("구매 영역을 찾을 수 없음")
                raise PurchaseUnavailableError("구매 영역을 찾을 수 없습니다. 구매 가능 시간을 확인해주세요.")
        except PurchaseUnavailableError:
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
            try:
                current_content = target.locator("body").inner_text(timeout=3000)
                if check_unavailable_message(current_content):
                    raise PurchaseUnavailableError("현재 구매 불가 시간대입니다.")
            except PurchaseUnavailableError:
                raise
            except Exception:
                pass
            raise PurchaseUnavailableError("구매 페이지를 불러올 수 없습니다. 구매 가능 시간을 확인해주세요.")
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

        hook_slack(
            config,
            (
                f"{config.count}개 복권 구매 성공! "
                f"\n자세하게 확인하기: {BASE_URL}/mypage/mylotteryledger"
            ),
        )

        now_date = get_now().date().strftime("%Y%m%d")
        numbers, info = fetch_today_purchase_numbers(
            session,
            now_date,
            debug_dir=config.debug_dir if config.debug_artifacts else None,
        )
        if numbers:
            lines = [", ".join(group) for group in numbers]
            hook_slack(config, "이번주 나의 행운의 번호는?!\n" + "\n".join(lines))
        elif info.get("status") == "drawing":
            hook_slack(config, info.get("message", "추첨 중입니다. 마이페이지에서 확인해주세요."))
        else:
            LOG.info("번호 조회 실패: %s", info)
            hook_slack(config, "구매 번호를 확인하지 못했습니다. 마이페이지에서 확인해주세요.")
    except BalanceError:
        hook_slack_btn(config)
    except PurchaseUnavailableError as exc:
        hook_slack(config, str(exc))
    except WeeklyLimitExceededError as exc:
        hook_slack(config, str(exc))
    except Exception as exc:
        if config.debug_artifacts and page:
            capture_screenshot(page, config.debug_dir, "buy_lotto_error")
            save_page_html(page, config.debug_dir, "buy_lotto_error")
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
