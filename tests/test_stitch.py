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

    voice, model = "fjnwTZkKtQOJaYzGLa6n", "eleven_v3"
    ssml0 = "Block zero."
    ssml1 = "Block one."
    data = {
        "version": 1, "slug": "stitch-test", "voice": voice, "model": model,
        "blocks": [
            {"index": 0, "type": "heading", "text": "Block zero",
             "ssml": ssml0, "hash": block_hash(ssml0, voice, model)},
            {"index": 1, "type": "paragraph", "text": "Block one",
             "ssml": ssml1, "hash": block_hash(ssml1, voice, model)},
        ],
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    m = load_manifest(p)

    cache = BlockCache(tmp_path / "cache")
    audio = poc_mp3.read_bytes()
    for b in m.blocks:
        cache.put(b.synth_hash, audio, [])  # marks not needed for the stitch test
    return m, cache


def test_stitch_produces_mp3_and_offsets(tmp_path, poc_mp3):
    require_ffmpeg()
    m, cache = _two_block_manifest(tmp_path, poc_mp3)
    out_mp3 = tmp_path / "out" / "document.mp3"

    offsets = stitch(m, cache, out_mp3)

    assert out_mp3.exists() and out_mp3.stat().st_size > 0
    assert len(offsets) == 2

    # A deterministic silence seam separates the heading from the paragraph, so
    # the blocks are NOT contiguous — there's a gap of exactly the seam size.
    from forge_narrator.stitch import _seam_silence
    gap = _seam_silence("heading", "paragraph")
    assert offsets[0].time_start == 0.0
    assert offsets[1].time_start == pytest.approx(offsets[0].time_end + gap, abs=0.05)
    assert offsets[1].time_end > offsets[1].time_start

    # Two copies of the same clip + the seam → ≈ 2× a single clip + gap,
    # and the final block ends at ≈ the total duration.
    single = probe_duration(shutil_which_ffprobe(), str(poc_mp3))
    total = probe_duration(shutil_which_ffprobe(), str(out_mp3))
    assert total == pytest.approx(2 * single + gap, abs=0.2)
    assert offsets[-1].time_end == pytest.approx(total, abs=0.1)


def shutil_which_ffprobe():
    return shutil.which("ffprobe")


def test_seam_silence_rules():
    from forge_narrator.stitch import _seam_silence
    # Larger separation before a heading/footnote than between paragraphs.
    assert _seam_silence("paragraph", "heading") > _seam_silence("paragraph", "paragraph")
    assert _seam_silence("paragraph", "footnote") > _seam_silence("paragraph", "paragraph")
    # A beat after a heading, before the body.
    assert _seam_silence("heading", "paragraph") > 0
    assert _seam_silence("paragraph", "paragraph") > 0
