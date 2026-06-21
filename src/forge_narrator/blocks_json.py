"""Emit document.blocks.json — the player's render source (Spec B §7).

Each entry::

    { "index": 0, "type": "heading", "text": "...",
      "word_start": 0, "word_end": 7,        # [start, end) into marks.json
      "time_start": 0.0, "time_end": 3.1 }   # from the stitch offsets

``word_*`` index into ``document.marks.json`` (the running word index — the sacred
invariant from the Overview); ``time_*`` come from the stitch offsets. The player
renders text from this file and highlights words via the marks.
"""

from __future__ import annotations

from .align import AlignedDoc
from .manifest import Manifest
from .stitch import BlockOffset


def build_blocks_json(
    manifest: Manifest,
    offsets: list[BlockOffset],
    aligned: AlignedDoc,
) -> list[dict]:
    if not (len(manifest.blocks) == len(offsets) == len(aligned.block_word_ranges)):
        raise ValueError(
            "blocks/offsets/word-ranges length mismatch: "
            f"{len(manifest.blocks)} / {len(offsets)} / "
            f"{len(aligned.block_word_ranges)}"
        )

    out = []
    for block, off, (w_start, w_end) in zip(
        manifest.blocks, offsets, aligned.block_word_ranges
    ):
        out.append({
            "index": block.index,
            "type": block.type,
            "text": block.text,
            "word_start": w_start,
            "word_end": w_end,
            "time_start": off.time_start,
            "time_end": off.time_end,
        })
    return out
