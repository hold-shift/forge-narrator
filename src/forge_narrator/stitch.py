"""Stitch block mp3s into document.mp3 and record the per-block offset table (§5).

Approach: decode every cached block mp3 to a canonical PCM WAV (fixed sample
rate / mono), concatenate the WAVs sample-accurately, then encode the whole thing
to ``document.mp3`` in a single pass. Offsets are accumulated from the exact WAV
durations, so the offset table sits on the same timeline as the final mp3 (no
per-segment mp3 padding drift accumulating across hundreds of blocks).

A deterministic **silence seam** is inserted between blocks for pacing, sized by
the block types on each side (``_seam_silence``) — a reliable, tunable beat before
headings, after headings, and between paragraphs, without relying on ElevenLabs v3
``[pause]`` tags (which vary 0.2–1.5s and leak into the alignment). The silence
carries no marks, so the highlight track stays clean and NotebookForge can stay
pure plain text.

Each block's start offset (now including the accumulated seam silence) shifts that
block's word marks into document-global time (see ``marks.assemble_document_marks``)
and feeds ``document.blocks.json``. Word→block mapping is by construction (per-block
synthesis), not from these offsets — they only provide the time axis.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .cache import BlockCache
from .ffmpeg import probe_duration, require_ffmpeg, run
from .manifest import Manifest

# Canonical intermediate PCM format. 24 kHz mono is ample for speech.
_SR = 24000

# Deterministic pacing: silence inserted at block seams, sized by the block types
# on each side (seconds). Reliable and tunable — unlike v3 `[pause]` tags, which
# vary 0.2–1.5s and leak into the alignment. Pure silence carries no marks.
_SEAM_BEFORE_HEADING_OR_FOOTNOTE = 0.8   # clear separation before a heading/footnote
_SEAM_AFTER_HEADING_OR_FOOTNOTE = 0.6    # beat after a heading/footnote, before the body
_SEAM_BETWEEN_PARAGRAPHS = 0.5           # breath between paragraphs


def _seam_silence(prev_type: str, next_type: str) -> float:
    """Silence (seconds) to insert between a ``prev_type`` block and a ``next_type``."""
    if next_type in ("heading", "footnote"):
        return _SEAM_BEFORE_HEADING_OR_FOOTNOTE
    if prev_type in ("heading", "footnote"):
        return _SEAM_AFTER_HEADING_OR_FOOTNOTE
    return _SEAM_BETWEEN_PARAGRAPHS


@dataclass(frozen=True)
class BlockOffset:
    index: int
    time_start: float
    time_end: float


def stitch(manifest: Manifest, cache: BlockCache, out_mp3: Path) -> list[BlockOffset]:
    """Concatenate block audio → ``out_mp3``; return per-block time offsets."""
    ffmpeg, ffprobe = require_ffmpeg()
    out_mp3 = Path(out_mp3)
    out_mp3.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="forge-stitch-") as tmp:
        tmpdir = Path(tmp)
        wavs: list[Path] = []
        offsets: list[BlockOffset] = []
        cursor = 0.0
        silence: dict[str, Path] = {}  # duration → reusable silence wav

        def silence_wav(seconds: float) -> Path:
            key = f"{seconds:.3f}"
            if key not in silence:
                sp = tmpdir / f"silence_{key}.wav"
                run([
                    ffmpeg, "-nostdin", "-y", "-f", "lavfi",
                    "-i", f"anullsrc=r={_SR}:cl=mono", "-t", key,
                    "-c:a", "pcm_s16le", str(sp),
                ])
                silence[key] = sp
            return silence[key]

        prev_type = None
        for block in manifest.blocks:
            if prev_type is not None:
                gap = _seam_silence(prev_type, block.type)
                if gap > 0:
                    wavs.append(silence_wav(gap))
                    cursor += gap
            src = cache.path_for(block.synth_hash)
            if not src.exists():
                raise FileNotFoundError(
                    f"block {block.index} not in cache ({src}); synthesise first"
                )
            wav = tmpdir / f"{block.index:06d}.wav"
            run([
                ffmpeg, "-nostdin", "-y", "-i", str(src),
                "-ac", "1", "-ar", str(_SR),
                "-c:a", "pcm_s16le", str(wav),
            ])
            dur = probe_duration(ffprobe, str(wav))
            offsets.append(BlockOffset(
                index=block.index,
                time_start=round(cursor, 3),
                time_end=round(cursor + dur, 3),
            ))
            cursor += dur
            wavs.append(wav)
            prev_type = block.type

        # Sample-accurate concat of the PCM WAVs, then single-pass mp3 encode.
        concat_list = tmpdir / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{w.as_posix()}'\n" for w in wavs), encoding="utf-8"
        )
        # CONSTANT bitrate (not VBR -q:a). The output is a long, seekable narration
        # file; browsers seek MP3 by assuming constant bitrate, so VBR makes
        # `audio.currentTime = X` land at the wrong byte (error grows with
        # position). CBR keeps seeks/scrubbing sample-accurate.
        run([
            ffmpeg, "-nostdin", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c:a", "libmp3lame", "-b:a", "96k", "-ar", str(_SR), "-ac", "1",
            str(out_mp3),
        ])

    return offsets
