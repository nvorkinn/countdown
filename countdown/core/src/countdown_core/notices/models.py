"""The Environment Agency's flood warnings and flood areas, as the floods provider and the notice board read them."""

from pydantic import BaseModel, ConfigDict, Field


class FloodWarning(BaseModel):
    """One item from the Environment Agency's /flood-monitoring/id/floods: the parts the provider and board read."""

    # Written back (to the store) in the EA's own names, so a stored warning reads as the EA sent it.
    model_config = ConfigDict(serialize_by_alias=True)

    description: str
    severity: str
    # 1 severe flood warning, 2 flood warning, 3 flood alert, 4 no longer in force.
    severity_level: int = Field(alias="severityLevel")
    # The flood area it's for (the area's `notation`), which says where it is.
    flood_area_id: str = Field(alias="floodAreaID")


class FloodWarningsResponse(BaseModel):
    """The Environment Agency's /flood-monitoring/id/floods: the warnings in force."""

    items: list[FloodWarning] = []


class FloodArea(BaseModel):
    """One item from the Environment Agency's /flood-monitoring/id/floodAreas: an area a warning can be for."""

    # The id a warning names it by (its floodAreaID).
    notation: str


class FloodAreasResponse(BaseModel):
    """The Environment Agency's /flood-monitoring/id/floodAreas."""

    items: list[FloodArea] = []
