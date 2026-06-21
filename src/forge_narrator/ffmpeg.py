"""ffmpeg / ffprobe helpers. ffmpeg is a HARD dependency (Spec B §2.1)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

# Canonical intermediate PCM format for sample-accurate concatenation.
_SR = 24000


class FfmpegError(Exception):
    pass


def require_ffmpeg() -> tuple[str, str]:
    """Return ``(ffmpeg, ffprobe)`` paths or raise with install guidance."""
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    missing = [n for n, p in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)) if not p]
    if missing:
        raise FfmpegError(
            f"{' and '.join(missing)} not found on PATH. "
            "Install via Homebrew: brew install ffmpeg"
        )
    return ffmpeg, ffprobe


def run(cmd: list[str]) -> str:
    """Run a command, raising ``FfmpegError`` with stderr on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise FfmpegError(
            f"{cmd[0]} failed (exit {proc.returncode}):\n{proc.stderr.strip()}"
        )
    return proc.stdout


def concat_mp3_bytes(parts: list[bytes]) -> bytes:
    """Concatenate several mp3 byte blobs into one mp3 (sample-accurate via PCM).

    Used to reassemble a block whose SSML had to be split across multiple Polly
    calls. Decodes each part to canonical PCM, concatenates, re-encodes once.
    """
    if len(parts) == 1:
        return parts[0]
    ffmpeg, _ = require_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="forge-concat-") as tmp:
        tmpdir = Path(tmp)
        wavs = []
        for i, blob in enumerate(parts):
            mp3 = tmpdir / f"{i:04d}.mp3"
            mp3.write_bytes(blob)
            wav = tmpdir / f"{i:04d}.wav"
            run([ffmpeg, "-nostdin", "-y", "-i", str(mp3),
                 "-ac", "1", "-ar", str(_SR), "-c:a", "pcm_s16le", str(wav)])
            wavs.append(wav)
        listing = tmpdir / "concat.txt"
        listing.write_text("".join(f"file '{w.as_posix()}'\n" for w in wavs), encoding="utf-8")
        out = tmpdir / "out.mp3"
        run([ffmpeg, "-nostdin", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c:a", "libmp3lame", "-q:a", "2", "-ar", str(_SR), "-ac", "1", str(out)])
        return out.read_bytes()


def probe_duration(ffprobe: str, path: str) -> float:
    """Exact media duration in seconds via ffprobe."""
    out = run([
        ffprobe, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ])
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise FfmpegError(f"could not read duration of {path}: {e}") from e
