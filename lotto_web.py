import os
from datetime import datetime
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from lotto_utils import LOGIN_URL


def login(page, user_id: str, user_pw: str, timeout_ms: int = 30000) -> None:
    # 프록시 환경 고려하여 domcontentloaded로 완화
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=timeout_ms)
    page.wait_for_selector("#inpUserId", state="visible", timeout=timeout_ms)
    
    # 입력 필드 포커스 및 입력
    page.click("#inpUserId")
    page.fill("#inpUserId", user_id)
    
    page.click("#inpUserPswdEncn")
    page.fill("#inpUserPswdEncn", user_pw)
    
    # 로그인 버튼 클릭 또는 엔터키 입력
    # 버튼 클릭이 안 먹힐 수 있으므로 엔터키도 시도
    page.press("#inpUserPswdEncn", "Enter")
    
    # 혹시 엔터키로 안 되면 버튼 클릭도 시도 (안전장치)
    try:
        page.click("#btnLogin", timeout=3000)
    except Exception:
        pass # 이미 넘어갔거나 버튼이 없으면 패스
    
    try:
        # 메인 페이지 URL 패턴 대기 (타임아웃 90초)
        page.wait_for_url("**/*.do?method=main*", timeout=timeout_ms, wait_until="domcontentloaded")
    except PlaywrightTimeoutError:
        # 타임아웃 발생 시 현재 URL과 상태 확인
        if "/login" in page.url:
            raise RuntimeError(f"로그인 시간 초과. 현재 URL: {page.url}")
        # URL은 변했지만 로딩이 안 끝난 경우일 수 있으므로 진행
        pass
        
    # 추가 로딩 대기 (필요시)
    # page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
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
