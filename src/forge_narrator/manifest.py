"""Manifest reader — the consumer side of the file-based contract with NotebookForge.

NotebookForge exports a **manifest zip** containing a single ``manifest.json``.
This module unzips (or reads a bare ``.json``), validates it, and exposes typed
``Manifest`` / ``Block`` objects to the rest of the pipeline. It makes NO network
calls — parsing is verified first (build order step 2).

Manifest schema (version 1)::

    {
      "version": 1,
      "slug": "junior",                  # output folder name: out/{slug}/
      "title": "Junior",                 # human label (optional)
      "voice": "Brian",
      "engine": "generative",
      "blocks": [
        {
          "index": 0,
          "type": "heading",             # "heading" | "paragraph"
          "text": "The boy I once knew", # plain text (transcript + render source)
          "ssml": "<speak>...</speak>",  # exact SSML to send to Polly
          "hash": "<sha256 hex>"         # sha256(ssml, voice, engine) — cache key
        },
        ...
      ]
    }

Only ``heading`` and ``paragraph`` blocks appear here; NotebookForge has already
stripped images, doc groups, nav, and (v1) footnotes per the Overview's
"What is narratable" table. The generator stays dumb: it speaks what it's given.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .hashing import block_hash

MANIFEST_NAME = "manifest.json"
SUPPORTED_VERSION = 1
BLOCK_TYPES = ("heading", "paragraph")


class ManifestError(Exception):
    """Raised when a manifest is missing, malformed, or fails validation."""


@dataclass(frozen=True)
class Block:
    """One narratable block (a heading or a paragraph)."""

    index: int
    type: str
    text: str
    ssml: str
    hash: str

    @property
    def billed_chars(self) -> int:
        """Characters submitted to Polly (the SSML string).

        Used for cost/throughput estimates. This is the full SSML length including
        tags — a deliberate over-estimate for the cost guard rail (AWS may bill
        only the spoken characters), so the printed bill is never a surprise low.
        """
        return len(self.ssml)


@dataclass(frozen=True)
class Manifest:
    """A parsed, validated manifest."""

    version: int
    slug: str
    title: str
    voice: str
    engine: str
    blocks: tuple[Block, ...]
    source: Path

    @property
    def transcript(self) -> str:
        """The known transcript (block plain text in order) for forced alignment."""
        return "\n\n".join(b.text for b in self.blocks)

    @property
    def total_billed_chars(self) -> int:
        return sum(b.billed_chars for b in self.blocks)


def _read_manifest_json(path: Path) -> dict:
    """Return the parsed ``manifest.json`` dict from a ``.zip`` or bare ``.json``."""
    if not path.exists():
        raise ManifestError(f"Manifest not found: {path}")

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if MANIFEST_NAME not in names:
                # Tolerate it being nested one level deep inside the zip.
                candidates = [n for n in names if n.endswith("/" + MANIFEST_NAME)]
                if not candidates:
                    raise ManifestError(
                        f"{path.name} has no {MANIFEST_NAME} (contains: {names})"
                    )
                member = candidates[0]
            else:
                member = MANIFEST_NAME
            raw = zf.read(member)
    else:
        raw = path.read_bytes()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ManifestError(f"{path.name}: invalid JSON in {MANIFEST_NAME}: {e}") from e


def _require(d: dict, key: str, where: str) -> object:
    if key not in d:
        raise ManifestError(f"{where}: missing required field '{key}'")
    return d[key]


def load_manifest(path: str | Path, *, verify_hashes: bool = True) -> Manifest:
    """Load and validate a manifest from ``path`` (a ``.zip`` or a ``.json``).

    With ``verify_hashes`` (default), each block's hash is recomputed and checked
    against the manifest. A mismatch raises ``ManifestError`` — it means the
    manifest is internally inconsistent or NotebookForge changed the hash recipe,
    either of which would corrupt the content-addressed cache.
    """
    path = Path(path)
    data = _read_manifest_json(path)

    if not isinstance(data, dict):
        raise ManifestError(f"{path.name}: top level must be a JSON object")

    version = _require(data, "version", path.name)
    if version != SUPPORTED_VERSION:
        raise ManifestError(
            f"{path.name}: unsupported manifest version {version!r} "
            f"(this tool supports version {SUPPORTED_VERSION})"
        )

    slug = str(_require(data, "slug", path.name)).strip()
    if not slug:
        raise ManifestError(f"{path.name}: 'slug' is empty")
    voice = str(_require(data, "voice", path.name))
    engine = str(_require(data, "engine", path.name))
    title = str(data.get("title", slug))

    raw_blocks = _require(data, "blocks", path.name)
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ManifestError(f"{path.name}: 'blocks' must be a non-empty list")

    blocks: list[Block] = []
    for i, rb in enumerate(raw_blocks):
        where = f"{path.name} block[{i}]"
        if not isinstance(rb, dict):
            raise ManifestError(f"{where}: must be a JSON object")
        btype = str(_require(rb, "type", where))
        if btype not in BLOCK_TYPES:
            raise ManifestError(
                f"{where}: type {btype!r} not in {BLOCK_TYPES}"
            )
        text = str(_require(rb, "text", where))
        ssml = str(_require(rb, "ssml", where))
        bhash = str(_require(rb, "hash", where))
        # 'index' is informational; we trust positional order as authoritative
        # (the sacred word-ordering invariant). Flag a mismatch early as a smell.
        declared_index = rb.get("index", i)
        if declared_index != i:
            raise ManifestError(
                f"{where}: declared index {declared_index} != position {i} "
                "(blocks must be in order)"
            )
        if verify_hashes:
            expected = block_hash(ssml, voice, engine)
            if expected != bhash:
                raise ManifestError(
                    f"{where}: hash mismatch — manifest says {bhash[:12]}…, "
                    f"recomputed {expected[:12]}…. Manifest is inconsistent or the "
                    "hash recipe diverged from NotebookForge."
                )
        blocks.append(Block(index=i, type=btype, text=text, ssml=ssml, hash=bhash))

    return Manifest(
        version=version,
        slug=slug,
        title=title,
        voice=voice,
        engine=engine,
        blocks=tuple(blocks),
        source=path,
    )
