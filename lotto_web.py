import os
from datetime import datetime
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import LOGIN_URL


def login(page, user_id: str, user_pw: str, timeout_ms: int = 30000) -> None:
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_selector("#inpUserId", state="visible", timeout=timeout_ms)
    
    # type()을 사용해야 onkeyup 이벤트 발생 -> enterUserLogin() -> RSA 암호화
    id_input = page.locator("#inpUserId")
    id_input.click()
    id_input.fill("")
    id_input.type(user_id, delay=50)
    
    pw_input = page.locator("#inpUserPswdEncn")
    pw_input.click()
    pw_input.fill("")
    pw_input.type(user_pw, delay=50)
    
    page.wait_for_timeout(500)
    page.locator("#btnLogin").click()
    
    try:
        page.wait_for_url("**/main*", timeout=timeout_ms, wait_until="domcontentloaded")
    except PlaywrightTimeoutError:
        if "/login" in page.url:
            raise RuntimeError(f"로그인 시간 초과. 현재 URL: {page.url}")
        if "/main" not in page.url:
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
