"""whispermlx forced alignment (Spec B §6) — adapted from poc/align_mlx.py.

Instead of blind transcription, we do **transcript-constrained** alignment: we
already know the exact words (block plain text) and each block's time window (the
stitch offsets), so we hand whispermlx one alignment segment per block
``{start, end, text}`` and let wav2vec2 place the words within it. This is more
robust on proper nouns ("Nui Dat", ranks, etc.) than blind transcription, and it
yields an exact word→block mapping as a side effect (segment i ↔ block i).

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

    # Flatten in segment order, recording each block's word range. We rely on
    # aligned["segments"] preserving input order and count (whisperx does).
    marks: list[dict] = []
    ranges: list[tuple[int, int]] = []
    aligned_segments = aligned["segments"]
    for i in range(len(manifest.blocks)):
        start_idx = len(marks)
        if i < len(aligned_segments):
            marks.extend(_flatten_segment_words(aligned_segments[i]))
        ranges.append((start_idx, len(marks)))

    return AlignedDoc(marks=marks, block_word_ranges=ranges)
