"""Command-line interface (Spec B §8, Spec C §10).

    forge-narrator estimate manifest.zip
    forge-narrator generate manifest.zip --out ./out [--yes] [--no-cache] [--concurrency N] [--upload]
    forge-narrator serve [--port 8765] [--out ./out]
    forge-narrator upload <slug> [--bucket NAME] [--base-url URL] [--dry-run]

`estimate` makes no API calls — it reads the manifest and reports characters and
the ElevenLabs credit cost. `generate` runs the full pipeline (synth → stitch →
assemble) and is gated behind a cost confirmation before any paid call. `serve`
launches the local web console (127.0.0.1) over the same pipeline. `upload` pushes
the three files in out/{slug}/ to Cloudflare R2 (via wrangler) and prints the base
URL to paste into NotebookForge.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .cache import DEFAULT_CACHE_DIR, BlockCache
from .cost import USD_PER_1K_CREDITS, estimate_manifest
from .manifest import ManifestError, load_manifest

DEFAULT_CONCURRENCY = 8  # Spec B §3a: ~5–10 in flight.


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("manifest", help="path to the manifest .zip (or bare manifest.json)")
    p.add_argument(
        "--cache-dir",
        default=str(DEFAULT_CACHE_DIR),
        help=f"block cache directory (default: {DEFAULT_CACHE_DIR})",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="ignore cached blocks (forces full re-synthesis)",
    )


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="forge-narrator", description=__doc__)
    ap.add_argument("--version", action="version", version=f"forge-narrator {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    est = sub.add_parser("estimate", help="report characters + credit cost, no API calls")
    _add_common(est)
    est.set_defaults(func=cmd_estimate)

    gen = sub.add_parser("generate", help="synthesise, stitch, assemble → out/{slug}/")
    _add_common(gen)
    gen.add_argument("--out", default="./out", help="output root (default: ./out)")
    gen.add_argument("--yes", action="store_true", help="skip the cost confirmation prompt")
    gen.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="concurrent ElevenLabs requests (default: %(default)s)",
    )
    gen.add_argument(
        "--char-cap",
        type=int,
        default=None,
        help="refuse if uncached characters exceed this cap",
    )
    gen.add_argument(
        "--upload",
        action="store_true",
        help="after writing the files, upload out/{slug}/ to Cloudflare R2 (wrangler)",
    )
    gen.set_defaults(func=cmd_generate)

    up = sub.add_parser("upload", help="upload out/{slug}/ to Cloudflare R2 (via wrangler)")
    up.add_argument("slug", help="document slug — the out/{slug}/ folder to upload")
    up.add_argument("--out", default="./out", help="output root (default: ./out)")
    up.add_argument(
        "--bucket", default=None,
        help="R2 bucket (default: $FORGE_R2_BUCKET or notebook-forge-audio)",
    )
    up.add_argument(
        "--base-url", default=None,
        help="public base URL override (default: $FORGE_R2_BASE_URL or resolved via wrangler)",
    )
    up.add_argument(
        "--dry-run", action="store_true",
        help="print the wrangler commands and base URL without uploading",
    )
    up.set_defaults(func=cmd_upload)

    srv = sub.add_parser("serve", help="run the local web console (127.0.0.1)")
    srv.add_argument("--port", type=int, default=8765, help="port (default: %(default)s)")
    srv.add_argument("--out", default="./out", help="output root (default: ./out)")
    srv.add_argument(
        "--cache-dir", default=str(DEFAULT_CACHE_DIR),
        help=f"block cache directory (default: {DEFAULT_CACHE_DIR})",
    )
    srv.add_argument(
        "--char-cap", type=int, default=None,
        help="refuse generation if uncached characters exceed this cap",
    )
    srv.set_defaults(func=cmd_serve)
    return ap


def _load(args) -> tuple:
    """Load the manifest + cache for either command. Exits cleanly on error."""
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as e:
        sys.exit(f"error: {e}")
    cache = BlockCache(args.cache_dir, enabled=not args.no_cache)
    return manifest, cache


def _print_estimate(manifest, est) -> None:
    print(f"Manifest:    {manifest.source.name}  (slug: {manifest.slug})")
    print(f"Voice/model: {manifest.voice} / {manifest.model}")
    print(f"Blocks:      {est.total_blocks}  "
          f"({est.cached_blocks} cached, {est.uncached_blocks} to synthesise)")
    print(f"Characters:  {est.total_chars:,} total  ·  {est.uncached_chars:,} uncached")
    print()
    print(f"Credits (uncached):  {est.credits:,}   (1 character = 1 credit)")
    print(f"Approx cost:         ~${est.cost_usd:,.2f}   "
          f"(plan-dependent; ~${USD_PER_1K_CREDITS:.2f} / 1k credits)")
    if est.uncached_blocks == 0:
        print("\nAll blocks cached — `generate` would cost $0 and only stitch + assemble.")


def cmd_estimate(args) -> int:
    manifest, cache = _load(args)
    est = estimate_manifest(manifest, cache)
    _print_estimate(manifest, est)
    return 0


def cmd_generate(args) -> int:
    manifest, cache = _load(args)
    est = estimate_manifest(manifest, cache)
    _print_estimate(manifest, est)
    print()

    # Guard rail (Spec B §3b): refuse beyond a configured character cap.
    if args.char_cap is not None and est.uncached_chars > args.char_cap:
        sys.exit(
            f"error: uncached characters ({est.uncached_chars:,}) exceed "
            f"--char-cap ({args.char_cap:,}). Refusing."
        )

    # Cost confirmation before any paid ElevenLabs call.
    if est.uncached_chars > 0 and not args.yes:
        reply = input(
            f"Proceed with ~{est.credits:,} credits (~${est.cost_usd:,.2f}) "
            "of ElevenLabs synthesis? [y/N] "
        )
        if reply.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1

    # Import the pipeline lazily so `estimate` stays import-light.
    from .pipeline import generate

    try:
        summary = generate(
            manifest,
            cache,
            out_root=Path(args.out),
            concurrency=args.concurrency,
            on_progress=_make_cli_printer(),
        )
    except Exception as e:  # surface cleanly rather than a raw traceback
        sys.exit(f"error: {e}")
    print(f"\nDone. Wrote {summary['out_dir']}/")
    print(f"  document.mp3 · document.marks.json · document.blocks.json "
          f"({summary['words']:,} words, {summary['duration_seconds']:.1f}s)")
    print(f"  {summary['blocks_cached']} cached, {summary['blocks_synthesised']} synthesised "
          f"· {summary['credits_spent']:,} credits (~${summary['cost_usd']:.2f})")

    if args.upload:
        from .upload import UploadError, upload_slug
        print()
        try:
            url = upload_slug(summary["slug"], out_root=args.out)
        except UploadError as e:
            sys.exit(f"error: upload failed: {e}")
        _print_upload_reminder(url)
    else:
        print(f"Validate with poc/player.html. To publish: "
              f"forge-narrator upload {summary['slug']}")
    return 0


def _make_cli_printer():
    """A printing on_progress callback — keeps the CLI's terminal output."""
    state = {"phase": None}

    def printer(ev: dict) -> None:
        ph = ev["phase"]
        if ph != state["phase"]:
            if state["phase"] == "synthesising":
                sys.stdout.write("\n")  # close the \r progress line
            state["phase"] = ph
            headers = {
                "synthesising": "[1/3] Synthesising blocks (ElevenLabs /with-timestamps)…",
                "stitching": "[2/3] Stitching → document.mp3…",
                "assembling": "[3/3] Assembling marks.json + blocks.json…",
            }
            if ph in headers:
                print(headers[ph])
        if ph == "synthesising":
            if ev["level"] == "warn":
                sys.stdout.write(f"\n  {ev['message']}\n")
            eta = f" · eta {ev['eta_seconds']}s" if ev["eta_seconds"] is not None else ""
            sys.stdout.write(
                f"\r  {ev['blocks_done']}/{ev['blocks_total']} blocks "
                f"({ev['blocks_cached']} cached) · {ev['chars_done']:,} chars "
                f"· ~${ev['cost_usd']:.2f}{eta}   "
            )
            sys.stdout.flush()

    return printer


def _print_upload_reminder(url: str) -> None:
    print('\nPaste this into NotebookForge → the document\'s Narration panel → '
          '"Audio base URL":')
    print(f"  {url}")


def cmd_upload(args) -> int:
    from .upload import UploadError, upload_slug

    try:
        url = upload_slug(
            args.slug,
            out_root=args.out,
            bucket=args.bucket,
            base_url=args.base_url,
            dry_run=args.dry_run,
        )
    except UploadError as e:
        sys.exit(f"error: {e}")
    if not args.dry_run:
        _print_upload_reminder(url)
    return 0


def cmd_serve(args) -> int:
    from .web.server import run_server

    run_server(
        host="127.0.0.1",
        port=args.port,
        out_root=args.out,
        cache_dir=args.cache_dir,
        char_cap=args.char_cap,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
