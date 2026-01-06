from lotto_utils import (
    as_int,
    entry_matches_date,
    extract_numbers_from_entry,
    extract_numbers_from_text,
)


def test_as_int_handles_invalid():
    assert as_int("10") == 10
    assert as_int("abc") == 0
    assert as_int(None) == 0


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
