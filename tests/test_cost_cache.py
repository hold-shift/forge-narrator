import json

import pytest

from forge_narrator.cache import BlockCache
from forge_narrator.cost import (
    USD_PER_1K_CREDITS,
    cost_for_chars,
    credits_for_chars,
    estimate_manifest,
)
from forge_narrator.manifest import load_manifest


def _manifest(tmp_path, manifest_dict):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest_dict), encoding="utf-8")
    return load_manifest(p)


def test_credit_and_cost_math():
    assert credits_for_chars(1234) == 1234           # 1 char = 1 credit
    assert cost_for_chars(0) == 0
    assert cost_for_chars(1000) == pytest.approx(USD_PER_1K_CREDITS)


def test_estimate_all_uncached(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    cache = BlockCache(tmp_path / "cache")
    est = estimate_manifest(m, cache)
    assert est.total_blocks == 3
    assert est.cached_blocks == 0
    assert est.uncached_blocks == 3
    assert est.uncached_chars == est.total_chars
    assert est.credits == est.uncached_chars
    assert est.cost_usd > 0


def test_estimate_with_some_cached(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    cache = BlockCache(tmp_path / "cache")
    cache.put(m.blocks[0].synth_hash, b"\x00\x01", [])  # pretend block 0 is cached
    est = estimate_manifest(m, cache)
    assert est.cached_blocks == 1
    assert est.uncached_blocks == 2
    assert est.uncached_chars == m.blocks[1].billed_chars + m.blocks[2].billed_chars


def test_no_cache_disables_reads(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    cache = BlockCache(tmp_path / "cache", enabled=False)
    cache.put(m.blocks[0].synth_hash, b"\x00\x01", [])  # written...
    assert not cache.has(m.blocks[0].synth_hash)        # ...but reads disabled
    est = estimate_manifest(m, cache)
    assert est.cached_blocks == 0


def test_cache_roundtrip_mp3_and_marks(tmp_path):
    cache = BlockCache(tmp_path / "cache")
    h = "a" * 64
    marks = [{"word": "hi", "start": 0.0, "end": 0.5}]
    assert not cache.has(h)
    cache.put(h, b"hello", marks)
    assert cache.has(h)
    assert cache.get_mp3(h) == b"hello"
    assert cache.get_marks(h) == marks


def test_cache_requires_both_artifacts(tmp_path):
    """A block with audio but no marks is NOT cached (must re-synthesise)."""
    cache = BlockCache(tmp_path / "cache")
    h = "b" * 64
    cache.path_for(h).parent.mkdir(parents=True, exist_ok=True)
    cache.path_for(h).write_bytes(b"audio-only")  # mp3 present, marks missing
    assert not cache.has(h)
