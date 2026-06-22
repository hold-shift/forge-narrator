"""ffmpeg / ffprobe helpers. ffmpeg is a HARD dependency (Spec B §2.1)."""

from __future__ import annotations

import json
import shutil
import subprocess


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
