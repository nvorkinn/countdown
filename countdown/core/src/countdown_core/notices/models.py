"""The Environment Agency's flood warnings, as the notice board reads them."""

from pydantic import BaseModel, Field


class FloodWarning(BaseModel):
    """One item from the Environment Agency's /flood-monitoring/id/floods."""

    description: str
    severity: str
    # 1 severe flood warning, 2 flood warning, 3 flood alert, 4 no longer in force.
    severity_level: int = Field(alias="severityLevel")


class FloodWarningsResponse(BaseModel):
    """The Environment Agency's /flood-monitoring/id/floods: the warnings in force."""

    items: list[FloodWarning] = []
