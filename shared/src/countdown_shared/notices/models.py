"""The Environment Agency's flood warnings and flood areas, as the floods provider and the notice board read them.

A warning, and the list of them (/flood-monitoring/id/floods), are the store contract's (countdown-api's FloodWarning
and FloodWarningsResponse, which `v1:notices:floods:<place>` holds), re-exported here. The flood areas are only the
floods provider's, to pick out the warnings near each postcode.
"""

from pydantic import BaseModel

from countdown_api.models import FloodWarning, FloodWarningsResponse

__all__ = ["FloodArea", "FloodAreasResponse", "FloodWarning", "FloodWarningsResponse"]


class FloodArea(BaseModel):
    """One item from the Environment Agency's /flood-monitoring/id/floodAreas: an area a warning can be for."""

    # The id a warning names it by (its floodAreaID).
    notation: str


class FloodAreasResponse(BaseModel):
    """The Environment Agency's /flood-monitoring/id/floodAreas."""

    items: list[FloodArea] = []
