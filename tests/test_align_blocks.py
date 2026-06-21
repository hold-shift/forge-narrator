"""Alignment word-ordering invariant + blocks.json assembly.

We can't run the real MLX aligner here (no GPU model / it's slow / needs audio),
so we inject a fake ``whispermlx`` that echoes the input segments with fabricated
word timings. This still exercises the part that matters: that marks come out in
block order and that each block's word range maps correctly into marks.json.
"""

import json
import sys
import types

import pytest

from forge_narrator.align import AlignedDoc, _flatten_segment_words, align_document
from forge_narrator.blocks_json import build_blocks_json
from forge_narrator.manifest import load_manifest
from forge_narrator.stitch import BlockOffset


def _manifest(tmp_path, manifest_dict):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest_dict), encoding="utf-8")
    return load_manifest(p)


def _install_fake_whispermlx(monkeypatch):
    """Fake aligner: each segment's words = its text split, timed within window."""
    fake = types.ModuleType("whispermlx")

    def load_align_model(language_code, device):
        return ("model", {"meta": True})

    def align(segments, model, metadata, audio, device, return_char_alignments):
        out_segments = []
        for seg in segments:
            words = seg["text"].split()
            span = (seg["end"] - seg["start"]) / max(1, len(words))
            wlist = []
            for i, w in enumerate(words):
                s = seg["start"] + i * span
                wlist.append({"word": w, "start": s, "end": s + span * 0.9})
            out_segments.append({"words": wlist})
        return {"segments": out_segments}

    fake.load_align_model = load_align_model
    fake.align = align
    monkeypatch.setitem(sys.modules, "whispermlx", fake)


def _offsets(manifest):
    offs, cursor = [], 0.0
    for b in manifest.blocks:
        dur = max(1.0, len(b.text) * 0.05)
        offs.append(BlockOffset(b.index, round(cursor, 3), round(cursor + dur, 3)))
        cursor += dur
    return offs


def test_flatten_skips_incomplete_words():
    seg = {"words": [
        {"word": "a", "start": 0.0, "end": 0.1},
        {"word": "b", "start": None, "end": 0.3},   # dropped
        {"word": "c"},                                # dropped
        {"word": "d", "start": 0.4, "end": 0.5},
    ]}
    out = _flatten_segment_words(seg)
    assert [w["word"] for w in out] == ["a", "d"]


def test_alignment_ordering_invariant(tmp_path, manifest_dict, monkeypatch):
    _install_fake_whispermlx(monkeypatch)
    m = _manifest(tmp_path, manifest_dict)
    offsets = _offsets(m)

    aligned = align_document(tmp_path / "document.mp3", m, offsets)

    # One range per block; ranges are contiguous and cover all marks exactly.
    assert len(aligned.block_word_ranges) == len(m.blocks)
    assert aligned.block_word_ranges[0][0] == 0
    assert aligned.block_word_ranges[-1][1] == len(aligned.marks)
    for (s, e), nxt in zip(aligned.block_word_ranges, aligned.block_word_ranges[1:]):
        assert e == nxt[0]  # no gaps, no overlap

    # Each block's word count matches its plain-text word count (fake tokeniser).
    for block, (s, e) in zip(m.blocks, aligned.block_word_ranges):
        assert (e - s) == len(block.text.split())

    # The marks for block 1 are exactly its words, in order.
    s, e = aligned.block_word_ranges[1]
    assert [w["word"] for w in aligned.marks[s:e]] == m.blocks[1].text.split()


def test_build_blocks_json(tmp_path, manifest_dict, monkeypatch):
    _install_fake_whispermlx(monkeypatch)
    m = _manifest(tmp_path, manifest_dict)
    offsets = _offsets(m)
    aligned = align_document(tmp_path / "document.mp3", m, offsets)

    blocks = build_blocks_json(m, offsets, aligned)
    assert len(blocks) == 3
    first = blocks[0]
    assert set(first) == {
        "index", "type", "text", "word_start", "word_end", "time_start", "time_end"
    }
    assert first["word_start"] == 0
    assert first["type"] == "heading"
    # time + word spans are monotonic across blocks
    assert blocks[0]["time_end"] <= blocks[1]["time_start"] + 1e-9
    assert blocks[0]["word_end"] == blocks[1]["word_start"]


def test_build_blocks_json_length_mismatch(tmp_path, manifest_dict):
    m = _manifest(tmp_path, manifest_dict)
    bad = AlignedDoc(marks=[], block_word_ranges=[(0, 0)])  # too few ranges
    with pytest.raises(ValueError, match="mismatch"):
        build_blocks_json(m, _offsets(m), bad)
