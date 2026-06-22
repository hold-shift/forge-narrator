"""document.blocks.json assembly: fields, word ranges, highlightable."""

import json

import pytest

from forge_narrator.blocks_json import build_blocks_json
from forge_narrator.manifest import load_manifest
from forge_narrator.stitch import BlockOffset


def _manifest(tmp_path, manifest_dict):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest_dict), encoding="utf-8")
    return load_manifest(p)


def test_build_blocks_json_fields_and_monotonic(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    offsets = [BlockOffset(0, 0.0, 1.0), BlockOffset(1, 1.0, 2.0), BlockOffset(2, 2.0, 3.0)]
    word_ranges = [(0, 2), (2, 5), (5, 6)]

    blocks = build_blocks_json(m, offsets, word_ranges)
    assert len(blocks) == 3
    assert set(blocks[0]) == {
        "index", "type", "text", "word_start", "word_end",
        "time_start", "time_end", "highlightable",
    }
    assert blocks[0]["word_start"] == 0
    assert blocks[0]["type"] == "heading"
    # contiguous + monotonic across blocks
    assert blocks[0]["word_end"] == blocks[1]["word_start"]
    assert blocks[0]["time_end"] <= blocks[1]["time_start"] + 1e-9
    # heading/paragraph are highlightable
    assert all(b["highlightable"] for b in blocks)


def test_footnote_not_highlightable(tmp_path, manifest_dict):
    from forge_narrator.hashing import block_hash

    ssml = manifest_dict["blocks"][1]["ssml"]
    manifest_dict["blocks"][1]["type"] = "footnote"
    manifest_dict["blocks"][1]["hash"] = block_hash(
        ssml, manifest_dict["voice"], manifest_dict["model"]
    )
    m = _manifest(tmp_path, manifest_dict)
    offsets = [BlockOffset(i, float(i), float(i) + 1) for i in range(3)]
    word_ranges = [(0, 1), (1, 2), (2, 3)]

    blocks = build_blocks_json(m, offsets, word_ranges)
    assert blocks[0]["highlightable"] is True
    assert blocks[1]["highlightable"] is False   # footnote
    assert blocks[2]["highlightable"] is True


def test_length_mismatch_raises(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    offsets = [BlockOffset(0, 0.0, 1.0)]  # too few
    with pytest.raises(ValueError, match="mismatch"):
        build_blocks_json(m, offsets, [(0, 1)])
