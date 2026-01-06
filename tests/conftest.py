import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

import pytest
import pytz


@pytest.fixture
def clean_env(monkeypatch):
    env_keys = [
        "DHL_USER_ID", "DHL_USER_PW", "SLACK_BOT_TOKEN", "SLACK_CHANNEL",
        "LOTTO_COUNT", "HEADLESS", "DEBUG", "DEBUG_ARTIFACTS", "DEBUG_DIR",
        "PROXY_USER", "PROXY_PW", "PROXY_ADDRESS",
    ]
    for key in env_keys:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("DHL_USER_ID", "test_user")
    monkeypatch.setenv("DHL_USER_PW", "test_pass")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")
    monkeypatch.setenv("SLACK_CHANNEL", "#test-channel")
    return monkeypatch


def make_mock_datetime(weekday: int, hour: int, minute: int = 0):
    korea_tz = pytz.timezone("Asia/Seoul")
    base_date = 6
    target_date = base_date + weekday
    return datetime(2025, 1, target_date, hour, minute, tzinfo=korea_tz)
