from requests import Session

from lotto_utils import (
    apply_proxy_to_session,
    as_int,
    entry_matches_date,
    extract_numbers_from_entry,
    extract_numbers_from_text,
)


def test_as_int_handles_invalid():
    assert as_int("10") == 10
    assert as_int("abc") == 0
    assert as_int(None) == 0


def test_apply_proxy_to_session_sets_http_and_https_proxy_with_escaped_auth():
    session = apply_proxy_to_session(Session(), "100.111.241.61:3128", "user name", "p@ss/word")
    expected = "http://user%20name:p%40ss%2Fword@100.111.241.61:3128"
    assert session.proxies == {"http": expected, "https": expected}


def test_extract_numbers_from_text_single_group():
    text = "1 2 3 4 5 6"
    assert extract_numbers_from_text(text) == [["1", "2", "3", "4", "5", "6"]]


def test_extract_numbers_from_text_multiple_groups():
    text = "1 2 3 4 5 6 7 8 9 10 11 12"
    assert extract_numbers_from_text(text) == [
        ["1", "2", "3", "4", "5", "6"],
        ["7", "8", "9", "10", "11", "12"],
    ]


def test_extract_numbers_from_entry_nested():
    entry = {
        "lottoNum": "3 4 5 6 7 8",
        "nested": {"wnNo": "9 10 11 12 13 14"},
        "list": ["1 2 3 4 5 6"],
    }
    numbers = extract_numbers_from_entry(entry)
    assert ["3", "4", "5", "6", "7", "8"] in numbers
    assert ["9", "10", "11", "12", "13", "14"] in numbers
    assert ["1", "2", "3", "4", "5", "6"] in numbers


def test_entry_matches_date():
    entry = {"date": "2025-01-01", "other": "foo"}
    assert entry_matches_date(entry, "20250101") is True
    assert entry_matches_date(entry, "20250102") is False
