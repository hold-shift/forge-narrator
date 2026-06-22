"""End-to-end generate pipeline (Spec B §1): synth → stitch → assemble → emit.

Writes ``out/{slug}/`` containing the three S3-contract files:
``document.mp3``, ``document.marks.json``, ``document.blocks.json`` — all sharing
one word ordering (the sacred invariant). The operator uploads that folder to S3.

There is no alignment stage: word timing is a by-product of ElevenLabs synthesis,
assembled by concatenating each block's marks shifted by its stitch offset.

Progress is reported through an optional ``on_progress(event: dict)`` callback
(Spec C §5/§8): the CLI passes one that prints, the web server one that pushes onto
an SSE queue. Cost/credit figures are computed here via ``cost`` — one source of
truth, never duplicated in the web layer.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .cache import BlockCache
from .cost import cost_for_chars, credits_for_chars
from .ffmpeg import require_ffmpeg
from .manifest import Manifest

# Phases reported in order (Spec C §3.3). "parsing" happens before generate (at
# upload/inspect); generate covers synthesising → stitching → assembling → done.
PHASES = ("parsing", "synthesising", "stitching", "assembling", "done")


class _Reporter:
    """Builds ProgressEvents and forwards them to ``on_progress`` (thread-safe)."""

    def __init__(self, on_progress, *, blocks_total, blocks_cached, chars_total):
        self._cb = on_progress
        self._lock = threading.Lock()
        self.blocks_total = blocks_total
        self.blocks_cached = blocks_cached
        self.chars_total = chars_total
        self.blocks_done = 0
        self.chars_done = 0      # uncached (billed) chars synthesised so far
        self.synth_done = 0      # synthesised (non-cached) blocks completed
        self._synth_t0 = None

    def _eta(self, phase):
        if phase != "synthesising" or not self._synth_t0 or self.synth_done == 0:
            return None
        remaining = (self.blocks_total - self.blocks_cached) - self.synth_done
        if remaining <= 0:
            return 0
        rate = (time.time() - self._synth_t0) / self.synth_done
        return int(rate * remaining)

    def _emit_locked(self, phase, level, message):
        if not self._cb:
            return
        self._cb({
            "phase": phase,
            "blocks_done": self.blocks_done,
            "blocks_total": self.blocks_total,
            "blocks_cached": self.blocks_cached,
            "chars_done": self.chars_done,
            "chars_total": self.chars_total,
            "credits_spent": credits_for_chars(self.chars_done),
            "cost_usd": round(cost_for_chars(self.chars_done), 4),
            "eta_seconds": self._eta(phase),
            "level": level,
            "message": message,
        })

    def phase(self, phase, message="", level="info"):
        with self._lock:
            if phase == "synthesising" and self._synth_t0 is None:
                self._synth_t0 = time.time()
            self._emit_locked(phase, level, message)

    def block(self, index, cached, chars, seconds):
        with self._lock:
            self.blocks_done += 1
            if not cached:
                self.chars_done += chars
                self.synth_done += 1
            msg = (f"block {index} cached" if cached
                   else f"block {index} synth {seconds:.1f}s")
            self._emit_locked("synthesising", "info", msg)

    def throttle(self, index, delay):
        with self._lock:
            self._emit_locked("synthesising", "warn",
                              f"block {index}: 429 — backing off {delay:.0f}s")


def generate(
    manifest: Manifest,
    cache: BlockCache,
    *,
    out_root: Path,
    concurrency: int = 8,
    on_progress=None,
) -> dict:
    """Run the full pipeline and return a result summary dict."""
    # Fail fast on the hard dependency before doing any paid work.
    require_ffmpeg()

    out_dir = Path(out_root) / manifest.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    uncached = [b for b in manifest.blocks if not cache.has(b.hash)]
    blocks_total = len(manifest.blocks)
    blocks_cached = blocks_total - len(uncached)
    chars_total = sum(b.billed_chars for b in uncached)

    rep = _Reporter(on_progress, blocks_total=blocks_total,
                    blocks_cached=blocks_cached, chars_total=chars_total)

    # 1–2. Synthesise (cache-aware, parallel). Imported lazily to keep estimate light.
    from .synth import synthesise

    rep.phase("synthesising", "synthesising blocks (ElevenLabs /with-timestamps)")
    result = synthesise(
        manifest, cache, concurrency=concurrency,
        on_block=rep.block, on_throttle=rep.throttle,
    )

    # 3. Stitch + offset table.
    from .stitch import stitch

    rep.phase("stitching", "stitching block mp3s → document.mp3")
    mp3_path = out_dir / "document.mp3"
    offsets = stitch(manifest, cache, mp3_path)

    # 4–5. Assemble marks (concat block marks shifted by offsets) + blocks.json.
    from .blocks_json import build_blocks_json
    from .marks import assemble_document_marks

    rep.phase("assembling", "assembling document.marks.json + document.blocks.json")
    marks, word_ranges = assemble_document_marks(manifest, cache, offsets)

    marks_path = out_dir / "document.marks.json"
    blocks_path = out_dir / "document.blocks.json"
    marks_path.write_text(json.dumps(marks, indent=2, ensure_ascii=False), encoding="utf-8")
    blocks = build_blocks_json(manifest, offsets, word_ranges)
    blocks_path.write_text(json.dumps(blocks, indent=2, ensure_ascii=False), encoding="utf-8")

    duration = offsets[-1].time_end if offsets else 0.0
    summary = {
        "slug": manifest.slug,
        "out_dir": str(out_dir),
        "mp3": str(mp3_path),
        "marks": str(marks_path),
        "blocks": str(blocks_path),
        "words": len(marks),
        "duration_seconds": duration,
        "blocks_total": blocks_total,
        "blocks_cached": result.from_cache,
        "blocks_synthesised": result.synthesised,
        "credits_spent": credits_for_chars(rep.chars_done),
        "cost_usd": round(cost_for_chars(rep.chars_done), 4),
    }
    rep.phase("done", f"done · {len(marks)} words · {duration:.1f}s")
    return summary
