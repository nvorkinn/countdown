"""The TfL notices provider's cut of TfL's road disruptions: the severities worth a notice, and which are near.

The notices themselves are made by the board as it draws (notices/tfl.py).
"""

import json
from math import asin, cos, radians, sin, sqrt
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from countdown_core.tfl.models import RoadDisruption

ROAD_RADIUS_KM = 3
# TfL's names for the road disruptions worth a notice (/Road/Meta/Severities has them all), worst first.
ROAD_SEVERITIES = ("Severe", "Serious")


def is_near(disruption: RoadDisruption, latitude: float, longitude: float) -> bool:
    """Whether a road disruption is within ROAD_RADIUS_KM of a place; one TfL gives no point for isn't."""
    try:
        lon, lat = json.loads(disruption.point or "")
    except ValueError, TypeError:
        return False
    return _distance_km(latitude, longitude, lat, lon) <= ROAD_RADIUS_KM


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))
