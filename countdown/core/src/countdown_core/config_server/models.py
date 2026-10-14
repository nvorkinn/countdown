from abc import ABC
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ApiConfig(ABC, BaseModel):
    enabled: bool | None = Field(default=True)


class TflConfig(ApiConfig):
    app_key: str = Field(default="")
    # Flat ordered list of NaPTAN stop IDs to cycle through on the display
    stop_ids: list[str] = Field(default_factory=list)


class SpotifyConfig(ApiConfig):
    # OAuth lives in auth-broker; the device only knows whether to show the panel.
    enabled: bool | None = Field(default=False)


class WeatherConfig(ApiConfig):
    api_key: str = Field(default="")
    location: str = Field(default="")


class GlowmarktConfig(ApiConfig):
    # None, not "": a credential has no legitimate empty value.
    username: str | None = Field(default=None)
    password: str | None = Field(default=None)

    @field_validator("username", "password", mode="before")
    @classmethod
    def blank_is_not_set(cls, value: Any) -> Any:
        """A cleared field can arrive as "" or whitespace rather than null."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def enabled_only_with_credentials(self) -> GlowmarktConfig:
        """The broker has no Glowmarkt switch (`enabled` is always true), so having both
        credentials is what switches it on."""
        if not (self.username and self.password):
            self.enabled = False
        return self


class NoticeBoardConfig(ApiConfig):
    # Where weather warnings, floods and road incidents are looked up for.
    postcode: str | None = Field(default=None)
    # Not sent by the broker: a copy of AppConfig.tfl, for the stops whose lines and
    # stations the TfL disruptions are about (see AppConfig.share_tfl_with_notice_board).
    tfl: TflConfig = Field(default_factory=TflConfig)


class AppConfig(BaseModel):
    """The device's config, as served by auth-broker. Sections deliberately have no
    defaults: a response missing one is rejected (and the previous config kept) rather
    than turning into an empty config that blanks the screen."""

    interval: int = Field(gt=0, description="The interval in seconds between updates.")
    tfl: TflConfig
    spotify: SpotifyConfig
    weather: WeatherConfig
    glowmarkt: GlowmarktConfig
    notice_board: NoticeBoardConfig = Field(default_factory=NoticeBoardConfig)
    # Required but nullable: the broker sends null once the device is paired.
    pairing_code: str | None

    @model_validator(mode="before")
    @classmethod
    def share_tfl_with_notice_board(cls, data: Any) -> Any:
        """The notice board's TfL disruptions are for the arrivals' stops, so it gets a copy
        of the tfl section rather than the broker sending it twice."""
        if isinstance(data, dict) and "tfl" in data:
            notice_board = data.get("notice_board") or {}
            if isinstance(notice_board, NoticeBoardConfig):
                notice_board = notice_board.model_dump()
            data = data | {"notice_board": notice_board | {"tfl": data["tfl"]}}
        return data
