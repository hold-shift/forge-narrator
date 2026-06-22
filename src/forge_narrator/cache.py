"""Content-addressed block cache (see Spec B §4).

Each block contributes TWO cached artifacts, keyed by the manifest-provided
``sha256(ssml + voice + model)``:

- ``cache/{hash}.mp3``         — the synthesised block audio.
- ``cache/{hash}.marks.json``  — the block-local word marks (times relative to the
                                 block's own audio, starting at 0).

Caching the marks too means a cached block contributes its timing on re-run
without re-calling the (paid) API. Because the key is the content hash:

- Re-running after a small NotebookForge edit re-synthesises only changed blocks.
- The cache never expires; it's safe to keep indefinitely. The model id is in the
  hash, so switching model invalidates everything correctly.
- ``--no-cache`` bypasses reads (forces full regen) but still writes, so a forced
  regen repopulates the cache.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CACHE_DIR = Path("cache")


class BlockCache:
    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE_DIR, *, enabled: bool = True):
        self.dir = Path(cache_dir)
        self.enabled = enabled

    def path_for(self, block_hash: str) -> Path:
        """Path to the block's mp3 (kept this name for the stitch step)."""
        return self.dir / f"{block_hash}.mp3"

    def marks_path(self, block_hash: str) -> Path:
        return self.dir / f"{block_hash}.marks.json"

    def has(self, block_hash: str) -> bool:
        """True if BOTH the audio and marks are cached (and reads are enabled)."""
        if not self.enabled:
            return False
        mp3, marks = self.path_for(block_hash), self.marks_path(block_hash)
        return (
            mp3.exists() and mp3.stat().st_size > 0
            and marks.exists() and marks.stat().st_size > 0
        )

    def get_mp3(self, block_hash: str) -> bytes | None:
        if not self.has(block_hash):
            return None
        return self.path_for(block_hash).read_bytes()

    def get_marks(self, block_hash: str) -> list[dict] | None:
        if not self.has(block_hash):
            return None
        return json.loads(self.marks_path(block_hash).read_text(encoding="utf-8"))

    def put(self, block_hash: str, audio: bytes, marks: list[dict]) -> Path:
        """Write the block's audio + marks atomically (tmp + rename).

        Writes regardless of ``enabled`` so a ``--no-cache`` forced regen still
        repopulates the cache. Returns the mp3 path.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        mp3 = self.path_for(block_hash)
        tmp_mp3 = mp3.with_suffix(".mp3.tmp")
        tmp_mp3.write_bytes(audio)
        tmp_mp3.replace(mp3)

        marks_file = self.marks_path(block_hash)
        tmp_marks = marks_file.with_suffix(".json.tmp")
        tmp_marks.write_text(
            json.dumps(marks, ensure_ascii=False), encoding="utf-8"
        )
        tmp_marks.replace(marks_file)
        return mp3
