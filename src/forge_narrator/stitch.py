"""Stitch block mp3s into document.mp3 and record the per-block offset table (§5).

Approach: decode every cached block mp3 to a canonical PCM WAV (fixed sample
rate / mono), concatenate the WAVs sample-accurately, then encode the whole thing
to ``document.mp3`` in a single pass. Offsets are accumulated from the exact WAV
durations, so the offset table sits on the same timeline as the final mp3 (no
per-segment mp3 padding drift accumulating across hundreds of blocks).

Each block's start offset shifts that block's word marks into document-global
time (see ``marks.assemble_document_marks``) and feeds ``document.blocks.json``.
Word→block mapping is by construction (per-block synthesis), not from these
offsets — they only provide the time axis.
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

        for block in manifest.blocks:
            src = cache.path_for(block.hash)
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
