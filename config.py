"""Unified configuration module for gazua lottery automation.

This module provides centralized configuration management, eliminating
duplication between buy_lotto.py and check_result.py.
"""

import argparse
import logging
import os
from dataclasses import dataclass
from typing import Optional


# =============================================================================
# Centralized Constants
# =============================================================================

BASE_URL = "https://dhlottery.co.kr"
LOGIN_URL = f"{BASE_URL}/login"
MAIN_INFO_URL = f"{BASE_URL}/selectMainInfo.do"
USER_MNDP_URL = f"{BASE_URL}/mypage/selectUserMndp.do"
GAME_URL = "https://el.dhlottery.co.kr/game/TotalGame.jsp?LottoId=LO40"

# Single source of truth for User-Agent (prevents detection from mismatch)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Default timeouts
DEFAULT_TIMEOUT_MS = 30000
DEFAULT_QUEUE_TIMEOUT_MS = 180000


# =============================================================================
# Configuration Dataclass
# =============================================================================


@dataclass
class Config:
    """Unified configuration for all gazua scripts.

    Attributes:
        user_id: DHL lottery user ID.
        user_pw: DHL lottery password.
        slack_token: Slack bot token for notifications.
        slack_channel: Slack channel for notifications.
        headless: Run browser in headless mode.
        debug: Enable debug logging.
        debug_artifacts: Save screenshots/HTML on errors.
        debug_dir: Directory for debug artifacts.
        timeout_ms: Default page timeout in milliseconds.
        slow_mo: Slow down browser operations (ms).
        count: Number of lottery tickets to purchase (buy_lotto only).
        queue_timeout_ms: Timeout for queue overlay (buy_lotto only).
        proxy_user: Proxy authentication username.
        proxy_pw: Proxy authentication password.
        proxy_address: Proxy server address (host:port).
    """

    user_id: str
    user_pw: str
    slack_token: str
    slack_channel: str
    headless: bool = True
    debug: bool = False
    debug_artifacts: bool = False
    debug_dir: str = "artifacts"
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    slow_mo: int = 0
    # Buy-specific options
    count: int = 1
    queue_timeout_ms: int = DEFAULT_QUEUE_TIMEOUT_MS
    # Proxy settings
    proxy_user: Optional[str] = None
    proxy_pw: Optional[str] = None
    proxy_address: Optional[str] = None


# =============================================================================
# Utility Functions
# =============================================================================


def parse_bool(value: Optional[str], default: bool = False) -> bool:
    """Parse boolean from string (environment variables, CLI args).

    Args:
        value: String value to parse.
        default: Default value if input is None.

    Returns:
        True if value represents a truthy string, False otherwise.
    """
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_env_file(path: str = ".env") -> None:
    """Load environment variables from .env file.

    Only sets variables that are not already defined in the environment.

    Args:
        path: Path to the .env file.
    """
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value


# =============================================================================
# Argument Parsing
# =============================================================================


def _create_base_parser(description: str) -> argparse.ArgumentParser:
    """Create argument parser with common options.

    SECURITY: No positional arguments for credentials to prevent
    exposure in process lists and shell history.

    Args:
        description: Parser description.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(description=description)

    # Credentials (prefer environment variables)
    parser.add_argument(
        "--user-id",
        dest="user_id",
        help="DHL_USER_ID (prefer environment variable)",
    )
    parser.add_argument(
        "--user-pw",
        dest="user_pw",
        help="DHL_USER_PW (prefer environment variable)",
    )
    parser.add_argument("--slack-token", dest="slack_token")
    parser.add_argument("--slack-channel", dest="slack_channel")

    # Browser options
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--headed", action="store_true")

    # Debug options
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--debug-artifacts", action="store_true")
    parser.add_argument("--debug-dir", default=None)

    # Timing options
    parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
    parser.add_argument("--slow-mo", type=int, default=0)

    # Proxy options
    parser.add_argument("--proxy-user", dest="proxy_user")
    parser.add_argument("--proxy-pw", dest="proxy_pw")
    parser.add_argument("--proxy-address", dest="proxy_address")

    return parser


def create_buy_parser() -> argparse.ArgumentParser:
    """Create argument parser for buy_lotto.py.

    Returns:
        Configured ArgumentParser with buy-specific options.
    """
    parser = _create_base_parser("동행복권 로또 자동 구매")
    parser.add_argument("--count", type=int, default=None)
    parser.add_argument(
        "--queue-timeout-ms", type=int, default=DEFAULT_QUEUE_TIMEOUT_MS
    )
    return parser


def create_check_parser() -> argparse.ArgumentParser:
    """Create argument parser for check_result.py.

    Returns:
        Configured ArgumentParser for result checking.
    """
    return _create_base_parser("동행복권 로또 결과 확인")


# =============================================================================
# Configuration Loading
# =============================================================================


def load_config(parser: argparse.ArgumentParser, require_count: bool = False) -> Config:
    """Load configuration from environment variables and CLI arguments.

    Priority: CLI arguments > environment variables > defaults.

    Args:
        parser: ArgumentParser instance to use.
        require_count: If True, validate count argument (for buy_lotto).

    Returns:
        Configured Config instance.

    Raises:
        SystemExit: If required settings are missing or invalid.
    """
    load_env_file()
    args = parser.parse_args()

    # Load values with CLI > env priority
    user_id = args.user_id or os.getenv("DHL_USER_ID")
    user_pw = args.user_pw or os.getenv("DHL_USER_PW")
    slack_token = args.slack_token or os.getenv("SLACK_BOT_TOKEN")
    slack_channel = args.slack_channel or os.getenv("SLACK_CHANNEL")

    # Headless mode logic
    headless_env = parse_bool(os.getenv("HEADLESS"), default=True)
    if args.headed:
        headless = False
    elif args.headless:
        headless = True
    else:
        headless = headless_env

    # Debug settings
    debug = args.debug or parse_bool(os.getenv("DEBUG"))
    debug_artifacts = args.debug_artifacts or parse_bool(os.getenv("DEBUG_ARTIFACTS"))
    debug_dir = args.debug_dir or os.getenv("DEBUG_DIR") or "artifacts"

    # Validate required settings
    missing = []
    if not user_id:
        missing.append("DHL_USER_ID (--user-id or environment variable)")
    if not user_pw:
        missing.append("DHL_USER_PW (--user-pw or environment variable)")
    if not slack_token:
        missing.append("SLACK_BOT_TOKEN (--slack-token or environment variable)")
    if not slack_channel:
        missing.append("SLACK_CHANNEL (--slack-channel or environment variable)")
    if missing:
        raise SystemExit(f"Missing required settings:\n  " + "\n  ".join(missing))

    assert user_id is not None
    assert user_pw is not None
    assert slack_token is not None
    assert slack_channel is not None

    count = 1
    count = 1
    if require_count:
        count_raw = getattr(args, "count", None) or os.getenv("LOTTO_COUNT")
        count = int(count_raw) if count_raw else 1
        if count <= 0:
            raise SystemExit("--count must be a positive integer")

    queue_timeout_ms = getattr(args, "queue_timeout_ms", DEFAULT_QUEUE_TIMEOUT_MS)

    # Proxy settings
    proxy_user = args.proxy_user or os.getenv("PROXY_USER")
    proxy_pw = args.proxy_pw or os.getenv("PROXY_PW")
    proxy_address = args.proxy_address or os.getenv("PROXY_ADDRESS")

    return Config(
        user_id=user_id,
        user_pw=user_pw,
        slack_token=slack_token,
        slack_channel=slack_channel,
        headless=headless,
        debug=debug,
        debug_artifacts=debug_artifacts,
        debug_dir=debug_dir,
        timeout_ms=args.timeout_ms,
        slow_mo=args.slow_mo,
        count=count,
        queue_timeout_ms=queue_timeout_ms,
        proxy_user=proxy_user,
        proxy_pw=proxy_pw,
        proxy_address=proxy_address,
    )


def setup_logging(debug: bool) -> None:
    """Configure logging with consistent format.

    Args:
        debug: If True, set log level to DEBUG; otherwise INFO.
    """
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
