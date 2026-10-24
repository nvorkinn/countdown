"""The parts of Glow's (Glowmarkt's) API responses the energy panel reads, as countdown-server stores them."""

from pydantic import BaseModel


class Readings(BaseModel):
    """A resource's readings: (time, kWh) pairs, each time a bucket's local wall-clock start as if it were UTC."""

    # Glow has sent the times both as epoch seconds and as ISO strings.
    data: list[tuple[int | float | str, float]]
