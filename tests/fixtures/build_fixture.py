#!/usr/bin/env python3
"""Build a small, valid manifest fixture from the POC sample prose.

Produces ``manifest.json`` and ``manifest.zip`` next to this file. The SSML mirrors
what NotebookForge would emit (heading gets prosody + breaks; paragraphs get a
trailing break), and each block's hash is computed with the canonical recipe so
the fixture passes ``load_manifest``'s integrity check.

    python tests/fixtures/build_fixture.py
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from forge_narrator.hashing import block_hash  # noqa: E402

VOICE = "Brian"
ENGINE = "generative"
SLUG = "junior-sample"
TITLE = "Junior (sample)"

HEADING = "The boy I once knew but now remember"

PARAGRAPHS = [
    "Junior hurries down the hill from the convent towards the railway yard. "
    "It is becoming cold and dew is forming on the ground. There will be a frost "
    "in the morning. It is dark already and not yet six o'clock.",
    "Mum will have something nice for tea although Junior is not all that fond of "
    "food. The fire in the stove though will be nice and after tea sitting around "
    "the stove in warm pyjamas with the oven open is best.",
    "The name Junior came about because his father was also Robert, and to avoid "
    "confusion the boy had been called Junior from infancy. It followed him through "
    "the primary school roll books.",
]


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def heading_ssml(text: str) -> str:
    return (
        f"<speak><break time='700ms'/>"
        f"<prosody rate='95%'>{esc(text)}</prosody>"
        f"<break time='400ms'/></speak>"
    )


def paragraph_ssml(text: str) -> str:
    return f"<speak>{esc(text)}<break time='500ms'/></speak>"


def build_manifest() -> dict:
    blocks = []
    specs = [("heading", HEADING, heading_ssml(HEADING))]
    for para in PARAGRAPHS:
        specs.append(("paragraph", para, paragraph_ssml(para)))

    for i, (btype, _text, ssml) in enumerate(specs):
        # Mirror the real NotebookForge export: SSML only, no plain text. The
        # generator derives readable text from the SSML.
        blocks.append({
            "index": i,
            "type": btype,
            "ssml": ssml,
            "hash": block_hash(ssml, VOICE, ENGINE),
        })

    return {
        "document_slug": SLUG,
        "title": TITLE,
        "voice": VOICE,
        "engine": ENGINE,
        "blocks": blocks,
    }


def main() -> None:
    here = Path(__file__).resolve().parent
    manifest = build_manifest()

    json_path = here / "manifest.json"
    json_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    zip_path = here / "manifest.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))

    print(f"Wrote {json_path}")
    print(f"Wrote {zip_path}")
    print(f"{len(manifest['blocks'])} blocks, "
          f"{sum(len(b['ssml']) for b in manifest['blocks']):,} SSML chars")


if __name__ == "__main__":
    main()
