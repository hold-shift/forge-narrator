"""The block hash — the spine of the whole system (see Overview §"The hash").

Each narratable block's hash is ``sha256`` over ``{ssml_string, voice, engine}``.
NotebookForge computes it on export; the generator caches Polly output by it so
only changed blocks re-synthesise, and changing voice/engine changes every hash
(forcing a correct full regen).

The hash travels in the manifest and is authoritative — the generator uses the
manifest-provided value as the cache key. This helper exists so we can (a) verify
manifest integrity and (b) build test fixtures. Keep it byte-identical to
NotebookForge's implementation (Spec A) so a recomputed hash matches the manifest.
"""

from __future__ import annotations

import hashlib

# A NUL separator between fields prevents ambiguity (e.g. ssml ending in the voice
# name). NotebookForge (Spec A) must use the same construction.
_SEP = "\x00"


def block_hash(ssml: str, voice: str, engine: str) -> str:
    """Return the hex sha256 for a block's ``(ssml, voice, engine)``."""
    payload = _SEP.join((ssml, voice, engine)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
