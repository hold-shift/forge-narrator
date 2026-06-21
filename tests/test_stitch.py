"""Real ffmpeg stitch integration test using the POC mp3 fixture."""

import json
import shutil

import pytest

from forge_narrator.cache import BlockCache
from forge_narrator.ffmpeg import probe_duration, require_ffmpeg
from forge_narrator.manifest import load_manifest
from forge_narrator.stitch import stitch

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _two_block_manifest(tmp_path, poc_mp3):
    """A 2-block manifest whose cache entries are both the POC mp3."""
    from forge_narrator.hashing import block_hash

    voice, engine = "Brian", "generative"
    ssml0 = "<speak>Block zero.</speak>"
    ssml1 = "<speak>Block one.</speak>"
    data = {
        "version": 1, "slug": "stitch-test", "voice": voice, "engine": engine,
        "blocks": [
            {"index": 0, "type": "heading", "text": "Block zero",
             "ssml": ssml0, "hash": block_hash(ssml0, voice, engine)},
            {"index": 1, "type": "paragraph", "text": "Block one",
             "ssml": ssml1, "hash": block_hash(ssml1, voice, engine)},
        ],
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    m = load_manifest(p)

    cache = BlockCache(tmp_path / "cache")
    audio = poc_mp3.read_bytes()
    for b in m.blocks:
        cache.put(b.hash, audio)
    return m, cache


def test_stitch_produces_mp3_and_offsets(tmp_path, poc_mp3):
    require_ffmpeg()
    m, cache = _two_block_manifest(tmp_path, poc_mp3)
    out_mp3 = tmp_path / "out" / "document.mp3"

    offsets = stitch(m, cache, out_mp3)

    assert out_mp3.exists() and out_mp3.stat().st_size > 0
    assert len(offsets) == 2

    # Offsets are contiguous and monotonic.
    assert offsets[0].time_start == 0.0
    assert offsets[0].time_end == offsets[1].time_start
    assert offsets[1].time_end > offsets[1].time_start

    # Two copies of the same clip → stitched duration ≈ 2× a single clip,
    # and ≈ the final block's time_end.
    single = probe_duration(shutil_which_ffprobe(), str(poc_mp3))
    total = probe_duration(shutil_which_ffprobe(), str(out_mp3))
    assert total == pytest.approx(2 * single, rel=0.05)
    assert offsets[-1].time_end == pytest.approx(total, rel=0.03)


def shutil_which_ffprobe():
    return shutil.which("ffprobe")
