"""GENERATED from core/contract/broker-openapi.json by core/contract/generate.py; do not edit.

The broker's wire format. AppConfig (models.py) is what the app uses; the tests check it accepts
everything in here."""


from enum import StrEnum

from pydantic import BaseModel


class Error(BaseModel):
    error: str


class Glowmarkt(BaseModel):
    username: str | None
    password: str | None


class NoticeBoard(BaseModel):
    postcode: str | None


class Orientation(StrEnum):
    horizontal = "horizontal"
    vertical = "vertical"


class Screen(BaseModel):
    orientation: Orientation
    width: int
    height: int


class Spotify(BaseModel):
    enabled: bool


class Tfl(BaseModel):
    app_key: str
    stop_ids: list[str]


class Weather(BaseModel):
    api_key: str
    location: str


class ConfigResponse(BaseModel):
    device_id: str
    interval: int
    screen: Screen
    weather: Weather
    notice_board: NoticeBoard
    tfl: Tfl
    spotify: Spotify
    glowmarkt: Glowmarkt
    pairing_code: str | None
    setup_missing: list[str]
