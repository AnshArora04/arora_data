from datetime import date, datetime, timezone

import pytest

from arora_data.dates import EASTERN, today_et


@pytest.mark.parametrize(
    "utc, expected",
    [
        (datetime(2026, 7, 1, 3, 59), date(2026, 6, 30)),  # EDT, UTC-4
        (datetime(2026, 7, 1, 4, 0), date(2026, 7, 1)),
        (datetime(2026, 12, 1, 4, 59), date(2026, 11, 30)),  # EST, UTC-5
        (datetime(2026, 12, 1, 5, 0), date(2026, 12, 1)),
    ],
)
def test_midnight_is_eastern_across_daylight_saving(utc, expected):
    assert today_et(utc.replace(tzinfo=timezone.utc)) == expected


def test_default_is_now_in_eastern():
    assert today_et() == datetime.now(EASTERN).date()


def test_naive_now_rejected():
    with pytest.raises(ValueError):
        today_et(datetime(2026, 7, 1, 3, 59))
