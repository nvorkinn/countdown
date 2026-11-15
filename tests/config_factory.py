from pydantic import TypeAdapter

from countdown_api import models as wire

CONFIGS = TypeAdapter(wire.ConfigResponse)


def broker_payload(**overrides) -> dict:
    """Build an e-paper screen's config as the broker stores it: complete and valid for the contract.

    It's built from the generated models, so it can't drift from them. A section given as a dict is merged into the
    typical one, anything else replaces it, e.g. broker_payload(interval=20, arrivals={"stops": [...]}).
    """
    typical = wire.EpaperConfig(
        device_id="a1b2c3d4e5f6",
        type="epaper",
        width=480,
        height=800,
        format="mono-1bpp-packed",
        pairing_code=None,
        interval=15,
        clock_format="24h",
        language="en",
        timezone="Europe/London",
        arrivals=wire.Arrivals(stops=[]),
        weather=wire.Weather(location="", key=None),
        notice_board=wire.NoticeBoard(postcode=None, country=None),
        spotify=wire.Spotify(enabled=False),
        apple_music=wire.AppleMusic(enabled=False),
        glowmarkt=wire.Glowmarkt(enabled=False),
        developer=wire.Developer(enabled=False),
        parcels=wire.Parcels(enabled=False),
        style=wire.EpaperStyle(orientation="vertical", drawing="hero", weather="columns", layout=None),
    ).model_dump(mode="json")
    return typical | {
        name: typical[name] | value if isinstance(value, dict) and isinstance(typical.get(name), dict) else value
        for name, value in overrides.items()
    }


def make_config(**overrides) -> wire.EpaperConfig:
    """Build a complete, typical e-paper screen's config, as the render pool reads it from the store.

    Sections are overridden as for broker_payload, e.g. make_config(style={"orientation": "horizontal"}).
    """
    return wire.EpaperConfig.model_validate(broker_payload(**overrides))


def stops(*ids: str) -> dict:
    """Return an arrivals section of TfL stops (an NSR: id is Entur's), for make_config(arrivals=...)."""
    return {"stops": [{"source": "entur" if id_.startswith("NSR:") else "tfl", "id": id_} for id_ in ids]}


def with_apple_music(config: wire.EpaperConfig) -> wire.EpaperConfig:
    """Return `config` with Apple Music switched on too."""
    return config.model_copy(update={"apple_music": wire.AppleMusic(enabled=True)})


def set_stops(config: wire.EpaperConfig, ids: list[str]) -> None:
    """Set `config`'s arrivals to the stops `ids`, as stops() makes them."""
    config.arrivals = wire.Arrivals.model_validate(stops(*ids))
