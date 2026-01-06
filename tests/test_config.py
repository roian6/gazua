from unittest.mock import patch

import pytest

from config import parse_bool, create_buy_parser, create_check_parser, load_config


class TestParseBool:
    def test_truthy_values(self):
        for val in ["1", "true", "True", "TRUE", "yes", "y", "on", "YES", "ON"]:
            assert parse_bool(val) is True

    def test_falsy_values(self):
        for val in ["0", "false", "False", "no", "n", "off", ""]:
            assert parse_bool(val) is False

    def test_none_returns_default(self):
        assert parse_bool(None) is False
        assert parse_bool(None, default=True) is True

    def test_whitespace_handling(self):
        assert parse_bool("  true  ") is True
        assert parse_bool("  1  ") is True


class TestLoadConfig:
    def test_missing_required_raises_system_exit(self, clean_env, monkeypatch):
        monkeypatch.setenv("LOTTO_COUNT", "")
        monkeypatch.setenv("PROXY_USER", "")
        monkeypatch.setenv("PROXY_PW", "")
        monkeypatch.setenv("PROXY_ADDRESS", "")
        with patch("sys.argv", ["buy_lotto.py"]):
            with patch("config.load_env_file"):
                with pytest.raises(SystemExit) as exc:
                    load_config(create_buy_parser(), require_count=True)
                assert "DHL_USER_ID" in str(exc.value)

    def test_loads_from_env(self, mock_env, monkeypatch):
        monkeypatch.delenv("LOTTO_COUNT", raising=False)
        monkeypatch.delenv("PROXY_USER", raising=False)
        monkeypatch.delenv("PROXY_PW", raising=False)
        monkeypatch.delenv("PROXY_ADDRESS", raising=False)
        with patch("sys.argv", ["buy_lotto.py"]):
            with patch("config.load_env_file"):
                cfg = load_config(create_buy_parser(), require_count=True)

        assert cfg.user_id == "test_user"
        assert cfg.user_pw == "test_pass"
        assert cfg.slack_token == "xoxb-test-token"
        assert cfg.slack_channel == "#test-channel"
        assert cfg.count == 1
        assert cfg.headless is True

    def test_cli_overrides_env(self, mock_env, monkeypatch):
        monkeypatch.setenv("LOTTO_COUNT", "3")
        with patch("sys.argv", ["buy_lotto.py", "--count", "5"]):
            with patch("config.load_env_file"):
                cfg = load_config(create_buy_parser(), require_count=True)
        assert cfg.count == 5

    def test_headless_flags(self, mock_env):
        with patch("sys.argv", ["buy_lotto.py", "--headed"]):
            with patch("config.load_env_file"):
                cfg = load_config(create_buy_parser(), require_count=True)
        assert cfg.headless is False

        with patch("sys.argv", ["buy_lotto.py", "--headless"]):
            with patch("config.load_env_file"):
                cfg = load_config(create_buy_parser(), require_count=True)
        assert cfg.headless is True

    def test_check_parser_no_count(self, mock_env):
        with patch("sys.argv", ["check_result.py"]):
            with patch("config.load_env_file"):
                cfg = load_config(create_check_parser(), require_count=False)
        assert cfg.user_id == "test_user"
        assert cfg.count == 1

    def test_invalid_count_raises_system_exit(self, mock_env, monkeypatch):
        monkeypatch.setenv("LOTTO_COUNT", "0")
        with patch("sys.argv", ["buy_lotto.py"]):
            with patch("config.load_env_file"):
                with pytest.raises(SystemExit) as exc:
                    load_config(create_buy_parser(), require_count=True)
                assert "positive" in str(exc.value).lower()
