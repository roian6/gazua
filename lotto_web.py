import os
from datetime import datetime
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from lotto_utils import LOGIN_URL


def login(page, user_id: str, user_pw: str, timeout_ms: int = 30000) -> None:
    # 프록시 환경 고려하여 domcontentloaded로 완화
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.wait_for_selector("#inpUserId", timeout=timeout_ms)
    page.fill("#inpUserId", user_id)
    page.fill("#inpUserPswdEncn", user_pw)
    with page.expect_navigation():
        page.click("#btnLogin")
    page.wait_for_load_state("load")
    if "/login" in page.url:
        raise RuntimeError("로그인에 실패했거나 추가 인증이 필요합니다.")


def capture_screenshot(page, debug_dir: str, name_prefix: str) -> Optional[str]:
    try:
        os.makedirs(debug_dir, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(debug_dir, f"{name_prefix}_{timestamp}.png")
        page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        return None


def save_page_html(page, debug_dir: str, name_prefix: str) -> Optional[str]:
    try:
        os.makedirs(debug_dir, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(debug_dir, f"{name_prefix}_{timestamp}.html")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(page.content())
        return path
    except Exception:
        return None


def wait_for_overlay_hidden(target, selector: str, timeout_ms: int = 120000) -> None:
    try:
        if target.locator(selector).is_visible():
            target.locator(selector).wait_for(state="hidden", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        raise RuntimeError("대기열 해제가 지연되고 있습니다.")
