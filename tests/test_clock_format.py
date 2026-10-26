from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from countdown_renderer.utils.clock import format_clock


@pytest.mark.parametrize(
    ("value", "hour_24", "hour_12"),
    [
        (time(0, 0), "00:00", "12:00 AM"),
        (time(0, 7, 59), "00:07", "12:07 AM"),
        (time(1, 5), "01:05", "1:05 AM"),
        (time(11, 59), "11:59", "11:59 AM"),
        (time(12, 0), "12:00", "12:00 PM"),
        (time(12, 7), "12:07", "12:07 PM"),
        (time(13, 5), "13:05", "1:05 PM"),
        (time(23, 59), "23:59", "11:59 PM"),
    ],
)
def test_clock_formats_have_unambiguous_midnight_noon_and_minutes(value, hour_24, hour_12):
    assert format_clock(value) == format_clock(value, "24h") == hour_24
    assert format_clock(value, "12h") == hour_12


@pytest.mark.parametrize(
    "zone",
    [ZoneInfo("Europe/London"), ZoneInfo("America/New_York"), timezone(timedelta(hours=5, minutes=30))],
)
@pytest.mark.parametrize("fold", [0, 1])
def test_formatting_preserves_the_values_wall_clock_offset_fold_and_machine_timestamp(zone, fold):
    value = datetime(2026, 10, 25, 1, 15, 32, tzinfo=zone, fold=fold)
    machine_timestamp, offset, instant = value.isoformat(), value.utcoffset(), value.timestamp()

    assert format_clock(value, "12h") == "1:15 AM"
    assert format_clock(value, "24h") == "01:15"
    assert value.isoformat() == machine_timestamp
    assert value.utcoffset() == offset
    assert value.timestamp() == instant
    assert value.fold == fold


def test_naive_datetime_is_already_a_local_wall_clock_not_the_renderers_time_zone():
    assert format_clock(datetime(2026, 1, 15, 18, 4), "12h") == "6:04 PM"  # noqa: DTZ001 -- naive on purpose


def test_unsupported_clock_format_is_an_explicit_error():
    with pytest.raises(ValueError, match="Unknown clock format"):
        format_clock(time(12), "invalid")  # type: ignore[arg-type]
