"""Upload the three output files to Cloudflare R2 via wrangler.

Closes the Overview "operator uploads to S3 manually" open question: after
``generate`` writes ``out/{slug}/`` the operator can push the three files to R2 and
get back the public base URL to paste into NotebookForge.

Mechanism (no new pip deps, no embedded keys): shell out to ``wrangler``. The
Cloudflare OAuth token lives inside wrangler (operator runs ``wrangler login`` once)
— forge-narrator never reads, stores, or logs any key or token. Bucket creation,
public-access, and CORS are one-time setup done by the operator, not this tool.

Object layout: ``{bucket}/{slug}/document.{mp3,marks.json,blocks.json}`` so each
memoir is reachable at ``{base}/{slug}`` (the player appends ``/document.mp3`` etc.).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

DEFAULT_BUCKET = "notebook-forge-audio"
ENV_BUCKET = "FORGE_R2_BUCKET"
ENV_BASE_URL = "FORGE_R2_BASE_URL"

# (filename, content-type) for the three S3-contract files, in upload order.
OUTPUT_FILES = (
    ("document.mp3", "audio/mpeg"),
    ("document.marks.json", "application/json"),
    ("document.blocks.json", "application/json"),
)

_PUB_URL_RE = re.compile(r"https://pub-[0-9a-z]+\.r2\.dev", re.IGNORECASE)

_SETUP_HINT = (
    "One-time R2 setup (run once, then retry):\n"
    "  wrangler r2 bucket create {bucket}\n"
    "  wrangler r2 bucket dev-url enable {bucket}\n"
    "  wrangler r2 bucket cors set {bucket} --file out/r2-cors.json\n"
    "(and `wrangler login` first if not yet authenticated)"
)


class UploadError(Exception):
    """Raised on a missing file, missing/unconfigured bucket, or wrangler failure."""


def wrangler_prefix() -> list[str]:
    """``wrangler`` if on PATH, else ``npx --yes wrangler@latest``."""
    if shutil.which("wrangler"):
        return ["wrangler"]
    return ["npx", "--yes", "wrangler@latest"]


def run_wrangler(args: list[str]) -> subprocess.CompletedProcess:
    """Run a wrangler subcommand (``args`` are the parts after the binary).

    The single subprocess entry point — tests monkeypatch this. The Cloudflare
    token lives inside wrangler; nothing here reads, stores, or logs it.
    """
    return subprocess.run(wrangler_prefix() + args, capture_output=True, text=True)


def _object_put_args(bucket: str, slug: str, name: str, path: Path, ctype: str) -> list[str]:
    return ["r2", "object", "put", f"{bucket}/{slug}/{name}",
            "--file", str(path), "--remote", "--content-type", ctype]


def _resolve_base(bucket: str, override: str | None) -> str:
    """Public base URL (no trailing slash). Override wins; else ask wrangler."""
    if override:
        return override.rstrip("/")
    cp = run_wrangler(["r2", "bucket", "dev-url", "get", bucket])
    if cp.returncode != 0:
        raise UploadError(
            f"could not resolve the R2 public base URL: "
            f"`wrangler r2 bucket dev-url get {bucket}` failed.\n"
            + _SETUP_HINT.format(bucket=bucket)
        )
    m = _PUB_URL_RE.search(cp.stdout or "")
    if not m:
        raise UploadError(
            f"no public r2.dev URL found for bucket {bucket!r} "
            "(is the public dev URL enabled?).\n" + _SETUP_HINT.format(bucket=bucket)
        )
    return m.group(0).rstrip("/")


def upload_slug(
    slug: str,
    *,
    out_root: str | Path = "./out",
    bucket: str | None = None,
    base_url: str | None = None,
    dry_run: bool = False,
    echo=print,
) -> str:
    """Upload ``out/{slug}/`` to R2 and return the final base URL (``{base}/{slug}``).

    With ``dry_run`` no subprocess runs at all — it just prints the exact wrangler
    commands and the (override or placeholder) base URL.
    """
    out_dir = Path(out_root) / slug
    files = []
    for name, ctype in OUTPUT_FILES:
        p = out_dir / name
        if not p.is_file():
            raise UploadError(
                f"missing {p} — run `generate` first (all three files are required)."
            )
        files.append((p, name, ctype))

    bucket = bucket or os.environ.get(ENV_BUCKET) or DEFAULT_BUCKET
    override = base_url or os.environ.get(ENV_BASE_URL)

    if dry_run:
        echo(f"[dry-run] bucket: {bucket}")
        for p, name, ctype in files:
            echo("[dry-run] " + " ".join(wrangler_prefix() + _object_put_args(bucket, slug, name, p, ctype)))
        if override:
            final = f"{override.rstrip('/')}/{slug}"
        else:
            echo("[dry-run] " + " ".join(wrangler_prefix() + ["r2", "bucket", "dev-url", "get", bucket]))
            final = f"https://pub-XXXX.r2.dev/{slug}"
        echo(f"[dry-run] base URL → {final}")
        return final

    base = _resolve_base(bucket, override)
    echo(f"Uploading out/{slug}/ → r2://{bucket}/{slug}/ …")
    for p, name, ctype in files:
        cp = run_wrangler(_object_put_args(bucket, slug, name, p, ctype))
        if cp.returncode != 0:
            raise UploadError(
                f"upload failed for {name} (wrangler exit {cp.returncode}): "
                f"{(cp.stderr or '').strip()[:300]}\n" + _SETUP_HINT.format(bucket=bucket)
            )
        echo(f"  ✓ {slug}/{name}  ({ctype})")
    final = f"{base}/{slug}"
    echo(f"\nBase URL: {final}")
    return final
