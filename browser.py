"""Browser automation module for gazua lottery automation."""

from typing import Tuple

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from config import USER_AGENT, Config


STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
    Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
    window.navigator.chrome = { runtime: {} };
"""


def create_browser_context(
    playwright: Playwright,
    config: Config,
) -> Tuple[Browser, BrowserContext]:
    """Create browser and context with stealth settings."""
    launch_options = {
        "headless": config.headless,
        "slow_mo": config.slow_mo if config.slow_mo > 0 else None,
    }

    if config.proxy_address:
        launch_options["proxy"] = {"server": f"http://{config.proxy_address}"}
        if config.proxy_user and config.proxy_pw:
            launch_options["proxy"]["username"] = config.proxy_user
            launch_options["proxy"]["password"] = config.proxy_pw

    browser = playwright.chromium.launch(**launch_options)

    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1920, "height": 1080},
        is_mobile=False,
        has_touch=False,
        device_scale_factor=1.0,
        locale="ko-KR",
        timezone_id="Asia/Seoul",
    )

    context.add_init_script(STEALTH_SCRIPT)
    return browser, context


def create_page(context: BrowserContext, config: Config) -> Page:
    """Create a new page with default timeouts."""
    page = context.new_page()
    page.set_default_timeout(config.timeout_ms)
    page.set_default_navigation_timeout(config.timeout_ms)
    return page
