"""What the bins provider stores for a household's bin collection days, and the renderer reads back.

The value is the store contract's StoredBinCollections (countdown-api's store.yaml: `bins`), re-exported here, under
v1:bins:<SHA1 of the address>. An address is a member of v1:sub:address, "<street>|<house_number>|<postcode>|<city>",
which `address_member` makes from a notice board's config.
"""

from countdown_api.models import BinCollection, NoticeBoardAddress, StoredBinCollections

__all__ = ["BinCollection", "StoredBinCollections", "address_member"]


def address_member(address: NoticeBoardAddress, postcode: str) -> str:
    """Return `address`'s v1:sub:address member, as the broker writes it: its fields and `postcode`, joined by |."""
    return f"{address.street}|{address.house_number}|{postcode}|{address.city}"
