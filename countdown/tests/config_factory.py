from countdown_api import models as wire


def broker_payload(**overrides) -> dict:
    """Build what GET /api/config returns: complete and valid for the broker's contract.

    It's built from the generated models, so it can't drift from them. A section given as a dict is merged into the
    typical one, anything else replaces it, e.g. broker_payload(interval=20, tfl={"stop_ids": ["940GZZLUKNG"]}).
    """
    typical = wire.ConfigResponse(
        device_id="device-123",
        interval=15,
        screen=wire.Screen(orientation="vertical", width=480, height=800),
        weather=wire.Weather(location=""),
        notice_board=wire.NoticeBoard(postcode=None),
        tfl=wire.Tfl(stop_ids=[]),
        spotify=wire.Spotify(enabled=False),
        glowmarkt=wire.Glowmarkt(enabled=False),
        pairing_code=None,
    ).model_dump(mode="json")
    return typical | {
        name: typical[name] | value if isinstance(value, dict) and isinstance(typical.get(name), dict) else value
        for name, value in overrides.items()
    }


def make_config(**overrides) -> wire.ConfigResponse:
    """Build a complete, typical config, as the app gets it from the broker.

    Sections are overridden as for broker_payload, e.g. make_config(tfl={"stop_ids": ["940GZZLUKNG"]}).
    """
    return wire.ConfigResponse.model_validate(broker_payload(**overrides))


def with_apple_music(config: wire.ConfigResponse) -> wire.ConfigResponse:
    """Return `config` with Apple Music switched on too."""
    return config.model_copy(update={"apple_music": wire.AppleMusic(enabled=True)})
