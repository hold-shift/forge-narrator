"""The block hash — the spine of the whole system (see Overview §"The hash").

Each narratable block's hash is ``sha256`` over ``{ssml_string, voice, engine}``.
NotebookForge computes it on export; the generator caches Polly output by it so
only changed blocks re-synthesise, and changing voice/engine changes every hash
(forcing a correct full regen).

The hash travels in the manifest and is authoritative — the generator uses the
manifest-provided value as the cache key. This helper exists so we can (a) verify
manifest integrity and (b) build test fixtures.

The recipe is **plain concatenation** ``sha256(ssml + voice + engine)`` with no
separator — reverse-engineered from a real NotebookForge export (Spec A) and
confirmed byte-identical, so a recomputed hash matches the manifest.
"""

from __future__ import annotations

import hashlib


def block_hash(ssml: str, voice: str, engine: str) -> str:
    """Return the hex sha256 for a block's ``(ssml, voice, engine)``."""
    payload = (ssml + voice + engine).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
