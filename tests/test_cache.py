from morrisons_mcp.cache import ProductCache


async def test_delete_prefix_removes_only_old_bop_rows(tmp_path):
    cache = ProductCache(db_path=str(tmp_path / "cache.db"))
    try:
        await cache.set("bop:1", {"name": "Unknown"})
        await cache.set("bop_v2:1", {"name": "Real"})
        await cache.set("search:bop:x", [1])
        await cache.set("fallback_v2:salt", {"source": "USDA"})
        await cache.delete_prefix("bop:")
        await cache.delete_prefix("fallback")
        assert await cache.get("fallback_v2:salt") is None
        assert await cache.get("bop:1") is None
        assert await cache.get("bop_v2:1") == {"name": "Real"}
        assert await cache.get("search:bop:x") == [1]
    finally:
        await cache.close()
