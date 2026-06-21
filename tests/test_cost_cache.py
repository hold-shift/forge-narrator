import json

from forge_narrator.cache import BlockCache
from forge_narrator.cost import (
    USD_PER_MILLION_CHARS,
    cost_for_chars,
    estimate_manifest,
    format_duration,
)
from forge_narrator.manifest import load_manifest


def _manifest(tmp_path, manifest_dict):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest_dict), encoding="utf-8")
    return load_manifest(p)


def test_cost_math():
    assert cost_for_chars(1_000_000) == USD_PER_MILLION_CHARS
    assert cost_for_chars(0) == 0


def test_estimate_all_uncached(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    cache = BlockCache(tmp_path / "cache")
    est = estimate_manifest(m, cache)
    assert est.total_blocks == 3
    assert est.cached_blocks == 0
    assert est.uncached_blocks == 3
    assert est.uncached_chars == est.total_chars
    assert est.cost_usd > 0


def test_estimate_with_some_cached(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    cache = BlockCache(tmp_path / "cache")
    cache.put(m.blocks[0].hash, b"\x00\x01")  # pretend block 0 is cached
    est = estimate_manifest(m, cache)
    assert est.cached_blocks == 1
    assert est.uncached_blocks == 2
    assert est.uncached_chars == m.blocks[1].billed_chars + m.blocks[2].billed_chars


def test_no_cache_disables_reads(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    cache = BlockCache(tmp_path / "cache", enabled=False)
    cache.put(m.blocks[0].hash, b"\x00\x01")  # written...
    assert not cache.has(m.blocks[0].hash)     # ...but reads disabled
    est = estimate_manifest(m, cache)
    assert est.cached_blocks == 0


def test_cache_roundtrip(tmp_path):
    cache = BlockCache(tmp_path / "cache")
    h = "a" * 64
    assert not cache.has(h)
    cache.put(h, b"hello")
    assert cache.has(h)
    assert cache.get(h) == b"hello"


def test_format_duration():
    assert format_duration(30).endswith("s")
    assert "min" in format_duration(600)
    assert "h" in format_duration(7200)
