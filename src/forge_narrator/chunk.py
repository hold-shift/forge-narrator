"""Split an over-long block's SSML into Polly-sized chunks.

Polly's generative ``SynthesizeSpeech`` rejects requests over ~3000 characters
(``TextLengthExceededException``). A few long paragraphs exceed that, so we split
them at sentence boundaries, synthesise each chunk, and concatenate the audio back
into one block mp3 (cached under the block's original hash — splitting is invisible
to caching and alignment).

Only plain prose (text + ``<break>`` tags) is split. If an over-long block carries
richer markup (e.g. ``<prosody>``) that can't be safely cut, we raise rather than
silently drop it — that block should be shortened in NotebookForge. Headings are
short and never hit this path.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# Conservative ceiling on each chunk's full SSML length, safely under Polly's
# ~3000 limit whether it counts total or billed characters.
MAX_SSML_CHARS = 2900
_WRAP_OVERHEAD = len("<speak></speak>")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_WS_RE = re.compile(r"\s+")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pack(units: list[str], budget: int) -> list[str]:
    """Greedily join space-separated units into groups of at most ``budget`` chars."""
    chunks: list[str] = []
    cur = ""
    for u in units:
        if not cur:
            cur = u
        elif len(cur) + 1 + len(u) <= budget:
            cur = f"{cur} {u}"
        else:
            chunks.append(cur)
            cur = u
    if cur:
        chunks.append(cur)
    return chunks


def split_ssml_for_polly(ssml: str, max_ssml_chars: int = MAX_SSML_CHARS) -> list[str]:
    """Return one or more ``<speak>…</speak>`` chunks, each within the size limit.

    Short blocks return ``[ssml]`` unchanged. The concatenation of all chunks'
    spoken text equals the original (whitespace-normalised), preserving word order.
    A trailing ``<break>`` (inter-block pacing) is kept only on the final chunk.
    """
    if len(ssml) <= max_ssml_chars:
        return [ssml]

    try:
        root = ET.fromstring(ssml)
    except ET.ParseError as e:
        raise ValueError(f"cannot split malformed SSML ({len(ssml)} chars): {e}") from e

    rich = [el for el in root.iter() if el is not root and el.tag != "break"]
    text = _WS_RE.sub(" ", "".join(root.itertext())).strip()
    if rich or not text:
        raise ValueError(
            f"block SSML is {len(ssml)} chars (over Polly's limit) and contains "
            "markup that cannot be safely split — shorten this block in NotebookForge"
        )

    trailing = ""
    m = re.search(r"(<break\b[^>]*/>)\s*</speak>\s*$", ssml)
    if m:
        trailing = m.group(1)

    budget = max_ssml_chars - _WRAP_OVERHEAD - len(trailing)
    # Sentences first; break any single over-budget sentence on word boundaries.
    units: list[str] = []
    for sentence in _SENTENCE_RE.split(text):
        if len(sentence) <= budget:
            units.append(sentence)
        else:
            units.extend(_pack(sentence.split(" "), budget))

    chunk_texts = _pack(units, budget)
    out = []
    for i, ct in enumerate(chunk_texts):
        tail = trailing if i == len(chunk_texts) - 1 else ""
        out.append(f"<speak>{_esc(ct)}{tail}</speak>")
    return out
