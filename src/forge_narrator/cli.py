"""Command-line interface (Spec B §8).

    forge-narrator estimate manifest.zip
    forge-narrator generate manifest.zip --out ./out [--yes] [--model small.en] [--no-cache]

`estimate` makes no API calls and needs no aligner — it just reads the manifest
and reports characters, cost and projected wall-clock. `generate` runs the full
pipeline and is gated behind a cost confirmation.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .cache import DEFAULT_CACHE_DIR, BlockCache
from .cost import estimate_manifest, format_duration
from .manifest import ManifestError, load_manifest

DEFAULT_CONCURRENCY = 9  # Spec B §3a: 8–10 in flight.


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("manifest", help="path to the manifest .zip (or bare manifest.json)")
    p.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help=f"block-audio cache directory (default: {DEFAULT_CACHE_DIR})",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="ignore cached block audio (forces full re-synthesis)",
    )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="forge-narrator", description=__doc__)
    ap.add_argument("--version", action="version", version=f"forge-narrator {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="report characters + cost, no API calls")
    _add_common(est)
    est.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="concurrency used only to project wall-clock (default: %(default)s)",
    )
    est.set_defaults(func=cmd_estimate)

    gen = sub.add_parser("generate", help="synthesise, stitch, align → out/{slug}/")
    _add_common(gen)
    gen.add_argument("--out", default="./out", help="output root (default: ./out)")
    gen.add_argument("--yes", action="store_true", help="skip the cost confirmation prompt")
    gen.add_argument("--model", default="small.en", help="whispermlx model (default: %(default)s)")
    gen.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="concurrent Polly requests (default: %(default)s)",
    )
    gen.add_argument(
        "--char-cap",
        type=int,
        default=None,
        help="refuse if uncached characters exceed this monthly cap",
    )
    gen.set_defaults(func=cmd_generate)
    return ap


def _load(args) -> tuple:
    """Load the manifest + cache for either command. Exits cleanly on error."""
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as e:
        sys.exit(f"error: {e}")
    cache = BlockCache(args.cache_dir, enabled=not args.no_cache)
    return manifest, cache


def _print_estimate(manifest, est, concurrency: int) -> None:
    print(f"Manifest:   {manifest.source.name}  (slug: {manifest.slug})")
    print(f"Voice/eng:  {manifest.voice} / {manifest.engine}")
    print(f"Blocks:     {est.total_blocks}  "
          f"({est.cached_blocks} cached, {est.uncached_blocks} to synthesise)")
    print(f"Characters: {est.total_chars:,} total  ·  {est.uncached_chars:,} uncached")
    print()
    print(f"Estimated cost (uncached):  ${est.cost_usd:,.2f}")
    print(f"Projected synth wall-clock: {format_duration(est.serial_seconds)} serial  "
          f"→  ~{format_duration(est.wall_clock_seconds(concurrency))} at {concurrency}× concurrency")
    if est.uncached_blocks == 0:
        print("\nAll blocks cached — `generate` would cost $0 and only stitch + align.")


def cmd_estimate(args) -> int:
    manifest, cache = _load(args)
    est = estimate_manifest(manifest, cache)
    _print_estimate(manifest, est, args.concurrency)
    return 0


def cmd_generate(args) -> int:
    manifest, cache = _load(args)
    est = estimate_manifest(manifest, cache)
    _print_estimate(manifest, est, args.concurrency)
    print()

    # Guard rail (Spec B §3b): refuse beyond a configured monthly character cap.
    if args.char_cap is not None and est.uncached_chars > args.char_cap:
        sys.exit(
            f"error: uncached characters ({est.uncached_chars:,}) exceed "
            f"--char-cap ({args.char_cap:,}). Refusing."
        )

    # Cost confirmation before any paid Polly call.
    if est.uncached_chars > 0 and not args.yes:
        reply = input(f"Proceed with ~${est.cost_usd:,.2f} of Polly synthesis? [y/N] ")
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    # Import the pipeline lazily so `estimate` works without boto3/whispermlx/ffmpeg.
    from .pipeline import generate

    try:
        out_dir = generate(
            manifest,
            cache,
            out_root=Path(args.out),
            model=args.model,
            concurrency=args.concurrency,
        )
    except Exception as e:  # surface cleanly rather than a raw traceback
        sys.exit(f"error: {e}")
    print(f"\nDone. Wrote {out_dir}/")
    print("  document.mp3 · document.marks.json · document.blocks.json")
    print("Validate with poc/player.html, then upload the folder to S3.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
