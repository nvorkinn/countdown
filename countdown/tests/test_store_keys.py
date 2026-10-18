"""The shared store's key names (countdown_core/store/keys.py)."""

import pytest

from countdown_core.store import keys


@pytest.mark.parametrize(("postcode", "place"), [("SW1A 1AA", "SW1A1AA"), ("N1 9GU", "N19GU"), ("0150", "0150")])
def test_a_place_is_its_postcode_without_the_space(postcode, place):
    assert keys.place(postcode) == place


def test_the_postcode_is_a_subscription_source():
    assert keys.subscription(keys.POSTCODE) == "v1:sub:postcode"
    assert keys.POSTCODE in keys.SOURCES


def test_the_met_office_warnings_are_by_place():
    assert keys.met_office("SE17 3LL") == "v1:notices:met-office:SE173LL"


@pytest.mark.parametrize("postcode", ["SE17 3LL", "SE173LL", "se17 3ll"])
def test_a_postcodes_flood_warnings_are_under_its_place(postcode):
    assert keys.floods(postcode) == "v1:notices:floods:SE173LL"
