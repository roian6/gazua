"""Slack notification module for gazua lottery automation.

This module provides unified Slack notification functions, eliminating
duplication between buy_lotto.py and check_result.py.
"""

import logging
from typing import Any, Dict, Optional

import requests
from requests import Response

from config import BASE_URL, Config

LOG = logging.getLogger(__name__)


def post_to_slack(
    payload: Dict[str, Any],
    token: str,
    max_retries: int = 3,
    timeout: int = 10,
    logger: Optional[logging.Logger] = None,
) -> Optional[Response]:
    """Post a message to Slack with retry logic.

    Handles rate limiting (429) and server errors (5xx) with exponential backoff.

    Args:
        payload: Slack message payload.
        token: Slack bot token.
        max_retries: Maximum number of retry attempts.
        timeout: Request timeout in seconds.
        logger: Logger for warning messages.

    Returns:
        Response object on success, None on failure.
    """
    import time

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    backoff = 1
    last_response = None

    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://slack.com/api/chat.postMessage",
                json=payload,
                headers=headers,
                timeout=timeout,
            )
            last_response = response

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                sleep_for = (
                    int(retry_after) if retry_after and retry_after.isdigit() else backoff
                )
                time.sleep(sleep_for)
                backoff *= 2
                continue

            if response.status_code >= 500:
                time.sleep(backoff)
                backoff *= 2
                continue

            return response
        except requests.RequestException as exc:
            if logger:
                logger.warning("Slack request failed: %s", exc)
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2
                continue
            return last_response

    return last_response


def get_korea_time_str() -> str:
    """Get current Korea time as formatted string."""
    from datetime import datetime

    import pytz

    korea_tz = pytz.timezone("Asia/Seoul")
    now = datetime.now(pytz.utc).astimezone(korea_tz)
    return now.strftime("%Y-%m-%d %H:%M:%S")


def notify(
    config: Config,
    message: str,
    bot_name: str = "로또 자동 구매 봇",
) -> None:
    """Send a Slack notification with timestamp.

    Args:
        config: Configuration with Slack credentials.
        message: Message to send.
        bot_name: Name to display in the notification.
    """
    korea_time_str = get_korea_time_str()
    payload = {
        "text": f"> {korea_time_str} *{bot_name} 알림* \n{message}",
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


def notify_low_balance(config: Config) -> None:
    """Send low balance notification with action button.

    Displays a button linking to the deposit page.

    Args:
        config: Configuration with Slack credentials.
    """
    korea_time_str = get_korea_time_str()
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
                        "url": f"{BASE_URL}/payment.do?method=payment",
                        "action_id": "button_action",
                    }
                ],
            },
        ],
    }
    post_to_slack(payload, config.slack_token, logger=LOG)
