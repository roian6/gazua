from unittest.mock import patch, MagicMock

from slack_notify import post_to_slack


class TestPostToSlack:
    def test_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch("requests.post", return_value=mock_response) as mock_post:
            response = post_to_slack({"text": "test"}, "xoxb-fake")

            assert response.status_code == 200
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args.kwargs["headers"]["Authorization"] == "Bearer xoxb-fake"

    def test_rate_limit_retry(self):
        rate_limit_response = MagicMock()
        rate_limit_response.status_code = 429
        rate_limit_response.headers = {"Retry-After": "1"}

        success_response = MagicMock()
        success_response.status_code = 200

        with patch("requests.post", side_effect=[rate_limit_response, success_response]):
            with patch("time.sleep"):
                response = post_to_slack({"text": "test"}, "xoxb-fake")
                assert response.status_code == 200

    def test_server_error_retry(self):
        error_response = MagicMock()
        error_response.status_code = 500

        success_response = MagicMock()
        success_response.status_code = 200

        with patch("requests.post", side_effect=[error_response, success_response]):
            with patch("time.sleep"):
                response = post_to_slack({"text": "test"}, "xoxb-fake")
                assert response.status_code == 200

    def test_max_retries_exhausted(self):
        error_response = MagicMock()
        error_response.status_code = 500

        with patch("requests.post", return_value=error_response):
            with patch("time.sleep"):
                response = post_to_slack({"text": "test"}, "xoxb-fake", max_retries=3)
                assert response.status_code == 500
