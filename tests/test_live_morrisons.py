"""Optional live check against the real Morrisons API. Skipped by default.

Run with: MORRISONS_LIVE=1 pytest tests/test_live_morrisons.py
Makes three requests (session cookie, one product, one dead product).
"""
import os

import pytest

from morrisons_mcp.cache import ProductCache
from morrisons_mcp.morrison_client import MorrisonClient, ProductNotFoundError

pytestmark = pytest.mark.skipif(
    os.getenv("MORRISONS_LIVE") != "1", reason="live Morrisons check; set MORRISONS_LIVE=1"
)


async def test_live_product_and_dead_product(tmp_path):
    cache = ProductCache(db_path=str(tmp_path / "cache.db"))
    client = MorrisonClient(cache=cache)
    try:
        # Morrisons British Whole Chicken Medium 1.45kg
        d = await client.get_product_detail("108444543")
        assert d.found and d.name
        assert d.net_quantity is not None and d.net_quantity.unit == "g"
        n = d.nutrition_per_100g
        assert n is not None and n.basis == "100g"
        assert n.energy_kcal and n.protein_g is not None and n.salt_g is not None

        with pytest.raises(ProductNotFoundError):
            await client.get_product_detail("999999999999")
    finally:
        await client.close()
        await cache.close()
