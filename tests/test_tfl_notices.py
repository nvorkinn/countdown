"""TfL's disruptions on the notice board, through the store: the TfL notices provider and the sources reading it.

fixtures/tfl_notices.json is a set of TfL's answers (and postcodes.io's, which provider-locations stores as where the
fixture's postcode is: the roads provider reads it there), and fixtures/tfl_notices_expected.json the notices made
from them at the fixture's `now` (recorded when the board asked TfL itself). The provider stores those answers, and
the board's sources must make the same notices from what it stored.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import fakeredis
import pytest
import requests
import responses
from location_factory import FakeLocations

from countdown_core import i18n
from countdown_core.notices.tfl_feed import is_near
from countdown_core.store import keys, reader
from countdown_core.store.tfl_notices import StoredLineStatus, StoredRoadDisruptions, StoredStationDisruptions
from countdown_core.tfl.models import PostcodeResponse
from countdown_core.tfl.tfl_client import TFL_URL, DisruptedPoints, Lines, RoadDisruptions, TflSource
from countdown_providers import runner as runner_module
from countdown_providers.locations.reader import LocationReader
from countdown_providers.runner import ProviderRunner
from countdown_providers.tfl_notices import main as provider_module
from countdown_providers.tfl_notices.main import (
    EVERY_ID,
    LINES_LOOKUP_EVERY,
    TflLineStatusProvider,
    TflRoadProvider,
    TflStationProvider,
)
from countdown_renderer.notices.notice import Notice, Severity
from countdown_renderer.notices.tfl import (
    NoStoredTflNoticesError,
    StoredTflLineStatusSource,
    StoredTflRoadSource,
    StoredTflStationSource,
    line_status_notices,
    road_notices,
    station_notices,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE = json.loads((FIXTURES / "tfl_notices.json").read_text())
# The notices made from FIXTURE's answers.
EXPECTED = json.loads((FIXTURES / "tfl_notices_expected.json").read_text())
NOW = datetime.fromisoformat(FIXTURE["now"])
STOPS = FIXTURE["stops"]
POSTCODE = FIXTURE["postcode"]
# Where it is, as provider-locations stores it.
PLACE = PostcodeResponse.model_validate(FIXTURE["postcodes_io"]).result
NORWEGIAN = "0150"


def as_json(notices: list[Notice]) -> list[dict]:
    """Return notices as the expected fixture has them."""
    return [
        {
            "source": n.source,
            "severity": n.severity.name,
            "text": n.text,
            "valid_until": n.valid_until.isoformat() if n.valid_until else None,
        }
        for n in notices
    ]


class FakeTfl:
    """TfL's API, answering with FIXTURE's answers, but for the paths a test makes fail or answer otherwise."""

    def __init__(self) -> None:
        self.failures: dict[str, int] = {}  # path -> HTTP status
        self.answers: dict[str, object] = {}  # path -> JSON

    def __call__(self, request):
        path = request.path_url.split("?")[0]
        if path in self.failures:
            return self.failures[path], {}, "{}"
        answer = self.answers[path] if path in self.answers else self._answer(path)
        if answer is None:
            return 404, {}, json.dumps({"message": "Not found"})
        return 200, {}, json.dumps(answer)

    @staticmethod
    def _answer(path: str) -> object:
        if match := re.fullmatch(r"/Line/([^/]+)/Status", path):
            # Each line asked for, in the order asked.
            by_id = {line["id"]: line for line in FIXTURE["line_status"]}
            return [by_id[i] for i in match[1].split(",") if i in by_id]
        if match := re.fullmatch(r"/StopPoint/([^/]+)/Disruption", path):
            # Each stop's, in the order asked.
            return [point for stop in match[1].split(",") for point in FIXTURE["disruptions"][stop]]
        if match := re.fullmatch(r"/StopPoint/([^/]+)", path):
            return FIXTURE["stop_points"].get(match[1])
        if path == "/Road/all/Disruption":
            return FIXTURE["roads"]
        return None


@pytest.fixture
def api():
    """Mock HTTP, with nothing answered yet (the `tfl` fixture adds TfL): any other request fails."""
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


@pytest.fixture
def tfl(api):
    fake = FakeTfl()
    api.add_callback(responses.GET, re.compile(rf"{re.escape(TFL_URL)}/.*"), callback=fake)
    return fake


def paths(api, prefix: str = "") -> list[str]:
    """Return the paths asked for (without TfL's host), in order, of those starting with `prefix`."""
    asked = [call.request.url.split("?")[0].removeprefix(TFL_URL) for call in api.calls]
    return [path for path in asked if path.startswith(prefix)]


@pytest.fixture
def located(store, monkeypatch):
    """Return a store with the fixture's postcode in it, as provider-locations places it, read by the renderer too."""
    server = fakeredis.FakeServer()
    monkeypatch.setattr(reader, "store", lambda: fakeredis.FakeRedis(server=server, decode_responses=True))
    return FakeLocations(PLACE, server=server)


@pytest.fixture
def redis(located):
    """Return the providers' (async) side of the store, whose (sync) other side the renderer reads."""
    return located.redis


def make_providers(
    located: FakeLocations | None = None,
) -> tuple[TflLineStatusProvider, TflStationProvider, TflRoadProvider]:
    """Make the three providers, the roads reading where the postcodes are from `located` (else just the fixture's)."""
    session = requests.Session()
    located = located or FakeLocations(PLACE)
    return (
        TflLineStatusProvider(TflSource(session), now=lambda: NOW),
        TflStationProvider(TflSource(session), now=lambda: NOW),
        TflRoadProvider(TflSource(session), located.reader, now=lambda: NOW),
    )


def run_cycles(redis, *cycle_providers) -> None:
    """Subscribe the fixture's stops and postcode (and a Norwegian one), then run a cycle of each provider."""

    async def cycles():
        await redis.sadd(keys.subscription(keys.TFL), *STOPS)
        await redis.sadd(keys.subscription(keys.POSTCODE), POSTCODE, NORWEGIAN)
        for provider in cycle_providers:
            await ProviderRunner(provider, redis).run_cycle()

    asyncio.run(cycles())


def stored_sources() -> tuple[StoredTflLineStatusSource, StoredTflStationSource, StoredTflRoadSource]:
    session = requests.Session()
    return (
        StoredTflLineStatusSource(session, STOPS),
        StoredTflStationSource(session, STOPS),
        StoredTflRoadSource(session, POSTCODE),
    )


# --- Parity ------------------------------------------------------------------------


def test_the_stored_answers_make_the_notices_the_renderer_made_from_tfl(api, tfl, located, redis):
    run_cycles(redis, *make_providers(located))
    lines, stations, roads = stored_sources()

    assert as_json(lines.fetch(NOW)) == EXPECTED["line_status"]
    assert as_json(stations.fetch(NOW)) == EXPECTED["stations"]
    assert as_json(roads.fetch(NOW)) == EXPECTED["roads"]


def test_the_notices_are_worked_out_when_read(api, tfl, located, redis):
    """Nothing about now is stored: the same values, read later, leave out what has ended since."""
    run_cycles(redis, *make_providers(located))
    lines, stations, roads = stored_sources()
    later = NOW + timedelta(hours=10)  # the severe delays, the bus diversion and the platform closure are over

    assert [n.text for n in lines.fetch(later)] == [EXPECTED["line_status"][1]["text"], "Issues Reported"]
    platform, *others = stations.fetch(later)
    # The platform closure's first listing is over, so its second, until tomorrow, is the one shown.
    assert (platform.text, platform.valid_until) == (EXPECTED["stations"][0]["text"], NOW + timedelta(days=1))
    assert as_json(others) == EXPECTED["stations"][1:]
    # The board drops the roads' notices once they're over; the source still reports them.
    assert len(roads.fetch(later)) == len(EXPECTED["roads"])


def test_the_tags_are_in_the_screens_language_when_read(api, tfl, located, redis):
    run_cycles(redis, *make_providers(located))
    lines, _, roads = stored_sources()
    i18n.set_locale("nb", "Europe/Oslo")
    try:
        assert [n.source for n in lines.fetch(NOW)] == ["Northern", "Northern", "155-bussen", "Busser"]
        assert {n.source for n in roads.fetch(NOW)} == {"Veier"}
    finally:
        i18n.set_locale("en", "Europe/London")


def test_what_is_stored_is_tfls_answers_under_tfls_names(api, tfl, located, redis):
    run_cycles(redis, *make_providers(located))
    stored = json.loads(asyncio.run(redis.get(keys.tfl_line_status("940GZZLUKNG"))))

    [northern] = stored["lines"]
    assert stored["fetched_at"] == FIXTURE["now"].replace("+00:00", "Z")
    assert set(northern) == {"id", "name", "modeName", "lineStatuses"}  # what the models read, less TfL's "$type"...
    assert northern["lineStatuses"][0]["statusSeverityDescription"] == "Severe Delays"
    assert northern["lineStatuses"][0]["validityPeriods"][0]["toDate"] == "2026-09-25T21:00:00Z"
    road = json.loads(asyncio.run(redis.get(keys.tfl_roads(POSTCODE))))["disruptions"][0]
    assert road["endDateTime"] == "2026-09-25T15:00:00Z"


# --- The provider ------------------------------------------------------------------


def test_line_status_asks_about_every_stops_lines_at_once(api, tfl, redis):
    lines, _, _ = make_providers()
    run_cycles(redis, lines)

    # The runner's batch is every stop, so one request for every line, each once (sorted stops: E, F, Kennington).
    assert paths(api, "/Line/") == ["/Line/133,155,333,415,northern/Status"]
    stored = {
        stop: StoredLineStatus.model_validate_json(asyncio.run(redis.get(keys.tfl_line_status(stop)))) for stop in STOPS
    }
    # Each stop has its own lines, in the order TfL lists them at the stop.
    assert {stop: [line.id for line in s.lines] for stop, s in stored.items()} == {
        "940GZZLUKNG": ["northern"],
        "490000077E": ["133", "155", "333"],
        "490000077F": ["133", "155", "415"],
    }
    assert ProviderRunner(lines, redis).batch_size == EVERY_ID


def test_line_status_looks_each_stops_lines_up_once_a_day(api, tfl, redis):
    lines, _, _ = make_providers()
    now = NOW
    lines.now = lambda: now
    run_cycles(redis, lines)
    run_cycles(redis, lines)
    assert len(paths(api, "/StopPoint/")) == 3
    assert len(paths(api, "/Line/")) == 2

    now += LINES_LOOKUP_EVERY
    run_cycles(redis, lines)
    assert len(paths(api, "/StopPoint/")) == 6


def test_line_status_keeps_a_stops_lines_while_tfl_cant_say_again(api, tfl, redis):
    lines, _, _ = make_providers()
    now = NOW
    lines.now = lambda: now
    run_cycles(redis, lines)
    tfl.failures["/StopPoint/940GZZLUKNG"] = 503

    now += LINES_LOOKUP_EVERY
    results = asyncio.run(lines.fetch(["940GZZLUKNG"]))

    assert [line.id for line in results["940GZZLUKNG"].lines] == ["northern"]


def test_line_status_leaves_out_a_stop_it_cant_look_up_and_keeps_the_others(api, tfl, caplog):
    tfl.failures["/StopPoint/490000077E"] = 500
    lines, _, _ = make_providers()

    results = asyncio.run(lines.fetch(STOPS))

    assert set(results) == {"940GZZLUKNG", "490000077F"}
    assert "Couldn't look up the lines at TfL stop 490000077E: HTTPError 500" in caplog.messages


def test_line_status_writes_a_stop_that_isnt_a_bus_stop_or_tube_station_with_no_lines(api, tfl):
    platform = dict(FIXTURE["stop_points"]["940GZZLUKNG"], naptanId="9400ZZLUKNG1", stopType="NaptanMetroPlatform")
    tfl.answers["/StopPoint/9400ZZLUKNG1"] = platform
    lines, _, _ = make_providers()

    results = asyncio.run(lines.fetch(["9400ZZLUKNG1"]))

    assert results["9400ZZLUKNG1"].lines == []
    assert paths(api, "/Line/") == []


def test_line_status_a_failed_request_leaves_out_only_the_stops_it_was_for(api, tfl, monkeypatch, caplog):
    monkeypatch.setattr(provider_module, "LINES_PER_REQUEST", 2)
    tfl.failures["/Line/155,333/Status"] = 500
    lines, _, _ = make_providers()

    results = asyncio.run(lines.fetch(STOPS))

    assert paths(api, "/Line/") == ["/Line/northern,133/Status", "/Line/155,333/Status", "/Line/415/Status"]
    # Both bus stops have the 155; their keys keep their last statuses until they expire.
    assert set(results) == {"940GZZLUKNG"}
    assert "Couldn't fetch the status of 2 TfL lines (155...): HTTPError 500" in caplog.messages


def test_line_status_a_line_tfl_leaves_out_has_nothing_to_report(api, tfl):
    tfl.answers["/Line/133,155,333/Status"] = [FIXTURE["line_status"][1]]
    lines, _, _ = make_providers()

    results = asyncio.run(lines.fetch(["490000077E"]))

    assert [line.id for line in results["490000077E"].lines] == ["133"]


def test_the_app_key_goes_with_every_request_to_tfl_and_stays_out_of_the_logs(api, tfl, located, redis, caplog):
    tfl.failures["/StopPoint/490000077E"] = 500
    session = requests.Session()
    run_cycles(
        redis,
        TflLineStatusProvider(TflSource(session, "secret"), now=lambda: NOW),
        TflStationProvider(TflSource(session, "secret"), now=lambda: NOW),
        TflRoadProvider(TflSource(session, "secret"), located.reader, now=lambda: NOW),
    )

    tfl_calls = [call for call in api.calls if call.request.url.startswith(TFL_URL)]
    assert tfl_calls
    assert all("app_key=secret" in call.request.url for call in tfl_calls)
    assert "secret" not in caplog.text


def test_stations_asks_about_each_stop(api, tfl, redis):
    _, stations, _ = make_providers()
    run_cycles(redis, stations)

    assert sorted(paths(api, "/StopPoint/")) == [f"/StopPoint/{stop}/Disruption" for stop in sorted(STOPS)]
    stored = StoredStationDisruptions.model_validate_json(asyncio.run(redis.get(keys.tfl_stations("490000077E"))))
    assert stored.disruptions == []  # nothing wrong is still written, so it isn't missing data


def test_stations_leaves_out_a_stop_it_cant_ask_about(api, tfl, caplog):
    tfl.failures["/StopPoint/490000077E/Disruption"] = 404
    _, stations, _ = make_providers()

    assert set(asyncio.run(stations.fetch(STOPS))) == {"940GZZLUKNG", "490000077F"}
    assert "Couldn't fetch the disruptions at TfL stop 490000077E: HTTPError 404" in caplog.messages


def test_roads_asks_tfl_once_for_every_place_and_keeps_whats_near_each(api, tfl, located, redis):
    located.put(PLACE.model_copy(update={"postcode": "E1 6AN", "latitude": 51.5196, "longitude": -0.0567}))
    _, _, roads = make_providers(located)

    asyncio.run(redis.sadd(keys.subscription(keys.POSTCODE), "E1 6AN"))
    run_cycles(redis, roads)

    assert paths(api, "/Road/") == ["/Road/all/Disruption"]
    assert api.calls[-1].request.params == {"severities": "Severe,Serious"}

    def stored_ids(postcode: str) -> list[str]:
        value = asyncio.run(redis.get(keys.tfl_roads(postcode)))
        return [d.id for d in StoredRoadDisruptions.model_validate_json(value).disruptions]

    # Kennington's collision and works (and the moderate works, which the renderer leaves out); Commercial Road's.
    assert stored_ids(POSTCODE) == ["TIMS-1", "TIMS-2", "TIMS-5"]
    assert stored_ids("E1 6AN") == ["TIMS-3"]
    assert asyncio.run(redis.exists(keys.tfl_roads(NORWEGIAN))) == 0


def test_roads_asks_only_tfl_and_never_about_a_norwegian_postcode(api, tfl):
    _, _, roads = make_providers()

    asyncio.run(roads.fetch([NORWEGIAN, POSTCODE]))
    results = asyncio.run(roads.fetch([NORWEGIAN, POSTCODE]))

    assert list(results) == [POSTCODE]
    # Where the postcode is came from the store, each time.
    assert [call.request.url.split("?")[0] for call in api.calls] == [f"{TFL_URL}/Road/all/Disruption"] * 2


def test_roads_leaves_out_a_postcode_that_doesnt_exist_and_never_logs_it(api, tfl, caplog):
    _, _, roads = make_providers(FakeLocations(not_found=("ZZ1 1ZZ",)))

    assert asyncio.run(roads.fetch(["ZZ1 1ZZ"])) == {}

    assert paths(api, "/Road/") == []  # nowhere to be near
    assert "ZZ1" not in caplog.text


def test_roads_leaves_out_a_postcode_not_placed_yet_and_keeps_the_others(api, tfl, caplog):
    _, _, roads = make_providers()

    with caplog.at_level("INFO"):
        assert list(asyncio.run(roads.fetch(["E1 6AN", POSTCODE]))) == [POSTCODE]
    assert caplog.messages == [
        (
            "No location in the store yet for 1 of 2 postcodes, so no TfL road notices for them this cycle "
            "(provider-locations writes them)"
        )
    ]


def test_roads_asks_tfl_nothing_while_no_postcode_is_placed(api, tfl):
    _, _, roads = make_providers(FakeLocations())

    assert asyncio.run(roads.fetch([POSTCODE])) == {}
    assert len(api.calls) == 0


def test_roads_logs_a_failure_without_the_postcodes(api, tfl, caplog):
    tfl.failures["/Road/all/Disruption"] = 503
    _, _, roads = make_providers()

    assert asyncio.run(roads.fetch([POSTCODE])) == {}
    assert caplog.messages == ["Couldn't fetch TfL's road disruptions: HTTPError 503"]


def test_each_kind_is_written_under_its_key_with_its_ttl(api, tfl, located, redis):
    lines, stations, roads = make_providers(located)
    run_cycles(redis, lines, stations, roads)

    for key, ttl_s in [
        (keys.tfl_line_status("940GZZLUKNG"), lines.ttl_s),
        (keys.tfl_stations("940GZZLUKNG"), stations.ttl_s),
        (keys.tfl_roads(POSTCODE), roads.ttl_s),
    ]:
        assert 0 < asyncio.run(redis.ttl(key)) <= ttl_s
    assert sorted(asyncio.run(redis.keys("v1:notices:*"))) == sorted(
        [keys.tfl_line_status(stop) for stop in STOPS]
        + [keys.tfl_stations(stop) for stop in STOPS]
        + [keys.tfl_roads(POSTCODE)]
    )


def test_each_provider_outlives_its_interval():
    for provider in make_providers():
        assert provider.ttl_s >= 3 * provider.interval_s


def test_the_process_runs_the_three_side_by_side_on_one_connection_which_the_roads_read_locations_on(monkeypatch):
    """Through main(), as the container starts it: one Redis client for the runners and the roads' locations."""
    ran = []

    class FakeRunner:
        def __init__(self, provider, redis, *, shard):
            self.provider = provider
            self.redis = redis

        async def run(self):
            ran.append((self.provider, self.redis))

        def stop(self):
            pass

    monkeypatch.setattr(runner_module, "ProviderRunner", FakeRunner)
    monkeypatch.setattr(provider_module, "configure_logging", lambda: None)
    monkeypatch.setenv("TFL_APP_KEY", "key")

    provider_module.main()

    assert [(provider.name, type(provider).__name__) for provider, _ in ran] == [
        ("tfl", "TflLineStatusProvider"),
        ("tfl", "TflStationProvider"),
        ("postcode", "TflRoadProvider"),
    ]
    [redis] = {id(redis): redis for _, redis in ran}.values()
    roads = ran[2][0]
    assert roads.locations.redis is redis


def test_main_serves_the_three_with_the_app_key_and_the_store_from_the_environment(monkeypatch):
    served = []

    async def fake_serve_all(serving, settings, *, redis):
        served.append((serving, settings, redis))

    monkeypatch.setattr(provider_module, "serve_all", fake_serve_all)
    monkeypatch.setattr(provider_module, "configure_logging", lambda: None)
    monkeypatch.setenv("TFL_APP_KEY", "key")
    monkeypatch.setenv("REDIS_URL", "redis://redis.example:6379/0")

    provider_module.main()

    [(serving, settings, redis)] = served
    assert [type(p) for p in serving] == [TflLineStatusProvider, TflStationProvider, TflRoadProvider]
    assert all(p.tfl.params == {"app_key": "key"} for p in serving)
    assert len({id(p.tfl.session) for p in serving}) == 3  # a session per thread
    assert settings.redis_url == "redis://redis.example:6379/0"
    # The roads read where the postcodes are on the runners' own client.
    assert isinstance(serving[2].locations, LocationReader)
    assert serving[2].locations.redis is redis
    assert redis.connection_pool.connection_kwargs["host"] == "redis.example"


# --- The renderer's sources --------------------------------------------------------


@pytest.mark.parametrize("index", [0, 1, 2], ids=["lines", "stations", "roads"])
def test_a_source_with_nothing_in_the_store_raises_a_no_stored_data_error(index):
    """So the board leaves it out."""
    source = stored_sources()[index]

    with pytest.raises(reader.NoStoredDataError):
        source.fetch(NOW)


def test_a_source_with_one_of_its_stops_missing_raises_rather_than_show_half(api, tfl, located, redis):
    run_cycles(redis, *make_providers(located))
    reader.store().delete(keys.tfl_line_status("490000077F"))
    lines, _, _ = stored_sources()

    with pytest.raises(NoStoredTflNoticesError, match="490000077F"):
        lines.fetch(NOW)


def test_a_source_without_a_store_says_so(monkeypatch):
    monkeypatch.setattr(reader, "store", lambda: None)
    _, _, roads = stored_sources()  # built without one, as the board builds it

    with pytest.raises(reader.StoreNotConfiguredError):
        roads.fetch(NOW)


def test_a_source_with_no_stops_has_nothing_to_say():
    assert StoredTflLineStatusSource(requests.Session(), []).fetch(NOW) == []


def test_a_line_serving_two_stops_is_one_notice(store):
    shared = {
        "id": "133",
        "name": "133",
        "modeName": "bus",
        "lineStatuses": [{"statusSeverity": 2, "statusSeverityDescription": "Suspended"}],
    }
    for stop in ("490000077E", "490000077F"):
        value = StoredLineStatus.model_validate({"fetched_at": NOW, "stop_id": stop, "lines": [shared]})
        store.set(keys.tfl_line_status(stop), value.model_dump_json())

    notices = StoredTflLineStatusSource(requests.Session(), ["490000077E", "490000077F"]).fetch(NOW)

    assert notices == [Notice("Bus 133", Severity.SEVERE, "Suspended")]


def test_the_road_source_reads_its_postcodes_place_however_its_written(store):
    value = StoredRoadDisruptions(fetched_at=NOW, postcode=POSTCODE, disruptions=[])
    store.set(keys.tfl_roads("se17 3ll"), value.model_dump_json())

    assert StoredTflRoadSource(requests.Session(), POSTCODE).fetch(NOW) == []


# --- Making the notices ------------------------------------------------------------


def _status(
    severity: int, description: str, periods: Sequence[tuple[datetime, datetime]] = (), reason: str | None = None
) -> dict:
    return {
        "statusSeverity": severity,
        "statusSeverityDescription": description,
        "reason": reason,
        "validityPeriods": [{"fromDate": f.isoformat(), "toDate": t.isoformat()} for f, t in periods],
    }


def test_line_status_reports_the_current_problems():
    today = (NOW - timedelta(hours=2), NOW + timedelta(hours=10))
    next_week = (NOW + timedelta(days=7), NOW + timedelta(days=8))
    lines = [
        {
            "id": "northern",
            "name": "Northern",
            "modeName": "tube",
            "lineStatuses": [_status(6, "Severe Delays", [today]), _status(3, "Part Suspended", [next_week])],
        },
        {"id": "12", "name": "12", "modeName": "bus", "lineStatuses": [_status(10, "Good Service")]},
    ]

    # No reason given: the status itself, tagged with the line.
    assert line_status_notices(Lines.validate_python(lines), NOW) == [
        Notice("Northern", Severity.WARNING, "Severe Delays", today[1])
    ]


def test_line_status_only_shows_the_statuses_worth_a_notice():
    """The same for every mode: a suspended bus route is as severe as a suspended line."""
    statuses = {
        "12": (2, "Suspended"),
        "northern": (2, "Suspended"),
        "victoria": (9, "Minor Delays"),
        "68": (0, "Special Service"),
        "district": (20, "Service Closed"),
        "circle": (17, "Issues Reported"),
        "central": (5, "Part Closure"),
    }
    lines = [
        {
            "id": line_id,
            "name": line_id.title(),
            "modeName": "bus" if line_id.isdigit() else "tube",
            "lineStatuses": [_status(code, description)],
        }
        for line_id, (code, description) in statuses.items()
    ]

    assert line_status_notices(Lines.validate_python(lines), NOW) == [
        Notice("Bus 12", Severity.SEVERE, "Suspended"),
        Notice("Northern", Severity.SEVERE, "Suspended"),
        Notice("Circle", Severity.INFO, "Issues Reported"),
        Notice("Central", Severity.PLANNED, "Part Closure"),
    ]


def test_line_status_shows_a_reason_once_for_routes_sharing_it():
    closure = (
        "WATERLOO ROAD, Southwark: Routes 1 68 and 188   cannot serve St. George's Circus. Use stops in Borough Road."
    )
    early, late = NOW + timedelta(hours=2), NOW + timedelta(hours=6)
    today = NOW - timedelta(hours=1)
    lines = [
        {
            "id": "1",
            "name": "1",
            "modeName": "bus",
            "lineStatuses": [_status(3, "Part Suspended", [(today, early)], closure)],
        },
        {
            "id": "68",
            "name": "68",
            "modeName": "bus",
            "lineStatuses": [_status(2, "Suspended", [(today, late)], closure)],
        },
        {
            "id": "188",
            "name": "188",
            "modeName": "bus",
            "lineStatuses": [_status(3, "Part Suspended", [(today, early)], closure)],
        },
    ]

    # One notice for all three routes: the most severe status, the latest end.
    assert line_status_notices(Lines.validate_python(lines), NOW) == [
        Notice(
            "Buses",
            Severity.SEVERE,
            "WATERLOO ROAD, Southwark: Routes 1 68 and 188 cannot serve St. George's Circus.",
            late,
        )
    ]


@pytest.mark.parametrize(
    ("reason", "line", "headline"),
    [
        (
            "Victoria Line: Severe delays due to a track fault at Brixton. GOOD SERVICE on the rest of the line.",
            "Victoria",
            "Severe delays due to a track fault at Brixton.",
        ),
        (
            (
                "CENTRAL LINE: Saturday 26 September, no service between Marble Arch and Loughton. "
                "Replacement buses operate."
            ),
            "Central",
            "Saturday 26 September, no service between Marble Arch and Loughton.",
        ),
        (
            "DOCKLANDS LIGHT RAILWAY: Sunday 27 September, no service between Shadwell and Tower Gateway.",
            "DLR",
            "Sunday 27 September, no service between Shadwell and Tower Gateway.",
        ),
        (
            "Waterloo & City line: service operates Monday to Friday only.",
            "Waterloo & City",
            "Service operates Monday to Friday only.",
        ),
        ("no full stop and no prefix", "Northern", "No full stop and no prefix"),
    ],
)
def test_line_status_headline_is_the_first_sentence_without_the_line_name(reason, line, headline):
    lines = [
        {"id": "x", "name": line, "modeName": "tube", "lineStatuses": [_status(6, "Severe Delays", reason=reason)]}
    ]

    [notice] = line_status_notices(Lines.validate_python(lines), NOW)

    assert (notice.source, notice.text) == (line, headline)


def test_station_notices_are_shortened_deduplicated_and_expire():
    points = [
        {
            "commonName": "Elephant & Castle Underground Station",
            "description": "ELEPHANT & CASTLE UNDERGROUND STATION: The lifts are out of service.",
            "toDate": (NOW + timedelta(days=5)).isoformat(),
        },
        {
            "commonName": "Elephant & Castle Underground Station",
            "description": "Elephant & Castle: The lifts are out of service.",
            "toDate": (NOW + timedelta(days=2)).isoformat(),
        },
        {
            "commonName": "Kennington Underground Station",
            "description": "Kennington: Entrance closed.",
            "toDate": (NOW - timedelta(hours=1)).isoformat(),
        },
    ]

    notices = station_notices(DisruptedPoints.validate_python(points), NOW)

    assert [n.text for n in notices] == ["Elephant & Castle: The lifts are out of service."]


def test_roads_only_reports_serious_incidents_nearby():
    near, far = "[-0.098,51.492]", "[-0.3,51.6]"
    disruptions = RoadDisruptions.validate_python(
        [
            {
                "id": "1",
                "severity": "Severe",
                "category": "Collisions",
                "comments": "[A2] Old Kent Road",
                "point": near,
            },
            {"id": "2", "severity": "Serious", "category": "Works", "comments": "[A13] Commercial Road", "point": far},
            {"id": "3", "severity": "Serious", "category": "Works", "comments": "No location"},
            {"id": "4", "severity": "Moderate", "category": "Works", "comments": "[A3] Kennington Road", "point": near},
        ]
    )

    nearby = [d for d in disruptions if is_near(d, 51.49, -0.1)]

    assert [d.id for d in nearby] == ["1", "4"]
    assert road_notices(nearby) == [Notice("Roads", Severity.WARNING, "Collisions: [A2] Old Kent Road")]
