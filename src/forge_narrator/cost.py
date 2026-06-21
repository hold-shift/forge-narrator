"""Cost + throughput estimation and the guard rail (Spec B §3b).

Numbers are anchored to the POC measurements:
- Generative pricing: **$30 per 1,000,000 characters**.
- Throughput: **~19 seconds per 1,000 characters** (serial). Synthesis is
  parallelised at run time, so wall-clock is divided by the concurrency.

Characters are counted as the SSML string length (what we submit to Polly) — a
deliberate slight over-estimate, so the guard rail never under-warns about a bill.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cache import BlockCache
from .manifest import Manifest

USD_PER_MILLION_CHARS = 30.0
SECONDS_PER_1K_CHARS = 19.0  # serial, generative (POC-measured)


@dataclass(frozen=True)
class Estimate:
    total_blocks: int
    cached_blocks: int
    uncached_blocks: int
    total_chars: int
    uncached_chars: int
    # Cost/time are for the UNCACHED work — that's the only paid/slow part.
    cost_usd: float
    serial_seconds: float

    def wall_clock_seconds(self, concurrency: int) -> float:
        """Approx wall-clock for the uncached synthesis at a given concurrency."""
        return self.serial_seconds / max(1, concurrency)


def cost_for_chars(chars: int) -> float:
    return chars / 1_000_000 * USD_PER_MILLION_CHARS


def serial_seconds_for_chars(chars: int) -> float:
    return chars / 1_000 * SECONDS_PER_1K_CHARS


def estimate_manifest(manifest: Manifest, cache: BlockCache) -> Estimate:
    """Compute an estimate, charging only for blocks not already cached."""
    total_chars = 0
    uncached_chars = 0
    cached = 0
    for b in manifest.blocks:
        total_chars += b.billed_chars
        if cache.has(b.hash):
            cached += 1
        else:
            uncached_chars += b.billed_chars
    uncached = len(manifest.blocks) - cached
    return Estimate(
        total_blocks=len(manifest.blocks),
        cached_blocks=cached,
        uncached_blocks=uncached,
        total_chars=total_chars,
        uncached_chars=uncached_chars,
        cost_usd=cost_for_chars(uncached_chars),
        serial_seconds=serial_seconds_for_chars(uncached_chars),
    )


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"
