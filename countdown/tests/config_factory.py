from countdown_api import models as wire
from countdown_core.config_server.models import AppConfig


def make_config(**overrides) -> AppConfig:
    """A complete, typical config, validated the same way as the broker's response.
    Override a whole section with a dict, e.g. make_config(tfl={"stop_ids": ["940GZZLUKNG"]})."""
    typical = {
        "interval": 15,
        "tfl": {},
        "spotify": {},
        "weather": {},
        "glowmarkt": {},
        "pairing_code": None,
    }
    return AppConfig.model_validate(typical | overrides)


def broker_payload(**sections) -> dict:
    """What GET /api/config returns: complete and valid for the broker's contract (built from the generated
    models, so it can't drift from it). Replace whole sections, e.g.
    broker_payload(interval=20, tfl={"app_key": "k", "stop_ids": ["940GZZLUKNG"]})."""
    typical = wire.ConfigResponse(
        device_id="device-123",
        interval=15,
        screen=wire.Screen(orientation="vertical", width=480, height=800),
        weather=wire.Weather(api_key="", location=""),
        notice_board=wire.NoticeBoard(postcode=None),
        tfl=wire.Tfl(app_key="", stop_ids=[]),
        spotify=wire.Spotify(enabled=False),
        glowmarkt=wire.Glowmarkt(username=None, password=None),
        pairing_code=None,
    )
    return typical.model_dump(mode="json") | sections
