import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

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
    fetch_json,
    write_debug_payload,
)
from lotto_web import capture_screenshot, login, save_page_html, wait_for_overlay_hidden
from slack_notify import notify, notify_low_balance

LOG = logging.getLogger(__name__)

GAME_API_BASE = "https://ol.dhlottery.co.kr"
READY_QUEUE_URL = f"{GAME_API_BASE}/olotto/game/egovUserReadySocket.json"
EXEC_BUY_URL = f"{GAME_API_BASE}/olotto/game/execBuy.do"
GAME_CONTEXT_URL = f"{GAME_API_BASE}/olotto/game/game645.do"
MAX_GAME_COUNT = 5
GAME_ALPHABET = ["A", "B", "C", "D", "E"]


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


def _parse_game_context(html: str) -> Dict[str, str]:
    context = {}
    for key in ("ROUND_DRAW_DATE", "WAMT_PAY_TLMT_END_DT"):
        match = re.search(rf"{key}\"?\s+value=\"([^\"]+)\"", html)
        if match:
            context[key] = match.group(1)
    round_match = re.search(r"id=\"curRound\"[^>]*>(\d+)<", html)
    if round_match:
        context["curRound"] = round_match.group(1)
    return context


def _build_auto_param(game_count: int) -> List[Dict[str, Any]]:
    limited = min(game_count, MAX_GAME_COUNT)
    return [
        {"genType": "0", "arrGameChoiceNum": None, "alpabet": GAME_ALPHABET[idx]}
        for idx in range(limited)
    ]


def _wait_for_ready_queue(session: Any, debug_dir: Optional[str]) -> str:
    start = time.time()
    while time.time() - start < 30:
        payload, _ = fetch_json(
            session,
            READY_QUEUE_URL,
            params={},
            method="POST",
            debug_dir=debug_dir,
            label="buy_ready_queue",
        )
        data = (payload or {}).get("result") or payload or {}
        ready_cnt = int(data.get("ready_cnt") or data.get("readyCnt") or 0)
        if ready_cnt <= 0:
            return data.get("ready_ip") or data.get("readyIp") or ""
        time.sleep(1)
    return ""


def _execute_buy_api(session: Any, game_count: int, debug_dir: Optional[str]) -> Dict[str, Any]:
    session.headers.update({
        "Referer": GAME_CONTEXT_URL,
        "Origin": GAME_API_BASE,
    })
    try:
        res = session.get(GAME_CONTEXT_URL, timeout=20)
        res.raise_for_status()
        context_html = res.text
        if debug_dir:
            write_debug_payload(
                debug_dir,
                "buy_game_context",
                {"meta": {"url": GAME_CONTEXT_URL}, "response_text": context_html[:20000]},
            )
    except Exception as exc:
        raise RuntimeError(f"게임 페이지 컨텍스트를 가져오지 못했습니다: {exc}") from exc

    context = _parse_game_context(context_html)
    if not context.get("curRound"):
        raise RuntimeError("회차 정보를 찾을 수 없습니다.")

    param = _build_auto_param(game_count)
    pay_amount = 1000 * len(param)
    direct_ip = _wait_for_ready_queue(session, debug_dir)

    params = {
        "round": context["curRound"],
        "direct": direct_ip,
        "nBuyAmount": str(pay_amount),
        "param": json.dumps(param, ensure_ascii=True),
        "ROUND_DRAW_DATE": context.get("ROUND_DRAW_DATE", ""),
        "WAMT_PAY_TLMT_END_DT": context.get("WAMT_PAY_TLMT_END_DT", ""),
        "gameCnt": len(param),
        "saleMdaDcd": "10",
    }

    payload, _ = fetch_json(
        session,
        EXEC_BUY_URL,
        params=params,
        method="POST",
        debug_dir=debug_dir,
        label="buy_exec_buy",
    )
    if not payload:
        raise RuntimeError("구매 API 응답이 없습니다.")
    return payload


def _interpret_buy_response(payload: Dict[str, Any]) -> Optional[str]:
    if payload.get("loginYn") == "N":
        raise RuntimeError("세션이 만료되었습니다. 다시 로그인해주세요.")
    if payload.get("isAllowed") == "N":
        raise RuntimeError("비정상적인 방법으로 접속하였습니다. 정상적인 PC 환경에서 접속하여 주시기 바랍니다.")
    if payload.get("isGameManaged") == "Y":
        raise PurchaseUnavailableError(payload.get("errorMsg") or "현재 구매가 제한되었습니다.")
    if payload.get("checkOltSaleTime") is False:
        raise PurchaseUnavailableError("현재 구매 불가 시간대입니다.")

    result = payload.get("result") or {}
    if isinstance(result, dict):
        code = result.get("resultCode")
        if code and str(code) not in {"0", "100", "000", "SUCCESS", "success"}:
            raise RuntimeError(result.get("resultMsg") or "구매 처리 중 오류가 발생했습니다.")
    return payload.get("message") or None



def read_layer_message(layer) -> str:
    try:
        return layer.locator(".layer-message").inner_text().strip()
    except Exception:
        return ""


def wait_for_dialog(target, timeout_ms: int) -> Optional[Tuple[str, str]]:
    confirm_layer = target.locator("#popupLayerConfirm")
    alert_layer = target.locator("#popupLayerAlert")
    limit_popup = target.locator("#recommend720Plus")
    end_time = time.time() + timeout_ms / 1000

    while time.time() < end_time:
        if limit_popup.count() > 0 and limit_popup.is_visible():
            LOG.info("구매 한도 초과 팝업 발견")
            return "limit", "주간 구매 한도 초과"
        if confirm_layer.is_visible():
            message = read_layer_message(confirm_layer)
            LOG.debug(f"confirm 다이얼로그 발견: {message}")
            clicked = safe_click(confirm_layer, "input[value='확인']")
            LOG.debug(f"확인 버튼 클릭 결과: {clicked}")
            time.sleep(0.5)
            if confirm_layer.is_visible():
                LOG.warning("확인 버튼 클릭 후에도 다이얼로그가 여전히 표시됨")
            return "confirm", message
        if alert_layer.is_visible():
            message = read_layer_message(alert_layer)
            LOG.debug(f"alert 다이얼로그 발견: {message}")
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


def _execute_buy_ui_flow(page, context, config) -> None:
    LOG.info("게임 페이지 세션 초기화")
    LOG.debug(f"GAME_URL: {GAME_URL}")
    page.set_extra_http_headers({"Referer": f"{BASE_URL}/main"})

    try:
        response = page.goto(GAME_URL, wait_until="domcontentloaded")
        if response:
            LOG.info(f"goto 응답: status={response.status}, url={response.url}")
        else:
            LOG.warning("goto 응답이 None입니다")
    except Exception as goto_err:
        LOG.error(f"goto 실패: {type(goto_err).__name__}: {goto_err}")
        LOG.error(f"현재 URL: {page.url}")
        raise

    page.wait_for_load_state("load", timeout=config.timeout_ms)

    if "m.dhlottery.co.kr" in page.url:
        LOG.warning(f"모바일 페이지로 리다이렉트됨: {page.url}")
        context.add_cookies([
            {"name": "PC_VER", "value": "Y", "domain": ".dhlottery.co.kr", "path": "/"}
        ])
        time.sleep(1)

    if "/login" in page.url.lower() or "login" in page.url.lower():
        body_text = page.locator("body").inner_text()[:500].replace("\n", " ")
        LOG.error("페이지 내용 덤프: %s", body_text)
        raise RuntimeError(f"게임 페이지 접근 실패 - 로그인 페이지로 리다이렉트됨: {page.url}")

    page.set_extra_http_headers({
        "Referer": GAME_URL,
        "Origin": "https://el.dhlottery.co.kr",
    })
    page.goto(GAME_CONTEXT_URL, wait_until="domcontentloaded")
    page.wait_for_load_state("load", timeout=config.timeout_ms)
    LOG.info(f"게임 iframe 직접 이동 완료. 현재 URL: {page.url}")
    target = page

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
        buy_area_count = buy_area.count()
        LOG.info(f"구매 영역 요소 수: {buy_area_count}")
        if buy_area_count == 0:
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
        LOG.info("자동번호 탭(#num2) 발견")
    except Exception:
        site_msg = extract_site_message(target)
        if check_session_expired(site_msg):
            raise RuntimeError(f"세션이 만료되었습니다. 다시 로그인해주세요. 사이트 메시지: {site_msg}")
        if check_unavailable_message(site_msg):
            raise PurchaseUnavailableError(f"현재 구매 불가 시간대입니다. 사이트 메시지: {site_msg}")
        raise PurchaseUnavailableError(f"구매 페이지를 불러올 수 없습니다. 사이트 메시지: {site_msg or '없음'}")
    auto_tab.click()
    LOG.info("자동번호 탭 클릭 완료")
    time.sleep(0.5)

    select_locator = target.locator("#amoundApply")
    select_locator.wait_for(state="visible", timeout=config.timeout_ms)
    LOG.info(f"수량 선택 드롭다운 발견, {config.count}개 선택 시도")
    select_locator.select_option(str(config.count))
    LOG.info("수량 선택 완료")
    time.sleep(0.3)

    confirm_btn = target.locator("#btnSelectNum")
    confirm_btn.wait_for(state="visible", timeout=config.timeout_ms)
    LOG.info("번호확인 버튼 발견")
    confirm_btn.click()
    LOG.info("번호확인 버튼 클릭 완료")
    time.sleep(0.5)

    select_gbn_a = target.locator("#selectGbnA")
    try:
        select_gbn_a.wait_for(state="visible", timeout=5000)
        gbn_text = select_gbn_a.inner_text()
        LOG.info(f"선택 상태 텍스트: {gbn_text}")
        if gbn_text == "미지정":
            raise RuntimeError("번호 선택이 적용되지 않았습니다.")
    except RuntimeError:
        raise
    except Exception as exc:
        LOG.warning(f"선택 상태 확인 실패: {exc}")

    buy_btn = target.locator("button#btnBuy")
    buy_btn.wait_for(state="visible", timeout=config.timeout_ms)
    LOG.info("구매하기 버튼 발견")
    buy_btn.click()
    LOG.info("구매하기 버튼 클릭 완료")

    dialog = wait_for_dialog(target, timeout_ms=10000)
    LOG.info(f"다이얼로그 결과: {dialog}")
    if dialog and dialog[0] == "limit":
        raise WeeklyLimitExceededError(dialog[1])
    elif dialog and dialog[0] == "confirm":
        followup = wait_for_dialog(target, timeout_ms=15000)
        LOG.info(f"후속 다이얼로그: {followup}")
        if followup is None:
            LOG.warning("구매 확인 후 응답 다이얼로그를 받지 못함 - 구매 실패 가능성")
            raise RuntimeError("구매 확인 후 응답을 받지 못했습니다. 마이페이지에서 확인하세요.")
        if followup[0] == "limit":
            raise WeeklyLimitExceededError(followup[1])
        elif followup[0] == "alert":
            alert_msg = followup[1]
            if "성공" in alert_msg or "완료" in alert_msg or "구매" in alert_msg:
                LOG.info(f"구매 성공 메시지 확인: {alert_msg}")
            else:
                raise RuntimeError(alert_msg or "구매 과정에서 오류가 발생했습니다.")
    elif dialog and dialog[0] == "alert":
        raise RuntimeError(dialog[1] or "구매 과정에서 오류가 발생했습니다.")
    elif dialog is None:
        LOG.warning("구매 버튼 클릭 후 다이얼로그가 나타나지 않음")
        raise RuntimeError("구매 다이얼로그가 나타나지 않았습니다.")

    safe_click(target, "input[name='closeLayer']")
    LOG.info("구매 플로우 완료")

def run(playwright: Playwright, config: Config) -> None:
    browser = None
    context = None
    page = None

    try:
        browser, context = create_browser_context(playwright, config)
        page = create_page(context, config)

        login(page, config.user_id, config.user_pw, timeout_ms=config.timeout_ms)
        LOG.info("로그인 후 현재 URL: %s", page.url)
        
        cookies = context.cookies()
        LOG.debug(f"현재 쿠키 수: {len(cookies)}")
        for c in cookies:
            LOG.debug(f"쿠키: {c.get('name')}={str(c.get('value', ''))[:20]}... domain={c.get('domain')}")

        is_sale_available, sale_status, sale_message = get_sale_status()
        if not is_sale_available:
            notify(config, sale_message or "현재 구매 불가 시간대입니다.")
            return

        session = build_session_from_context(
            context,
            proxy_address=config.proxy_address,
            proxy_user=config.proxy_user,
            proxy_pw=config.proxy_pw,
        )
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

        purchase_done = False
        try:
            LOG.info("API 구매 시도")
            payload = _execute_buy_api(
                session,
                config.count,
                config.debug_dir if config.debug_artifacts else None,
            )
            _interpret_buy_response(payload)
            purchase_done = True
            LOG.info("API 구매 성공")
        except (PurchaseUnavailableError, WeeklyLimitExceededError):
            raise
        except Exception as api_exc:
            LOG.warning(f"API 구매 실패, UI 흐름으로 전환: {api_exc}")

        if not purchase_done:
            _execute_buy_ui_flow(page, context, config)
            purchase_done = True


        if not purchase_done:
            raise RuntimeError("구매를 완료하지 못했습니다.")

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
