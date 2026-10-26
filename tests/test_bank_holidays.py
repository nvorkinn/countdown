"""gov.uk's bank holidays through the store: the provider writing `v1:notices:bank-holidays` and the board reading it.

gov.uk is mocked (responses) with tests/fixtures/bank_holidays.json, an answer in its own shape, and the provider and
the board share one fake Redis (fakeredis), as they share the host's.
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import fakeredis
import pytest
import requests
import responses
from test_utils import TEST_DEVICE_ID

from countdown_providers.bank_holidays.main import BANK_HOLIDAYS_URL, UK, BankHolidaysProvider
from countdown_providers.runner import ProviderRunner
from countdown_renderer import moment
from countdown_renderer.notices.calendar import NoStoredBankHolidaysError, StoredBankHolidaySource, bank_holiday_notices
from countdown_renderer.notices.notice import Notice, Severity
from countdown_renderer.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_shared import i18n
from countdown_shared.store import keys, reader
from countdown_shared.store.bank_holidays import DIVISIONS, BankHoliday, StoredBankHolidays

GOV_UK = json.loads((Path(__file__).parent / "fixtures" / "bank_holidays.json").read_text(encoding="utf-8"))
FETCHED_AT = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)


def make_provider() -> BankHolidaysProvider:
    return BankHolidaysProvider(requests.Session(), now=lambda: FETCHED_AT)


@pytest.fixture
def server(monkeypatch):
    """Return the fake Redis the provider writes to and the renderer reads (reader.store), as on the host."""
    server = fakeredis.FakeServer()
    renderer_store = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(reader, "store", lambda: renderer_store)
    return server


def provider_redis(server):
    return fakeredis.FakeAsyncRedis(server=server, decode_responses=True)


def provide(server, *, status: int = 200, answer: dict = GOV_UK) -> int:
    """Run one of the provider's cycles, gov.uk answering `answer` (or failing with `status`); return keys written."""
    with responses.RequestsMock() as gov_uk:
        gov_uk.get(BANK_HOLIDAYS_URL, status=status, json=answer)
        return asyncio.run(ProviderRunner(make_provider(), provider_redis(server)).run_cycle())


def stored(server) -> StoredBankHolidays | None:
    return reader.read(fakeredis.FakeRedis(server=server), keys.bank_holidays(), StoredBankHolidays)


# --- The provider -----------------------------------------------------------------------------------------------------


def test_the_key_is_one_for_all_of_the_uk():
    assert keys.bank_holidays() == "v1:notices:bank-holidays"
    assert BankHolidaysProvider(requests.Session()).output_key(UK) == "v1:notices:bank-holidays"


def test_the_provider_stores_gov_uks_answer_with_when_it_fetched_it():
    with responses.RequestsMock() as gov_uk:
        gov_uk.get(BANK_HOLIDAYS_URL, json=GOV_UK)
        results = asyncio.run(make_provider().fetch([UK]))

    assert list(results) == [UK]
    holidays = results[UK]
    assert holidays.fetched_at == FETCHED_AT
    assert list(holidays.divisions) == list(DIVISIONS)
    for division in DIVISIONS:
        assert [e.model_dump(mode="json") for e in holidays.divisions[division].events] == [
            {key: event[key] for key in ("title", "date", "notes")} for event in GOV_UK[division]["events"]
        ]


def test_the_provider_fetches_only_its_one_id():
    with responses.RequestsMock():  # nothing registered: a call to gov.uk would fail
        assert asyncio.run(make_provider().fetch(["something-else"])) == {}


def test_the_runner_writes_it_with_nothing_subscribed_and_keeps_it_for_days(server):
    provider = make_provider()

    assert provide(server) == 1

    redis = fakeredis.FakeRedis(server=server)
    assert redis.keys(f"{keys.PREFIX}:sub:*") == []  # nothing asked for it
    assert stored(server).fetched_at == FETCHED_AT
    assert timedelta(days=2) < timedelta(seconds=redis.ttl(keys.bank_holidays())) <= timedelta(seconds=provider.ttl_s)
    # A few times a day, and kept through many failed fetches in a row.
    assert 2 <= timedelta(days=1) / timedelta(seconds=provider.interval_s) <= 6
    assert provider.ttl_s >= 10 * provider.interval_s


def test_a_gov_uk_outage_keeps_the_stored_holidays(server, caplog):
    provide(server)
    before = fakeredis.FakeRedis(server=server).get(keys.bank_holidays())

    assert provide(server, status=503) == 0

    assert fakeredis.FakeRedis(server=server).get(keys.bank_holidays()) == before
    assert "bank-holidays: couldn't fetch 1 ids" in caplog.text


def test_an_answer_missing_a_division_is_not_stored(server):
    provide(server)
    before = fakeredis.FakeRedis(server=server).get(keys.bank_holidays())

    assert provide(server, answer={"england-and-wales": GOV_UK["england-and-wales"]}) == 0

    assert fakeredis.FakeRedis(server=server).get(keys.bank_holidays()) == before


# --- The board's source ---------------------------------------------


def ending(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


# The notices expected, recorded from the board's own gov.uk source (hence OLD_ANSWERS): (now, postcodes.io's
# country) -> (text, valid_until), or None for no notice. A holiday ends at midnight after it, UK time.
OLD_ANSWERS = [
    (datetime(2026, 11, 24, 12, tzinfo=UTC), None, None),  # Christmas is over a month away
    (datetime(2026, 11, 26, 12, tzinfo=UTC), None, ("Fri 25 Dec: Christmas Day", ending(2026, 12, 26))),
    (datetime(2026, 11, 26, 12, tzinfo=UTC), "Scotland", ("Mon 30 Nov: St Andrew’s Day", ending(2026, 12, 1))),  # noqa: RUF001 -- gov.uk's apostrophe
    (datetime(2026, 12, 24, 12, tzinfo=UTC), "Wales", ("Tomorrow: Christmas Day", ending(2026, 12, 26))),
    (datetime(2026, 12, 28, 9, tzinfo=UTC), None, ("Today: Boxing Day (substitute day)", ending(2026, 12, 29))),
    (datetime(2026, 12, 31, 12, tzinfo=UTC), "Scotland", ("Tomorrow: New Year’s Day", ending(2027, 1, 2))),  # noqa: RUF001 -- gov.uk's apostrophe
    (datetime(2027, 1, 2, 12, tzinfo=UTC), "Scotland", ("Mon 4 Jan: 2nd January (substitute day)", ending(2027, 1, 5))),
    (datetime(2027, 1, 2, 12, tzinfo=UTC), None, None),  # the fixture's England and Wales stop at New Year's Day
    (
        datetime(2026, 6, 20, 12, tzinfo=UTC),
        "Northern Ireland",
        ("Mon 13 Jul: Battle of the Boyne (Orangemen’s Day) (substitute day)", ending(2026, 7, 13, 23)),  # noqa: RUF001 -- gov.uk's apostrophe
    ),
    (datetime(2026, 6, 20, 12, tzinfo=UTC), "England", None),  # not Northern Ireland's
    # 00:30 BST is already the 31st in the UK.
    (datetime(2026, 8, 30, 23, 30, tzinfo=UTC), None, ("Today: Summer bank holiday", ending(2026, 8, 31, 23))),
]


@pytest.mark.parametrize(("now", "country", "expected"), OLD_ANSWERS)
def test_the_stored_holidays_give_the_notices_gov_uk_gave(server, now, country, expected):
    provide(server)

    notices = StoredBankHolidaySource(country).fetch(now)

    assert notices == ([Notice("Bank holiday", Severity.REMINDER, *expected)] if expected else [])


# "Less than a month away" is up to the same day next month, which a shorter month clamps to its last day.
@pytest.mark.parametrize(
    ("today", "holiday", "mentioned"),
    [
        (date(2026, 10, 18), date(2026, 11, 17), True),
        (date(2026, 10, 18), date(2026, 11, 18), False),
        (date(2026, 12, 18), date(2027, 1, 17), True),  # across the new year
        (date(2026, 12, 18), date(2027, 1, 18), False),
        (date(2027, 1, 31), date(2027, 2, 27), True),  # February's last day is the 28th
        (date(2027, 1, 31), date(2027, 2, 28), False),
        (date(2028, 1, 31), date(2028, 2, 28), True),  # a leap year's is the 29th
        (date(2028, 1, 31), date(2028, 2, 29), False),
        (date(2026, 10, 31), date(2026, 11, 29), True),  # and November's the 30th
        (date(2026, 10, 31), date(2026, 11, 30), False),
    ],
)
def test_a_bank_holiday_is_mentioned_until_the_same_day_next_month(today, holiday, mentioned):
    now = datetime.combine(today, datetime.min.time().replace(hour=12), tzinfo=UTC)

    notices = bank_holiday_notices([BankHoliday(title="Test Day", date=holiday)], now)

    assert bool(notices) is mentioned


def test_tomorrow_becomes_today_at_midnight_from_the_same_stored_holidays(server):
    provide(server)
    source = StoredBankHolidaySource()

    [before] = source.fetch(datetime(2026, 12, 24, 23, 55, tzinfo=UTC))
    [after] = source.fetch(datetime(2026, 12, 25, 0, 5, tzinfo=UTC))

    # The board reads every source on every poll, so that's on time; reading the store is all it does.
    assert (before.text, after.text) == ("Tomorrow: Christmas Day", "Today: Christmas Day")


@pytest.fixture
def norwegian():
    i18n.set_locale("nb", "Europe/London")
    yield
    i18n.set_locale("en", "Europe/London")


def test_the_notice_is_worded_in_the_screens_language_when_it_is_drawn(server, norwegian):
    provide(server)
    source = StoredBankHolidaySource()

    assert source.fetch(datetime(2026, 12, 24, 12, tzinfo=UTC)) == [
        Notice("Offentlig fridag", Severity.REMINDER, "I morgen: Christmas Day", ending(2026, 12, 26))
    ]
    i18n.set_locale("en", "Europe/London")
    assert source.fetch(datetime(2026, 12, 24, 12, tzinfo=UTC))[0].text == "Tomorrow: Christmas Day"


def test_no_stored_holidays_is_a_no_stored_data_error_so_the_board_leaves_them_out(server):
    with pytest.raises(NoStoredBankHolidaysError, match="provider-bank-holidays") as raised:
        StoredBankHolidaySource().fetch(datetime(2026, 12, 24, 12, tzinfo=UTC))

    assert isinstance(raised.value, reader.NoStoredDataError)


def test_stored_holidays_without_the_division_are_a_lookup_error(server):
    holidays = StoredBankHolidays(fetched_at=FETCHED_AT, divisions={"scotland": GOV_UK["scotland"]})
    fakeredis.FakeRedis(server=server).set(keys.bank_holidays(), holidays.model_dump_json())

    with pytest.raises(NoStoredBankHolidaysError, match="england-and-wales"):
        StoredBankHolidaySource().fetch(datetime(2026, 12, 24, 12, tzinfo=UTC))
    assert StoredBankHolidaySource("Scotland").fetch(datetime(2026, 12, 24, 12, tzinfo=UTC))


def test_without_a_store_the_board_is_still_built_and_only_this_source_fails(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)
    client = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings(postcode=None))
    [source] = [s for s in client.sources if isinstance(s, StoredBankHolidaySource)]

    with pytest.raises(reader.StoreNotConfiguredError):
        source.fetch(datetime(2026, 12, 24, 12, tzinfo=UTC))


def test_the_board_drops_the_bank_holiday_once_its_key_has_expired(server, monkeypatch):
    now = datetime(2026, 12, 24, 12, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    # The moment the board draws at (core.moment), as a render sets it.
    monkeypatch.setattr(moment, "now", lambda: FrozenDatetime.now(UTC))
    provide(server)
    client = NoticeBoardClient(TEST_DEVICE_ID, NoticeBoardSettings(postcode=None))
    [notice] = asyncio.run(client.update()).notices
    assert notice.text == "Tomorrow: Christmas Day"

    fakeredis.FakeRedis(server=server).delete(keys.bank_holidays())  # the provider's been down for a week
    now += timedelta(minutes=1)

    # Its only source with data to read has none, so "All clear" would be a guess.
    assert asyncio.run(client.update()).message == "Not available"
