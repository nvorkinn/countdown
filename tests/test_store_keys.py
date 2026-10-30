"""The shared store's key names (countdown_shared/store/keys.py)."""

import pytest

from countdown_shared.store import keys


@pytest.mark.parametrize(("postcode", "place"), [("SW1A 1AA", "SW1A1AA"), ("N1 9GU", "N19GU"), ("0150", "0150")])
def test_a_place_is_its_postcode_without_the_space(postcode, place):
    assert keys.place(postcode) == place


def test_the_postcode_is_a_subscription_source():
    assert keys.subscription(keys.POSTCODE) == "v1:sub:postcode"
    assert keys.POSTCODE in keys.SOURCES


@pytest.mark.parametrize(
    ("postcode", "key"),
    [("SE17 3LL", "v1:location:SE173LL"), ("se17 3ll", "v1:location:SE173LL"), ("0150", "v1:location:0150")],
)
def test_a_postcodes_location_is_under_its_place(postcode, key):
    assert keys.location(postcode) == key


def test_the_met_office_warnings_are_by_place():
    assert keys.met_office("SE17 3LL") == "v1:notices:met-office:SE173LL"


@pytest.mark.parametrize("postcode", ["SE17 3LL", "SE173LL", "se17 3ll"])
def test_a_postcodes_flood_warnings_are_under_its_place(postcode):
    assert keys.floods(postcode) == "v1:notices:floods:SE173LL"


def test_a_devices_parcels_are_under_its_id():
    assert keys.parcels("abc") == "v1:parcels:abc"


def test_the_pace_key_is_per_source_and_item():
    assert keys.pace("developer", "abc") == "v1:pace:developer:abc"
    assert keys.developer("abc") == "v1:developer:abc"
    assert keys.DEVELOPER in keys.SOURCES
