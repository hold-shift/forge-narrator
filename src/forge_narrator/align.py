"""whispermlx forced alignment (Spec B §6) — adapted from poc/align_mlx.py.

Instead of blind transcription, we do **transcript-constrained** alignment: we
already know the exact words (block plain text) and each block's time window (the
stitch offsets), so we hand whispermlx one alignment segment per block
``{start, end, text}`` and let wav2vec2 place the words within it. This is more
robust on proper nouns ("Nui Dat", ranks, etc.) than blind transcription — e.g. it
keeps "roll books" as two words where blind transcription merged "rollbooks".

NOTE: whispermlx.align **re-segments internally** (it splits each input segment
into sentence-sized output segments), so the returned ``segments`` do NOT map 1:1
to input blocks. We therefore flatten ALL returned words in time order and assign
each to a block by the **stitch-offset time window** — robust because the SSML
``<break>`` between blocks puts the boundary in silence, where no word lands.

Output marks are byte-compatible with the POC (`poc/sample.marks.mlx.json`):
a flat list of ``{"word", "start", "end"}`` in seconds — so the POC player
validates generator output unchanged.

whispermlx runs on the M2 GPU via MLX automatically; its ``device="cpu"`` arg is
vestigial (Spec B §2.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .manifest import Manifest
from .stitch import BlockOffset


@dataclass
class AlignedDoc:
    marks: list[dict]                       # flat [{word, start, end}], seconds
    block_word_ranges: list[tuple[int, int]]  # per block: [word_start, word_end)


def _flatten_segment_words(seg: dict) -> list[dict]:
    """Extract usable {word,start,end} from one aligned segment (POC format)."""
    out = []
    for w in seg.get("words", []):
        if "start" in w and "end" in w and w["start"] is not None and w["end"] is not None:
            out.append({
                "word": w["word"],
                "start": round(float(w["start"]), 3),
                "end": round(float(w["end"]), 3),
            })
    return out


def _assign_words_to_blocks(
    marks: list[dict], offsets: list[BlockOffset]
) -> list[tuple[int, int]]:
    """Partition time-ordered ``marks`` into per-block [start, end) index ranges.

    A word belongs to the block whose time window contains its midpoint. Blocks
    are contiguous and ordered, so this is a single forward pass; the last block
    absorbs any trailing words (guards against a final word timed a hair past the
    last offset).
    """
    ranges: list[tuple[int, int]] = []
    mi = 0
    n = len(marks)
    for bi, off in enumerate(offsets):
        start = mi
        if bi == len(offsets) - 1:
            mi = n  # last block takes the remainder
        else:
            while mi < n and (marks[mi]["start"] + marks[mi]["end"]) / 2 < off.time_end:
                mi += 1
        ranges.append((start, mi))
    return ranges


def align_document(
    audio_path: Path,
    manifest: Manifest,
    offsets: list[BlockOffset],
    *,
    model: str = "small.en",
    language: str = "en",
) -> AlignedDoc:
    """Force-align ``audio_path`` against the known block transcript.

    Returns the flat marks list plus, for each block, the [start, end) range of
    word indices it owns in that list — the sacred word-ordering invariant made
    explicit. The order of words in the SSML, the mp3, the marks and the blocks
    is identical by construction (one segment per block, in manifest order).
    """
    try:
        import whispermlx
    except ImportError as e:
        raise RuntimeError(
            "whispermlx not installed — needed for alignment "
            "(pip install whispermlx, Python 3.11 on Apple Silicon)"
        ) from e

    audio_path = str(audio_path)

    # One alignment segment per block, using the stitch offsets as the window and
    # the block plain text as the known transcript.
    segments = [
        {"start": off.time_start, "end": off.time_end, "text": block.text}
        for block, off in zip(manifest.blocks, offsets)
    ]

    align_model, metadata = whispermlx.load_align_model(
        language_code=language, device="cpu"
    )
    aligned = whispermlx.align(
        segments, align_model, metadata, audio_path, device="cpu",
        return_char_alignments=False,
    )

    # Flatten ALL returned segments in time order (whispermlx re-segments, so the
    # returned segments don't correspond to input blocks). Then assign words to
    # blocks by the stitch-offset time windows.
    marks: list[dict] = []
    for seg in aligned["segments"]:
        marks.extend(_flatten_segment_words(seg))

    ranges = _assign_words_to_blocks(marks, offsets)
    return AlignedDoc(marks=marks, block_word_ranges=ranges)
