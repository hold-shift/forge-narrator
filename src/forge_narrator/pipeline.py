"""End-to-end generate pipeline (Spec B §1): synth → stitch → align → emit.

Writes ``out/{slug}/`` containing the three S3-contract files:
``document.mp3``, ``document.marks.json``, ``document.blocks.json`` — all sharing
one word ordering (the sacred invariant). The operator uploads that folder to S3.
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
    model: str = "small.en",
    concurrency: int = 9,
) -> Path:
    """Run the full pipeline and return the output directory."""
    # Fail fast on the hard dependency before doing any paid work.
    require_ffmpeg()

    out_dir = Path(out_root) / manifest.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1–2. Synthesise (cache-aware, parallel). Imported here to keep estimate light.
    from .synth import synthesise

    print("[1/4] Synthesising blocks (Polly, cached by hash)…")
    result = synthesise(manifest, cache, concurrency=concurrency, progress=_progress)
    print(f"      {result.from_cache} from cache, {result.synthesised} synthesised.")

    # 3. Stitch + offset table.
    from .stitch import stitch

    print("[2/4] Stitching → document.mp3…")
    mp3_path = out_dir / "document.mp3"
    offsets = stitch(manifest, cache, mp3_path)

    # 4. Forced alignment → marks (+ exact word→block ranges).
    from .align import align_document

    print(f"[3/4] Aligning with whispermlx ({model})…")
    aligned = align_document(mp3_path, manifest, offsets, model=model)
    print(f"      {len(aligned.marks)} words aligned.")

    # 5–6. Emit blocks.json + write marks.json.
    from .blocks_json import build_blocks_json

    print("[4/4] Writing marks.json + blocks.json…")
    marks_path = out_dir / "document.marks.json"
    blocks_path = out_dir / "document.blocks.json"
    marks_path.write_text(
        json.dumps(aligned.marks, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    blocks = build_blocks_json(manifest, offsets, aligned)
    blocks_path.write_text(
        json.dumps(blocks, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return out_dir
