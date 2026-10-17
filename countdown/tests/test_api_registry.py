import asyncio
import datetime as dt
import logging

import pytest
import responses
from config_factory import make_config
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from test_utils import REGISTRATION
from weather_factory import forecast_json

from countdown_api.models import Glowmarkt, Spotify
from countdown_api.models import Weather as WeatherSection
from countdown_core.core.abstract_client import AbstractClient, ClientStatus
from countdown_core.core.api_registry import (
    API_NAMES,
    ApiRegistry,
    ClientClasses,
    FailedClient,
    is_enabled,
    section_for,
)
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_core.developer.developer_client import DeveloperClient, DeveloperSettings
from countdown_core.glow.glow_client import GlowClient
from countdown_core.home_assistant.otlp_publisher import OtlpPublisher
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.spotify.spotify_client import SpotifyClient
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.weather.models import WeatherPeriod
from countdown_core.weather.weather_client import FORECAST_URL, WeatherClient


class FakeClient(AbstractClient):
    """Scriptable stand-in: each update() returns/raises the next item in `results`."""

    def __init__(self, *results):
        super().__init__(REGISTRATION)
        self.results = list(results)
        self.initialised = False
        self.updates = 0

    def _initialise(self) -> None:
        self.initialised = True

    def _update(self):
        self.updates += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def unbuildable_spotify(monkeypatch):
    """Make building the Spotify client raise, as a constructor that can't get what it needs would."""

    def broken(self, registration, config):
        AbstractClient.__init__(self, registration, config)  # so its __del__ can still close the session
        msg = "can't build"
        raise ValueError(msg)

    monkeypatch.setattr(SpotifyClient, "__init__", broken)


def registry_with(**clients: AbstractClient) -> ApiRegistry:
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    registry.clients.update(clients)
    return registry


@responses.activate
def test_weather_rotates_only_when_the_registry_actually_polls_it():
    responses.add(responses.GET, FORECAST_URL, json=forecast_json())
    client = WeatherClient(REGISTRATION, WeatherSection(location="", api_key=""))
    registry = registry_with(weather=client)
    seen = []
    for _ in range(5):
        panels = asyncio.run(registry.update_all())
        seen.append(panels["weather"].weather.period)
        assert asyncio.run(registry.update_all())["weather"] is panels["weather"]
        client.last_updated -= client.poll_interval + dt.timedelta(seconds=1)

    assert seen == [
        WeatherPeriod.NOW,
        WeatherPeriod.TONIGHT,
        WeatherPeriod.TOMORROW,
        WeatherPeriod.WEEKEND,
        WeatherPeriod.NOW,
    ]
    assert len(responses.calls) == 1


def drawn(panels: dict) -> dict:
    """Keep the panels that aren't MessagePanels standing in for one."""
    return {name: panel for name, panel in panels.items() if not isinstance(panel, MessagePanel)}


def messages(panels: dict) -> dict:
    return {name: panel.message for name, panel in panels.items() if isinstance(panel, MessagePanel)}


def test_api_names_match_the_config_field_names():
    """on_config_update looks each client's config up by name on the ConfigResponse."""
    config = make_config()
    for member in ClientClasses:
        assert hasattr(config, member.api_name)


def test_each_client_is_configured_with_its_section_of_the_config():
    config = make_config(tfl={"stop_ids": ["940GZZLUKNG"]}, notice_board={"postcode": "SE17 3LL"})

    assert section_for("tfl", config) == DeparturesSettings.from_config(config)
    assert section_for("weather", config) is config.weather
    assert section_for("spotify", config) == config.spotify
    assert section_for("glowmarkt", config) is config.glowmarkt
    # The notice board also gets the TfL key, for the road incidents near the postcode.
    assert section_for("notice_board", config) == NoticeBoardSettings(
        postcode="SE17 3LL", tfl_app_key=config.tfl.app_key
    )


def test_every_panel_is_drawn_in_the_configs_one_style():
    """The shared style wins over each panel's own, which the broker still sends for older screens."""
    config = make_config(
        style="card",
        tfl={"style": "grouped"},
        spotify={"enabled": True, "style": "airy"},
        notice_board={"postcode": "SE17 3LL", "style": "hero"},
    )

    assert section_for("tfl", config).style == "card"
    assert section_for("spotify", config).style == "card"
    assert section_for("notice_board", config).style == "card"


def test_the_developer_panel_is_off_unless_the_broker_switches_it_on():
    """A broker that predates it sends no developer section at all."""
    assert section_for("developer", make_config()) == DeveloperSettings(enabled=False, style="hero")
    assert not is_enabled(section_for("developer", make_config(developer={"enabled": False})))

    switched_on = section_for("developer", make_config(style="airy", developer={"enabled": True}))

    assert switched_on == DeveloperSettings(enabled=True, style="airy")
    assert is_enabled(switched_on)


def test_on_config_update_builds_the_developer_client_when_it_is_switched_on():
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(make_config(developer={"enabled": True})))

    assert isinstance(registry.clients["developer"], DeveloperClient)
    assert ClientClasses.DEVELOPER in registry.available()
    # Its notices go to the notice board with the departures' disruptions.
    assert "DeveloperNoticeSource" in source_names(registry)


def test_a_broker_without_the_shared_style_leaves_each_panel_on_its_own():
    """Their old names are drawn as the shared look nearest them: grouped was airy's, boxed goes to card."""
    config = make_config(
        tfl={"style": "grouped"},
        spotify={"enabled": True, "style": "classic"},
        notice_board={"postcode": "SE17 3LL", "style": "boxed"},
    )

    assert section_for("tfl", config).style == "airy"
    assert section_for("spotify", config).style == "hero"
    assert section_for("notice_board", config).style == "card"


def test_only_spotify_has_a_switch_in_the_config():
    assert is_enabled(Spotify(enabled=True))
    assert not is_enabled(Spotify(enabled=False))


def test_the_apis_without_a_switch_are_always_on():
    config = make_config()

    for name in ("tfl", "weather", "notice_board"):
        assert is_enabled(section_for(name, config))


@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"username": "me@example.com"},
        {"password": "hunter2"},
        {"username": "  ", "password": "hunter2"},
        {"username": "me@example.com", "password": ""},
    ],
)
def test_glowmarkt_is_off_without_both_credentials(credentials):
    """The broker has no Glowmarkt switch, so the credentials decide.

    A cleared field can arrive as "" or whitespace as well as null.
    """
    assert not is_enabled(Glowmarkt(**{"username": None, "password": None, **credentials}))


def test_glowmarkt_is_on_with_both_credentials():
    assert is_enabled(Glowmarkt(username="me@example.com", password=" pass word "))


def test_on_config_update_registers_the_enabled_clients():
    config = make_config()
    config.spotify.enabled = True
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(config))

    assert {name: type(client) for name, client in registry.clients.items()} == {
        "notice_board": NoticeBoardClient,
        "tfl": DeparturesClient,
        "weather": WeatherClient,
        "spotify": SpotifyClient,
    }


def source_names(registry: ApiRegistry) -> list[str]:
    return [type(source).__name__ for source in registry.clients["notice_board"].client_sources]


def test_the_notice_board_polls_the_departures_disruptions_for_their_stops():
    config = make_config(departures={"stops": [{"source": "entur", "id": "NSR:StopPlace:58366"}]})
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(config))

    assert registry.clients["notice_board"].client_sources == registry.clients["tfl"].notice_sources()
    assert source_names(registry) == ["EnturSituationsSource"]


def test_new_stops_change_the_disruptions_without_rebuilding_the_notice_board():
    """Norway's disruptions go with Norway's stops, and the board keeps its postcode's place."""
    norway = make_config(
        notice_board={"postcode": "SE17 3LL", "country": "GB"},
        departures={"stops": [{"source": "entur", "id": "NSR:StopPlace:58366"}]},
    )
    london = make_config(
        notice_board={"postcode": "SE17 3LL", "country": "GB"},
        departures={"stops": [{"source": "tfl", "id": "940GZZLUKNG"}]},
    )
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    asyncio.run(registry.on_config_update(norway))
    board = registry.clients["notice_board"]

    asyncio.run(registry.on_config_update(london))

    assert registry.clients["notice_board"] is board
    assert source_names(registry) == ["TflLineStatusSource", "TflStationSource"]


def test_a_rebuilt_notice_board_gets_the_departures_disruptions_too():
    stops = {"stops": [{"source": "tfl", "id": "940GZZLUKNG"}]}
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    asyncio.run(registry.on_config_update(make_config(departures=stops, notice_board={"postcode": "0150"})))

    asyncio.run(registry.on_config_update(make_config(departures=stops, notice_board={"postcode": "SE17 3LL"})))

    assert source_names(registry) == ["TflLineStatusSource", "TflStationSource"]


def test_on_config_update_skips_disabled_clients():
    config = make_config()  # spotify is off until the broker says otherwise
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(config))

    assert set(registry.clients) == {"notice_board", "tfl", "weather"}  # glowmarkt: no credentials


def test_on_config_update_initialises_new_clients_even_if_one_fails(monkeypatch):
    initialised = []

    async def broken(self):
        msg = "down"
        raise ConnectionError(msg)

    async def healthy(self):
        initialised.append(self)

    monkeypatch.setattr(DeparturesClient, "initialise", broken)
    monkeypatch.setattr(WeatherClient, "initialise", healthy)
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(make_config()))

    assert initialised == [registry.clients["weather"]]


def test_on_config_update_survives_a_client_that_cannot_be_built(unbuildable_spotify):
    config = make_config()
    config.spotify.enabled = True
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(config))

    assert registry.clients["spotify"].status == ClientStatus.ERROR
    assert "tfl" in registry.clients


def test_unchanged_config_keeps_the_existing_clients_and_their_panels():
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    asyncio.run(registry.on_config_update(make_config()))
    before = dict(registry.clients)
    registry.panels["tfl"] = "arrivals"

    asyncio.run(registry.on_config_update(make_config()))

    assert all(registry.clients[name] is client for name, client in before.items())
    assert registry.panels == {"tfl": "arrivals"}


def test_a_changed_config_replaces_only_that_client_and_drops_its_stale_panel():
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    asyncio.run(registry.on_config_update(make_config()))
    old_tfl, old_weather = registry.clients["tfl"], registry.clients["weather"]
    registry.panels.update(tfl="old arrivals", weather="sunny")
    changed = make_config()
    changed.tfl.stop_ids = ["940GZZLUKNG"]  # resolving it fails offline, which is fine here

    asyncio.run(registry.on_config_update(changed))

    assert registry.clients["tfl"] is not old_tfl
    assert registry.clients["tfl"].config.stop_ids("tfl") == ["940GZZLUKNG"]
    assert registry.clients["weather"] is old_weather
    assert registry.panels == {"weather": "sunny"}


def test_a_client_that_is_switched_off_is_dropped_with_its_panel():
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    asyncio.run(registry.on_config_update(make_config(spotify={"enabled": True})))
    registry.panels["spotify"] = "now playing"
    off = make_config(spotify={"enabled": False})

    asyncio.run(registry.on_config_update(off))

    assert "spotify" not in registry.clients
    assert "spotify" not in registry.panels


def test_a_change_the_client_says_is_irrelevant_does_not_rebuild_it():
    """Open-Meteo is keyless, so WeatherClient ignores api_key changes.

    Rebuilding would only throw away its cached coordinates and forecast.
    """
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    asyncio.run(registry.on_config_update(make_config()))
    weather = registry.clients["weather"]
    changed = make_config()
    changed.weather.api_key = "new-key"

    asyncio.run(registry.on_config_update(changed))
    assert registry.clients["weather"] is weather

    changed.weather.location = "Paris"
    asyncio.run(registry.on_config_update(changed))
    assert registry.clients["weather"] is not weather


def test_update_all_returns_each_clients_panel():
    registry = registry_with(tfl=FakeClient("arrivals"), weather=FakeClient("sunny"))

    assert drawn(asyncio.run(registry.update_all())) == {"tfl": "arrivals", "weather": "sunny"}


def test_update_all_has_a_message_for_every_api_without_a_client():
    """So the display never has an area with nothing to draw."""
    registry = registry_with(tfl=FakeClient("arrivals"))

    assert messages(asyncio.run(registry.update_all())) == {
        "notice_board": "Not configured",
        "glowmarkt": "Not configured",
        "weather": "Not configured",
        "spotify": "Not configured",
        "developer": "Not configured",
    }


def test_update_all_has_a_message_for_a_disabled_client():
    """Enabled in the config but missing what it needs, like Glowmarkt without credentials."""
    glow = FakeClient(None)
    glow.status = ClientStatus.DISABLED
    registry = registry_with(glowmarkt=glow)

    assert messages(asyncio.run(registry.update_all()))["glowmarkt"] == "Not configured"


def test_update_all_only_polls_clients_that_are_due_but_still_returns_their_last_panel():
    """A client that isn't due yet must still have its last panel drawn, not blanked."""
    tfl, glow = FakeClient("arrivals-1", "arrivals-2"), FakeClient("energy")
    registry = registry_with(tfl=tfl, glowmarkt=glow)
    asyncio.run(registry.update_all())
    tfl.last_updated -= tfl.poll_interval  # tfl is due again; glow (just polled) is not

    panels = asyncio.run(registry.update_all())

    assert (tfl.updates, glow.updates) == (2, 1)
    assert drawn(panels) == {"tfl": "arrivals-2", "glowmarkt": "energy"}


def test_update_all_keeps_the_last_good_panel_when_a_client_raises():
    weather = FakeClient("sunny", ConnectionError("down"))
    registry = registry_with(weather=weather)
    asyncio.run(registry.update_all())
    weather.last_updated = None

    panels = asyncio.run(registry.update_all())

    assert drawn(panels) == {"weather": "sunny"}


def test_a_client_that_raises_during_update_is_logged_with_its_traceback(caplog):
    """So the journal shows where it went wrong, not just the exception's message."""
    registry = registry_with(weather=FakeClient(ConnectionError("down")))

    asyncio.run(registry.update_all())

    (record,) = [r for r in caplog.records if "Error updating weather" in r.getMessage()]
    assert record.levelno == logging.ERROR
    assert isinstance(record.exc_info[1], ConnectionError)
    assert "Traceback" in caplog.text
    assert "_update" in caplog.text


def test_update_all_replaces_the_last_panel_when_a_client_has_nothing_to_show():
    """A client's own message (nothing playing, no stops set) is a real answer, unlike an error."""
    nothing_playing = SpotifyClient.message_panel("Nothing playing on:")
    spotify = FakeClient("now playing", nothing_playing)
    registry = registry_with(spotify=spotify)
    asyncio.run(registry.update_all())
    spotify.last_updated = None

    assert asyncio.run(registry.update_all())["spotify"] is nothing_playing


def test_update_all_says_a_client_that_has_never_succeeded_could_not_connect():
    registry = registry_with(tfl=FakeClient(RuntimeError("boom")), weather=FakeClient("sunny"))

    panels = asyncio.run(registry.update_all())

    assert messages(panels)["tfl"] == "Could not connect"
    assert panels["tfl"].title == "Arrivals"
    assert panels["weather"] == "sunny"


def test_the_publisher_reports_the_registrys_own_clients_and_shares_its_status():
    publisher = OtlpPublisher(API_NAMES)
    registry = ApiRegistry(REGISTRATION, publisher)
    registry.clients["tfl"] = FakeClient("arrivals")

    assert registry.pub is publisher
    assert publisher.clients is registry.clients  # so later clients are reported too
    assert registry.status is publisher.device_status
    assert publisher.api_names == [member.api_name for member in ClientClasses]


@pytest.mark.parametrize("bad", [ValueError("bad"), KeyError("bad")])
def test_update_all_never_lets_one_clients_failure_escape(bad):
    registry = registry_with(tfl=FakeClient(bad), weather=FakeClient("fine"))

    assert asyncio.run(registry.update_all())["weather"] == "fine"


def test_a_client_that_could_not_be_built_shows_as_an_error_in_the_health_payload(unbuildable_spotify):
    config = make_config()
    config.spotify.enabled = True
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(config))

    reader = InMemoryMetricReader()
    registry.pub._reader = reader
    asyncio.run(_start(registry.pub))
    data = {m.name: m.data.data_points for m in reader.get_metrics_data().resource_metrics[0].scope_metrics[0].metrics}
    assert {"api": "spotify", "status": "error"} in [dict(p.attributes) for p in data["countdown.api.status"]]
    assert data["countdown.problem"][0].value == 1


def test_a_client_that_could_not_be_built_is_reported_again_by_update_all_without_raising(unbuildable_spotify, caplog):
    caplog.set_level(logging.INFO)
    config = make_config()
    config.spotify.enabled = True
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    asyncio.run(registry.on_config_update(config))
    caplog.clear()

    panels = asyncio.run(registry.update_all())

    assert messages(panels)["spotify"] == "Could not connect"
    assert "Error updating spotify" in caplog.text


def test_a_failed_client_is_rebuilt_when_its_config_changes_and_dropped_when_switched_off():
    failed = FailedClient(REGISTRATION, make_config().spotify, ValueError("can't build"))

    assert failed.needs_refresh(make_config(spotify={"enabled": True}).spotify)

    registry = registry_with(spotify=failed)
    registry.panels["spotify"] = "old panel"
    asyncio.run(registry.on_config_update(make_config()))  # spotify is disabled in the new config
    assert "spotify" not in registry.clients


def test_available_is_the_built_clients_that_are_not_disabled():
    disabled = FakeClient()
    disabled.status = ClientStatus.DISABLED
    registry = registry_with(
        tfl=FakeClient(),
        glowmarkt=disabled,
        weather=FailedClient(REGISTRATION, WeatherSection(api_key="", location=""), ValueError()),
    )

    # A failing client keeps its area (to say so); a disabled or missing one doesn't.
    assert registry.available() == frozenset({ClientClasses.TFL, ClientClasses.WEATHER})


def test_available_leaves_out_glowmarkt_without_credentials_and_spotify_when_off():
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(make_config()))

    assert registry.available() == frozenset({ClientClasses.TFL, ClientClasses.WEATHER, ClientClasses.NOTICE_BOARD})


@pytest.mark.parametrize("username", [None, "", "   "])
def test_available_leaves_out_glowmarkt_when_its_username_is_cleared(username):
    """The broker has no Glowmarkt switch, so it's the credentials themselves that decide."""
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))

    asyncio.run(registry.on_config_update(make_config(glowmarkt={"username": username, "password": "pw"})))

    assert ClientClasses.GLOWMARKT not in registry.available()


def test_a_client_missing_what_it_needs_is_not_built_and_comes_back_fresh(monkeypatch):
    """Clearing Glowmarkt's username removes its client altogether.

    Putting it back builds a new one rather than reviving the old.
    """
    monkeypatch.setattr(GlowClient, "_initialise", lambda self: asyncio.sleep(0))  # no network
    registry = ApiRegistry(REGISTRATION, OtlpPublisher(API_NAMES))
    with_credentials = make_config(glowmarkt={"username": "me@example.com", "password": "pw"})

    asyncio.run(registry.on_config_update(with_credentials))
    first = registry.clients["glowmarkt"]
    asyncio.run(registry.on_config_update(make_config(glowmarkt={"username": None, "password": "pw"})))
    assert "glowmarkt" not in registry.clients
    assert "glowmarkt" not in registry.panels

    asyncio.run(registry.on_config_update(with_credentials))
    assert isinstance(registry.clients["glowmarkt"], GlowClient)
    assert registry.clients["glowmarkt"] is not first


def test_show_stops_tells_the_tfl_client_how_many_to_fetch():
    registry = registry_with(tfl=DeparturesClient(REGISTRATION, DeparturesSettings(())))

    registry.show_stops(4)

    assert registry.clients["tfl"].stops_per_update == 4


def test_show_stops_without_a_tfl_client_does_nothing():
    registry_with(weather=FakeClient()).show_stops(4)


async def _start(pub):
    pub.start()
    await asyncio.gather(*pub._tasks)
