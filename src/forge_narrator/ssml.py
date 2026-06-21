"""Extract spoken plain text from a block's SSML.

NotebookForge exports SSML only — no separate plain text — so the generator
derives the readable text itself. That text is used for (a) the forced-alignment
transcript and (b) ``document.blocks.json`` (the player's render source).

Real exports use only ``<speak>``, ``<break>`` and ``<prosody>`` tags with no
entities, so walking the XML text nodes recovers the prose exactly (breaks are
self-closing → contribute nothing; prosody text is kept). A regex fallback covers
any malformed SSML rather than failing the run.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def ssml_to_text(ssml: str) -> str:
    """Return the spoken text of an SSML string, whitespace-normalised."""
    try:
        text = "".join(ET.fromstring(ssml).itertext())
    except ET.ParseError:
        text = html.unescape(_TAG_RE.sub(" ", ssml))
    return _WS_RE.sub(" ", text).strip()
