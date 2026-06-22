"""End-to-end generate pipeline (Spec B §1): synth → stitch → assemble → emit.

Writes ``out/{slug}/`` containing the three S3-contract files:
``document.mp3``, ``document.marks.json``, ``document.blocks.json`` — all sharing
one word ordering (the sacred invariant). The operator uploads that folder to S3.

There is no alignment stage: word timing is a by-product of ElevenLabs synthesis,
assembled by concatenating each block's marks shifted by its stitch offset.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .cache import BlockCache
from .ffmpeg import require_ffmpeg
from .manifest import Manifest


def _progress(done: int, total: int) -> None:
    sys.stdout.write(f"\r  synth: {done}/{total} blocks ready")
    sys.stdout.flush()
    if done >= total:
        sys.stdout.write("\n")


def generate(
    manifest: Manifest,
    cache: BlockCache,
    *,
    out_root: Path,
    concurrency: int = 8,
) -> Path:
    """Run the full pipeline and return the output directory."""
    # Fail fast on the hard dependency before doing any paid work.
    require_ffmpeg()

    out_dir = Path(out_root) / manifest.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1–2. Synthesise (cache-aware, parallel). Imported lazily to keep estimate light.
    from .synth import synthesise

    print("[1/3] Synthesising blocks (ElevenLabs /with-timestamps, cached by hash)…")
    result = synthesise(manifest, cache, concurrency=concurrency, progress=_progress)
    print(f"      {result.from_cache} from cache, {result.synthesised} synthesised.")

    # 3. Stitch + offset table.
    from .stitch import stitch

    print("[2/3] Stitching → document.mp3…")
    mp3_path = out_dir / "document.mp3"
    offsets = stitch(manifest, cache, mp3_path)

    # 4–5. Assemble marks (concat block marks shifted by offsets) + blocks.json.
    from .blocks_json import build_blocks_json
    from .marks import assemble_document_marks

    print("[3/3] Assembling marks.json + blocks.json…")
    marks, word_ranges = assemble_document_marks(manifest, cache, offsets)
    print(f"      {len(marks)} words across {len(manifest.blocks)} blocks.")

    marks_path = out_dir / "document.marks.json"
    blocks_path = out_dir / "document.blocks.json"
    marks_path.write_text(
        json.dumps(marks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    blocks = build_blocks_json(manifest, offsets, word_ranges)
    blocks_path.write_text(
        json.dumps(blocks, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return out_dir
