"""Cost estimation and the guard rail (Spec B §3b) — ElevenLabs credit model.

ElevenLabs bills **1 character = 1 credit** (Multilingual v2/v3). Credits map to
USD by plan/overage rate, so the dollar figure is approximate; the credit count is
exact. Anchor: the full archive (~3.87M chars) on the fast-and-overage paid path
is ~$390–450, i.e. roughly $0.11 per 1,000 credits.

Characters are counted as the full SSML/text string length (what we submit) — a
deliberate slight over-estimate, so the guard rail never under-warns about a bill.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cache import BlockCache
from .manifest import Manifest

CREDITS_PER_CHAR = 1  # ElevenLabs: 1 character = 1 credit
# Approximate, plan/overage dependent. ~$0.11/1k credits → ~$425 for 3.87M chars.
USD_PER_1K_CREDITS = 0.11


@dataclass(frozen=True)
class Estimate:
    total_blocks: int
    cached_blocks: int
    uncached_blocks: int
    total_chars: int
    uncached_chars: int
    # Credits + cost are for the UNCACHED work — the only paid part.
    credits: int
    cost_usd: float


def credits_for_chars(chars: int) -> int:
    return chars * CREDITS_PER_CHAR


def cost_for_chars(chars: int) -> float:
    return credits_for_chars(chars) / 1_000 * USD_PER_1K_CREDITS


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
        credits=credits_for_chars(uncached_chars),
        cost_usd=cost_for_chars(uncached_chars),
    )
