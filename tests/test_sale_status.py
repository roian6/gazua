from unittest.mock import patch

from conftest import make_mock_datetime


class TestGetSaleStatus:
    def test_available_on_weekday(self):
        from lotto_utils import get_sale_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(0, 12)):
            available, status, _ = get_sale_status()
            assert available is True
            assert status == "available"

    def test_available_saturday_morning(self):
        from lotto_utils import get_sale_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(5, 10)):
            available, status, _ = get_sale_status()
            assert available is True
            assert status == "available"

    def test_drawing_saturday_evening(self):
        from lotto_utils import get_sale_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(5, 20, 30)):
            available, status, _ = get_sale_status()
            assert available is False
            assert status == "drawing"

    def test_closed_saturday_night(self):
        from lotto_utils import get_sale_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(5, 21)):
            available, status, _ = get_sale_status()
            assert available is False
            assert status == "closed"

    def test_closed_sunday_early_morning(self):
        from lotto_utils import get_sale_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(6, 5)):
            available, status, _ = get_sale_status()
            assert available is False
            assert status == "closed"

    def test_available_sunday_after_6am(self):
        from lotto_utils import get_sale_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(6, 6)):
            available, status, _ = get_sale_status()
            assert available is True
            assert status == "available"


class TestGetResultCheckStatus:
    def test_available_on_weekday(self):
        from lotto_utils import get_result_check_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(0, 12)):
            available, status, _ = get_result_check_status()
            assert available is True
            assert status == "available"

    def test_before_draw_saturday_afternoon(self):
        from lotto_utils import get_result_check_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(5, 18)):
            available, status, _ = get_result_check_status()
            assert available is False
            assert status == "before_draw"

    def test_processing_saturday_after_draw(self):
        from lotto_utils import get_result_check_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(5, 21, 30)):
            available, status, _ = get_result_check_status()
            assert available is False
            assert status == "processing"

    def test_available_saturday_late_night(self):
        from lotto_utils import get_result_check_status
        with patch("lotto_utils.get_now", return_value=make_mock_datetime(5, 22)):
            available, status, _ = get_result_check_status()
            assert available is True
            assert status == "available"
