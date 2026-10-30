import pytest

from _db.registry import RegistryRow
from _metrics.registry import RegistryCache, derive_pipeline_pairs


class FakeRegistryDriver:

    def __init__(self, signature: str, rows: list[RegistryRow]):
        self.signature = signature
        self.rows = rows
        self.signature_calls = 0
        self.rows_calls = 0

    async def fetch_registry_signature(self) -> str:
        self.signature_calls += 1
        return self.signature

    async def fetch_registry_rows(self) -> list[RegistryRow]:
        self.rows_calls += 1
        return self.rows


def test_derive_pipeline_pairs_is_one_to_one_p5():
    rows = [
        RegistryRow(pipeline_id=1, source_system="mysql", target_system="postgresql"),
        RegistryRow(pipeline_id=2, source_system="mysql", target_system="postgresql"),
    ]
    pairs = derive_pipeline_pairs(rows)
    assert len(pairs) == 2
    assert {p.pipeline_id for p in pairs} == {1, 2}


@pytest.mark.asyncio
async def test_first_refresh_always_fetches_rows():
    driver = FakeRegistryDriver("sig-a", [RegistryRow(1, "mysql", "postgresql")])
    cache = RegistryCache()
    result = await cache.refresh("server-1", driver)
    assert result.changed is True
    assert len(result.pairs) == 1
    assert driver.rows_calls == 1


@pytest.mark.asyncio
async def test_unchanged_signature_skips_the_expensive_row_fetch_p6():
    driver = FakeRegistryDriver("sig-a", [RegistryRow(1, "mysql", "postgresql")])
    cache = RegistryCache()
    await cache.refresh("server-1", driver)

    result = await cache.refresh("server-1", driver)
    assert result.changed is False
    assert driver.rows_calls == 1
    assert driver.signature_calls == 2


@pytest.mark.asyncio
async def test_changed_signature_triggers_a_fresh_fetch():
    driver = FakeRegistryDriver("sig-a", [RegistryRow(1, "mysql", "postgresql")])
    cache = RegistryCache()
    await cache.refresh("server-1", driver)

    driver.signature = "sig-b"
    driver.rows = [RegistryRow(1, "mysql", "postgresql"), RegistryRow(2, "mysql", "mssql")]
    result = await cache.refresh("server-1", driver)

    assert result.changed is True
    assert driver.rows_calls == 2
    assert len(result.pairs) == 2
    assert len(cache.pairs_for("server-1")) == 2


def test_derive_pipeline_pairs_platform_is_the_server_read_from_not_parsed_from_source_system_p23():
    rows = [RegistryRow(pipeline_id=1, source_system="totally-unrelated-label", target_system="also-unrelated")]
    pairs = derive_pipeline_pairs(rows, platform="postgresql")
    assert pairs[0].platform == "postgresql"


def test_derive_pipeline_pairs_platform_defaults_to_empty_string_for_callers_that_omit_it():
    rows = [RegistryRow(pipeline_id=1, source_system="mysql", target_system="postgresql")]
    pairs = derive_pipeline_pairs(rows)
    assert pairs[0].platform == ""


@pytest.mark.asyncio
async def test_registry_cache_refresh_threads_platform_through_to_pairs_p23():
    driver = FakeRegistryDriver("sig-a", [RegistryRow(1, "mysql", "postgresql")])
    cache = RegistryCache()
    result = await cache.refresh("server-1", driver, platform="mysql")
    assert result.pairs[0].platform == "mysql"
    assert cache.pairs_for("server-1")[0].platform == "mysql"


@pytest.mark.asyncio
async def test_force_refresh_next_bypasses_the_unchanged_signature_short_circuit_p89():
    driver = FakeRegistryDriver("sig-a", [RegistryRow(1, "mysql", "postgresql")])
    cache = RegistryCache()
    await cache.refresh("server-1", driver)
    assert driver.rows_calls == 1

    driver.rows = [RegistryRow(1, "mysql", "postgresql"), RegistryRow(9, "mysql", "mssql")]
    cache.force_refresh_next("server-1")
    result = await cache.refresh("server-1", driver)

    assert result.changed is True
    assert driver.rows_calls == 2
    assert len(result.pairs) == 2


@pytest.mark.asyncio
async def test_force_refresh_next_only_applies_to_the_very_next_call_p89():
    driver = FakeRegistryDriver("sig-a", [RegistryRow(1, "mysql", "postgresql")])
    cache = RegistryCache()
    await cache.refresh("server-1", driver)

    cache.force_refresh_next("server-1")
    await cache.refresh("server-1", driver)
    assert driver.rows_calls == 2

    result = await cache.refresh("server-1", driver)
    assert result.changed is False
    assert driver.rows_calls == 2


@pytest.mark.asyncio
async def test_servers_are_cached_independently():
    driver_a = FakeRegistryDriver("sig-a", [RegistryRow(1, "mysql", "postgresql")])
    driver_b = FakeRegistryDriver("sig-b", [RegistryRow(2, "mssql", "mysql")])
    cache = RegistryCache()

    await cache.refresh("server-a", driver_a)
    await cache.refresh("server-b", driver_b)

    assert len(cache.pairs_for("server-a")) == 1
    assert len(cache.pairs_for("server-b")) == 1
    assert cache.pairs_for("server-a")[0].pipeline_id == 1
    assert cache.pairs_for("server-b")[0].pipeline_id == 2
