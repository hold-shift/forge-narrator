"""Content-addressed block-audio cache (see Spec B §4).

A block's Polly mp3 is stored at ``cache/{hash}.mp3`` where ``hash`` is the
manifest-provided ``sha256(ssml, voice, engine)``. Because the key is the content
hash:

- Re-running after a small NotebookForge edit re-synthesises only changed blocks.
- The cache never expires; it's safe to keep indefinitely.
- ``--no-cache`` bypasses reads (forces full regen) but still writes, so a forced
  regen repopulates the cache.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_CACHE_DIR = Path("cache")


class BlockCache:
    def __init__(self, cache_dir: str | Path = DEFAULT_CACHE_DIR, *, enabled: bool = True):
        self.dir = Path(cache_dir)
        self.enabled = enabled

    def path_for(self, block_hash: str) -> Path:
        return self.dir / f"{block_hash}.mp3"

    def has(self, block_hash: str) -> bool:
        """True if this block is cached (and reads are enabled)."""
        if not self.enabled:
            return False
        p = self.path_for(block_hash)
        return p.exists() and p.stat().st_size > 0

    def get(self, block_hash: str) -> bytes | None:
        if not self.has(block_hash):
            return None
        return self.path_for(block_hash).read_bytes()

    def put(self, block_hash: str, audio: bytes) -> Path:
        """Write block audio atomically (tmp + rename) and return its path.

        Writes regardless of ``enabled`` so that a ``--no-cache`` forced regen
        still repopulates the cache for next time.
        """
        self.dir.mkdir(parents=True, exist_ok=True)
        final = self.path_for(block_hash)
        tmp = final.with_suffix(".mp3.tmp")
        tmp.write_bytes(audio)
        tmp.replace(final)
        return final
