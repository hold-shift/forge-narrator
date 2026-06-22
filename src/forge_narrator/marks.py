"""Word marks — char→word grouping and document-marks assembly (Spec B §6).

There is no alignment stage. ElevenLabs `/with-timestamps` returns per-character
start/end times alongside each block's audio; ``group_chars_to_words`` collapses
those into block-local word marks (the exact logic from
``poc/elevenlabs_probe.py``). ``assemble_document_marks`` then concatenates every
block's cached marks in order, shifting each by that block's stitch offset, to
produce the flat document-global ``[{word,start,end}]`` list the player consumes —
byte-compatible with the POC format.

Word→block mapping is **by construction**: each block's marks come from that
block's own synthesis, so the per-block word ranges fall out of the concatenation
(no time-window heuristic, no boundary guessing).
"""

from __future__ import annotations

from .cache import BlockCache
from .manifest import Manifest
from .stitch import BlockOffset


def group_chars_to_words(
    characters: list[str],
    starts: list[float],
    ends: list[float],
) -> list[dict]:
    """Collapse per-character alignment into ``[{word, start, end}]``.

    Split on whitespace; a word's start is its first char's start and its end is
    its last char's end. Times are rounded to ms. (Seed: probe.group_chars_to_words.)
    """
    words: list[dict] = []
    cur = ""
    cur_start = None
    prev_end = 0.0
    for ch, st, en in zip(characters, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": round(cur_start, 3),
                              "end": round(prev_end, 3)})
                cur = ""
                cur_start = None
        else:
            if not cur:
                cur_start = st
            cur += ch
            prev_end = en
    if cur:
        words.append({"word": cur, "start": round(cur_start, 3),
                      "end": round(prev_end, 3)})
    return words


def shift_marks(block_marks: list[dict], offset: float) -> list[dict]:
    """Return block-local marks shifted into document time by ``offset`` seconds."""
    return [
        {"word": m["word"],
         "start": round(m["start"] + offset, 3),
         "end": round(m["end"] + offset, 3)}
        for m in block_marks
    ]


def assemble_document_marks(
    manifest: Manifest,
    cache: BlockCache,
    offsets: list[BlockOffset],
) -> tuple[list[dict], list[tuple[int, int]]]:
    """Build the flat document marks + per-block ``[word_start, word_end)`` ranges.

    For each block in order: load its cached block-local marks, shift them by the
    block's stitch offset, append. The running word index gives each block's range
    — the sacred word-ordering invariant, exact by construction.
    """
    if len(manifest.blocks) != len(offsets):
        raise ValueError(
            f"blocks/offsets length mismatch: {len(manifest.blocks)} / {len(offsets)}"
        )

    marks: list[dict] = []
    ranges: list[tuple[int, int]] = []
    for block, off in zip(manifest.blocks, offsets):
        block_marks = cache.get_marks(block.hash)
        if block_marks is None:
            raise FileNotFoundError(
                f"block {block.index} marks not in cache; synthesise first"
            )
        start = len(marks)
        marks.extend(shift_marks(block_marks, off.time_start))
        ranges.append((start, len(marks)))
    return marks, ranges
