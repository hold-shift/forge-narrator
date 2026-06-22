"""char→word grouping + document-marks assembly (offset-shift monotonicity)."""

import json

import pytest

from forge_narrator.cache import BlockCache
from forge_narrator.manifest import load_manifest
from forge_narrator.marks import (
    assemble_document_marks,
    group_chars_to_words,
    shift_marks,
)
from forge_narrator.stitch import BlockOffset


def test_group_chars_to_words_basic():
    chars = list("hi there")
    starts = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
    ends = [0.1, 0.2, 0.25, 0.4, 0.5, 0.6, 0.7, 0.8]
    words = group_chars_to_words(chars, starts, ends)
    assert words == [
        {"word": "hi", "start": 0.0, "end": 0.2},
        {"word": "there", "start": 0.3, "end": 0.8},
    ]


def test_group_handles_leading_and_trailing_space():
    chars = list("  a ")
    starts = [0.0, 0.1, 0.2, 0.3]
    ends = [0.1, 0.2, 0.3, 0.4]
    words = group_chars_to_words(chars, starts, ends)
    assert words == [{"word": "a", "start": 0.2, "end": 0.3}]


def test_shift_marks():
    block = [{"word": "x", "start": 0.0, "end": 0.5},
             {"word": "y", "start": 0.6, "end": 1.0}]
    shifted = shift_marks(block, 10.0)
    assert shifted == [{"word": "x", "start": 10.0, "end": 10.5},
                       {"word": "y", "start": 10.6, "end": 11.0}]


def _manifest(tmp_path, manifest_dict):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest_dict), encoding="utf-8")
    return load_manifest(p)


def test_assemble_document_marks_offsets_and_ranges(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    cache = BlockCache(tmp_path / "cache")

    # Block-local marks (each starts at 0); counts: 2, 3, 1.
    block_marks = [
        [{"word": "A", "start": 0.0, "end": 0.4}, {"word": "Heading", "start": 0.5, "end": 0.9}],
        [{"word": "First", "start": 0.0, "end": 0.3}, {"word": "para", "start": 0.4, "end": 0.7},
         {"word": "here", "start": 0.8, "end": 1.0}],
        [{"word": "Second", "start": 0.0, "end": 0.6}],
    ]
    for b, bm in zip(m.blocks, block_marks):
        cache.put(b.hash, b"audio", bm)

    offsets = [BlockOffset(0, 0.0, 1.0), BlockOffset(1, 1.0, 2.2), BlockOffset(2, 2.2, 3.0)]

    marks, ranges = assemble_document_marks(m, cache, offsets)

    # Counts + ranges
    assert len(marks) == 6
    assert ranges == [(0, 2), (2, 5), (5, 6)]
    assert ranges[0][0] == 0 and ranges[-1][1] == len(marks)
    for (s, e), nxt in zip(ranges, ranges[1:]):
        assert e == nxt[0]  # contiguous

    # Shift applied: block 1's first word shifted by 1.0; block 2's by 2.2.
    assert marks[2] == {"word": "First", "start": 1.0, "end": 1.3}
    assert marks[5] == {"word": "Second", "start": 2.2, "end": 2.8}

    # Globally monotonic non-decreasing starts.
    assert all(marks[i]["start"] <= marks[i + 1]["start"] for i in range(len(marks) - 1))


def test_assemble_raises_when_block_marks_missing(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    cache = BlockCache(tmp_path / "cache")  # nothing cached
    offsets = [BlockOffset(i, float(i), float(i) + 1) for i in range(3)]
    with pytest.raises(FileNotFoundError, match="marks not in cache"):
        assemble_document_marks(m, cache, offsets)
