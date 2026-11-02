"""The parcel notices: one for each state a stored parcel is in, from what the parcel provider stored.

The store contract's parcels.json (countdown_api.fixtures) is the stored value, which the broker's tests read too.
"""

import asyncio
import datetime as dt
import json

import pytest
from config_factory import make_config
from test_utils import TEST_DEVICE_ID

from countdown_api import fixtures
from countdown_renderer import moment
from countdown_renderer.api_registry import section_for
from countdown_renderer.notices.notice import Notice, Severity
from countdown_renderer.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_renderer.notices.notice_board_panel import NoticeBoardPanel
from countdown_renderer.notices.parcels import StoredParcelsSource
from countdown_renderer.system_screens.message_panel import MessagePanel
from countdown_shared import i18n
from countdown_shared.store import keys, parcels

FIXTURE = fixtures.load("parcels.json")
KEY = keys.parcels(FIXTURE["device_id"])
# The fixture's day, at midday in London.
NOW = dt.datetime(2026, 10, 17, 11, 0, tzinfo=dt.UTC)


def _parcel(description, status, expected=None):
    return {
        "description": description,
        "carrier": "DHL",
        "tracking_number": "JD0000000000000009",
        "status": status,
        "expected_date": expected,
        "updated_at": "2026-10-17T08:00:00+00:00",
    }


def _put(store, *parcels_, device_id=TEST_DEVICE_ID):
    """Store `parcels_` as the parcel provider would, with the fixture's `fetched_at`."""
    value = {"fetched_at": FIXTURE["value"]["fetched_at"], "parcels": list(parcels_)}
    store.set(keys.parcels(device_id), json.dumps(value))


@pytest.fixture
def source() -> StoredParcelsSource:
    return StoredParcelsSource(TEST_DEVICE_ID)


@pytest.fixture
def locale():
    """Set the screen's language and time zone, and put them back."""

    def set_locale(language, timezone):
        i18n.set_locale(language, timezone)

    yield set_locale
    i18n.set_locale("en", "Europe/London")


def _texts(notices):
    return [(n.source, n.severity, n.text) for n in notices]


def test_the_contracts_example_shows_every_parcel(source, store):
    store.set(KEY.replace(FIXTURE["device_id"], TEST_DEVICE_ID), json.dumps(FIXTURE["value"]))

    assert _texts(source.fetch(NOW)) == [
        ("Parcels", Severity.INFO, "Out for delivery: Running shoes"),
        ("Parcels", Severity.INFO, "Parcel needs attention: Phone case"),
        ("Parcels", Severity.REMINDER, "Parcel arriving Mon 19 Oct: Desk lamp"),
        ("Parcels", Severity.REMINDER, "Parcel on its way: Gift"),
        ("Parcels", Severity.REMINDER, "Delivered: Book"),
    ]


def test_parcels_are_named_by_the_day_they_are_expected(source, store):
    _put(
        store,
        _parcel("Lamp", "in_transit", "2026-10-17"),
        _parcel("Book", "in_transit", "2026-10-18"),
        _parcel("Shoes", "ordered", "2026-10-20"),
    )

    assert _texts(source.fetch(NOW)) == [
        ("Parcels", Severity.REMINDER, "Parcel arriving today: Lamp"),
        ("Parcels", Severity.REMINDER, "Parcel arriving tomorrow: Book"),
        ("Parcels", Severity.REMINDER, "Parcel arriving Tue 20 Oct: Shoes"),
    ]


def test_a_parcel_expected_before_today_is_late(source, store):
    _put(store, _parcel("Lamp", "in_transit", "2026-10-16"))

    assert _texts(source.fetch(NOW)) == [("Parcels", Severity.REMINDER, "Parcel late: Lamp")]


def test_a_parcel_with_no_date_is_on_its_way(source, store):
    _put(store, _parcel("Gift", "ordered"))

    assert _texts(source.fetch(NOW)) == [("Parcels", Severity.REMINDER, "Parcel on its way: Gift")]


@pytest.mark.parametrize(
    ("status", "severity", "text"),
    [
        ("delivered", Severity.REMINDER, "Delivered: Book"),
        ("exception", Severity.INFO, "Parcel needs attention: Book"),
    ],
)
def test_a_delivered_parcel_or_an_exception_has_its_own_notice_whatever_its_day(source, store, status, severity, text):
    _put(store, _parcel("Book", status, "2026-10-17"))

    assert _texts(source.fetch(NOW)) == [("Parcels", severity, text)]


def test_a_parcel_out_for_delivery_is_not_also_arriving_today(source, store):
    _put(store, _parcel("Shoes", "out_for_delivery", "2026-10-17"))

    assert [n.text for n in source.fetch(NOW)] == ["Out for delivery: Shoes"]


def test_several_parcels_in_a_state_are_one_notice_with_their_count(source, store):
    _put(
        store,
        _parcel("Shoes", "out_for_delivery", "2026-10-17"),
        _parcel("Lamp", "out_for_delivery", "2026-10-17"),
        _parcel("Case", "exception"),
        _parcel("Bag", "exception"),
        _parcel("Old", "in_transit", "2026-10-15"),
        _parcel("Older", "in_transit", "2026-10-14"),
        _parcel("Case", "in_transit", "2026-10-17"),
        _parcel("Gift", "ordered", "2026-10-17"),
        _parcel("Cable", "unknown", "2026-10-17"),
        _parcel("Pen", "in_transit", "2026-10-18"),
        _parcel("Ink", "in_transit", "2026-10-18"),
        _parcel("Mug", "in_transit", "2026-10-21"),
        _parcel("Cup", "in_transit", "2026-10-21"),
        _parcel("Hat", "in_transit"),
        _parcel("Sock", "ordered"),
        _parcel("Book", "delivered"),
        _parcel("Map", "delivered"),
    )

    assert _texts(source.fetch(NOW)) == [
        ("Parcels", Severity.INFO, "2 parcels out for delivery"),
        ("Parcels", Severity.INFO, "2 parcels need attention"),
        ("Parcels", Severity.REMINDER, "2 parcels late"),
        ("Parcels", Severity.REMINDER, "3 parcels arriving today"),
        ("Parcels", Severity.REMINDER, "2 parcels arriving tomorrow"),
        ("Parcels", Severity.REMINDER, "2 parcels arriving Wed 21 Oct"),
        ("Parcels", Severity.REMINDER, "2 parcels on their way"),
        ("Parcels", Severity.REMINDER, "2 parcels delivered"),
    ]


def test_an_empty_list_is_no_notice(source, store):
    _put(store)

    assert source.fetch(NOW) == []


def test_today_is_the_screens_day_not_utc(source, store, locale):
    _put(store, _parcel("Lamp", "in_transit", "2026-12-18"))
    late = dt.datetime(2026, 12, 17, 23, 30, tzinfo=dt.UTC)  # 00:30 on the 18th in Oslo, still the 17th in London

    assert _texts(source.fetch(late)) == [("Parcels", Severity.REMINDER, "Parcel arriving tomorrow: Lamp")]
    locale("nb", "Europe/Oslo")
    assert _texts(source.fetch(late)) == [("Pakker", Severity.REMINDER, "Pakke kommer i dag: Lamp")]


def test_the_notices_are_in_the_screens_language(source, store, locale):
    _put(
        store,
        _parcel("Shoes", "out_for_delivery"),
        _parcel("Lamp", "in_transit", "2026-10-17"),
        _parcel("Case", "in_transit", "2026-10-17"),
        _parcel("Pen", "in_transit", "2026-10-18"),
        _parcel("Mug", "in_transit", "2026-10-20"),
        _parcel("Old", "in_transit", "2026-10-16"),
        _parcel("Hat", "in_transit"),
        _parcel("Bag", "exception"),
        _parcel("Book", "delivered"),
    )
    locale("nb", "Europe/London")

    assert _texts(source.fetch(NOW)) == [
        ("Pakker", Severity.INFO, "Ute for levering: Shoes"),
        ("Pakker", Severity.INFO, "Pakke trenger oppfølging: Bag"),
        ("Pakker", Severity.REMINDER, "Pakke forsinket: Old"),
        ("Pakker", Severity.REMINDER, "2 pakker kommer i dag"),
        ("Pakker", Severity.REMINDER, "Pakke kommer i morgen: Pen"),
        ("Pakker", Severity.REMINDER, "Pakke kommer tir. 20 okt.: Mug"),
        ("Pakker", Severity.REMINDER, "Pakke på vei: Hat"),
        ("Pakker", Severity.REMINDER, "Levert: Book"),
    ]


@pytest.mark.parametrize("expected", ["2026-10-17", "2026-10-18", "2026-10-20"])
def test_a_notice_naming_a_day_ends_at_the_screens_midnight(source, store, locale, expected):
    _put(store, _parcel("Lamp", "in_transit", expected))

    (notice,) = source.fetch(NOW)
    assert notice.valid_until == dt.datetime(2026, 10, 18, 0, 0, tzinfo=i18n.local_zone())
    locale("nb", "Europe/Oslo")
    (notice,) = source.fetch(NOW)
    assert notice.valid_until == dt.datetime(2026, 10, 17, 22, 0, tzinfo=dt.UTC)


def test_the_source_raises_while_nothing_is_stored_so_the_board_leaves_it_out(source):
    with pytest.raises(parcels.NoStoredParcelsError):
        source.fetch(NOW)


def test_the_source_reads_its_own_screens_key(source, store):
    _put(store, _parcel("Shoes", "out_for_delivery"), device_id="someone-else")

    with pytest.raises(parcels.NoStoredParcelsError):
        source.fetch(NOW)


# --- on the board --------------------------------------------------------------------------------------------------


def _board(**config) -> NoticeBoardClient:
    return NoticeBoardClient(TEST_DEVICE_ID, section_for("notice_board", make_config(**config)))


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(moment, "now", lambda: NOW)


@pytest.mark.parametrize(
    ("overrides", "enabled"),
    [({}, False), ({"parcels": {"enabled": False}}, False), ({"parcels": {"enabled": True}}, True)],
)
def test_the_board_reads_parcels_only_when_the_config_switches_them_on(overrides, enabled):
    settings = section_for("notice_board", make_config(**overrides))

    assert isinstance(settings, NoticeBoardSettings)
    assert settings.parcels is enabled
    board = NoticeBoardClient(TEST_DEVICE_ID, settings)
    assert any(isinstance(s, StoredParcelsSource) for s in board.sources) is enabled


def test_the_board_shows_the_parcel_notices(store):
    _put(store, _parcel("Shoes", "out_for_delivery", "2026-10-17"))
    board = _board(parcels={"enabled": True})

    panel = asyncio.run(board.update())

    assert isinstance(panel, NoticeBoardPanel)
    assert Notice("Parcels", Severity.INFO, "Out for delivery: Shoes") in panel.notices


def test_the_board_shows_no_parcel_notices_when_they_are_off(store):
    _put(store, _parcel("Shoes", "out_for_delivery", "2026-10-17"))
    board = _board()

    panel = asyncio.run(board.update())

    # Nothing else is stored either, so the board has no data: the parcels were not read.
    assert isinstance(panel, MessagePanel)
    assert panel.message == "Not available"


def test_the_board_is_not_available_rather_than_all_clear_once_the_parcels_key_has_lapsed():
    board = _board(parcels={"enabled": True})

    panel = asyncio.run(board.update())

    assert isinstance(panel, MessagePanel)
    assert panel.message == "Not available"
    assert board.expired
