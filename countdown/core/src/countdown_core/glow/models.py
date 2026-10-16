"""The parts of Glow's (Glowmarkt's) API responses the energy panel reads."""

from pydantic import BaseModel


class Resource(BaseModel):
    """One of an entity's data streams, such as its electricity consumption."""

    name: str
    resourceId: str  # noqa: N815 -- Glow's own JSON field name


class Entity(BaseModel):
    """A meter (a "virtual entity" to Glow) and the data streams it offers."""

    resources: list[Resource]


class Readings(BaseModel):
    """A resource's readings: (time, kWh) pairs."""

    data: list[tuple[int, float]]
