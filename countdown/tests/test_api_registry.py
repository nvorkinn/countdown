"""The API clients one render builds (ApiRegistry): which, from what part of the config, and the panels they give.

A registry is built afresh for every render (render.render) and kept for none: what a screen last showed comes back
in only as the previous render's panels, handed to poll().
"""

import datetime as dt
import logging
from pathlib import Path

import pytest
import responses
from config_factory import make_config, with_apple_music
from test_utils import REGISTRATION
from weather_factory import forecast_json

from countdown_api.models import Glowmarkt
from countdown_api.models import Weather as WeatherSection
from countdown_core.core.abstract_client import AbstractClient, ClientStatus
from countdown_core.core.api_registry import (
    ApiRegistry,
    ClientClasses,
    FailedClient,
    is_enabled,
    section_for,
)
from countdown_core.departures.departures_client import DeparturesClient, DeparturesSettings
from countdown_core.developer.developer_client import DeveloperClient, DeveloperSettings
from countdown_core.glow.glow_client import GlowClient
from countdown_core.notices.notice_board_client import NoticeBoardClient, NoticeBoardSettings
from countdown_core.spotify.spotify_client import APPLE_MUSIC, SPOTIFY, AppleMusicClient, MusicSettings, SpotifyClient
from countdown_core.store import keys, reader
from countdown_core.store.models import StoredForecast
from countdown_core.system_screens.message_panel import MessagePanel
from countdown_core.weather.models import ForecastResponse, WeatherPeriod
from countdown_core.weather.weather_client import WeatherClient


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
    """Return a registry holding just `clients`, as if the config had built them."""
    registry = ApiRegistry(REGISTRATION, make_config())
    registry.clients.clear()
    registry.clients.update(clients)
    return registry


def _put_forecast(store) -> str:
    forecast = ForecastResponse.model_validate(forecast_json())
    key = keys.weather("London")
    store.set(
        key, StoredForecast(fetched_at=dt.datetime.now(dt.UTC), location="London", forecast=forecast).model_dump_json()
    )
    return key


def test_weather_shows_the_tab_of_the_minute_it_is_polled_in(store, rotation_clock):
    _put_forecast(store)
    seen = []
    for _ in range(5):
        # A registry of its own each minute, as each render builds one.
        registry = registry_with(weather=WeatherClient(REGISTRATION, WeatherSection(location="London")))
        seen.append(registry.poll()["weather"].weather.period)
        rotation_clock.advance()

    assert seen == [
        WeatherPeriod.NOW,
        WeatherPeriod.TONIGHT,
        WeatherPeriod.TOMORROW,
        WeatherPeriod.WEEKEND,
        WeatherPeriod.NOW,
    ]


@responses.activate  # nothing registered: the notice board's calls fail at once
def test_a_renderer_without_a_store_shows_the_panels_that_need_one_as_failing(monkeypatch):
    """Not a crash: the notice board, which doesn't need one, still works (developer: test_developer_client)."""
    monkeypatch.setattr(reader, "store", lambda: None)
    config = make_config(
        tfl={"stop_ids": ["490000123W"]},
        weather={"location": "London"},
        spotify={"enabled": True},
        glowmarkt={"enabled": True},
    )

    registry = ApiRegistry(REGISTRATION, config)

    for name in ("tfl", "weather", "spotify", "glowmarkt"):
        client = registry.clients[name]
        assert isinstance(client, FailedClient), name
        assert isinstance(client.error, reader.StoreNotConfiguredError)
        assert client.status == ClientStatus.ERROR
    assert isinstance(registry.clients["notice_board"], NoticeBoardClient)
    panels = registry.panels(registry.poll())
    assert {name: panels[name].message for name in ("tfl", "weather", "spotify", "glowmarkt")} == dict.fromkeys(
        ("tfl", "weather", "spotify", "glowmarkt"), "Could not connect"
    )


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
    assert section_for("spotify", config) == MusicSettings.from_config(config)
    assert section_for("glowmarkt", config) is config.glowmarkt
    assert section_for("notice_board", config) == NoticeBoardSettings(postcode="SE17 3LL")


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


def test_the_music_is_on_with_either_service():
    assert is_enabled(MusicSettings(spotify=True))
    assert is_enabled(MusicSettings(spotify=False, apple_music=True))
    assert not is_enabled(MusicSettings(spotify=False))


def test_the_apis_without_a_switch_are_always_on():
    config = make_config()

    for name in ("tfl", "weather", "notice_board"):
        assert is_enabled(section_for(name, config))


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize(
    "credentials",
    [
        {},
        {"username": None, "password": None},  # what a broker sent while they were deprecated
        {"username": "me@example.com", "password": "hunter2"},
    ],
)
def test_glowmarkt_is_on_exactly_when_its_switch_is(enabled, credentials):
    """Only the switch counts: the readings come from the store, and no credentials ever switch the panel on."""
    assert is_enabled(Glowmarkt.model_validate({"enabled": enabled, **credentials})) is enabled


def test_the_developer_client_is_built_when_it_is_switched_on():
    registry = ApiRegistry(REGISTRATION, make_config(developer={"enabled": True}))

    assert isinstance(registry.clients["developer"], DeveloperClient)
    assert ClientClasses.DEVELOPER in registry.available()
    # Its notices go to the notice board with the departures' disruptions.
    assert "DeveloperNoticeSource" in source_names(registry)


def test_the_enabled_clients_are_built():
    config = make_config()
    config.spotify.enabled = True

    registry = ApiRegistry(REGISTRATION, config)

    assert {name: type(client) for name, client in registry.clients.items()} == {
        "notice_board": NoticeBoardClient,
        "tfl": DeparturesClient,
        "weather": WeatherClient,
        "spotify": SpotifyClient,
    }
    assert registry.sections == {name: section_for(name, config) for name in registry.clients}


def test_apple_music_alone_builds_its_client_and_with_spotify_too_the_spotify_one():
    alone = ApiRegistry(REGISTRATION, with_apple_music(make_config(spotify={"enabled": False})))
    both = ApiRegistry(REGISTRATION, with_apple_music(make_config(spotify={"enabled": True})))

    assert type(alone.clients["spotify"]) is AppleMusicClient
    assert type(both.clients["spotify"]) is SpotifyClient
    assert both.clients["spotify"].config.services == (SPOTIFY, APPLE_MUSIC)


def test_apple_musics_area_says_whose_service_could_not_connect():
    registry = ApiRegistry(REGISTRATION, with_apple_music(make_config(spotify={"enabled": False})))

    panel = registry.panels({})["spotify"]

    assert (panel.title, panel.message) == ("Apple Music", "Could not connect")


def source_names(registry: ApiRegistry) -> list[str]:
    return [type(source).__name__ for source in registry.clients["notice_board"].client_sources]


@responses.activate
def test_the_notice_board_polls_the_departures_disruptions_for_their_stops():
    config = make_config(departures={"stops": [{"source": "entur", "id": "NSR:StopPlace:58366"}]})

    registry = ApiRegistry(REGISTRATION, config)

    assert registry.clients["notice_board"].client_sources == registry.clients["tfl"].notice_sources()
    assert source_names(registry) == ["StoredEnturSituationsSource"]


@responses.activate
def test_the_disruptions_follow_the_stops():
    """Norway's disruptions go with Norway's stops, London's with London's."""
    board = {"postcode": "SE17 3LL", "country": "GB"}
    norway = make_config(notice_board=board, departures={"stops": [{"source": "entur", "id": "NSR:StopPlace:58366"}]})
    london = make_config(notice_board=board, departures={"stops": [{"source": "tfl", "id": "940GZZLUKNG"}]})

    assert source_names(ApiRegistry(REGISTRATION, norway)) == ["StoredEnturSituationsSource"]
    assert source_names(ApiRegistry(REGISTRATION, london)) == ["StoredTflLineStatusSource", "StoredTflStationSource"]


def test_disabled_apis_get_no_client():
    registry = ApiRegistry(REGISTRATION, make_config())  # spotify is off until the broker says otherwise

    assert set(registry.clients) == {"notice_board", "tfl", "weather"}  # glowmarkt: switched off
    assert set(registry.sections) == set(registry.clients)


def test_a_client_that_cannot_be_built_stands_in_as_failed(unbuildable_spotify, caplog):
    config = make_config()
    config.spotify.enabled = True

    registry = ApiRegistry(REGISTRATION, config)

    assert isinstance(registry.clients["spotify"], FailedClient)
    assert registry.statuses()["spotify"] == ClientStatus.ERROR
    assert "tfl" in registry.clients
    assert "Error building spotify client" in caplog.text


@responses.activate
def test_a_client_that_could_not_be_built_says_it_could_not_connect_and_is_logged_once(unbuildable_spotify, caplog):
    caplog.set_level(logging.INFO)
    config = make_config()
    config.spotify.enabled = True
    registry = ApiRegistry(REGISTRATION, config)
    caplog.clear()

    panels = registry.panels(registry.poll())

    assert messages(panels)["spotify"] == "Could not connect"
    assert "Error updating spotify" not in caplog.text  # its constructor's error was logged as it was built


def test_a_client_that_fails_to_initialise_is_an_error_and_the_others_carry_on(monkeypatch):
    def broken(self):
        msg = "down"
        raise ConnectionError(msg)

    monkeypatch.setattr(DeparturesClient, "_initialise", broken)
    registry = ApiRegistry(REGISTRATION, make_config(weather={"location": "London"}))

    polled = registry.poll()

    assert "tfl" not in polled
    assert "weather" in polled
    assert registry.statuses()["tfl"] == ClientStatus.ERROR
    assert registry.statuses()["weather"] == ClientStatus.CONNECTED


def test_poll_returns_each_clients_panel():
    registry = registry_with(tfl=FakeClient("arrivals"), weather=FakeClient("sunny"))

    assert registry.poll() == {"tfl": "arrivals", "weather": "sunny"}


def test_poll_reads_each_client_once_and_initialises_it_first():
    tfl = FakeClient("arrivals")

    registry_with(tfl=tfl).poll()

    assert (tfl.initialised, tfl.updates) == (True, 1)


def test_there_is_a_message_for_every_api_without_a_client():
    """So the display never has an area with nothing to draw."""
    registry = registry_with(tfl=FakeClient("arrivals"))

    assert messages(registry.panels(registry.poll())) == {
        "notice_board": "Not configured",
        "glowmarkt": "Not configured",
        "weather": "Not configured",
        "spotify": "Not configured",
        "developer": "Not configured",
    }


def test_there_is_a_message_for_a_disabled_client():
    """Enabled in the config but missing what it needs."""
    glow = FakeClient(None)
    glow.status = ClientStatus.DISABLED
    registry = registry_with(glowmarkt=glow)

    assert messages(registry.panels(registry.poll()))["glowmarkt"] == "Not configured"


def test_a_client_that_raises_gives_its_previous_panel():
    registry = registry_with(weather=FakeClient(ConnectionError("down")), tfl=FakeClient("arrivals"))

    assert registry.poll({"weather": "sunny", "tfl": "old arrivals"}) == {"weather": "sunny", "tfl": "arrivals"}


def test_a_client_that_raises_with_no_previous_panel_could_not_connect():
    registry = registry_with(tfl=FakeClient(RuntimeError("boom")), weather=FakeClient("sunny"))

    panels = registry.panels(registry.poll())

    assert messages(panels)["tfl"] == "Could not connect"
    assert panels["tfl"].title == "Arrivals"
    assert panels["weather"] == "sunny"


# --- Expiry: each provider's TTL is the only expiry there is ---------------------------------------------------------

DEVELOPER_FIXTURE = (Path(__file__).parent / "fixtures" / "developer.json").read_text()


def _put_developer(store) -> str:
    key = keys.developer(REGISTRATION.device_id)
    store.set(key, DEVELOPER_FIXTURE)
    return key


STORED = {
    # The API, its client as the registry builds it, and how its provider fills its key (returning the key).
    "weather": (lambda: WeatherClient(REGISTRATION, WeatherSection(location="London")), _put_forecast),
    "developer": (lambda: DeveloperClient(REGISTRATION, DeveloperSettings(enabled=True, style="hero")), _put_developer),
}


@pytest.mark.parametrize("api", list(STORED))
def test_an_expired_key_draws_not_available_and_not_the_previous_panel(store, api):
    """No last-good copy: once the provider's key has expired, the panel says so rather than draw what it last had."""
    build, put = STORED[api]
    key = put(store)
    first = registry_with(**{api: build()}).poll()
    assert not isinstance(first[api], MessagePanel)

    store.delete(key)  # its TTL is up: the provider has been down for a few of its cycles
    client = build()
    registry = registry_with(**{api: client})
    panel = registry.poll(first)[api]

    assert isinstance(panel, MessagePanel)
    assert (panel.title, panel.message) == (type(client).panel_title, "Not available")
    assert registry.expired() == {api}


@pytest.mark.parametrize("api", list(STORED))
def test_redis_not_answering_draws_the_previous_panel(store, monkeypatch, api):
    """Redis being unreachable isn't the data expiring: the screen keeps what it drew."""
    build, put = STORED[api]
    put(store)
    first = registry_with(**{api: build()}).poll()

    def unreachable(*args, **kwargs):
        raise reader.redis.exceptions.ConnectionError

    monkeypatch.setattr(store, "get", unreachable)
    registry = registry_with(**{api: build()})

    assert registry.poll(first)[api] is first[api]
    assert registry.expired() == frozenset()


def test_a_key_missing_from_the_first_poll_is_not_available_rather_than_could_not_connect(store):
    """The store answered: it just has nothing for the panel yet."""
    registry = registry_with(developer=STORED["developer"][0]())

    assert messages(registry.panels(registry.poll()))["developer"] == "Not available"


def test_an_expired_key_is_logged_without_a_traceback(store, caplog):
    registry = registry_with(developer=STORED["developer"][0]())

    with caplog.at_level(logging.WARNING):
        registry.poll()

    [record] = caplog.records
    assert record.getMessage() == (
        "DeveloperClient: No developer data in the store for device device-123; its panel says it's not available"
    )
    assert record.exc_info is None


def test_a_client_that_raises_is_logged_with_its_traceback(caplog):
    """So the journal shows where it went wrong, not just the exception's message."""
    registry = registry_with(weather=FakeClient(ConnectionError("down")))

    registry.poll()

    (record,) = [r for r in caplog.records if "Error updating weather" in r.getMessage()]
    assert record.levelno == logging.ERROR
    assert isinstance(record.exc_info[1], ConnectionError)
    assert "Traceback" in caplog.text
    assert "_update" in caplog.text


def test_a_clients_own_message_is_drawn_rather_than_its_previous_panel():
    """A client's own message (nothing playing, no stops set) is a real answer, unlike an error."""
    nothing_playing = SpotifyClient.message_panel("Nothing playing on:")
    registry = registry_with(spotify=FakeClient(nothing_playing))

    assert registry.poll({"spotify": "now playing"})["spotify"] is nothing_playing


@pytest.mark.parametrize("bad", [ValueError("bad"), KeyError("bad")])
def test_one_clients_failure_never_escapes(bad):
    registry = registry_with(tfl=FakeClient(bad), weather=FakeClient("fine"))

    assert registry.poll()["weather"] == "fine"


def test_the_statuses_are_each_clients():
    failed = FailedClient(REGISTRATION, make_config().weather, ValueError("can't build"))
    registry = registry_with(tfl=FakeClient("arrivals"), weather=failed)
    registry.poll()

    assert registry.statuses() == {"tfl": ClientStatus.CONNECTED, "weather": ClientStatus.ERROR}


def test_the_pace_keys_are_the_clients_own(store):
    registry = ApiRegistry(REGISTRATION, make_config(developer={"enabled": True}))

    assert registry.pace_keys() == [keys.pace(keys.DEVELOPER, REGISTRATION.device_id)]


def test_available_is_the_built_clients_that_are_not_disabled():
    disabled = FakeClient()
    disabled.status = ClientStatus.DISABLED
    registry = registry_with(
        tfl=FakeClient(),
        glowmarkt=disabled,
        weather=FailedClient(REGISTRATION, WeatherSection(location=""), ValueError()),
    )

    # A failing client keeps its area (to say so); a disabled or missing one doesn't.
    assert registry.available() == frozenset({ClientClasses.TFL, ClientClasses.WEATHER})


def test_available_leaves_out_glowmarkt_and_spotify_when_off():
    registry = ApiRegistry(REGISTRATION, make_config())

    assert registry.available() == frozenset({ClientClasses.TFL, ClientClasses.WEATHER, ClientClasses.NOTICE_BOARD})


def test_glowmarkt_switched_on_builds_its_client():
    assert isinstance(
        ApiRegistry(REGISTRATION, make_config(glowmarkt={"enabled": True})).clients["glowmarkt"], GlowClient
    )


def test_show_stops_tells_the_tfl_client_how_many_to_fetch():
    registry = registry_with(tfl=DeparturesClient(REGISTRATION, DeparturesSettings(())))

    registry.show_stops(4)

    assert registry.clients["tfl"].stops_per_update == 4


def test_show_stops_without_a_tfl_client_does_nothing():
    registry_with(weather=FakeClient()).show_stops(4)
