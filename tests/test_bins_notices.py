"""The bin collection notice on a Norwegian board, read from the store (`v1:bins:<SHA1 of the address>`, fakeredis)."""

from datetime import UTC, date, datetime

import pytest
import requests
from test_utils import TEST_DEVICE_ID

from countdown_api.models import NoticeBoardAddress
from countdown_renderer.notices.bins import NoStoredBinCollectionsError, StoredBinCollectionSource, bin_notices
from countdown_renderer.notices.notice import Severity
from countdown_renderer.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_shared import i18n
from countdown_shared.store import keys
from countdown_shared.store.bins import BinCollection, StoredBinCollections, address_member
from countdown_shared.store.location import StoredLocation
from countdown_shared.tfl.models import Postcode

# Sunday 22 November 2026, at noon in Oslo.
NOW = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)
ADDRESS = "Thorvald Meyers gate|10|0555|Oslo"


def stored(*collections: tuple[str, list[date]], found: bool = True) -> StoredBinCollections:
    return StoredBinCollections(
        fetched_at=NOW,
        found=found,
        collections=[BinCollection(fraction=fraction, dates=dates) for fraction, dates in collections],
    )


OSLO = stored(
    ("paper", [date(2026, 11, 26), date(2026, 11, 27)]),
    ("residualWaste", [date(2026, 11, 23), date(2026, 11, 30)]),
)


@pytest.fixture
def oslo_screen():
    i18n.set_locale("en", "Europe/Oslo")
    yield
    i18n.set_locale("en", "Europe/London")


def test_the_next_collection_is_a_reminder_until_the_end_of_its_day(oslo_screen):
    [notice] = bin_notices(OSLO, NOW)
    assert (notice.source, notice.severity, notice.text) == ("Bins", Severity.REMINDER, "Tomorrow: Residual waste")
    assert notice.valid_until == datetime(2026, 11, 24, tzinfo=notice.valid_until.tzinfo)


def test_every_kind_of_waste_emptied_that_day_is_named_once(oslo_screen):
    same_day = stored(
        ("paper", [date(2026, 11, 22)]),
        ("residualWaste", [date(2026, 11, 22)]),
        ("residualWasteContainer", [date(2026, 11, 22)]),
        ("glassContainer", [date(2026, 11, 25)]),
    )
    [notice] = bin_notices(same_day, NOW)
    assert notice.text == "Today: Paper, Residual waste"


def test_a_kind_of_waste_the_messages_dont_know_is_other_waste(oslo_screen):
    [notice] = bin_notices(stored(("compost", [date(2026, 11, 23)])), NOW)
    assert notice.text == "Tomorrow: Other waste"


def test_it_is_worded_in_norwegian_on_a_norwegian_screen():
    i18n.set_locale("nb", "Europe/Oslo")
    try:
        [notice] = bin_notices(OSLO, NOW)
    finally:
        i18n.set_locale("en", "Europe/London")
    assert (notice.source, notice.text) == ("Avfall", "I morgen: Restavfall")


def test_past_days_are_skipped_and_a_week_ahead_is_too_far(oslo_screen):
    assert bin_notices(stored(("paper", [date(2026, 11, 21), date(2026, 11, 29)])), NOW) == []
    [notice] = bin_notices(stored(("paper", [date(2026, 11, 21), date(2026, 11, 28)])), NOW)
    assert notice.text.endswith(": Paper")


def test_an_address_binable_has_no_calendar_for_has_no_notice(oslo_screen):
    assert bin_notices(stored(found=False), NOW) == []


def test_the_source_reads_the_address_s_key(store, oslo_screen):
    source = StoredBinCollectionSource(requests.Session(), ADDRESS)
    with pytest.raises(NoStoredBinCollectionsError):
        source.fetch(NOW)
    store.set(keys.bins(ADDRESS), OSLO.model_dump_json())
    [notice] = source.fetch(NOW)
    assert notice.text == "Tomorrow: Residual waste"


def test_a_member_is_the_address_and_the_board_s_postcode():
    address = NoticeBoardAddress(street="Thorvald Meyers gate", house_number="10", city="Oslo")
    assert address_member(address, "0555") == ADDRESS


def test_a_norwegian_board_with_an_address_reads_its_bin_collections(store):
    place = Postcode(postcode="0555", latitude=59.92, longitude=10.76, country="Norway", admin_district="Oslo")
    store.set(
        keys.location("0555"),
        StoredLocation(fetched_at=NOW, postcode="0555", found=True, location=place).model_dump_json(),
    )
    client = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings("0555", "NO", address=ADDRESS))
    client._ensure_location(NOW)
    assert type(client.sources[-1]).__name__ == "StoredBinCollectionSource"
    assert client.sources[-1].address == ADDRESS
    without = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings("0555", "NO"))
    without._ensure_location(NOW)
    assert "StoredBinCollectionSource" not in [type(source).__name__ for source in without.sources]
