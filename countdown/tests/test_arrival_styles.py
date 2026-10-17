import pytest
from tfl_factory import tfl_panel

from countdown_core.tfl.models import BusArrival, SingleStopPoint

STOP = SingleStopPoint(
    naptanId="490000123W",
    commonName="Elephant & Castle",
    modes=["bus"],
    additionalProperties=[],
    stopType="NaptanPublicBusCoachTram",
    stopLetter="W",
)


def _buses(count: int, line: str = "36") -> list[BusArrival]:
    return [
        BusArrival(naptanId="x", lineName=f"{line}{i}", timeToStation=60 * i, modeName="bus", destinationName="Peckham")
        for i in range(count)
    ]


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_a_stop_with_nothing_due_draws_just_its_header(style):
    img = tfl_panel(STOP, [], style).render(262, 185)

    assert img.size == (262, 185)
    _, top, _, bottom = img.getchannel("A").getbbox()
    assert top < 10
    if style != "card":  # the card's outline runs the full height
        assert bottom < 40


@pytest.mark.parametrize("style", ["airy", "hero", "card"])
def test_more_departures_than_fit_stop_at_the_bottom_edge(style):
    img = tfl_panel(STOP, _buses(20), style).render(262, 185)

    assert img.size == (262, 185)
    assert img.getchannel("A").getbbox()[3] <= 185


@pytest.mark.parametrize("style", ["hero", "card"])
def test_a_long_route_number_shrinks_to_fit_its_badge(style):
    img = tfl_panel(STOP, _buses(2, line="SL10X"), style).render(262, 185)

    assert img.getchannel("A").getbbox() is not None
