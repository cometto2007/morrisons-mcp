from morrisons_mcp.cache import ProductCache


async def test_delete_prefix_removes_only_old_bop_rows(tmp_path):
    cache = ProductCache(db_path=str(tmp_path / "cache.db"))
    try:
        await cache.set("bop:1", {"name": "Unknown"})
        await cache.set("bop_v3:1", {"name": "Real"})
        await cache.set("search:bop:x", [1])
        await cache.set("fallback_v2:salt", {"source": "USDA"})
        await cache.delete_prefix("bop:")
        await cache.delete_prefix("fallback")
        assert await cache.get("fallback_v2:salt") is None
        assert await cache.get("bop:1") is None
        assert await cache.get("bop_v3:1") == {"name": "Real"}
        assert await cache.get("search:bop:x") == [1]
    finally:
        await cache.close()


async def test_startup_drops_older_parser_rows_and_keeps_current(tmp_path, monkeypatch):
    from morrisons_mcp import morrison_client, server

    db = str(tmp_path / "cache.db")
    current = f"{morrison_client._BOP_CACHE_PREFIX}1"
    cache = ProductCache(db_path=db)
    await cache.set("bop_v2:1", {"name": "Stale parse"})
    await cache.set(current, {"name": "Current parse"})
    await cache.close()

    monkeypatch.setenv("CACHE_DB_PATH", db)
    async with server.app_lifespan(server.mcp):
        pass

    cache = ProductCache(db_path=db)
    try:
        assert await cache.get("bop_v2:1") is None
        assert await cache.get(current) == {"name": "Current parse"}
    finally:
        await cache.close()
